#!/usr/bin/env python3
"""self_improver.py - self-evolution Phase 1: a GEPA-style reflective improver over ONE overlay file, ``manner.md``.

Samantha Initiative roadmap item 13 / experiment 8 / lane B E1 (docs/research/samantha-initiative-2026-11-03.md and
``.../lane-B-agents-self-improvement.md``); the loop's design is docs/architecture/self-evolution-nightly-loop.md. Operator
doc: docs/knowledge/self-improver.md.

WHAT IT CHANGES. Nothing live. The target is the brain's manner block - the words zoe-data sends on the ``zoe-manner:``
envelope (``services/zoe-data/lexicons_data/en.json`` ``manner.block``) and that an overlay ``manner.md``
(labs/flue-zoe-brain-2x/src/harness.ts) may replace. The frozen Gemma model is never touched. A winning candidate is
SHADOW-APPLIED only: written under this tool's own state directory with a ``would_apply`` ledger row. Promotion to the
live overlay is a human step (``npm run harness -- switch``); this module has no code path to it, and ``safe_write``
refuses any path inside the repository or the live overlay root (pinned by a negative control in the tests).

ONE NIGHT (``night``):
  1. Gates (live only): the improver slot (default 03:30 to the 04:25 deadline, after the 12B window and before the 04:30
     voice regression), harness quiet hours + panel yield (``harness_quiet.check``), ``night_busy.sh test`` (the night
     window or the backup is running), ``zoe-voice-regression`` not active, no ``land_voice_pr.sh``, the brain-window lock
     free, MemAvailable, the fourteen-night stop rule, the reflection key present. Any one refuses the night (exit 3/4).
  2. Seed = the CURRENT manner text: the live overlay's ``manner.md`` when the brain runs one, else the shipped lexicon.
  3. DEV traces of the seed (``manner_block_ab.py --phase dev``, cached per seed sha): the only thing the reflection
     sees, with the documented intent of each failed behaviour. Never the held-out asks, needles, golds or judge text.
  4. Reflection -> K=2 candidates (one GENERAL rewrite, one TARGETED at the most-failed behaviour) from a strong cloud
     model on a capped key (``ZOE_IMPROVER_ANTHROPIC_KEY``, read by NAME at call time, never printed or stored), with a
     hard per-night token AND dollar cap enforced before every call. ``--dry-run`` uses an offline proposer: no key.
  5. Held-out probe on the 4B (``manner_block_ab.py --phase heldout``): arms R0 (seed), C1, C2 and the SAMPLING CONTROL
     S1, S2 (the seed again, two more draws: equal brain cost to the two candidates).
  6. Verdict: a WIN needs the best candidate's held-out pass rate STRICTLY above the best of R0/S1/S2 (both sides take a
     max of draws, so the selection bias is symmetric) AND the collateral gate: ``bar_reliability.compare`` (the pass^k /
     collateral-damage gate, #2278) of candidate vs R0 is not ``worse`` and has no more per-ask regressions than the
     sampling arms show against R0 (the measured noise floor). On a would-be win the regression phase (P2/P5a/P5c/P6.b/
     P7/P8 + the bar sample S1/S3/S4/S28-S31) runs for R0 vs the winner; any half that separates WORSE (Wilson) vetoes.
  7. Shadow-apply the winner, write the night row, the lessons (edit + won/lost only) and the stop-rule state.

STOP RULE (pre-registered, lane B E1 / design section 9). Only LIVE, MEASURED nights count; a skipped, refused or
unmeasured night is logged and not counted. Win in >= 10 of the first 14 measured nights = Phase 1 passes; a 5th loss
makes 10/14 impossible, so the loop STOPS early (``STOPPED`` file; every later live night refuses). Dry-run nights never
count.

State (``--home``, default ~/.zoe/self-improver): ``nights.jsonl`` (one row per night), ``nights/<id>/`` (seed, candidates,
reflection metadata, verdict, ``shadow/manner.md``), ``shadow-ledger.jsonl``, ``lessons.jsonl``, ``cost/<id>.json``,
``state.json``, ``ab/`` (the A/B's rows + brain-time ledger). Exit: 0 measured night (win or loss) / 2 unmeasured or
error / 3 skipped by a gate / 4 refused (stop rule, no key, night already measured).

Stdlib only (the anthropic SDK is imported lazily, live reflection only); the A/B rig runs as a SUBPROCESS on the
zoe-data interpreter, so this module and its tests run on the slim CI lane.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
from subprocess import Popen as _Spawn   # the one process spawner (run_in_group); named so the no-other-file-handle pin never mistakes it for a bare open call
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

REPO = Path(__file__).resolve().parents[2]
SCHEMA = "zoe.self_improver.night/1"
HOME_DEFAULT = Path.home() / ".zoe" / "self-improver"
LIVE_OVERLAY_ROOT = Path.home() / ".zoe" / "brain-harness"     # the overlay ROOT labs/flue-zoe-brain-2x/scripts documents
FLUE_ENV = REPO / "labs" / "flue-zoe-brain-2x" / ".env"
LEXICON = REPO / "services" / "zoe-data" / "lexicons_data" / "en.json"
AB_SCRIPT = REPO / "scripts" / "perf" / "manner_block_ab.py"
HARNESS_LOCK = "/tmp/zoe-voice-harness.lock"
BRAIN_WINDOW_LOCK = "/tmp/zoe-brain-window.lock"
LANDER_PATTERN = r"(^|[ /])land_voice_pr\.sh( |$)"

KEY_ENV = "ZOE_IMPROVER_ANTHROPIC_KEY"            # read by NAME at call time; the value is never logged, stored or echoed
MODEL_DEFAULT = "claude-sonnet-5-5"
#: $ per million tokens (input, output). A model with no price here is REFUSED: a dollar cap needs a price.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
MAX_OUTPUT_TOKENS_PER_CALL = 6000                 # thinking + the answer; the reservation assumes all of it is spent
K_CANDIDATES = 2
KINDS = ("general", "targeted")
#: the product's own budget for the block (manner_block.MAX_BLOCK_CHARS = 160 tokens x 4); under the 800-char wire ceiling
MAX_MANNER_CHARS = 640
#: harness.ts FORBIDDEN_MARKERS, mirrored: an overlay carrying one is refused by the brain's loader
FORBIDDEN_MARKERS = ("<<<", ">>>", "<start_of_turn", "<end_of_turn", "<|", "|>", "[[zoe-harness-render:")
CONTROL_RE = re.compile(r"[\u0000-\u0009\u000b-\u001f\u007f]")
CELL_TARGET = {"flaw": "P5b.b", "good": "P5b.u", "P6": "P6.a", "P9": "P9.a"}
#: the documented intent of each primary behaviour (docs/knowledge/manner-block.md) - what the reflection may read. Not a
#: rubric, not a needle list: the scorers' words never leave the rig.
HALF_INTENT = {
    "P5b.b": "when the person shares a plan with a real problem (health, safety, money, a harsh message, a missing basic), "
             "the reply names that problem, kindly, early",
    "P5b.u": "when the plan is genuinely fine, the reply says so plainly without inventing a concern or a warning",
    "P6.a": "when the person shares a feeling, the reply shows it was heard before anything else",
    "P9.a": "when the assistant was handed a due follow-up, it voices it",
}
STOP_NIGHTS, STOP_WINS = 14, 10
EXIT_MEASURED, EXIT_UNMEASURED, EXIT_SKIPPED, EXIT_REFUSED = 0, 2, 3, 4
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HELDOUT_ID_RE = re.compile(r"-(flaw|good)H-")


# ─────────────────────────────────────────────────────────────────────────────
# Small loaders (by FILE PATH, like bar_reliability: scripts/maintenance shadows service modules)
# ─────────────────────────────────────────────────────────────────────────────

def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def bar_reliability():
    return _load("bar_reliability", REPO / "scripts" / "perf" / "bar_reliability.py")


def typed_env():
    return _load("typed_env", REPO / "services" / "zoe-data" / "typed_env.py")


def harness_quiet():
    return _load("harness_quiet", REPO / "scripts" / "maintenance" / "harness_quiet.py")


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class Config:
    model: str = MODEL_DEFAULT
    max_tokens_night: int = 40_000            # reflection tokens (input + output) per night
    max_usd_night: float = 0.40               # reflection dollars per night
    brain_budget_s: float = 1500.0            # 4B brain time per night across every A/B phase
    deadline: str = "04:25"                   # nothing of the night may still run after this (04:30 = voice regression)
    start_after: str = "03:30"                # never before the 12B window has had its time
    cells: tuple[str, ...] = ("flaw", "good")
    regress_scale: float = 0.5
    min_mem_mb: int = 1500

    @classmethod
    def from_env(cls) -> "Config":
        te = typed_env()
        return cls(model=te.env_str("ZOE_IMPROVER_MODEL", MODEL_DEFAULT),
                   max_tokens_night=te.env_int("ZOE_IMPROVER_MAX_TOKENS_NIGHT", 40_000),
                   max_usd_night=te.env_float("ZOE_IMPROVER_MAX_USD_NIGHT", 0.40),
                   brain_budget_s=te.env_float("ZOE_IMPROVER_BRAIN_BUDGET_S", 1500.0),
                   deadline=te.env_str("ZOE_IMPROVER_DEADLINE", "04:25"),
                   cells=tuple(c for c in te.env_list("ZOE_IMPROVER_CELLS", ("flaw", "good")) if c))


def _hm(raw: str) -> dt.time:
    h, m = raw.split(":")
    return dt.time(int(h), int(m))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Shadow-only writes: the ONE write path of this module
# ─────────────────────────────────────────────────────────────────────────────

class ShadowViolation(RuntimeError):
    """A write that could reach the live brain (the repo, the live overlay) - never allowed."""


def live_overlay_roots(flue_env: Path | None = None) -> list[Path]:
    """Every place the live brain could read an overlay from: the documented root, and whatever ZOE_BRAIN_HARNESS_DIR
    (the flue sidecar's env file; a PATH, not a secret) points at, plus its parent (the ``current`` symlink's root)."""
    roots = [LIVE_OVERLAY_ROOT]
    configured = _env_file_value(FLUE_ENV if flue_env is None else flue_env, "ZOE_BRAIN_HARNESS_DIR")
    if configured:
        p = Path(os.path.expanduser(configured))
        roots += [p, p.parent]
    return roots


def forbidden_roots(extra: Iterable[Path] = ()) -> list[Path]:
    return [REPO, Path.home() / "assistant", *live_overlay_roots(), *extra]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def check_home(home: Path, extra_forbidden: Iterable[Path] = ()) -> Path:
    """The state directory must be outside the repo and every live overlay root (resolved: a symlink cannot smuggle it in)."""
    real = Path(os.path.realpath(home))
    for root in forbidden_roots(extra_forbidden):
        r = Path(os.path.realpath(root))
        if _inside(real, r) or _inside(r, real):
            raise ShadowViolation(f"state directory {home} overlaps {root} (the repo or a live overlay root): refused")
    return real


def safe_write(home: Path, rel: str | Path, text: str, *, extra_forbidden: Iterable[Path] = ()) -> Path:
    """Write ``text`` at ``home/rel`` - and ONLY there. Refuses an absolute or escaping ``rel``, a path whose resolved
    location leaves ``home`` (a planted symlink), and anything inside the repo or a live overlay root."""
    home_real = check_home(home, extra_forbidden)
    rel = Path(rel)
    if rel.is_absolute() or ".." in rel.parts:
        raise ShadowViolation(f"{rel}: not a plain relative path")
    if not rel.parts:
        raise ShadowViolation("empty path")
    home_real.mkdir(parents=True, exist_ok=True)
    # Walk the directories one component at a time WITHOUT following links: a planted symlink anywhere on the way is refused
    # before anything is created through it.
    cur = home_real
    for part in rel.parts[:-1]:
        cur = cur / part
        if cur.is_symlink():
            raise ShadowViolation(f"{rel}: {cur} is a symlink")
        if not cur.exists():
            cur.mkdir()
        elif not cur.is_dir():
            raise ShadowViolation(f"{rel}: {cur} is not a directory")
    target = cur / rel.parts[-1]
    real_parent = Path(os.path.realpath(target.parent))
    if not _inside(real_parent, home_real):
        raise ShadowViolation(f"{rel}: resolves outside the state directory ({real_parent})")
    if target.is_symlink():
        raise ShadowViolation(f"{rel}: is a symlink")
    for root in forbidden_roots(extra_forbidden):
        if _inside(real_parent, Path(os.path.realpath(root))):
            raise ShadowViolation(f"{rel}: inside {root}")
    # A fresh, never-followed temp file (O_EXCL | O_NOFOLLOW: a planted ``*.tmp`` symlink cannot redirect the bytes), then an
    # atomic rename onto the target (rename replaces a name, it never writes through one).
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        data = memoryview(text.encode("utf-8"))
        while data:
            data = data[os.write(fd, data):]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)
    return target


def append_jsonl(home: Path, rel: str, row: dict, **kw) -> None:
    path = Path(os.path.realpath(home)) / rel
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    safe_write(home, rel, existing + json.dumps(row, sort_keys=True) + "\n", **kw)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# The seed and the candidate rules
# ─────────────────────────────────────────────────────────────────────────────

def _env_file_value(path: Path, name: str) -> str | None:
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    except OSError:
        return None
    return None


def current_manner(flue_env: Path | None = None, lexicon: Path | None = None) -> tuple[str, str]:
    """(text, source): the live overlay's manner.md when the brain is configured with one, else the shipped English block."""
    lexicon = LEXICON if lexicon is None else Path(lexicon)
    configured = _env_file_value(FLUE_ENV if flue_env is None else flue_env, "ZOE_BRAIN_HARNESS_DIR")
    if configured:
        p = Path(os.path.expanduser(configured)) / "manner.md"
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            if text.endswith("\n"):          # harness.ts decodeText strips exactly ONE trailing newline (renderCompiledFiles appends it)
                text = text[:-1]
            return text, f"overlay:{p}"
    return json.loads(Path(lexicon).read_text(encoding="utf-8"))["manner"]["block"], f"lexicon:{lexicon.name}"


def validate_manner(text: str) -> str:
    """The rules a manner.md must pass to be loadable by the brain (harness.ts checkText) and to fit the product's block
    budget. Raises ValueError with the reason."""
    if not text or not text.strip():
        raise ValueError("empty")
    if text != text.strip():
        raise ValueError("leading or trailing whitespace")
    if len(text) > MAX_MANNER_CHARS:
        raise ValueError(f"{len(text)} chars, over the {MAX_MANNER_CHARS}-char block budget")
    if CONTROL_RE.search(text):
        raise ValueError("control character")
    for marker in FORBIDDEN_MARKERS:
        if marker in text:
            raise ValueError(f"forbidden marker {marker!r}")
    return text


@dataclasses.dataclass
class Candidate:
    name: str
    kind: str
    text: str
    why: str = ""

    @property
    def sha256(self) -> str:
        return sha(self.text)


# ─────────────────────────────────────────────────────────────────────────────
# The hard per-night reflection cap
# ─────────────────────────────────────────────────────────────────────────────

class CapExceeded(RuntimeError):
    pass


#: per-message framing the API adds around the text (role markers, the system wrapper): generous on purpose
FRAMING_TOKENS = 256


def estimate_tokens(text: str) -> int:
    """An UPPER BOUND on the input tokens of ``text``: a byte-level BPE token is at least one UTF-8 byte, so a text can never
    be more tokens than it has bytes. Far above the real count (~4 chars a token in English) - deliberately: the reservation
    must never under-count what a call can cost."""
    return len(text.encode("utf-8")) + FRAMING_TOKENS


@dataclasses.dataclass
class CostMeter:
    """Spend is charged at its WORST CASE before a call (``reserve``) and persisted at once, then settled to the billed usage
    after it (``settle``). A process killed mid-call therefore leaves the reservation counted: a re-run of the night can never
    spend the same allowance twice."""
    model: str
    max_tokens: int
    max_usd: float
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    refused: int = 0
    home: Path | None = None
    night: str | None = None

    def prices(self) -> tuple[float, float]:
        if self.model not in PRICES_PER_MTOK:
            raise CapExceeded(f"no price for model {self.model!r}: a dollar cap needs one (add it to PRICES_PER_MTOK)")
        return PRICES_PER_MTOK[self.model]

    def usd(self, inp: int, out: int) -> float:
        pi, po = self.prices()
        return inp * pi / 1e6 + out * po / 1e6

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def spent_usd(self) -> float:
        return self.usd(self.input_tokens, self.output_tokens)

    def reserve(self, est_input: int, max_output: int) -> tuple[int, int]:
        """Refuse a call whose WORST case (the input bound + all of max_output) would cross either cap; otherwise CHARGE that
        worst case now (and persist it) and return it for ``settle``. Called before every call."""
        if self.tokens + est_input + max_output > self.max_tokens:
            self.refused += 1
            raise CapExceeded(f"token cap: {self.tokens} spent + up to {est_input + max_output} > {self.max_tokens}")
        if self.spent_usd + self.usd(est_input, max_output) > self.max_usd:
            self.refused += 1
            raise CapExceeded(f"dollar cap: ${self.spent_usd:.4f} spent + up to ${self.usd(est_input, max_output):.4f} "
                              f"> ${self.max_usd:.2f}")
        self.input_tokens += est_input
        self.output_tokens += max_output
        self.persist()
        return est_input, max_output

    def settle(self, reserved: tuple[int, int], inp: int | None, out: int | None) -> None:
        """Replace a reservation with the billed usage. Unknown usage keeps the reservation; usage ABOVE it is counted in full."""
        ri, ro = reserved
        if inp is not None and out is not None:
            self.input_tokens += int(inp) - ri
            self.output_tokens += int(out) - ro
        self.calls += 1
        self.persist()

    def record(self, inp: int, out: int) -> None:
        """Usage with no reservation (tests, an imported total)."""
        self.input_tokens += int(inp)
        self.output_tokens += int(out)
        self.calls += 1
        self.persist()

    def persist(self) -> None:
        if self.home is not None and self.night is not None:
            self.save(self.home, self.night)

    def public(self) -> dict:
        return {"model": self.model, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "calls": self.calls, "refused": self.refused, "usd": round(self.spent_usd, 5) if self.model in PRICES_PER_MTOK else None,
                "cap_tokens": self.max_tokens, "cap_usd": self.max_usd}

    @classmethod
    def for_night(cls, home: Path, night: str, cfg: Config) -> "CostMeter":
        """The night's meter, resumed from disk: a re-run of the same night shares ONE cap."""
        m = cls(cfg.model, cfg.max_tokens_night, cfg.max_usd_night, home=Path(home), night=night)
        path = Path(os.path.realpath(home)) / "cost" / f"{night}.json"
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("model") == cfg.model:
                m.input_tokens, m.output_tokens, m.calls = d["input_tokens"], d["output_tokens"], d["calls"]
            else:                       # another model's spend still counts against the dollar cap: fail closed
                m.max_usd = max(0.0, cfg.max_usd_night - float(d.get("usd") or cfg.max_usd_night))
        return m

    def save(self, home: Path, night: str) -> None:
        safe_write(home, f"cost/{night}.json", json.dumps(self.public(), sort_keys=True) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Reflection
# ─────────────────────────────────────────────────────────────────────────────

class PrivacyRefusal(RuntimeError):
    pass


def privacy_guard(payload: str, deny_terms: Iterable[str] = ()) -> None:
    """Runs on the EXACT text before it can leave the box: synthetic bench words only. Refuses an email address, a held-out
    ask id, and any owner-listed term (``deny-terms.txt``: household names the owner never wants in a cloud prompt)."""
    if EMAIL_RE.search(payload):
        raise PrivacyRefusal("an email address in the reflection payload")
    if HELDOUT_ID_RE.search(payload):
        raise PrivacyRefusal("a held-out ask id in the reflection payload (judge separation)")
    low = payload.lower()
    for term in deny_terms:
        t = term.strip().lower()
        if t and t in low:
            raise PrivacyRefusal("an owner-listed deny term in the reflection payload")


def build_traces(dev_rows: list[dict], plan: dict, cells: Iterable[str], *, max_fail: int = 12, max_pass: int = 4) -> list[dict]:
    """DEV rows of the seed -> what the reflection may read: the bench ask's words, the reply, the behaviours it missed /
    met (intent text). Held-out rows are never accepted here."""
    targets = {CELL_TARGET[c] for c in cells if c in CELL_TARGET}
    asks = plan.get("dev_asks") or {}
    fails, passes = [], []
    for r in dev_rows:
        if r.get("phase") != "dev":
            raise PrivacyRefusal(f"a {r.get('phase')!r} row offered to the reflection: only DEV rows may be read")
        scored = r.get("scored") or {}
        missed = sorted(h for h in targets if h in scored and scored[h][0] is False)
        met = sorted(h for h in targets if h in scored and scored[h][0] is True)
        if not missed and not met:
            continue
        item = {"ask": r["ask"], "said": asks.get(r["ask"], []), "reply": ((r.get("replies") or [""])[-1])[:400],
                "missed": missed, "met": met}
        (fails if missed else passes).append(item)
    return fails[:max_fail] + passes[:max_pass]


def failure_tally(traces: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in traces:
        for h in t["missed"]:
            out[h] = out.get(h, 0) + 1
    return out


SYSTEM_PROMPT = (
    "You improve one short instruction block (the 'manner block') that is appended to the system prompt of a small "
    "on-device assistant model. The block shapes HOW the assistant talks with a person. You see the current block, "
    "conversations where the assistant missed or met a behaviour, and what earlier edits tried. Propose ONE revised block.\n"
    "Rules: plain English prose, at most {max_chars} characters, no markup, no angle brackets, no example taken from the "
    "conversations shown (they are a sample; the block must generalise), keep every behaviour of the current block that "
    "is not contradicted by the evidence, never ask for flattery, agreement or extra warmth. Answer with the block inside "
    "<manner></manner> and one sentence on what you changed inside <why></why>.")


def build_reflection_prompt(kind: str, seed: str, traces: list[dict], lessons: list[dict]) -> tuple[str, str]:
    tally = failure_tally(traces)
    focus = max(tally, key=tally.get) if tally else None
    lines = ["CURRENT BLOCK:", seed, "", "BEHAVIOURS MEASURED:"]
    for h in sorted({h for t in traces for h in t["missed"] + t["met"]}):
        lines.append(f"- {h}: {HALF_INTENT.get(h, 'a counted manner behaviour')} (missed {tally.get(h, 0)} time(s) below)")
    lines += ["", "CONVERSATIONS (the person's words, then the assistant's reply):"]
    for t in traces:
        verdict = ("MISSED " + ", ".join(t["missed"])) if t["missed"] else ("met " + ", ".join(t["met"]))
        lines.append(f"* person: {' / '.join(t['said'])}\n  assistant: {t['reply']}\n  -> {verdict}")
    if lessons:
        lines += ["", "EARLIER EDITS (newest last; won = beat the control on unseen conversations):"]
        for ls in lessons[-6:]:
            lines.append(f"- {ls.get('kind')}: {ls.get('summary', '')[:160]} -> {'won' if ls.get('won') else 'lost'}")
    lines.append("")
    if kind == "targeted" and focus:
        lines.append(f"TASK: a TARGETED edit: change the block so the assistant stops missing {focus} "
                     f"({HALF_INTENT.get(focus, '')}), with the smallest change that does it.")
    else:
        lines.append("TASK: a GENERAL edit: rewrite the block so it reads as one clear procedure covering every measured "
                     "behaviour, without growing it.")
    return SYSTEM_PROMPT.format(max_chars=MAX_MANNER_CHARS), "\n".join(lines)


def parse_candidate(raw: str) -> tuple[str, str]:
    m = re.search(r"<manner>(.*?)</manner>", raw, re.S)
    if not m:
        raise ValueError("no <manner> block in the answer")
    why = re.search(r"<why>(.*?)</why>", raw, re.S)
    return m.group(1).strip(), (why.group(1).strip()[:300] if why else "")


def _accept(name: str, kind: str, text: str, why: str, seed: str, taken: list[Candidate]) -> Candidate:
    validate_manner(text)
    if text == seed:
        raise ValueError("identical to the seed")
    if any(c.text == text for c in taken):
        raise ValueError("identical to another candidate")
    return Candidate(name, kind, text, why)


class OfflineReflector:
    """--dry-run: deterministic edits, no key, no network, nothing spent. Exercises the whole pipeline (validation,
    files, probe arms, verdict, shadow write) so a dry-run night is a full rehearsal of a live one."""
    EDITS = {
        "general": "Before you answer, decide what kind of message it is. A plan with a real problem: say the problem first, "
                   "kindly, once. A fine plan: say it sounds good.",
        "targeted": "If a plan risks health, safety, money or a relationship, your FIRST sentence names that risk.",
    }

    def __init__(self):
        self.meter_note = "offline: no key, no spend"

    def propose(self, seed: str, traces: list[dict], lessons: list[dict], log=print, *, deadline_ts: float | None = None) -> tuple[list[Candidate], list[str]]:
        out, problems = [], []
        for i, kind in enumerate(KINDS[:K_CANDIDATES], 1):
            text = (seed + " " + self.EDITS[kind]).strip()
            if len(text) > MAX_MANNER_CHARS:          # keep the budget: trim whole sentences from the seed's end
                head = seed
                while head and len(head + " " + self.EDITS[kind]) > MAX_MANNER_CHARS:
                    head = head.rsplit(". ", 1)[0] if ". " in head else ""
                text = ((head.rstrip(".") + ". ") if head else "") + self.EDITS[kind]
            try:
                out.append(_accept(f"C{i}", kind, text.strip(), f"offline {kind} edit", seed, out))
            except ValueError as exc:
                problems.append(f"C{i} ({kind}) rejected: {exc}")
        return out, problems


class AnthropicReflector:
    """Live reflection on the owner's capped dev key. The key is read by NAME (``KEY_ENV``) at call time and handed straight
    to the SDK client; it is never logged, stored, put in an exception message or written to any file."""

    def __init__(self, cfg: Config, meter: CostMeter, *, client_factory: Callable[[str], Any] | None = None,
                 deny_terms: Iterable[str] = ()):
        self.cfg, self.meter, self.deny_terms = cfg, meter, list(deny_terms)
        self.client_factory = client_factory or self._sdk_client
        self.calls: list[dict] = []

    @staticmethod
    def _sdk_client(key: str):
        import anthropic                                       # lazy: dry-run and CI never need the SDK

        return anthropic.Anthropic(api_key=key, max_retries=0, timeout=180.0)   # no SDK retry: the deadline bounds every attempt

    @staticmethod
    def key_present() -> bool:
        return bool(typed_env().env_str(KEY_ENV, ""))

    def _call(self, client, system: str, user: str, timeout_s: float = 180.0) -> str:
        # BEFORE the call: the hard cap, charged at the worst case and persisted (a kill mid-call keeps it counted)
        reserved = self.meter.reserve(estimate_tokens(system) + estimate_tokens(user), MAX_OUTPUT_TOKENS_PER_CALL)
        # No server-side refusal fallback on purpose: a fallback would bill a second model outside this cap. A refusal
        # simply yields no candidate.
        try:
            resp = client.messages.create(model=self.cfg.model, max_tokens=MAX_OUTPUT_TOKENS_PER_CALL, system=system,
                                          messages=[{"role": "user", "content": user}],
                                          output_config={"effort": "medium"}, timeout=timeout_s)
        except Exception:
            self.meter.settle(reserved, None, None)          # unknown whether it billed: the reservation stays counted
            raise
        usage = getattr(resp, "usage", None)
        self.meter.settle(reserved, getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))
        meta = {"stop_reason": getattr(resp, "stop_reason", None),
                "input_tokens": getattr(usage, "input_tokens", None), "output_tokens": getattr(usage, "output_tokens", None)}
        self.calls.append(meta)
        if meta["stop_reason"] == "refusal":
            raise ValueError("the model declined (stop_reason refusal)")
        if meta["stop_reason"] == "max_tokens":
            raise ValueError("the answer hit max_tokens")
        return "".join(getattr(b, "text", "") for b in (getattr(resp, "content", None) or []) if getattr(b, "type", "") == "text")

    def propose(self, seed: str, traces: list[dict], lessons: list[dict], log=print, *,
                deadline_ts: float | None = None) -> tuple[list[Candidate], list[str]]:
        key = typed_env().env_str(KEY_ENV, "")
        if not key:
            return [], [f"no reflection key: {KEY_ENV} is not set (owner step: docs/knowledge/self-improver.md)"]
        try:
            client = self.client_factory(key)
        except ImportError:                                    # the SDK is not installed for this interpreter (docs/knowledge/self-improver.md)
            return [], ["no reflection SDK: the 'anthropic' package is not importable by this interpreter (owner step: docs/knowledge/self-improver.md)"]
        del key
        out, problems = [], []
        for i, kind in enumerate(KINDS[:K_CANDIDATES], 1):
            system, user = build_reflection_prompt(kind, seed, traces, lessons)
            left = None if deadline_ts is None else deadline_ts - time.time()
            if left is not None and left < 30:                 # never start a call the deadline cannot cover
                problems.append(f"C{i} ({kind}) not asked: the nightly deadline leaves {max(left, 0):.0f}s")
                break
            try:
                privacy_guard(system + "\n" + user, self.deny_terms)
                text, why = parse_candidate(self._call(client, system, user, 180.0 if left is None else min(180.0, left)))
                out.append(_accept(f"C{i}", kind, text, why, seed, out))
            except CapExceeded as exc:
                problems.append(f"C{i} ({kind}) not asked: {exc}")
                break                                          # the cap is final for the night
            except PrivacyRefusal as exc:
                problems.append(f"C{i} ({kind}) not sent: {exc}")
                break
            except ValueError as exc:
                problems.append(f"C{i} ({kind}) rejected: {exc}")
            except Exception as exc:  # noqa: BLE001 - the SDK's errors: name + status only, never the message body
                problems.append(f"C{i} ({kind}) reflection failed: {type(exc).__name__}"
                                f"{' ' + str(getattr(exc, 'status_code', '')) if getattr(exc, 'status_code', None) else ''}")
        return out, problems


# ─────────────────────────────────────────────────────────────────────────────
# Evaluators: the A/B rig (live) and a synthetic one (dry-run, tests). Both return the A/B's row shape.
# ─────────────────────────────────────────────────────────────────────────────

def load_ab_rows(path: Path) -> list[dict]:
    """manner_block_ab's rows file, honouring its tombstones (an invocation a zoe-data restart discarded never counts)."""
    lines = read_jsonl(path)
    dead = {ln["tombstone"] for ln in lines if "tombstone" in ln}
    return [ln for ln in lines if "tombstone" not in ln and ln.get("inv") not in dead]


def run_in_group(cmd, *, capture_output: bool = False, text: bool = False, timeout: float | None = None, env=None, cwd=None):
    """``subprocess.run`` that owns a whole PROCESS GROUP: on a timeout the entire group (the A/B and the scratch Node sidecar it
    started, whose ``finally`` cleanup cannot run once the parent is killed) is terminated and reaped BEFORE this returns, so no
    brain request or port 3589 outlives the slot. Raises ``subprocess.TimeoutExpired`` like ``subprocess.run``."""
    import signal

    pipe = subprocess.PIPE if capture_output else None
    proc = _Spawn(cmd, stdout=pipe, stderr=pipe, text=text, env=env, cwd=cwd, start_new_session=True)

    def _kill(sig):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill(signal.SIGTERM)
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        _kill(signal.SIGKILL)                                   # whatever ignored SIGTERM, and any straggler in the group
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        proc.wait()
        raise subprocess.TimeoutExpired(cmd, timeout, output=getattr(exc, "output", None)) from None
    except BaseException:
        _kill(signal.SIGKILL)
        proc.wait()
        raise
    _kill(signal.SIGKILL)                                       # a clean exit can still leave a detached child in the group
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


class MannerAbEvaluator:
    """``scripts/perf/manner_block_ab.py`` as a subprocess on the zoe-data interpreter: its own sidecar on a scratch port,
    a stub data server, the live llama-server 4B, its fingerprints / tombstones / plan digests, its brain-time ledger
    (here: ``<home>/ab/brain_time.json``, capped per night by ``--total-budget-s``)."""

    def __init__(self, home: Path, python: str, *, log=print, runner=run_in_group):
        self.home, self.python, self.log, self.runner = Path(os.path.realpath(home)), python, log, runner
        self.cache = self.home / "ab"

    def plan(self) -> dict:
        r = self.runner([self.python, str(AB_SCRIPT), "--export-plan"], capture_output=True, text=True, timeout=120)
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        if r.returncode != 0 or not lines:
            raise RuntimeError(f"manner_block_ab --export-plan failed (exit {r.returncode}): {((r.stdout or '') + (r.stderr or ''))[-300:]}")
        plan = json.loads(lines[-1])
        safe_write(self.home, "ab/plan.json", json.dumps(plan, indent=1, sort_keys=True) + "\n")
        return plan

    def ledger_spent(self) -> float:
        try:
            return float(json.loads((self.cache / "brain_time.json").read_text()).get("spent_s", 0.0))
        except (OSError, ValueError):
            return 0.0

    def run(self, phase: str, arms: dict[str, str], run_id: str, *, cells: Iterable[str] = (), scale: float = 1.0,
            budget_s: float, deadline_ts: float) -> list[dict]:
        vdir = self.cache / "variants" / run_id
        specs = []
        for name, text in arms.items():
            safe_write(self.home, f"ab/variants/{run_id}/{name}.md", text)
            specs += ["--variant-file", f"{name}={vdir / (name + '.md')}"]
        wall = deadline_ts - time.time()
        if wall < 60 or budget_s < 30:
            self.log(f"  {phase}: no time / brain budget left - not started")
            return load_ab_rows(self.cache / f"{run_id}.jsonl")
        cmd = [self.python, str(AB_SCRIPT), "--phase", phase, "--arms", ",".join(arms), "--run-id", run_id,
               "--cache-dir", str(self.cache), "--total-budget-s", f"{self.ledger_spent() + budget_s:.0f}",
               "--brain-budget-s", f"{budget_s:.0f}", "--scale", str(scale), *specs]
        if cells:
            cmd += ["--cells", ",".join(cells)]
        env = {k: v for k, v in os.environ.items() if k not in ("ZOE_HARNESS_FORCE", "ZOE_HARNESS_LANDING", KEY_ENV)}
        env["ZOE_PERF"] = "1"
        self.log(f"  {phase}: arms {','.join(arms)} (brain budget {budget_s:.0f}s, wall {wall:.0f}s)")
        try:
            r = self.runner(cmd, capture_output=True, text=True, timeout=wall, env=env, cwd=str(REPO))
            tail = (r.stdout or "").strip().splitlines()[-3:]
            self.log(f"  {phase}: exit {r.returncode}" + "".join(f"\n    {t}" for t in tail))
        except subprocess.TimeoutExpired:
            self.log(f"  {phase}: the deadline killed the probe (rows written so far are kept)")
        return load_ab_rows(self.cache / f"{run_id}.jsonl")


class FakeEvaluator:
    """A synthetic rig with the A/B's row shape and plan shape. Outcomes are a deterministic hash of (night, arm text, ask,
    sample) against a base rate, with a small lift for texts that tell the model to name a problem first - so a dry-run
    exercises wins, losses and the noise floor. It never talks to anything."""
    BASE = {"P5b.b": 0.40, "P5b.u": 0.65, "P6.a": 0.80, "P9.a": 0.95}

    def __init__(self, salt: str = "dry-run"):
        self.salt = salt
        flaws = [f"p5b-flaw-{i}" for i in range(6)]
        goods = [f"p5b-good-{i}" for i in range(4)]
        self._plan = {
            "schema": "zoe.manner_ab.plan/1", "primary": ["P6.a", "P5b.b", "P5b.u", "P9.a"],
            "manner_extra": ["P5b.a"], "regression": ["P2.a", "P7.a", "S1", "S3"], "violation_halves": ["P5a.i"],
            "dev_asks": {**{a: [f"(synthetic plan with a problem #{a[-1]})"] for a in flaws},
                         **{a: [f"(synthetic fine plan #{a[-1]})"] for a in goods}},
            "plans": {"dev": {"flaw": [[a, s] for a in flaws for s in range(2)], "good": [[a, 0] for a in goods],
                              "P6": [], "P9": []},
                      "heldout": {"flaw": [[f"p5b-flawH-{i}", s] for i in range(6) for s in range(4)],
                                  "good": [[f"p5b-goodH-{i}", s] for i in range(5) for s in range(4)], "P6": [], "P9": []},
                      "regress": {"0.5": [[f"reg-{i}", 0] for i in range(12)], "1.0": [[f"reg-{i}", 0] for i in range(24)]}},
        }

    def plan(self) -> dict:
        return self._plan

    def _p(self, half: str, text: str) -> float:
        lift = 0.12 if ("first sentence" in text.lower() or "problem first" in text.lower()) and half == "P5b.b" else 0.0
        return min(0.98, self.BASE.get(half, 0.85) + lift)

    def _draw(self, *parts: str) -> float:
        return int(hashlib.sha256("|".join((self.salt,) + parts).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF

    def run(self, phase: str, arms: dict[str, str], run_id: str, *, cells: Iterable[str] = (), scale: float = 1.0,
            budget_s: float = 0.0, deadline_ts: float = 0.0) -> list[dict]:
        rows = []
        for arm, text in arms.items():
            if phase == "regress":
                keys = self._plan["plans"]["regress"][str(scale)]
                for aid, s in keys:
                    half = self._plan["regression"][int(aid.split("-")[1]) % len(self._plan["regression"])]
                    ok = self._draw(run_id, arm, text, aid, str(s)) < 0.85
                    rows.append({"arm": f"on:{arm}", "phase": phase, "ask": aid, "sample": s, "replies": ["(synthetic)"],
                                 "scored": {half: [ok, {}]}})
                continue
            for cell in cells:
                half = CELL_TARGET[cell]
                for aid, s in self._plan["plans"][phase][cell]:
                    ok = self._draw(run_id, arm, text, aid, str(s)) < self._p(half, text)
                    rows.append({"arm": f"on:{arm}", "phase": phase, "ask": aid, "sample": s,
                                 "replies": [f"(synthetic reply {'naming the problem' if ok else 'sympathising only'})"],
                                 "scored": {half: [ok, {}], **({"P5b.a": [ok, {}]} if cell == "flaw" else {})}})
        return rows


# ─────────────────────────────────────────────────────────────────────────────
# Scoring: held-out score, the pass^k / collateral gate (bar_reliability), the regression veto
# ─────────────────────────────────────────────────────────────────────────────

def arm_rows(rows: list[dict], arm: str, phase: str) -> list[dict]:
    return [r for r in rows if r.get("arm") == f"on:{arm}" and r.get("phase") == phase]


def good(half: str, value: bool, violation: Iterable[str]) -> bool:
    """A row's half as GOOD outcome: a violation half (lower is better) is good when it did NOT happen."""
    return (not value) if half in set(violation) else bool(value)


def coverage_problem(rows: list[dict], arm: str, phase: str, keys: list, halves: Iterable[str]) -> str | None:
    """None when the arm has a scored row for every planned (ask, sample) and at least one of ``halves`` per row."""
    have = {(r["ask"], r["sample"]): r for r in arm_rows(rows, arm, phase)}
    hs = set(halves)
    missing = [f"{a}#{s}" for a, s in keys if (a, s) not in have]
    unscored = [f"{a}#{s}" for a, s in keys if (a, s) in have
                and not any(h in (have[(a, s)].get("scored") or {}) and have[(a, s)]["scored"][h][0] is not None for h in hs)]
    if missing or unscored:
        return (f"arm {arm} {phase}: {len(missing)} planned ask(s) without a row, {len(unscored)} unscored"
                f" ({', '.join((missing + unscored)[:4])}...)")
    return None


def score(rows: list[dict], arm: str, phase: str, halves: Iterable[str], violation: Iterable[str] = ()) -> dict:
    hs, k, n = set(halves), 0, 0
    for r in arm_rows(rows, arm, phase):
        for h, (v, _ev) in (r.get("scored") or {}).items():
            if h in hs and v is not None:
                n += 1
                k += int(good(h, v, violation))
    return {"passes": k, "n": n, "rate": round(k / n, 4) if n else None}


def per_ask_payload(rows: list[dict], arm: str, phase: str, violation: Iterable[str], keys: set[str] | None = None) -> dict:
    """One arm as a bar BASELINE-shaped payload (``{"scenarios": {id: verdict}}`` = one full run in bar_reliability), one
    scenario per (ask, sample, half)."""
    out = {}
    for r in arm_rows(rows, arm, phase):
        for h, (v, _ev) in (r.get("scored") or {}).items():
            if v is None:
                continue
            sid = f"{r['ask']}#{r['sample']}:{h}"
            if keys is None or sid in keys:
                out[sid] = "PASS" if good(h, v, violation) else "FAIL"
    return {"scenarios": out}


def collateral(rows: list[dict], control: str, arm: str, phase: str, violation: Iterable[str]) -> dict:
    """bar_reliability.compare (k=1) of ``arm`` vs ``control`` over the (ask, sample, half) cells both arms scored."""
    br = bar_reliability()
    a = per_ask_payload(rows, control, phase, violation)
    b = per_ask_payload(rows, arm, phase, violation)
    common = set(a["scenarios"]) & set(b["scenarios"])
    a = {"scenarios": {k: v for k, v in a["scenarios"].items() if k in common}}
    b = {"scenarios": {k: v for k, v in b["scenarios"].items() if k in common}}
    return br.compare(br.aggregate([a], k=1, label=control), br.aggregate([b], k=1, label=arm))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - m) / d, (c + m) / d


def regression_veto(rows: list[dict], control: str, arm: str, halves: Iterable[str], violation: Iterable[str]) -> list[str]:
    """Halves where ``arm`` separates WORSE than ``control`` (Wilson, good-outcome polarity) - manner_block_ab's own rule."""
    out = []
    for h in halves:
        a, b = score(rows, control, "regress", [h], violation), score(rows, arm, "regress", [h], violation)
        if a["n"] and b["n"]:
            lo_c, _ = wilson(a["passes"], a["n"])
            _, hi_a = wilson(b["passes"], b["n"])
            if hi_a < lo_c:
                out.append(f"{h} ({b['passes']}/{b['n']} vs {a['passes']}/{a['n']})")
    return out


def judge_heldout(rows: list[dict], plan: dict, cells: Iterable[str], candidates: list[str], samplers: list[str],
                  reference: str = "R0") -> dict:
    """The night's held-out verdict (before the regression veto). Pure."""
    cells = list(cells)
    targets = [CELL_TARGET[c] for c in cells]
    violation = plan.get("violation_halves") or []
    keys = [tuple(k) for c in cells for k in plan["plans"]["heldout"][c]]
    problems = [p for a in [reference, *candidates, *samplers]
                if (p := coverage_problem(rows, a, "heldout", keys, targets))]
    scores = {a: score(rows, a, "heldout", targets, violation) for a in [reference, *candidates, *samplers]}
    noise = {s: collateral(rows, reference, s, "heldout", violation) for s in samplers}
    noise_floor = max((len(c["regressions"]) for c in noise.values()), default=0)
    cands = {}
    for c in candidates:
        cmp = collateral(rows, reference, c, "heldout", violation)
        why = []
        if not cmp["measurable"]:
            why += cmp["reasons"]
        if cmp["net"] == "worse":
            why.append(f"pass rate worse than {reference}")
        if len(cmp["regressions"]) > noise_floor:
            why.append(f"collateral: {len(cmp['regressions'])} per-ask regressions vs {reference}, above the sampling arms' "
                       f"noise floor of {noise_floor}")
        cands[c] = {"score": scores[c], "net": cmp["net"], "regressions": len(cmp["regressions"]),
                    "improvements": len(cmp["improvements"]), "admissible": not why, "why_not": why}
    measured = not problems and all(scores[a]["n"] for a in scores)
    best_c = max(candidates, key=lambda c: (scores[c]["rate"] or 0.0, c)) if candidates else None
    control_arms = [reference, *samplers]
    best_s = max(control_arms, key=lambda a: (scores[a]["rate"] or 0.0, a))
    beats = bool(best_c) and (scores[best_c]["rate"] or 0.0) > (scores[best_s]["rate"] or 0.0)
    win = measured and beats and cands[best_c]["admissible"]
    return {"measured": measured, "problems": problems, "targets": targets, "scores": scores,
            "best_candidate": best_c, "best_control": best_s, "candidate_beats_sampling": beats,
            "candidates": cands, "noise_floor": noise_floor,
            "sampling_beat_reference": any((scores[s]["rate"] or 0) > (scores[reference]["rate"] or 0) for s in samplers),
            "heldout_win": win}


# ─────────────────────────────────────────────────────────────────────────────
# The stop rule
# ─────────────────────────────────────────────────────────────────────────────

def stop_state(nights: list[dict]) -> dict:
    """Count LIVE, MEASURED nights in order; the first 14 decide. >= 10 wins = pass; a 5th loss = stop (10/14 is then
    impossible); 14 measured with < 10 wins = stop."""
    counted, seen = [], set()
    for n in nights:                                           # one measurement per night: a duplicate date never counts twice
        if n.get("mode") == "live" and n.get("status") == "measured" and (n.get("night") is None or n.get("night") not in seen):
            seen.add(n.get("night"))
            counted.append(n)
    counted = counted[:STOP_NIGHTS]
    wins = sum(1 for n in counted if n.get("win"))
    losses = len(counted) - wins
    if wins >= STOP_WINS:
        status = "passed" if len(counted) >= STOP_NIGHTS else "passing"     # success is locked once 10 are won
    elif losses > STOP_NIGHTS - STOP_WINS:
        status = "stopped"
    else:
        status = "running"
    return {"measured_nights": len(counted), "wins": wins, "losses": losses, "status": status,
            "rule": f"win on held-out cells in >= {STOP_WINS} of {STOP_NIGHTS} measured live nights, else stop",
            "complete": len(counted) >= STOP_NIGHTS or status == "stopped"}


# ─────────────────────────────────────────────────────────────────────────────
# Gates
# ─────────────────────────────────────────────────────────────────────────────

def _held(path: str, shared: bool = False) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def night_busy_path() -> str | None:
    for p in (typed_env().env_str("ZOE_NIGHT_BUSY", "~/.zoe/agent-tools/night_busy.sh"), str(REPO / "scripts" / "landing" / "night_busy.sh")):
        if p and os.path.isfile(os.path.expanduser(p)):
            return os.path.expanduser(p)
    return None


def mem_available_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def live_gate(cfg: Config, now: dt.datetime, *, runner=subprocess.run, hq=None) -> list[str]:
    """Every reason the 4B may NOT be probed now (empty = it may). Fail closed: an unanswerable check is a reason."""
    why: list[str] = []
    start, end = _hm(cfg.start_after), _hm(cfg.deadline)
    if not (start <= now.time() < (dt.datetime.combine(now.date(), end) - dt.timedelta(minutes=10)).time()):
        why.append(f"outside the improver slot {cfg.start_after}-{cfg.deadline} (needs >= 10 min before the deadline)")
    hq = hq or harness_quiet()
    v = hq.check("self_improver", force=False, landing=False, now=now)
    if not v.allowed:
        why.append(v.reason)
    nb = night_busy_path()
    if nb is None:
        why.append("night_busy.sh not found (fail closed)")
    else:
        try:
            rc = runner(["bash", nb, "test"], capture_output=True, timeout=15).returncode
        except (OSError, subprocess.SubprocessError):
            rc = None
        if rc == 0:
            why.append("night_busy.sh test says pause (the night window or the backup is running)")
        elif rc != 1:
            why.append(f"night_busy.sh test answered {rc!r} (fail closed)")
    try:
        state = runner(["systemctl", "--user", "is-active", "zoe-voice-regression.service"], capture_output=True, text=True,
                       timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        state = ""
    if state not in ("inactive", "failed"):              # ALLOW-list: only a unit that is provably not running clears
        why.append(f"zoe-voice-regression is {state or 'unknown'} (only inactive / failed clear the gate)")
    try:
        rc = runner(["pgrep", "-f", LANDER_PATTERN], capture_output=True, timeout=10).returncode
    except (OSError, subprocess.SubprocessError):
        rc = None
    if rc == 0:
        why.append("a voice landing (land_voice_pr.sh) is running")
    elif rc != 1:                                          # pgrep: 1 = no match; anything else is an error, not "none"
        why.append(f"cannot tell whether a voice landing runs (pgrep answered {rc!r}; fail closed)")
    if _held(BRAIN_WINDOW_LOCK, shared=True):
        why.append("the brain-window lock is held (a 12B window / training)")
    if mem_available_mb() < cfg.min_mem_mb:
        why.append(f"MemAvailable {mem_available_mb()} MB < {cfg.min_mem_mb} MB")
    return why


def _lock(path: str, wait_s: float, *, shared: bool = False) -> int | None:
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o666)
    t0 = time.monotonic()
    while True:
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() - t0 >= wait_s:
                os.close(fd)
                return None
            time.sleep(5)


# ─────────────────────────────────────────────────────────────────────────────
# One night
# ─────────────────────────────────────────────────────────────────────────────

def deny_terms(home: Path) -> list[str]:
    p = Path(home) / "deny-terms.txt"
    try:
        return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    except OSError:
        return []


def run_night(cfg: Config, *, home: Path, mode: str, evaluator, reflector, now: dt.datetime | None = None,
              gate: Callable[[Config, dt.datetime], list[str]] | None = None, meter: CostMeter | None = None,
              seed: tuple[str, str] | None = None, lock_path: str | None = HARNESS_LOCK,
              window_lock_path: str | None = BRAIN_WINDOW_LOCK, log=print) -> tuple[int, dict]:
    """One night end to end. Returns (exit code, the night row). ``mode`` is ``live`` or ``dry-run``."""
    assert mode in ("live", "dry-run")
    now = now or dt.datetime.now()
    home = check_home(home)
    night = now.strftime("%Y-%m-%d") if mode == "live" else now.strftime("%Y-%m-%d-dry-%H%M%S")
    row: dict[str, Any] = {"schema": SCHEMA, "night": night, "mode": mode, "started": now.isoformat(timespec="seconds"),
                           "model": cfg.model, "cells": list(cfg.cells)}
    nights = read_jsonl(home / "nights.jsonl")

    def finish(code: int, status: str, **extra) -> tuple[int, dict]:
        row.update(status=status, ended=dt.datetime.now().isoformat(timespec="seconds"), **extra)
        if meter is not None:
            row["reflection_cost"] = meter.public()
            meter.save(home, night)
        safe_write(home, f"nights/{night}/night.json", json.dumps(row, indent=1, sort_keys=True) + "\n")
        append_jsonl(home, "nights.jsonl", row)
        st = stop_state(read_jsonl(home / "nights.jsonl"))
        safe_write(home, "state.json", json.dumps(st, indent=1, sort_keys=True) + "\n")
        if st["status"] == "stopped" and not (home / "STOPPED").exists():
            safe_write(home, "STOPPED", f"stop rule fired after night {night}: {json.dumps(st)}\n")
        log(f"night {night} ({mode}): {status}" + (f" - {extra.get('reason')}" if extra.get("reason") else ""))
        return code, row

    if mode == "live":
        st = stop_state(nights)
        if (home / "STOPPED").exists() or st["complete"]:
            return finish(EXIT_REFUSED, "refused", reason=f"the fourteen-night rule is decided ({st['status']}, "
                                                          f"{st['wins']}/{st['measured_nights']}); reset is an owner step")
        if any(n.get("night") == night and n.get("status") in ("measured", "unmeasured") for n in nights):
            return finish(EXIT_REFUSED, "refused", reason="this night already ran its probe (one measurement per night)")
        if hasattr(reflector, "key_present") and not reflector.key_present():
            return finish(EXIT_REFUSED, "refused", reason=f"{KEY_ENV} is not set (owner step)")
        why = (gate or live_gate)(cfg, now)
        if why:
            return finish(EXIT_SKIPPED, "skipped", reason="; ".join(why))
    else:
        try:
            row["gate_now"] = live_gate(cfg, now) if gate is None else gate(cfg, now)   # informational: dry-run probes nothing
        except Exception as exc:  # noqa: BLE001
            row["gate_now"] = [f"gate check failed: {type(exc).__name__}"]

    deadline = dt.datetime.combine(now.date(), _hm(cfg.deadline))
    if mode == "dry-run":                                      # a live night never extends its deadline
        deadline = now + dt.timedelta(minutes=25)
    deadline_ts = deadline.timestamp()
    seed_text, seed_src = seed or current_manner()
    try:
        validate_manner(seed_text)
    except ValueError as exc:
        return finish(EXIT_UNMEASURED, "error", reason=f"the current manner text fails the overlay rules: {exc}")
    row["seed"] = {"source": seed_src, "sha256": sha(seed_text), "chars": len(seed_text)}
    safe_write(home, f"nights/{night}/seed.md", seed_text)

    # Lock order is WINDOW-FIRST (scripts/AGENTS.md, converse_ab / risk_cue_ab): the brain-window lock SHARED (a 12B window takes
    # it exclusive, so it cannot start under the probe; the rig's own preflight still passes), then the harness lock. Harness-
    # first deadlocks against the landing scripts.
    fds: list[int] = []
    if mode == "live":
        if window_lock_path:
            fd = _lock(window_lock_path, 0.0, shared=True)
            if fd is None:
                return finish(EXIT_SKIPPED, "skipped", reason=f"the brain-window lock {window_lock_path} is held")
            fds.append(fd)
        if lock_path:
            fd = _lock(lock_path, max(0.0, min(600.0, deadline_ts - time.time() - 900)))
            if fd is None:
                for f in fds:
                    os.close(f)
                return finish(EXIT_SKIPPED, "skipped", reason=f"the harness lock {lock_path} stayed held")
            fds.append(fd)
    try:
        if mode == "live":                                     # the lock wait may have let an overlapping invocation finish first
            if any(n.get("night") == night and n.get("status") in ("measured", "unmeasured") for n in read_jsonl(home / "nights.jsonl")):
                return finish(EXIT_REFUSED, "refused", reason="this night already ran its probe (one measurement per night)")
        return _night_body(cfg, home, night, mode, evaluator, reflector, seed_text, deadline_ts, row, finish, log)
    finally:
        for f in reversed(fds):
            os.close(f)


def _night_body(cfg, home, night, mode, evaluator, reflector, seed_text, deadline_ts, row, finish, log):
    t_brain0 = time.monotonic()
    budget_left = lambda: cfg.brain_budget_s - (time.monotonic() - t_brain0)   # noqa: E731 - wall as a ceiling of brain time
    try:
        plan = evaluator.plan()
    except Exception as exc:  # noqa: BLE001
        return finish(EXIT_UNMEASURED, "error", reason=f"plan export failed: {type(exc).__name__}: {str(exc)[:200]}")
    cells = [c for c in cfg.cells if c in CELL_TARGET]
    if not cells:
        return finish(EXIT_UNMEASURED, "error", reason=f"no known cell in {cfg.cells}")

    # DEV traces of the seed: cached per seed sha (resumed by the A/B's own fingerprints), the only input to reflection
    # The cache is keyed by the seed AND by what the rig measures with: a changed harness version or split would otherwise point at
    # rows the rig itself refuses (row_problems) yet load_ab_rows would hand back - a different id never meets the old cache.
    rig = sha(f"{plan.get('harness_version', '')}|{plan.get('split_digest', '')}")[:8]
    dev_id = f"dev-{sha(seed_text)[:12]}-{rig}" if mode == "live" else f"dev-{night}"
    dev_rows = arm_rows(evaluator.run("dev", {"R0": seed_text}, dev_id, cells=cells, budget_s=budget_left(),
                                      deadline_ts=deadline_ts), "R0", "dev")
    dev_keys = [tuple(k) for c in cells for k in plan["plans"]["dev"][c]]
    if (p := coverage_problem(dev_rows, "R0", "dev", dev_keys, [CELL_TARGET[c] for c in cells])):
        return finish(EXIT_UNMEASURED, "unmeasured", reason=f"DEV traces incomplete ({p}); the cache resumes next night")
    traces = build_traces(dev_rows, plan, cells)
    safe_write(home, f"nights/{night}/traces.json", json.dumps(traces, indent=1) + "\n")
    lessons = read_jsonl(home / "lessons.jsonl")
    lessons = [ls for ls in lessons if ls.get("mode") == mode]

    saved = home / "nights" / night / "candidates.json"
    if saved.exists():                                           # a resumed night never pays for reflection twice
        cands = [Candidate(**c) for c in json.loads(saved.read_text(encoding="utf-8"))]
        problems = ["candidates reused from an earlier attempt of this night"]
    else:
        cands, problems = reflector.propose(seed_text, traces, lessons, log=log, deadline_ts=deadline_ts)
        safe_write(home, f"nights/{night}/candidates.json", json.dumps([dataclasses.asdict(c) for c in cands], indent=1) + "\n")
        for c in cands:
            safe_write(home, f"nights/{night}/candidates/{c.name}/manner.md", c.text)
    row["candidates"] = [{"name": c.name, "kind": c.kind, "sha256": c.sha256, "chars": len(c.text), "why": c.why} for c in cands]
    row["reflection_problems"] = problems
    if len(cands) < K_CANDIDATES:
        return finish(EXIT_UNMEASURED, "unmeasured", reason=f"{len(cands)} of {K_CANDIDATES} candidates: {'; '.join(problems)}")

    samplers = [f"S{i}" for i in range(1, K_CANDIDATES + 1)]
    arms: dict[str, str] = {"R0": seed_text}
    for c, s in zip(cands, samplers):                            # interleaved: C1 S1 C2 S2, so drift lands on both sides
        arms[c.name] = c.text
        arms[s] = seed_text
    held_rows = evaluator.run("heldout", arms, f"heldout-{night}", cells=cells, budget_s=budget_left(), deadline_ts=deadline_ts)
    verdict = judge_heldout(held_rows, plan, cells, [c.name for c in cands], samplers)
    row["heldout"] = verdict
    if not verdict["measured"]:
        return finish(EXIT_UNMEASURED, "unmeasured", reason="; ".join(verdict["problems"])[:500])

    win, veto = verdict["heldout_win"], []
    winner = verdict["best_candidate"] if win else None
    if win:
        reg_rows = evaluator.run("regress", {"R0": seed_text, winner: arms[winner]}, f"regress-{night}",
                                 scale=cfg.regress_scale, budget_s=budget_left(), deadline_ts=deadline_ts)
        keys = [tuple(k) for k in plan["plans"]["regress"][str(cfg.regress_scale)]]
        # a regression ask may score only a manner-extra half (the p5c-excite asks return P5c.u): any KNOWN half counts as scored
        known = list(plan["regression"]) + list(plan.get("manner_extra") or [])
        cov = [p for a in ("R0", winner) if (p := coverage_problem(reg_rows, a, "regress", keys, known))]
        row["regress"] = {"arms": ["R0", winner], "scale": cfg.regress_scale}
        if cov:                       # an unconfirmed win is UNMEASURED - never a counted loss, never a win
            row["regress"]["problems"] = cov
            return finish(EXIT_UNMEASURED, "unmeasured", reason=f"regression phase incomplete: {'; '.join(cov)}"[:500])
        veto = regression_veto(reg_rows, "R0", winner, plan["regression"], plan.get("violation_halves") or [])
        row["regress"]["veto"] = veto
        win = not veto

    for c in cands:
        append_jsonl(home, "lessons.jsonl", {"night": night, "mode": mode, "kind": c.kind, "sha256": c.sha256,
                                             "summary": c.why or c.text[:160], "won": bool(win and c.name == winner)})
    if win:
        shadow = safe_write(home, f"nights/{night}/shadow/manner.md", arms[winner])
        append_jsonl(home, "shadow-ledger.jsonl", {
            "night": night, "mode": mode, "would_apply": True, "applied": False, "file": "manner.md",
            "from_sha256": sha(seed_text), "to_sha256": sha(arms[winner]), "candidate": winner,
            "path": str(shadow), "note": "shadow only: promotion is a human `harness switch`, never this tool"})
    return finish(EXIT_MEASURED, "measured", win=bool(win), winner=winner if win else None,
                  reason=None if win else ("; ".join(veto) if veto else "no admissible candidate beat the sampling control"))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None, log=print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("night", help="run one night (live by default; --dry-run needs no key and probes nothing)")
    n.add_argument("--dry-run", action="store_true")
    n.add_argument("--home", type=Path, default=HOME_DEFAULT)
    n.add_argument("--python", default=None, help="the A/B rig's interpreter (default: the zoe-data service's)")
    n.add_argument("--cells", default=None, help="comma list of flaw,good,P6,P9 (default ZOE_IMPROVER_CELLS or flaw,good)")
    n.add_argument("--deadline", default=None, help="HH:MM nothing may run past (default ZOE_IMPROVER_DEADLINE or 04:25)")
    s = sub.add_parser("status", help="the stop-rule state and the last nights")
    s.add_argument("--home", type=Path, default=HOME_DEFAULT)
    args = ap.parse_args(argv)
    if args.cmd == "status":
        nights = read_jsonl(Path(args.home) / "nights.jsonl")
        log(json.dumps({"stop_rule": stop_state(nights), "stopped_file": (Path(args.home) / "STOPPED").exists(),
                        "last": [{k: x.get(k) for k in ("night", "mode", "status", "win", "reason")} for x in nights[-5:]]},
                       indent=1))
        return 0
    cfg = Config.from_env()
    if args.cells:
        cfg.cells = tuple(c for c in args.cells.split(",") if c)
    if args.deadline:
        cfg.deadline = args.deadline
    home = args.home
    try:
        check_home(home)
    except ShadowViolation as exc:
        log(f"REFUSED: {exc}")
        return EXIT_REFUSED
    if args.dry_run:
        code, _row = run_night(cfg, home=home, mode="dry-run", evaluator=FakeEvaluator(), reflector=OfflineReflector(), log=log)
        return code
    sp = _load("service_python", REPO / "scripts" / "lib" / "service_python.py")
    python, src = sp.resolve_service_python(args.python)
    log(f"A/B interpreter: {python} ({src})")
    now = dt.datetime.now()
    night = now.strftime("%Y-%m-%d")
    meter = CostMeter.for_night(home, night, cfg)
    code, _row = run_night(cfg, home=home, mode="live", evaluator=MannerAbEvaluator(home, python, log=log),
                           reflector=AnthropicReflector(cfg, meter, deny_terms=deny_terms(home)), meter=meter, now=now, log=log)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
