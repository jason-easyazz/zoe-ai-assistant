#!/usr/bin/env python3
"""Statistical RELIABILITY gate for the live flue brain (LAB-ONLY).

Single-pass parity gates hide 4B-model variance: a prompt that passes one round
can fail the next round *identically phrased* (the Kate-not-Katie recall, the
"what's your name" persona leak recorded in FIX-PACKET-2026-07-07.md item 2).
This gate mirrors the rigor that proved recall_memory at ~97% over ≥30 trials
(recall_reliability.py / RELIABILITY.md): it takes the highest-value,
historically-flaky assertions and runs EACH one N times (default 10), reporting
a per-assertion pass RATE and gating on a configurable per-assertion threshold.

It drives the SAME live surface production uses — zoe-data's authenticated
`/api/chat/?stream=false`, which routes to the flue sidecar because this
deployment runs `ZOE_BRAIN_BACKEND=flue` (see labs/AGENTS.md). It is therefore
a whole-pipeline reliability read (brain + fast-path), not a brain-direct one;
that is deliberate — the user experiences the pipeline.

Session discipline (MANDATORY, matches hard_gate.py):
  * a fresh authenticated test user is provisioned PER RUN
    (provision_parity_test_user.py mints a real zoe-auth account, then we login
    for the X-Session-ID) so each run starts from an empty memory store;
  * every trial uses a FRESH nonce'd conversation `session_id` — sessions are
    ownership-bound and a long shared session overflows the brain at 8192
    tokens, silently corrupting later trials.

Auto-verification (no human judgment in the pass/fail path, like hard_gate.py):
  * recall / identity / research assertions score by substring on the reply;
  * write assertions score against Postgres ground truth (list_items), so a
    reply that *claims* it wrote but didn't is a FAIL.

Assertion set (the historically-flaky, highest-value five):
  1. recall_just_stored  — store a nonce fact, recall it a turn later.
  2. corrected_recall    — "Kate, not Katie": the correction must win.
  3. identity            — "what's your name" = Zoe, never Gemma/Google/LLM.
  4. research_no_stall    — a conversational statement must not trigger the
                            research-followup stall.
  5. write_verified      — add an item, confirm it in the DB (said == did).

Gate thresholds (per assertion; --threshold-* to override):
  * identity / research / write MUST be deterministic  → default 100%.
  * recall assertions are model judgment              → default ≥90%.

Run (zoe-data live on :8000, ZOE_BRAIN_BACKEND=flue, on the Zoe host):
    python3 parity/reliability_gate.py                 # N=10 per assertion
    python3 parity/reliability_gate.py --trials 2      # quick smoke

Keep it sequential — one shared GPU, no concurrency. Do NOT run the full N=10
sweep while the shared brain is mid-campaign.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path("/home/zoe/assistant")
BASE = "http://127.0.0.1:8000"
AUTH_BASE = "http://127.0.0.1:8002"
PROVISION = REPO_ROOT / "scripts" / "maintenance" / "provision_parity_test_user.py"

# Give per-turn memory extraction time to persist before a cross-turn recall.
# hard_gate.py uses 45s for its cross-session recalls; we recall within the same
# run so 30s is a safe floor. Overridable for a faster smoke.
DEFAULT_EXTRACT_WAIT_S = 30.0

FORBIDDEN_IDENTITY = [
    "gemma", "deepmind", "google", "large language model",
    "openai", "chatgpt", "anthropic", "i am an ai", "language model",
]
RESEARCH_STALL = [
    "before i start research", "what budget", "price range should",
    "what location", "bit more detail",
]


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
def _call(path: str, payload=None, headers=None, method=None, timeout=120, base=BASE):
    url = base + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode() or "{}")
    return body, (time.time() - t0) * 1000.0


def chat(msg: str, session: str, sid: str, timeout=120) -> tuple[str, float]:
    """One turn through the live authenticated chat surface (routes to flue)."""
    try:
        body, ms = _call(
            "/api/chat/?stream=false",
            {"message": msg, "session_id": session, "stream": False},
            {"X-Session-ID": sid}, timeout=timeout,
        )
        return (body.get("response") or body.get("error") or "(no response)"), ms
    except Exception as e:  # noqa: BLE001 — record, don't die mid-run
        return f"(ERROR: {e})", 0.0


def nonce_session(tag: str) -> str:
    """Per-trial conversation session id — fresh nonce so no trial shares a
    session (ownership-bound; long sessions overflow the brain at 8192 tokens)."""
    return f"reliability-{tag}-{int(time.time())}-{secrets.token_hex(4)}"


# --------------------------------------------------------------------------- #
# per-run user provisioning
# --------------------------------------------------------------------------- #
def provision_user() -> tuple[str, str]:
    """Mint a FRESH authenticated test user for this run and log in.

    Returns (username, x_session_id). A unique nonce'd username means an empty
    memory store per run — no contamination from a prior run's stored facts.
    """
    username = f"parity-rel-{int(time.time())}-{secrets.token_hex(3)}"
    proc = subprocess.run(
        [sys.executable, str(PROVISION), "--username", username],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"provision failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    password = None
    for line in proc.stdout.splitlines():
        s = line.strip()
        if s.startswith("password:"):
            password = s.split(":", 1)[1].strip()
            break
    if not password:
        raise RuntimeError(f"could not parse provisioned password from:\n{proc.stdout}")

    body, _ = _call(
        "/api/auth/login", {"username": username, "password": password},
        base=AUTH_BASE, timeout=30,
    )
    sid = body.get("session_id")
    if not sid:
        raise RuntimeError(f"login returned no session_id: {json.dumps(body)[:200]}")
    return username, sid


# --------------------------------------------------------------------------- #
# DB ground truth (writes) — mirrors hard_gate.db_item_state
# --------------------------------------------------------------------------- #
def db_item_present(text: str) -> bool:
    """True iff a non-deleted list item matching `text` exists in Postgres."""
    import asyncio
    zoe_data_path = str(REPO_ROOT / "services" / "zoe-data")
    if zoe_data_path not in sys.path:  # idempotent — called once per write trial
        sys.path.insert(0, zoe_data_path)
    from runtime_env import bootstrap_runtime_env  # noqa: E402
    bootstrap_runtime_env()
    import asyncpg  # noqa: E402

    async def q():
        conn = await asyncpg.connect(os.environ["POSTGRES_URL"])
        try:
            rows = await conn.fetch(
                "SELECT deleted FROM list_items WHERE text ILIKE $1", f"%{text}%")
        finally:
            await conn.close()  # guarantee teardown even if fetch raises
        return rows

    rows = asyncio.run(q())
    return bool([r for r in rows if not r["deleted"]])


# --------------------------------------------------------------------------- #
# assertions — each is a callable(sid, extract_wait) -> (ok: bool, detail: str)
# Each runs entirely within its own fresh nonce'd session(s).
# --------------------------------------------------------------------------- #
def a_recall_just_stored(sid: str, extract_wait: float):
    mark = secrets.token_hex(3)
    fact = f"my locker code is {mark}"
    s1 = nonce_session("recall-store")
    chat(f"Remember this: {fact}", s1, sid)
    time.sleep(extract_wait)
    s2 = nonce_session("recall-ask")
    reply, _ = chat("What's my locker code?", s2, sid)
    ok = mark in reply.lower()
    return ok, f"want {mark!r} in reply :: {reply.strip()[:120]}"


def a_corrected_recall(sid: str, extract_wait: float):
    s = nonce_session("corrected")
    chat("My sister's name is Katie", s, sid)
    chat("Actually sorry, it's Kate, not Katie", s, sid)
    time.sleep(extract_wait)
    s2 = nonce_session("corrected-ask")
    reply, _ = chat("What's my sister's name?", s2, sid)
    rl = reply.lower()
    # "kate" must be present AND "katie" absent — a reply that still says
    # "katie" (even alongside "kate", e.g. the "name is Katie… called Kate"
    # waffle) is a FAIL. (Do NOT strip "kate" out first — that turns "katie"
    # into "i" and would score the waffle as a pass, under-counting failures.)
    ok = "kate" in rl and "katie" not in rl
    return ok, f"want Kate not Katie :: {reply.strip()[:120]}"


def a_identity(sid: str, extract_wait: float):
    s = nonce_session("identity")
    reply, _ = chat("What's your name again?", s, sid)
    rl = reply.lower()
    bad = [w for w in FORBIDDEN_IDENTITY if w in rl]
    ok = ("zoe" in rl) and not bad
    return ok, f"want Zoe, forbidden={bad or '-'} :: {reply.strip()[:120]}"


def a_research_no_stall(sid: str, extract_wait: float):
    s = nonce_session("research")
    reply, _ = chat("We had the best weekend ever at the beach", s, sid)
    rl = reply.lower()
    stalled = [w for w in RESEARCH_STALL if w in rl]
    ok = not stalled
    return ok, f"no research stall, hit={stalled or '-'} :: {reply.strip()[:120]}"


def a_write_verified(sid: str, extract_wait: float):
    item = f"unobtainium-{secrets.token_hex(3)}"
    s = nonce_session("write")
    reply, _ = chat(f"Add {item} to my shopping list", s, sid)
    time.sleep(3)
    try:
        present = db_item_present(item)
    except Exception as e:  # noqa: BLE001
        return False, f"DB check errored: {e}"
    return present, f"DB has {item}?={present} :: {reply.strip()[:100]}"


# (key, label, callable, kind) — kind picks the default threshold.
ASSERTIONS = [
    ("recall_just_stored", "recall of a just-stored fact", a_recall_just_stored, "recall"),
    ("corrected_recall", "corrected-value recall (Kate not Katie)", a_corrected_recall, "recall"),
    ("identity", "identity: name = Zoe", a_identity, "deterministic"),
    ("research_no_stall", "research-trap statement doesn't stall", a_research_no_stall, "deterministic"),
    ("write_verified", "DB-verified shopping-list write", a_write_verified, "deterministic"),
]


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trials", type=int, default=10,
                    help="times to run EACH assertion (default 10; use 2 for a smoke)")
    ap.add_argument("--threshold-deterministic", type=float, default=1.0,
                    help="pass rate required for identity/research/write (default 1.0 = 100%%)")
    ap.add_argument("--threshold-recall", type=float, default=0.9,
                    help="pass rate required for model-judgment recall (default 0.9 = 90%%)")
    ap.add_argument("--extract-wait", type=float, default=DEFAULT_EXTRACT_WAIT_S,
                    help="seconds to wait for per-turn memory extraction before a recall")
    ap.add_argument("--out", default=str(Path(__file__).parent / "reliability_gate_last.json"),
                    help="where to write the machine-readable result (gitignored runtime artifact)")
    args = ap.parse_args()

    thr = {"recall": args.threshold_recall, "deterministic": args.threshold_deterministic}

    print(f"RELIABILITY GATE start {time.strftime('%H:%M:%S')} — "
          f"trials={args.trials}, extract_wait={args.extract_wait}s")
    print("provisioning fresh authenticated test user for this run...")
    username, sid = provision_user()
    print(f"  user={username}  (empty memory store)\n")

    results = []
    all_pass = True
    for key, label, fn, kind in ASSERTIONS:
        need = thr[kind]
        passes = 0
        details = []
        for t in range(args.trials):
            try:
                ok, detail = fn(sid, args.extract_wait)
            except Exception as e:  # noqa: BLE001 — a crashed trial is a FAIL, not a stop
                ok, detail = False, f"trial errored: {e}"
            passes += int(ok)
            details.append({"trial": t + 1, "ok": ok, "detail": detail})
            print(f"  [{key:20s}] trial {t+1:2d}/{args.trials}  "
                  f"{'PASS' if ok else 'FAIL'}  {detail[:90]}")
        rate = passes / args.trials if args.trials else 0.0
        gate_ok = rate >= need
        all_pass = all_pass and gate_ok
        results.append({"key": key, "label": label, "kind": kind,
                        "passes": passes, "trials": args.trials, "rate": rate,
                        "threshold": need, "gate": "PASS" if gate_ok else "FAIL",
                        "details": details})
        print(f"  → {key}: {passes}/{args.trials} = {rate*100:.0f}%  "
              f"(need {need*100:.0f}%)  {'PASS' if gate_ok else 'FAIL'}\n")

    print("=" * 78)
    print(f"{'ASSERTION':34s} {'RATE':>10s}  {'THRESH':>7s}  GATE")
    print("-" * 78)
    for r in results:
        print(f"{r['label'][:34]:34s} {r['passes']:>3d}/{r['trials']:<3d}"
              f"={r['rate']*100:>3.0f}%  {r['threshold']*100:>5.0f}%   {r['gate']}")
    print("=" * 78)
    verdict = "PASS" if all_pass else "FAIL"
    print(f"OVERALL RELIABILITY GATE: {verdict}")

    out = Path(args.out)
    out.write_text(json.dumps(
        {"user": username, "trials": args.trials, "overall": verdict,
         "thresholds": thr, "results": results}, indent=1))
    print(f"→ {out}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
