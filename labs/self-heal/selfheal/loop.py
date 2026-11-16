"""The loop driver: fault (a fixture) -> packet -> engine -> diff policy -> supervised gates -> local
landing -> proof on the merged tree -> ledger, in bounded rounds, with the measures in the result.

It composes what exists: `Runner` (detect, ticket, caps, worktree, diff policy, gates, commit, recovery
at every start), `ProposerEngine` (the propose() protocol) and `LedgerLanding` (land without GitHub).

Every non-heal ends in ONE reason from the closed list `REASONS`. Whether another round runs is a
TABLE lookup on that reason (`RETRY`) under a deterministic round cap (never above the per-fault cap
of the hard floor). Nothing an engine says decides it: there is no severity, score or "confidence"
input. The next round's packet carries the previous reason as a token, nothing else.
"""
from __future__ import annotations

import re
import time
from collections import Counter

from . import locallander, proposer
from .config import MAX_PER_FAULT_CAP, Config
from .gates import GATES
from .locallander import LedgerLanding
from .proposer import ProposerEngine
from .runner import Runner

# diff policy (safety.check_diff) + runner refusals + run outcomes that are not a heal
POLICY = ("engine_committed", "engine_staged", "empty_diff", "forbidden_path", "scratch_planted", "report_planted",
          "test_deleted", "test_weakened", "process_control", "outside_repo", "missing_test", "gate_failed",
          "engine_failed", "live_checkout_changed")
RUN = ("kill_switch", "lock", "quiet_hours", "voice_lander_running", "night_busy", "night_busy_missing", "daily_cap",
       "unsafe_binary", "full_local_needs_supervisor", "sandbox_unavailable", "rollback_stuck", "test_lock_timeout",
       "no_fault", "all_capped", "no_engine", "detect_failed", "collection_error", "planned", "error", "unclassified")
REASONS = tuple(dict.fromkeys((*proposer.REASONS, *POLICY, *locallander.REASONS, *RUN)))
# Another round MAY help: the engine answered badly, not out of bounds. Everything else stops the loop:
# a patch that reached outside its allow-list, a rollback, a refusal to run at all.
RETRY = frozenset({"patch_empty", "patch_malformed", "patch_does_not_apply", "patch_oversized", "diff_cap", "empty_diff",
                   "missing_test", "gate_failed", "engine_error", "budget_time", "budget_turns", "budget_tokens", "usage_malformed"})
assert RETRY <= set(REASONS)

# --- the report: closed codes, ids and numbers ONLY ----------------------------------------------
# A report is a diagnostic read in the morning. It never carries text that came from an engine, a fault
# packet, a patch or a command line. `SCHEMA` is the whole contract: every leaf is a number, a bool, or a
# member of a closed set / a strict id shape; `check_report` refuses anything else (fail closed), and the
# message it raises names the KEY, never the value.
OUTCOMES = ("healed", "refused", "discarded", "failed", "rolled_back", "rollback_stuck", "interrupted", "planned",
            "no_engine", "no_fault", "all_capped", "detect_failed", "collection_error", "error", "not_healed",
            "healed_local", "pr_open", "pr_closed", "pending", "other")
# the printed PR steps of a `this_unit` landing, by kind (the command line itself is never reported)
PR_STEPS = (("git", "push"), "git_push"), (("gh", "pr"), "gh_pr_create_draft")
STEP_KINDS = ("git_push", "gh_pr_create_draft", "other", *proposer.WOULD_RUN_STEPS)
SHAPE_NAMES = (*locallander.SHAPES, "other")
GATE_NAMES = (*GATES, "")
TICKET_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{0,6}")
PROOF = re.compile(r"[0-9a-f]{64}")


def _closed(*names):
    return frozenset(names)


ROUND_SCHEMA = {   # key -> int | float | "id" | a closed set | (closed set,) = a list of members
    "round": int, "outcome": _closed(*OUTCOMES), "reason": _closed("", *REASONS), "gate": _closed(*GATE_NAMES),
    "ticket": TICKET_ID, "seconds": float, "turns": (int, None), "tokens": (int, None),
    "usage": _closed(*proposer.USAGE_CODES), "would_run": (_closed(*STEP_KINDS),)}
SCHEMA = {
    "healed": bool, "engine": _closed(*proposer.ENGINE_NAMES, "other"), "shape": _closed(*SHAPE_NAMES),
    "max_rounds": int, "fault_to_verified_fix_s": (float, None), "attempts": int, "rollbacks": int,
    "refusals_by_reason": ("map", _closed(*REASONS)), "rounds": ("rows", ROUND_SCHEMA),
    "ledger": ("row", {"chain_ok": bool, "entries": int}), "proof": PROOF, "would_run": (_closed(*STEP_KINDS),)}


def _leaf_ok(spec, v) -> bool:
    if spec is bool:
        return isinstance(v, bool)
    if spec is int:
        return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10**9
    if spec is float:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 10**9
    if isinstance(spec, re.Pattern):
        return v == "" or (isinstance(v, str) and spec.fullmatch(v) is not None)
    if isinstance(spec, frozenset):
        return isinstance(v, str) and v in spec
    if isinstance(spec, tuple) and len(spec) == 2 and spec[1] is None:
        return v is None or _leaf_ok(spec[0], v)
    return False


def check_report(rep: dict, schema: dict = SCHEMA, path: str = "report") -> dict:
    """Raise ValueError (key path only, never a value) unless every field is in the schema and typed to it."""
    if not isinstance(rep, dict) or set(rep) != set(schema):
        raise ValueError(f"{path}: fields differ from the schema")
    for k, spec in schema.items():
        v, here = rep[k], f"{path}.{k}"
        if isinstance(spec, tuple) and spec and spec[0] == "map":
            if not isinstance(v, dict) or not all(_leaf_ok(spec[1], x) and _leaf_ok(int, n) for x, n in v.items()):
                raise ValueError(f"{here}: not a closed-code count map")
        elif isinstance(spec, tuple) and spec and spec[0] == "rows":
            if not isinstance(v, list):
                raise ValueError(f"{here}: not a list")
            for i, row in enumerate(v):
                check_report(row, spec[1], f"{here}[{i}]")
        elif isinstance(spec, tuple) and spec and spec[0] == "row":
            check_report(v, spec[1], here)
        elif isinstance(spec, tuple) and len(spec) == 1:
            if not isinstance(v, list) or not all(_leaf_ok(spec[0], x) for x in v):
                raise ValueError(f"{here}: not a list of closed codes")
        elif not _leaf_ok(spec, v):
            raise ValueError(f"{here}: not of its closed type")
    return rep


def _code(value, allowed) -> str:
    return value if isinstance(value, str) and value in allowed else "other"


def pr_steps(argvs) -> list:
    """Printed PR command lines -> step KINDS. The command line itself is never reported."""
    out = []
    for argv in argvs or []:
        head = tuple(argv[:2]) if isinstance(argv, (list, tuple)) else ()
        out.append(next((kind for prefix, kind in PR_STEPS if head == prefix), "other"))
    return out[:proposer.WOULD_RUN_MAX]


def classify(out: dict) -> str:
    """The one reason a run did not heal ('' when it did), always a member of REASONS."""
    outcome = out.get("outcome", "")
    if outcome == "healed":
        return ""
    raw = out.get("rule") if outcome == "refused" else outcome
    if outcome in ("discarded", "failed", "rolled_back", "rollback_stuck", "interrupted"):
        raw = out.get("why")
    reason = str(raw or outcome).split(":")[0].split(",")[0]
    return reason if reason in REASONS else "unclassified"


class HealLoop:
    def __init__(self, cfg: Config, engine, *, max_rounds: int = MAX_PER_FAULT_CAP, shape=None, probe=None,
                 applier=None, now=None, clock=time.monotonic, sleep=time.sleep, hooks=None):
        self.cfg, self.engine, self.now, self.clock = cfg, engine, now, clock
        self.max_rounds = max(1, min(max_rounds, cfg.per_fault_cap, MAX_PER_FAULT_CAP))
        self.land_kw = dict(shape=shape, probe=probe, clock=clock, sleep=sleep, hooks=hooks)
        self.applier = applier

    def run(self, junit: str) -> dict:
        """`junit`: the fault fixture (a JUnit report naming the failing test on the base branch)."""
        t0, rounds, refusals, rollbacks, healed, last, landing = self.clock(), [], Counter(), 0, False, "", None
        for n in range(1, self.max_rounds + 1):
            eng = ProposerEngine(self.cfg, self.engine)
            eng.attempt, eng.last_refusal = n, last
            landing = LedgerLanding(self.cfg, self.applier, **self.land_kw)
            t1 = self.clock()
            out = Runner(self.cfg, engine=eng, landing=landing, **({"now": self.now} if self.now else {})).run_once(junit=junit)
            last = classify(out)
            bad = [g["gate"] for g in out.get("gates", []) if g.get("status") == "fail" and g.get("gate") in GATES]
            tid = out.get("ticket", "")
            rounds.append({"round": n, "outcome": _code(out.get("outcome"), OUTCOMES), "reason": last,
                           "gate": (bad or [""])[0], "ticket": tid if isinstance(tid, str) and TICKET_ID.fullmatch(tid) else "",
                           "seconds": round(self.clock() - t1, 2),
                           "turns": eng.last.get("turns"), "tokens": eng.last.get("tokens"),
                           "usage": eng.last.get("usage", "unreported"), "would_run": list(eng.last.get("would_run", []))})
            if not last:
                healed = True
                break
            refusals[last] += 1
            rollbacks += out.get("outcome") in ("rolled_back", "rollback_stuck")
            if last not in RETRY:
                break
        ok, entries = landing.ledger.verify()
        landed = [e for e in landing.ledger.entries() if e["event"] == "landed"] if ok else []
        proof = landed[-1]["proof"] if healed and landed else ""
        return check_report({
            "healed": healed, "engine": proposer.engine_code(getattr(self.engine, "name", None)),
            "shape": _code(getattr(landing.shape, "name", None), SHAPE_NAMES), "max_rounds": self.max_rounds,
            # the measures
            "fault_to_verified_fix_s": round(self.clock() - t0, 2) if healed else None,
            "attempts": len(rounds), "rollbacks": rollbacks, "refusals_by_reason": dict(sorted(refusals.items())),
            "rounds": rounds, "ledger": {"chain_ok": ok, "entries": entries},
            "proof": proof if isinstance(proof, str) and PROOF.fullmatch(proof) else "",
            "would_run": pr_steps(landing.would_run) if healed else [],
        })
