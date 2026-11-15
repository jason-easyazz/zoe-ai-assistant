#!/usr/bin/env python3
"""The simulator's own checks (lab only; hand-run, not collected by CI): python3 labs/first-boot/test_sim.py"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sim  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

STAYS = {"ideal", "silences", "mistakes", "child_answers", "walks_away", "power_loss", "owner_refuses_first"}


def by_name(results):
    return {r.name: r for r in results}


def main() -> int:
    ideal = by_name(asyncio.run(_all(today=False)))
    today = by_name(asyncio.run(_all(today=True)))
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    for name, r in ideal.items():
        check(not r.dead_ends, f"{name}: dead ends {r.dead_ends}")
        check(not r.redone_after_restart, f"{name}: redone after restart {r.redone_after_restart}")
        if name in STAYS:
            check(r.ready, f"{name}: never reached a working household (left {r.left})")
    check(not ideal["walks_away_for_good"].ready and "PARKED" in ideal["walks_away_for_good"].trail,
          "walking away for good must park, not fail")
    check(ideal["power_loss"].trail.count("POWER_LOSS") == 2 and ideal["power_loss"].trail.count("resume") == 2,
          "power loss must resume (not say hello again) after each restart")
    later = ideal["says_later"]
    check(later.ready is False and "owner_voice" in later.left and not later.dead_ends,
          "a 'no' to the voice is heard, the flow finishes, and the household is honestly NOT ready")
    # the bound the design states is the CORE flow's declared budget (README, docs/architecture/first-boot.md); the ideal person's
    # whole flow also walks every deferrable step, so it is reported, not bounded (first run: 1140 s declared against a 570 s core)
    check(sim.fs.core_budget_s() <= 600, f"core flow declared {sim.fs.core_budget_s()} s")
    check(ideal["ideal"].declared_s >= sim.fs.core_budget_s(), "the ideal whole flow cannot be shorter than the core flow")
    # today: the very first step has no mechanism, so a buyer stops there
    check("STUCK:owner_account" in today["ideal"].trail, "today a blank unit must be reported stuck at owner_account")
    for name, r in today.items():
        check(not r.dead_ends, f"today {name}: dead ends {r.dead_ends}")
    for line in failures:
        print("FAIL", line)
    print(f"{len(failures)} failures, {len(ideal)} scenarios x 2 modes")
    return 1 if failures else 0


async def _all(today: bool):
    return [await sim.run(s, today=today) for s in SCENARIOS]


if __name__ == "__main__":
    raise SystemExit(main())
