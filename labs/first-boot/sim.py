#!/usr/bin/env python3
"""First-boot simulator: the state machine + the flow script against a SCRATCH store, with a scripted person.

Lab only (labs/AGENTS.md): never imported by a service, never in CI, touches no live database, no model, no network. It builds a
throwaway SQLite store with the columns ``first_boot_probe`` reads, then plays a scenario: each step is offered by
``first_boot.next_step``, the scripted person answers with a BEHAVIOUR (yes, later, silence, unclear, fail, child, walk_away...),
``first_boot_script.transition`` says what Zoe does next, and when a step's action succeeds the sim writes what the real mechanism
would write (an auth_users row, a speaker_profiles row, ...) so the NEXT probe reads it back. Nothing is told to the state
machine directly: it only ever sees the store.

THE NUMBERS (per scenario): step actions until the household is ready, offers made, declared time (the step budgets from the
script plus TURN_S per extra turn; DECLARED, not measured), dead ends (a behaviour with no forward transition: must be 0), help
points hit (a person stuck without outside help), parks, and, in ``today`` mode, every step whose mechanism does not exist yet.

    python3 labs/first-boot/sim.py            # every scenario, ideal mechanisms
    python3 labs/first-boot/sim.py --today    # the same, with the mechanisms that are 'missing' today failing
    python3 labs/first-boot/sim.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "services" / "zoe-data"))

import aiosqlite  # noqa: E402

import first_boot as fb  # noqa: E402
import first_boot_probe as probe  # noqa: E402
import first_boot_script as fs  # noqa: E402
import speaker_models  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402
from scratch_store import SCHEMA  # noqa: E402

OWNER = "owner-sim"
#: a repeat / rephrase / explain turn, declared
TURN_S = 15
#: hard stop: a scenario that offers more than this many steps is reported as a loop (a dead end in time)
MAX_OFFERS = 80
T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
_SIM_ENV = ("ZOE_TIMEZONE", "ZOE_HOUSEHOLD_COUNTRY", "ZOE_LOCATION_COUNTRY", "ZOE_FORGET_LEDGER_SALT", "ZOE_OFFBOX_CONFIG_DIR")


@dataclass
class Result:
    name: str
    ready: bool = False
    actions_to_ready: int = 0
    actions: int = 0
    offers: int = 0
    declared_s: int = 0
    dead_ends: list = field(default_factory=list)
    help_points: list = field(default_factory=list)
    parks: int = 0
    missing_mechanisms: list = field(default_factory=list)
    redone_after_restart: list = field(default_factory=list)
    left: list = field(default_factory=list)
    trail: list = field(default_factory=list)


class Person:
    """Answers from the scenario: ``answers[step]`` is consumed in order; when it runs out the person says yes."""

    def __init__(self, answers: dict):
        self._answers = {k: list(v) for k, v in answers.items()}

    def answer(self, step_id: str) -> str:
        queue = self._answers.get(step_id) or []
        return queue.pop(0) if queue else "yes"


async def _do(db, step_id: str, ctx: dict) -> None:
    """What the real mechanism would leave behind when the step succeeds."""
    stamp = ctx["now"].strftime("%Y-%m-%d %H:%M:%S")
    writes = {
        "owner_account": [("INSERT INTO auth_users VALUES (?, 'owner', 'admin', 'hash', ?, 1)", (OWNER, stamp))],
        "panel_paired": [("INSERT INTO panels VALUES ('panel-ab12', 'Panel-AB12', NULL, 1)", ()),
                         ("INSERT INTO device_tokens VALUES ('panel-ab12', 0)", ())],
        "panel_named": [("INSERT INTO rooms VALUES ('room-1', 'Kitchen', 'kitchen')", ()),
                        ("UPDATE panels SET name = 'Kitchen', room_id = 'room-1' WHERE panel_id = 'panel-ab12'", ())],
        "owner_pin": [("INSERT INTO passcodes VALUES (?, 1)", (OWNER,))],
        "owner_voice": [("INSERT INTO speaker_profiles VALUES ('sp-1', ?, 3, ?, ?, ?)",
                         (OWNER, stamp, stamp, speaker_models.RESNET34_MODEL_ID))],
        "first_win": [("INSERT INTO reminders VALUES ('rem-1', ?, 1)", (OWNER,))],
        "phone_linked": [("INSERT INTO push_subscriptions VALUES (?, 'endpoint-placeholder')", (OWNER,))],
        "more_members": [("INSERT INTO auth_users VALUES ('member-sim', 'member', 'user', 'hash', ?, 1)", (stamp,))],
        "child_marked": [("INSERT INTO member_modes VALUES ('child-sim', 'kid', 1)", ())],
        "calendar": [("INSERT INTO connected_accounts VALUES ('ca-1', ?, 'calendar', 'active')", (OWNER,))],
        "music": [("INSERT INTO connected_accounts VALUES ('ca-2', ?, 'music', 'active')", (OWNER,))],
    }
    for sql, params in writes.get(step_id, []):
        await db.execute(sql, params)
    await db.commit()
    if step_id == "forget_secret":
        os.environ["ZOE_FORGET_LEDGER_SALT"] = "sim-" + "x" * 28
    elif step_id == "time_and_place":
        os.environ["ZOE_TIMEZONE"] = "Europe/Dublin"
    elif step_id == "backup_target":
        (Path(ctx["backup_dir"]) / "offbox.env").write_text("# simulated target\n")


async def _seed(db, seed: list) -> None:
    for sql in seed:
        await db.execute(sql)
    await db.commit()


def _offer_queue(facts: fb.Facts, offered_optional: set) -> str | None:
    """Core first (next_step), then each optional item once."""
    step = fb.next_step(facts)
    if step is not None:
        return step.id
    for s in fb.evaluate(facts):
        if s.tier == fb.OPTIONAL and s.state == fb.MISSING and s.id not in offered_optional:
            return s.id
    return None


async def run(scn: dict, *, today: bool = False) -> Result:
    res = Result(scn["name"])
    person = Person(scn.get("answers", {}))
    saved = {k: os.environ.get(k) for k in _SIM_ENV}
    for k in _SIM_ENV:
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory(prefix="first-boot-sim-") as tmp:
        path, backup_dir = str(Path(tmp) / "scratch.db"), str(Path(tmp) / "backup")
        Path(backup_dir).mkdir()
        ctx = {"now": T0, "backup_dir": backup_dir}
        async with aiosqlite.connect(path) as db:
            for ddl in SCHEMA:
                await db.execute(ddl)
            await _seed(db, scn.get("seed", []))
        completed: list = []
        restarts = list(scn.get("power_loss_after", []))
        try:
            while True:                                  # one pass = one boot; a power loss starts a new pass
                rebooted = await _boot(path, ctx, person, res, completed, restarts, today, scn)
                if not rebooted:
                    break
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    return res


async def _boot(path, ctx, person, res, completed, restarts, today, scn) -> bool:
    """Run until done, parked for good, or a power loss (True = reboot). In-memory state does not survive a reboot."""
    retries: dict = {}
    offered_optional: set = set()
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        facts = await probe.gather(db=db, now=ctx["now"], backup_dir=ctx["backup_dir"])
        opener = "hello" if not facts.owner_account else "resume"
        res.trail.append(opener)
        if opener == "hello":
            res.declared_s += int(fs.step("hello")["budget_s"])
        while res.offers < MAX_OFFERS:
            facts = await probe.gather(db=db, now=ctx["now"], backup_dir=ctx["backup_dir"])
            if fb.ready(facts) and not res.ready:
                res.ready, res.actions_to_ready = True, res.actions
            sid = _offer_queue(facts, offered_optional)
            if sid is None:
                res.declared_s += int(fs.step("done")["budget_s"])
                res.trail.append("done")
                res.left = [s.id for s in fb.missing(facts, include_optional=True)]
                return False
            if fb.BY_ID[sid].tier == fb.OPTIONAL:
                offered_optional.add(sid)
            res.offers += 1
            behaviour = person.answer(sid)
            if behaviour == "yes" and today and fs.step(sid)["backed_by"]["status"] == "missing":
                res.missing_mechanisms.append(sid)
                behaviour = "fail"
            t = fs.transition(sid, behaviour)
            res.trail.append(f"{sid}:{behaviour}->{t}")
            if t is None:
                res.dead_ends.append((sid, behaviour))
                return False
            if t == "action":
                if sid in completed:
                    res.redone_after_restart.append(sid)
                await _do(db, sid, ctx)
                completed.append(sid)
                res.actions += 1
                res.declared_s += int(fs.step(sid)["budget_s"])
                ctx["now"] += timedelta(seconds=int(fs.step(sid)["budget_s"]))
                if restarts and restarts[0] == sid:
                    restarts.pop(0)
                    res.trail.append("POWER_LOSS")
                    return True
                continue
            res.declared_s += TURN_S
            ctx["now"] += timedelta(seconds=TURN_S)
            if t in ("repeat", "rephrase"):
                retries[sid] = retries.get(sid, 0) + 1
                if retries[sid] <= fs.structure()["retries"]:
                    continue
                t = "park"
            if t == "defer":
                await probe.defer(OWNER, sid, now=ctx["now"], db=db)
                offered_optional.add(sid)
                continue
            if t == "help":
                res.help_points.append(sid)
            if t in ("help", "explain", "ask_adult", "park"):
                res.parks += 1 if t == "park" else 0
                retries[sid] = 0
                if person.answer("_after_park") == "gone":   # nobody comes back: setup stays paused, nothing is lost
                    res.trail.append("PARKED")
                    res.left = [s.id for s in fb.missing(facts, include_optional=True)]
                    return False
                ctx["now"] += timedelta(minutes=10)
                if t == "help" and sid not in completed:
                    if fb.BY_ID.get(sid) and fb.BY_ID[sid].tier != fb.BLOCKING:
                        await probe.defer(OWNER, sid, now=ctx["now"], db=db)
                        offered_optional.add(sid)
                    elif today and fs.step(sid)["backed_by"]["status"] == "missing":
                        res.trail.append("STUCK:" + sid)    # a blocking step with no mechanism: nobody can get past it
                        res.left = [s.id for s in fb.missing(facts, include_optional=True)]
                        return False
                continue
            if t == "next":
                continue
    res.dead_ends.append(("loop", "max_offers"))
    return False


def report(results: list, *, today: bool) -> dict:
    return {
        "mode": "today" if today else "ideal",
        "static": {"dead_ends": fs.dead_ends(), "help_points": fs.help_points(), "core_budget_s": fs.core_budget_s(),
                   "problems": fs.problems()},
        "scenarios": [{k: v for k, v in r.__dict__.items() if k != "trail"} | {"trail": r.trail} for r in results],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--today", action="store_true", help="mechanisms marked 'missing' in the script fail")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    chosen = [s for s in SCENARIOS if not args.only or s["name"] == args.only]
    results = [asyncio.run(run(s, today=args.today)) for s in chosen]
    rep = report(results, today=args.today)
    if args.json:
        print(json.dumps(rep, indent=1, default=list))
        return 0
    st = rep["static"]
    print(f"mode={rep['mode']}  static dead ends={len(st['dead_ends'])}  help points={len(st['help_points'])}  "
          f"core budget={st['core_budget_s']} s  script problems={len(st['problems'])}")
    print(f"{'scenario':<22}{'ready':>6}{'acts->ready':>12}{'offers':>8}{'declared':>10}{'dead':>6}{'help':>6}{'parks':>6}"
          f"{'redone':>8}  missing mechanisms / left")
    for r in results:
        tail = ",".join(r.missing_mechanisms) or "-"
        print(f"{r.name:<22}{str(r.ready):>6}{r.actions_to_ready if r.ready else '-':>12}{r.offers:>8}{r.declared_s:>9}s"
              f"{len(r.dead_ends):>6}{len(r.help_points):>6}{r.parks:>6}{len(r.redone_after_restart):>8}  {tail} / "
              f"{','.join(r.left) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
