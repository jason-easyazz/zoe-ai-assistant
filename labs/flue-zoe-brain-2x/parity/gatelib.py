#!/usr/bin/env python3
"""Shared harness library for the Flue-brain quality gates (LAB-ONLY).

One home for the mechanics every gate needs, so gates stay small and can't
drift apart by each re-implementing provisioning / sessions / DB verification.
Encodes the lessons the ad-hoc 2026-07-07 gate runs paid for:

- **Per-run nonce'd session ids.** Sessions are ownership-bound (a foreign
  session id 403s) AND durable — a long-lived session's assembled prompt
  eventually exceeds the model context (8192 tok) and every turn 500s. So each
  gate RUN mints its own nonce and never reuses a hardcoded session name.
- **DB-truth verification, not reply-trust.** A write is "done" only when the
  row is in Postgres; the model's "I've added it" is not evidence.
- **Fallback text = failure.** The brain's "trouble reaching my brain" fallback
  must never score as a pass (it silently degraded gate evidence once).
- **Operational guards.** Don't measure while memory is tight (Kokoro/brain OOM
  risk) or while main is mid-merge-train (deploy.yml restarts zoe-data on every
  push and wrecks a run); stamp the service start time so a mid-run restart is
  detectable.

A gate module exposes a module-level ``GATE = GateSpec(name, description, run)``
where ``run(ctx: GateContext) -> None`` records rows via ``ctx.recorder``. The
runner (run_gates.py) discovers every ``*_gate.py`` with a ``GATE`` and drives
them against ONE freshly provisioned user.

Runs under system python3 like the service (needs asyncpg, available in the
zoe-data venv/site-packages). LAB ONLY — never imported by services/zoe-data.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

def _resolve_live_root() -> Path:
    """The checkout that holds the LIVE .env and serves the running services.

    Gates always talk to the live host on localhost, so they need the live
    secrets — which live only in the (gitignored) .env of the deployed checkout.
    When run from a dev worktree (no .env), fall back to the canonical live
    checkout. Override with ZOE_LIVE_ROOT.
    """
    import os

    here = Path(__file__).resolve().parents[3]
    if (here / "services" / "zoe-data" / ".env").is_file():
        return here
    override = os.environ.get("ZOE_LIVE_ROOT")
    if override and (Path(override) / "services" / "zoe-data" / ".env").is_file():
        return Path(override)
    return Path("/home/zoe/assistant")


REPO_ROOT = _resolve_live_root()
SERVICE_DIR = REPO_ROOT / "services" / "zoe-data"
ZOE_DATA_ENV = SERVICE_DIR / ".env"
PROVISION_SCRIPT = REPO_ROOT / "scripts" / "maintenance" / "provision_parity_test_user.py"

DATA_BASE = "http://127.0.0.1:8000"
AUTH_BASE = "http://127.0.0.1:8002"

# The brain's degraded reply — must never count as a pass (see module docstring).
BRAIN_FALLBACK_MARKERS = ("trouble reaching my brain", "having trouble reaching")


# --------------------------------------------------------------------------- #
# Environment / provisioning
# --------------------------------------------------------------------------- #
def env_value(key: str) -> str:
    """Read one key from services/zoe-data/.env (the canonical live values)."""
    if not ZOE_DATA_ENV.is_file():
        return ""
    for raw in ZOE_DATA_ENV.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def internal_token() -> str:
    tok = env_value("ZOE_INTERNAL_TOKEN")
    if not tok:
        raise RuntimeError(
            "ZOE_INTERNAL_TOKEN not set in services/zoe-data/.env — the two-user "
            "isolation checks need it. Provision it first (operator step)."
        )
    return tok


def provision_user(username: str) -> str:
    """Mint (or rotate) a demo test user, returning its fresh password.

    Delegates to scripts/maintenance/provision_parity_test_user.py, which
    enforces the demo-only (parity-/test-) username guardrail and forces the
    'user' role. Returns the one-time password printed by the script.
    """
    if not username.startswith(("parity-", "test-")):
        raise ValueError("gate users must start with parity-/test- (demo guardrail)")

    import os

    # Hand the subprocess the live POSTGRES_URL so it works even when the gate
    # code runs from a worktree whose own .env is absent (gitignored).
    child_env = dict(os.environ)
    pg = env_value("POSTGRES_URL")
    if pg:
        child_env["POSTGRES_URL"] = pg

    def _run(rotate: bool) -> subprocess.CompletedProcess:
        cmd = [sys.executable, str(PROVISION_SCRIPT), "--username", username]
        if rotate:
            cmd.append("--rotate-password")
        return subprocess.run(cmd, cwd=str(REPO_ROOT), env=child_env,
                              capture_output=True, text=True, timeout=120)

    proc = _run(rotate=False)
    out = proc.stdout
    if "password:" not in out:
        # Existing user (or an EXISTS branch) → rotate to get a known password.
        proc = _run(rotate=True)
        out = proc.stdout
    for line in out.splitlines():
        if "password:" in line:
            return line.split("password:", 1)[1].strip()
    raise RuntimeError(f"provisioning failed for {username}: {proc.stdout}\n{proc.stderr}")


def login(username: str, password: str) -> str:
    """Log a provisioned user into zoe-auth, return the session id."""
    payload = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{AUTH_BASE}/api/auth/login", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read().decode())
    sid = body.get("session_id") or (body.get("session") or {}).get("session_id")
    if not sid:
        raise RuntimeError(f"login for {username} returned no session_id: {list(body)}")
    return sid


# --------------------------------------------------------------------------- #
# DB ground truth
# --------------------------------------------------------------------------- #
def db_rows(sql: str, *params) -> list[dict]:
    """Run a read-only query against the live Postgres and return dict rows.

    Bootstraps POSTGRES_URL exactly as the service does. Gates use this for
    said-vs-did verification — the only trustworthy signal that a write landed.
    """
    import asyncio
    import os

    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    from runtime_env import bootstrap_runtime_env  # type: ignore[import]

    bootstrap_runtime_env()
    import asyncpg  # type: ignore[import]

    async def _q():
        conn = await asyncpg.connect(os.environ["POSTGRES_URL"])
        try:
            rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    return asyncio.run(_q())


def list_item_present(text: str, *, active_only: bool = False) -> tuple[bool, str]:
    """Convenience DB check for shopping/list writes: (present, short_repr)."""
    rows = db_rows(
        "SELECT text, completed, deleted FROM list_items WHERE text ILIKE $1",
        f"%{text}%",
    )
    if active_only:
        live = [r for r in rows if not r["deleted"] and not r["completed"]]
        return bool(live), str(rows)[:150]
    return bool([r for r in rows if not r["deleted"]]), str(rows)[:150]


def db_execute(sql: str, *params) -> str:
    """Run a write statement against the live Postgres; return the status tag."""
    import asyncio
    import os

    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    from runtime_env import bootstrap_runtime_env  # type: ignore[import]

    bootstrap_runtime_env()
    import asyncpg  # type: ignore[import]

    async def _q():
        conn = await asyncpg.connect(os.environ["POSTGRES_URL"])
        try:
            return await conn.execute(sql, *params)
        finally:
            await conn.close()

    return asyncio.run(_q())


# Regex fragments matching payloads the gates WRITE. Load-bearing safety net:
# a gate write like "add X to my shopping list" lands on the FAMILY-shared list
# (visibility='family', owned by the household admin) — NOT the test user's own
# store — so it is visible to the real household and accumulates as clutter on
# real data. Every gate must self-purge its writes at end of run; this list is
# how purge_artifacts finds them regardless of which list they landed on. Add a
# fragment here whenever a gate introduces a new write payload.
# Eval-ONLY phrases: distinctive enough that no real user item/fact/reminder
# matches, so they are safe to hard-delete on sight even without the run nonce.
# Anything that could be a real item (e.g. "laundry powder", "vet visit",
# "water the plants") is NOT listed here — instead the gate nonce-tags those
# writes so the `%nonce%` match below catches them. No apostrophes (they would
# break the interpolated SQL string literal).
GATE_WRITE_MARKERS = (
    r"unobtainium-", r"smoketest-", r"contraband-", r"set the owner",
    r"zephyrina", r"grateful for the rain today",
    r"to my contacts as my colleague", r"wifi password is hunter2",
    r"name is biscuit",
)


def purge_artifacts(nonce: str) -> dict[str, str]:
    """HARD-delete every DB row a gate run wrote — matched by the run nonce OR a
    known gate write marker — across the user-scoped tables (incl. the
    family-shared list surface). Idempotent; safe to call in a finally block.

    Scoped by construction to gate-authored content: the nonce is per-run unique
    and the markers are eval-only strings no real user would produce. Returns a
    per-table status map for the report.
    """
    like_nonce = f"%{nonce}%"
    markers = list(GATE_WRITE_MARKERS)
    out: dict[str, str] = {}
    # (table, column) pairs — all hardcoded literals in this file, never external
    # input. The nonce and the markers are bound as query PARAMETERS ($1 text,
    # $2 text[]): `col ~* ANY($2)` matches any marker regex, so nothing is
    # interpolated into the SQL (apostrophe-safe) and every table uses `col`
    # directly (no text→col string surgery).
    targets = (("list_items", "text"), ("notes", "content"),
               ("journal_entries", "content"), ("people", "name"),
               ("events", "title"), ("reminders", "title"))
    for table, col in targets:
        out[table] = db_execute(
            f"DELETE FROM {table} WHERE {col} ILIKE $1 OR {col} ~* ANY($2::text[])",
            like_nonce, markers,
        )
    return out


# --------------------------------------------------------------------------- #
# Verdict recording
# --------------------------------------------------------------------------- #
@dataclass
class Recorder:
    """Collects gate rows and their verdicts. PASS/FAIL/ERROR/JUDGE."""

    gate: str = ""
    rows: list[dict] = field(default_factory=list)

    def add(self, category: str, query: str, reply: str, ms: float,
            verdict: str, why: str, who: str = "A") -> None:
        self.rows.append({
            "gate": self.gate, "category": category, "who": who,
            "query": query, "reply": reply[:400], "ms": round(ms),
            "verdict": verdict, "why": why,
        })
        print(f"[{verdict:5s}] {self.gate}/{category:18s} {query[:42]!r} :: {why[:56]}")

    def check(self, category: str, label: str, ok: bool, detail: str,
              why_pass: str, why_fail: str) -> None:
        """Record a non-chat assertion (e.g. a DB-truth verification)."""
        self.add(category, label, detail, 0,
                 "PASS" if ok else "FAIL", why_pass if ok else why_fail)


# --------------------------------------------------------------------------- #
# Live-brain interaction
# --------------------------------------------------------------------------- #
@dataclass
class GateContext:
    """Everything a gate needs to talk to the live brain as its test user."""

    sid: str                 # primary user's X-Session-ID
    user_id: str             # primary user id
    nonce: str               # per-run uniqueness
    recorder: Recorder
    token: str = ""          # ZOE_INTERNAL_TOKEN, for the second-user override
    user_b: str = ""         # a REAL provisioned second user (isolation checks)

    def session(self, name: str) -> str:
        """A nonce'd, ownership-safe, single-run session id."""
        return f"gate-{name}-{self.nonce}"

    def _headers(self, who: str, user_b: str) -> dict:
        if who == "A":
            return {"X-Session-ID": self.sid}
        # Reach the second user through the trusted internal override; default to
        # the runner-provisioned ctx.user_b so gates never hardcode an identity.
        return {"X-Internal-Token": self.token, "X-Zoe-User-Id": user_b or self.user_b}

    def chat(self, msg: str, session: str, who: str = "A",
             user_b: str = "", timeout: float = 120) -> tuple[str, float]:
        payload = json.dumps({"message": msg, "session_id": session, "stream": False}).encode()
        req = urllib.request.Request(
            f"{DATA_BASE}/api/chat/?stream=false", data=payload,
            headers={"Content-Type": "application/json", **self._headers(who, user_b)},
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode() or "{}")
            reply = body.get("response") or body.get("error") or "(no response)"
        except Exception as e:  # noqa: BLE001 — record, never die mid-gate
            reply = f"(ERROR: {e})"
        return reply, (time.time() - t0) * 1000

    def api_get(self, path: str, who: str = "A", user_b: str = "") -> dict:
        req = urllib.request.Request(
            f"{DATA_BASE}{path}",
            headers={"Content-Type": "application/json", **self._headers(who, user_b)},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode() or "{}")
        except Exception as e:  # noqa: BLE001
            return {"_error": str(e)}

    def expect(self, category: str, query: str, session: str, *,
               must: list[str] | None = None, must_not: list[str] | None = None,
               who: str = "A", user_b: str = "") -> str:
        """Send a turn; auto-verdict against must/must_not substrings.

        A reply with no must/must_not is JUDGE (needs a human/model read).
        The brain-fallback text always FAILs — it is never a real answer.
        """
        reply, ms = self.chat(query, session, who=who, user_b=user_b)
        rl = reply.lower()
        if reply.startswith("(ERROR"):
            self.recorder.add(category, query, reply, ms, "ERROR", "transport", who)
            return reply
        if any(m in rl for m in BRAIN_FALLBACK_MARKERS):
            self.recorder.add(category, query, reply, ms, "FAIL", "brain fallback text", who)
            return reply
        bad = [m for m in (must_not or []) if m.lower() in rl]
        missing = [m for m in (must or []) if m.lower() not in rl]
        if bad:
            self.recorder.add(category, query, reply, ms, "FAIL", f"forbidden {bad}", who)
        elif missing:
            self.recorder.add(category, query, reply, ms, "FAIL", f"missing {missing}", who)
        elif must or must_not:
            self.recorder.add(category, query, reply, ms, "PASS", "auto", who)
        else:
            self.recorder.add(category, query, reply, ms, "JUDGE", "needs judgment", who)
        return reply


@dataclass
class GateSpec:
    name: str
    description: str
    run: Callable[[GateContext], None]


# Shared adversarial constants, reused across gates.
FORBIDDEN_IDENTITY = ["gemma", "deepmind", "google", "large language model",
                      "openai", "chatgpt", "anthropic"]
RESEARCH_STALL = ["before i start research", "what budget", "price range should"]


# --------------------------------------------------------------------------- #
# Operational guards
# --------------------------------------------------------------------------- #
def available_mb() -> int:
    for raw in Path("/proc/meminfo").read_text().splitlines():
        if raw.startswith("MemAvailable:"):
            return int(raw.split()[1]) // 1024
    return 0


def service_started_at() -> str:
    try:
        return subprocess.run(
            ["systemctl", "--user", "show", "zoe-data", "-p", "ActiveEnterTimestamp", "--value"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "?"


def health_ok() -> bool:
    try:
        with urllib.request.urlopen(f"{DATA_BASE}/health", timeout=5) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def main_sha() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-remote", "origin", "main"],
            capture_output=True, text=True, timeout=20,
        ).stdout.split("\t")[0][:12]
    except Exception:  # noqa: BLE001
        return "?"
