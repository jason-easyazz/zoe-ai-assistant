#!/usr/bin/env python3
"""person_half_replay.py - the person-half guards, replayed offline over the KEPT replies of a samantha_person run.

Why: the three guards (hold the fact, ask when ambiguous, a clean goodbye) are code in front of / behind the brain, so
the brain's own turn-1 answers do not change when they are on - only what Zoe finally says on the pushback / the
ambiguous request / the farewell does. That makes the kept replies of a `samantha_person.py --keep-replies` run a fair
substrate: for every ask this script asks the REAL guard code what it would have said (history and roster rebuilt
from the ask itself, the owner-stated row faked with the real `memory_authority` stamps), puts that reply in place
of the brain's, and hands the edited payload to the bench's OWN `rescore()` - the same scorers, Wilson bars and
verdicts as the live table. Everything the guards do not touch is carried over verbatim, so "no regression" is a
diff, not a claim.

What it is NOT: a live run. It does not exercise the DB history read, the memory search or the roster query (those are
faked from the ask), and the replies it keeps are the ones the unchanged stack gave. The live run is the same
command as the baseline, after the flags are `enforce` on the service:

    ZOE_PERF=1 flock /tmp/zoe-voice-harness.lock python3 scripts/perf/samantha_person.py --keep-replies --only P5a,P7,P8,P12

Usage:
    python3 scripts/perf/person_half_replay.py [--results ~/.cache/zoe/samantha_person_last.json] [--out FILE] [--json]

Needs `samantha_person.py` next to this file (PR #1933) or in $ZOE_PERSON_BENCH_DIR. Never touches a live service.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (os.environ.get("ZOE_PERSON_BENCH_DIR"), str(HERE), str(REPO / "services" / "zoe-data")):
    if p and p not in sys.path:
        sys.path.insert(0, p)

try:
    import samantha_person as sp  # noqa: E402
except ImportError as exc:  # pragma: no cover - the bench is a separate PR
    sys.exit(f"samantha_person.py not found ({exc}); set ZOE_PERSON_BENCH_DIR to the folder holding it")

import ask_when_ambiguous as awa  # noqa: E402
import clean_goodbye as cg  # noqa: E402
import hold_the_fact as htf  # noqa: E402
import memory_authority as ma  # noqa: E402

DEFAULT_RESULTS = Path.home() / ".cache" / "zoe" / "samantha_person_last.json"
UID = "demo_bar_replay"
CELLS = ("P5a", "P7", "P8", "P12")
HOLD_KINDS = ("hold", "update", "neutral", "p12_flip")
#: P10 is sampled FROM the guarded cells' replies (P2 / P6 / P7), so it is derived, not independent: reported, not "untouched"
DERIVED = ("P10",)


def _filler(ask: "sp.Ask") -> list:
    """The non-probe turns in front of the question (P12): the user's filler, and a bland assistant ack."""
    out: list = []
    for t in ask.turns:
        if not t.probe:
            out += [("user", t.render([])), ("assistant", "That sounds nice.")]
    return out


def _owner_row(world: "sp.World"):
    """The row the live write path leaves for `say_dentist`: the owner's words, class user_stated."""
    text = f"User has the dentist on {world.day} for a {world.ailment}."
    meta = {"status": "approved", "authority_class": ma.USER_STATED, "authority": ma.USER_STATED}
    return types.SimpleNamespace(id="row-dentist", text=text, metadata=meta)


class Replay:
    def __init__(self, world: "sp.World"):
        self.world = world

    def hold(self, ask: "sp.Ask", replies: list) -> str:
        """What the hold tier says on the pushback, or '' (the brain's reply stands)."""
        w = self.world
        q = ask.turns[-2].render([]) if len(ask.turns) >= 2 else ""
        pushback = ask.turns[-1].render(replies[:1])
        if pushback is None:
            return ""
        row = _owner_row(w)
        history = list(reversed(_filler(ask) + [("user", q), ("assistant", replies[0])]))

        async def _history(_sid):
            return list(history)

        async def _row(_uid, _question, held):
            return row if any(htf._same(held, a) for a in htf.atoms(row.text)) and \
                ma.row_authority(row.metadata, row.text) in ma.PROTECTED else None

        async def _apply(*_a, **_k):                       # the edit itself is MemoryService.review: faked as accepted
            return True

        saved = htf._history, htf._owner_row, htf._apply_update, os.environ.get(htf.ENV)
        htf._history, htf._owner_row, htf._apply_update = _history, _row, _apply
        os.environ[htf.ENV] = "enforce"
        try:
            return asyncio.run(htf.handle(pushback, UID, "replay"))
        finally:
            htf._history, htf._owner_row, htf._apply_update = saved[0], saved[1], saved[2]
            if saved[3] is None:
                os.environ.pop(htf.ENV, None)
            else:
                os.environ[htf.ENV] = saved[3]

    def ask(self, text: str) -> str:
        w = self.world
        people = [awa.Candidate("1", f"Marisol {w.marisol_a}", "sister"), awa.Candidate("2", f"Marisol {w.marisol_b}", "colleague"),
                  awa.Candidate("3", f"{w.solo_first} {w.solo_last}", "brother")]
        amb = awa.ambiguity(text, awa.repeated_first_names(people))
        return amb.question if amb else ""

    def goodbye(self, text: str, reply: str) -> str:
        kind = cg.classify(text)
        return cg.clean(kind, reply, text) if kind else reply


def guarded(payload: dict, arm: str = "none") -> tuple:
    """(edited payload, per-kind counts of replies the guards replaced)."""
    out = copy.deepcopy(payload)
    ev = {e["ask"]: e for e in out["arms"][arm].get("evidence", [])}
    replaced: dict = {}
    for seed in payload.get("worlds", [sp.BASE_SEED]):
        world = sp.World(seed)
        rp = Replay(world)
        for ask in sp.build_asks(world, CELLS, payload.get("n_cap"), payload.get("p12_sessions", 4)):
            e = ev.get(ask.id)
            if not e or not e.get("replies") or e.get("unexercised"):
                continue
            replies = e["replies"]
            new = ""
            if ask.kind in HOLD_KINDS and len(replies) >= 2:
                new = rp.hold(ask, replies)
            elif ask.kind in ("ambig", "clear"):
                new = rp.ask(ask.turns[0].text)
            elif ask.kind in ("goodbye", "silence", "presence"):
                fixed = rp.goodbye(ask.turns[0].text, replies[-1])
                new = fixed if fixed != replies[-1] else ""
            if new and new != replies[-1]:
                e["replies"] = replies[:-1] + [new]
                replaced[ask.kind] = replaced.get(ask.kind, 0) + 1
    return out, replaced


def table(before: dict, after: dict, cells=CELLS + DERIVED) -> list:
    rows = []
    b, a = sp.result_by_half(before), sp.result_by_half(after)
    for hid in sorted(b, key=lambda h: (sp.CELLS.index(sp.HALF[h].cell), h)):
        if sp.HALF[hid].cell not in cells:
            continue
        x, y = b[hid], a[hid]
        rows.append((hid, sp.HALF[hid].label[:62], x["bar"], f'{x["k"]}/{x["n"]}', x["verdict"], f'{y["k"]}/{y["n"]}', y["verdict"]))
    return rows


def untouched(before: dict, after: dict) -> list:
    """Halves outside the guarded (and derived) cells whose k/n/verdict moved (must be empty)."""
    b, a = sp.result_by_half(before), sp.result_by_half(after)
    return [h for h in b if sp.HALF[h].cell not in CELLS + DERIVED
            and (b[h]["k"], b[h]["n"], b[h]["verdict"]) != (a[h]["k"], a[h]["n"], a[h]["verdict"])]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", default=str(DEFAULT_RESULTS))
    ap.add_argument("--arm", default="none")
    ap.add_argument("--out")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    payload = json.loads(Path(args.results).read_text())
    before = sp.rescore(payload)
    edited, replaced = guarded(payload, args.arm)
    after = sp.rescore(edited)
    rows = table(before[args.arm], after[args.arm], CELLS + DERIVED)
    moved = untouched(before[args.arm], after[args.arm])
    result = {"replaced": replaced, "halves": [dict(zip(("half", "label", "bar", "before", "before_verdict", "after", "after_verdict"), r)) for r in rows],
              "untouched_halves_moved": moved}
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1))
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(f"replies replaced by a guard: {replaced}")
        print(f'{"half":8} {"what":64} {"bar":10} {"before":>10} {"":14} {"after":>8}')
        for r in rows:
            print(f"{r[0]:8} {r[1]:64} {r[2]:10} {r[3]:>10} {r[4]:14} {r[5]:>8} {r[6]}")
        print("halves outside P5a/P7/P8/P12 (and the derived P10) that moved:", moved or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
