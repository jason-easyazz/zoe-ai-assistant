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
                   "missing_test", "gate_failed", "engine_error", "budget_time", "budget_turns", "budget_tokens"})
assert RETRY <= set(REASONS)


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
            rounds.append({"round": n, "outcome": out.get("outcome", ""), "reason": last, "gate": (bad or [""])[0],
                           "ticket": out.get("ticket", ""), "seconds": round(self.clock() - t1, 2),
                           "turns": eng.last.get("turns"), "tokens": eng.last.get("tokens"),
                           "would_run": eng.last.get("would_run")})
            if not last:
                healed = True
                break
            refusals[last] += 1
            rollbacks += out.get("outcome") in ("rolled_back", "rollback_stuck")
            if last not in RETRY:
                break
        ok, entries = landing.ledger.verify()
        landed = [e for e in landing.ledger.entries() if e["event"] == "landed"] if ok else []
        return {
            "healed": healed, "engine": self.engine.name, "shape": landing.shape.name, "max_rounds": self.max_rounds,
            # the measures
            "fault_to_verified_fix_s": round(self.clock() - t0, 2) if healed else None,
            "attempts": len(rounds), "rollbacks": rollbacks, "refusals_by_reason": dict(sorted(refusals.items())),
            "rounds": rounds, "ledger": {"chain_ok": ok, "entries": entries},
            "proof": landed[-1]["proof"] if healed and landed else "",
            "would_run": landing.would_run if healed else [],
        }
