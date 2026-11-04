#!/usr/bin/env python3
"""continuity_cells.py - ZMB / Samantha-bar continuity cells C1, C3, C4 (Samantha Initiative lane C E3, success metric section 9).

The Samantha bar asks "does she remember a fact" (turns, same day, or one backdated day). It cannot say whether a THREAD survives a
month: carried at 1 / 7 / 30 days, not made the person tell it twice, not served stale. This module is that instrument. It is
DETERMINISTIC and OFFLINE: neutral synthetic multi-session scripts, a simulated clock, and scorers that read a reply with needle
and marker matching only - no judge model, no network, no database, nothing written. A system under test is any object with

    tell(text, now)        # the member says something (a session on simulated day ``now``)
    ask(text, now) -> str  # the member asks, the system replies

so the same scripts drive the in-process reference systems below (the instrument's own controls) and, later, an adapter over the
live chat API with a backdate (not built here: it needs the brain, so it is queued behind the night window, see
docs/knowledge/samantha-bar.md "Continuity cells").

Cells (the lane's C5 forget-completeness already exists as bar S25 / ZMB axis F and is not repeated):

  C1 thread carry     probe a topic loosely ("Back to the <topic>, where did we get to?") 1 / 7 / 30 days after it was told; a probe CARRIES
                      when the reply holds the thread's CURRENT needle. Reported per horizon with a Wilson interval. Target >= 70% at 7 d.
  C3 stale serve      for threads whose fact changed (dormant) or finished (resolved): the reply asserts the OLD value without the new one
                      (or without a superseding word), or raises a finished thread as still open. Target < 2%.
  C4 re-telling       the reply ASKS the member for a slot they already gave ("what was it again?") - a question, a request marker and no
                      needle. A non-answer ("OK.") is NOT a re-tell: the cell is not the inverse of C1. Target < 10%.

The 12 threads (4 live, 4 dormant that change on day 2, 4 resolved on day 3) are fictional and neutral on purpose: this repo is public.
``run_controls()`` is the self-test: each cell must go the declared way on a system built to break exactly that cell. A cell whose
control does not go red is not an instrument (lane C E3 failure rule) and ``main --selftest`` exits 1.

CLI:
    continuity_cells.py --dry-run      # print the script and the probe plan; nothing runs
    continuity_cells.py --selftest     # the reference systems + controls; exit 0 only when every control goes the declared way
    continuity_cells.py --json F       # with --selftest: write the full result

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

HORIZONS = (1, 7, 30)                 # days after the thread was told (day 0)
CHANGE_DAY = 2                        # a dormant thread's fact changes
RESOLVE_DAY = 3                       # a resolved thread is finished
TARGETS = {"carry_7d_min": 0.70, "retell_max": 0.10, "stale_max": 0.02}   # section 9 of the Initiative record

STATES = ("live", "dormant", "resolved")


@dataclass(frozen=True)
class Thread:
    key: str
    state: str                        # live | dormant | resolved
    topic: str                        # how the member refers to it
    told: str                         # said on day 0
    needle: str                       # the CURRENT value a carrying reply holds
    old_needle: str = ""              # the value before the change (dormant) - serving it alone is stale
    change: str = ""                  # said on CHANGE_DAY (dormant)
    resolve: str = ""                 # said on RESOLVE_DAY (resolved)

    def probe_days(self) -> tuple[int, ...]:
        if self.state == "live":
            return HORIZONS
        if self.state == "dormant":
            return (30,)              # left alone until the long horizon
        return (7, 30)                # resolved: after it was finished


# Neutral, fictional, public-repo safe. Needles are distinctive single tokens so a match means the fact, not a coincidence.
THREADS: tuple[Thread, ...] = (
    Thread("L1", "live", "the garden shed", "I'm repainting the garden shed this month, the colour is sage.", "sage"),
    Thread("L2", "live", "the book club pick", "Our book club is reading The Lantern Maker this term.", "lantern maker"),
    Thread("L3", "live", "the bike service", "The bike goes in for its service at the Kestrel workshop.", "kestrel"),
    Thread("L4", "live", "the pottery class", "I started a pottery class, it runs on Wednesday evenings.", "wednesday"),
    Thread("D1", "dormant", "the conference talk", "I'm giving a conference talk about beekeeping in Marlow.", "harbourton",
           old_needle="marlow", change="Update on the conference talk: it moved from Marlow to Harbourton."),
    Thread("D2", "dormant", "the hiking trip", "We plan to hike the Alder Ridge trail in spring.", "autumn",
           old_needle="spring", change="The hiking trip changed, we're going in autumn instead of spring."),
    Thread("D3", "dormant", "the recital", "My niece's recital is at the Fennel Hall.", "orchard theatre",
           old_needle="fennel", change="The recital moved to the Orchard Theatre, not the Fennel Hall."),
    Thread("D4", "dormant", "the quilt", "I'm sewing a quilt in blue and cream for the fair.", "green and gold",
           old_needle="blue and cream", change="I changed my mind about the quilt, it is green and gold now."),
    Thread("R1", "resolved", "the tax paperwork", "I need to file the tax paperwork, the reference is Juniper-44.", "juniper-44",
           resolve="The tax paperwork is filed and sorted, all done."),
    Thread("R2", "resolved", "the library fine", "I owe the library a small fine for the Cobalt Atlas.", "cobalt atlas",
           resolve="I paid the library fine, that's finished."),
    Thread("R3", "resolved", "the dentist booking", "I must book the dentist with Dr Marrow.", "marrow",
           resolve="The dentist is booked and done."),
    Thread("R4", "resolved", "the parcel", "A parcel from Thistledown is due, I have to be home for it.", "thistledown",
           resolve="The parcel arrived, that's sorted."),
)

# A reply that merely mentions a finished thread is fine when it says it is finished; these say it is STILL open.
_OPEN_MARKERS = ("still need", "still have to", "still waiting", "still pending", "still outstanding", "not yet", "yet to",
                 "don't forget", "remember to", "hasn't been", "haven't yet", "is due", "needs to be", "still to do")
_DONE_MARKERS = ("filed", "paid", "booked", "arrived", "done", "finished", "sorted", "settled", "resolved", "completed")
_SUPERSEDE_MARKERS = ("instead", "changed", "moved", "used to", "was originally", "no longer", "not anymore", "rather than",
                      "switched", "previously", "updated")
_ASK_MARKERS = ("what was", "what is", "what's", "which", "remind me", "tell me", "could you tell", "can you tell", "who is",
                "when is", "when was", "where is", "where was", "again")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' -]+", " ", (text or "").lower())).strip()


def has(text: str, needle: str) -> bool:
    return bool(needle) and norm(needle) in norm(text)


def any_marker(text: str, markers: Iterable[str]) -> bool:
    t = norm(text)
    return any(m in t for m in markers)


def probe_text(th: Thread) -> str:
    return f"Back to {th.topic} - where did we get to?"


# --- scorers (pure, deterministic) ---------------------------------------------------------------------------------------------

def score_carry(th: Thread, reply: str) -> bool:
    """C1: the reply holds the thread's CURRENT value. A resolved thread carries by being known and finished: the needle OR a done word."""
    if th.state == "resolved":
        return has(reply, th.needle) or any_marker(reply, _DONE_MARKERS)
    return has(reply, th.needle)


def score_stale(th: Thread, reply: str) -> bool:
    """C3 (True = the reply is STALE). Dormant: the old value without the new one, or with it but no superseding word.
    Resolved: raised as still open. A live thread cannot be stale in this script (nothing changed)."""
    if th.state == "dormant":
        old, new = has(reply, th.old_needle), has(reply, th.needle)
        return old and (not new or not any_marker(reply, _SUPERSEDE_MARKERS))
    if th.state == "resolved":
        return any_marker(reply, _OPEN_MARKERS) and not any_marker(reply, _DONE_MARKERS)
    return False


def score_retell(th: Thread, reply: str) -> bool:
    """C4 (True = the member is made to tell it again): a question that asks for the slot and does not hold it."""
    t = reply or ""
    return "?" in t and any_marker(t, _ASK_MARKERS) and not has(t, th.needle) and not has(t, th.old_needle)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def _rate(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "wilson95": [round(lo, 4), round(hi, 4)]}


# --- the run ---------------------------------------------------------------------------------------------------------------------

class System(Protocol):
    def tell(self, text: str, now: int) -> None: ...
    def ask(self, text: str, now: int) -> str: ...


def timeline(threads: Iterable[Thread] = THREADS) -> list[dict[str, Any]]:
    """The simulated-time plan: every tell and probe, ordered by day (tells before probes on the same day). No clock is read."""
    ev: list[dict[str, Any]] = []
    for th in threads:
        ev.append({"day": 0, "kind": "tell", "thread": th.key, "text": th.told})
        if th.change:
            ev.append({"day": CHANGE_DAY, "kind": "tell", "thread": th.key, "text": th.change})
        if th.resolve:
            ev.append({"day": RESOLVE_DAY, "kind": "tell", "thread": th.key, "text": th.resolve})
        for d in th.probe_days():
            ev.append({"day": d, "kind": "probe", "thread": th.key, "text": probe_text(th)})
    order = {"tell": 0, "probe": 1}
    return sorted(ev, key=lambda e: (e["day"], order[e["kind"]], e["thread"]))


def run(system: System, threads: Iterable[Thread] = THREADS) -> dict[str, Any]:
    """Drive ``system`` through the timeline and score every probe. Pure given a deterministic system."""
    by_key = {t.key: t for t in threads}
    probes: list[dict[str, Any]] = []
    for e in timeline(by_key.values()):
        if e["kind"] == "tell":
            system.tell(e["text"], e["day"])
            continue
        th = by_key[e["thread"]]
        reply = system.ask(e["text"], e["day"])
        probes.append({"thread": th.key, "state": th.state, "day": e["day"], "carried": score_carry(th, reply),
                       "stale": score_stale(th, reply), "retold": score_retell(th, reply), "reply": reply[:160]})
    carry = {f"{d}d": _rate(sum(p["carried"] for p in probes if p["day"] == d), sum(1 for p in probes if p["day"] == d))
             for d in HORIZONS}
    stale_pool = [p for p in probes if p["state"] in ("dormant", "resolved")]
    cells = {
        "C1": {"name": "thread carry", "by_horizon": carry},
        "C3": {"name": "stale serve", **_rate(sum(p["stale"] for p in stale_pool), len(stale_pool))},
        "C4": {"name": "re-telling", **_rate(sum(p["retold"] for p in probes), len(probes))},
    }
    verdict = {
        "C1": "PASS" if (carry["7d"]["rate"] or 0.0) >= TARGETS["carry_7d_min"] else "FAIL",
        "C3": "PASS" if (cells["C3"]["rate"] if cells["C3"]["rate"] is not None else 1.0) < TARGETS["stale_max"] else "FAIL",
        "C4": "PASS" if (cells["C4"]["rate"] if cells["C4"]["rate"] is not None else 1.0) < TARGETS["retell_max"] else "FAIL",
    }
    return {"cells": cells, "verdict": verdict, "probes": probes, "targets": TARGETS, "threads": len(by_key)}


# --- reference systems: the instrument's own controls ----------------------------------------------------------------------------

@dataclass
class _Mem:
    facts: dict[str, list[tuple[int, str]]] = field(default_factory=dict)   # topic -> [(day, sentence)]


def _topic_of(text: str, threads: Iterable[Thread] = THREADS) -> Thread | None:
    for th in threads:
        if th.topic in text.lower():
            return th
    return None


def _sentence_topic(text: str, threads: Iterable[Thread] = THREADS) -> str | None:
    """Which thread does a told sentence belong to: the one sharing the most distinctive WORDS with it (ties: first)."""
    words = set(norm(text).split())
    best, best_n = None, 0
    for th in threads:
        toks = {w for tok in (th.needle, th.old_needle, *th.topic.split()[1:]) for w in norm(tok).split() if len(w) > 3}
        n = len(toks & words)
        if n > best_n:
            best, best_n = th.key, n
    return best


class PerfectMemory:
    """Remembers every thread, keeps the newest, knows when it was finished. Must PASS all three cells."""
    def __init__(self) -> None:
        self.mem: dict[str, tuple[Thread, str]] = {}

    def tell(self, text: str, now: int) -> None:
        k = _sentence_topic(text)
        th = next((t for t in THREADS if t.key == k), None)
        if th:
            self.mem[th.key] = (th, text)

    def ask(self, text: str, now: int) -> str:
        th = _topic_of(text)
        if not th or th.key not in self.mem:
            return "I'm not sure which one you mean."
        if th.state == "resolved":
            return f"That one is done - it's filed and sorted ({th.needle})."
        if th.state == "dormant":
            return f"It changed: it is {th.needle} now, instead of {th.old_needle}."
        return f"We left it with {th.needle}."


class Amnesiac:
    """Remembers nothing and asks the member to say it all again. Must FAIL C1 and C4 (the re-tell control)."""
    def tell(self, text: str, now: int) -> None:
        pass

    def ask(self, text: str, now: int) -> str:
        return "Sorry, what was it again? Could you tell me about that?"


class Silent:
    """Knows nothing and says nothing useful, without asking. Must FAIL C1 but score 0 on C4 - C4 is not the inverse of C1."""
    def tell(self, text: str, now: int) -> None:
        pass

    def ask(self, text: str, now: int) -> str:
        return "OK."


class StaleServer(PerfectMemory):
    """Never updates: serves the first value of a changed thread and says a finished one is still open. Must FAIL C3."""
    def ask(self, text: str, now: int) -> str:
        th = _topic_of(text)
        if not th or th.key not in self.mem:
            return "I'm not sure which one you mean."
        if th.state == "resolved":
            return f"You still need to sort out {th.topic}, remember to do it ({th.needle})."
        if th.state == "dormant":
            return f"It is {th.old_needle}."
        return f"We left it with {th.needle}."


class DecayingMemory(PerfectMemory):
    """Forgets everything after ``keep_days`` of simulated time. Must keep C1@1d and lose C1@7d / @30d."""
    def __init__(self, keep_days: int = 3) -> None:
        super().__init__()
        self.keep_days = keep_days
        self._told: dict[str, int] = {}

    def tell(self, text: str, now: int) -> None:
        super().tell(text, now)
        k = _sentence_topic(text)
        if k:
            self._told[k] = now

    def ask(self, text: str, now: int) -> str:
        th = _topic_of(text)
        if th and now - self._told.get(th.key, now) > self.keep_days:
            return "Sorry, I don't have anything on that."
        return super().ask(text, now)


def run_controls() -> list[str]:
    """Every cell must go the declared way on a system built to break it. Returns the problems ([] = the instrument discriminates)."""
    bad: list[str] = []

    def chk(cond: bool, msg: str) -> None:
        if not cond:
            bad.append(msg)

    perfect, amn, sil, stale, decay = (run(s) for s in (PerfectMemory(), Amnesiac(), Silent(), StaleServer(), DecayingMemory()))
    chk(perfect["verdict"] == {"C1": "PASS", "C3": "PASS", "C4": "PASS"}, f"the perfect system must pass every cell: {perfect['verdict']}")
    chk(amn["verdict"]["C1"] == "FAIL" and amn["verdict"]["C4"] == "FAIL", "the amnesiac must fail C1 and C4")
    chk(sil["verdict"]["C1"] == "FAIL" and sil["verdict"]["C4"] == "PASS", "a silent non-answer must fail C1 but NOT count as a re-tell")
    chk(stale["verdict"]["C3"] == "FAIL" and stale["verdict"]["C1"] == "PASS", "the stale server must fail C3 and still pass C1")
    chk(decay["cells"]["C1"]["by_horizon"]["1d"]["rate"] == 1.0 and decay["cells"]["C1"]["by_horizon"]["7d"]["rate"] == 0.0
        and decay["cells"]["C1"]["by_horizon"]["30d"]["rate"] == 0.0, "a decaying memory must carry at 1 d and lose 7 d / 30 d")
    chk(perfect["cells"]["C4"]["rate"] == 0.0 and perfect["cells"]["C3"]["rate"] == 0.0, "the perfect system has zero re-telling and zero stale")
    return bad


def plan_text() -> str:
    ev = timeline()
    probes = [e for e in ev if e["kind"] == "probe"]
    lines = [f"continuity cells: {len(THREADS)} threads (4 live, 4 dormant changed on day {CHANGE_DAY}, 4 resolved on day {RESOLVE_DAY}), "
             f"{len(probes)} probes over simulated days {sorted({e['day'] for e in ev})}; no clock, no network"]
    for th in THREADS:
        lines.append(f"  {th.key} {th.state:8s} {th.topic!r}  probes at {list(th.probe_days())} d")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", metavar="FILE")
    args = ap.parse_args(argv)
    if args.dry_run or not args.selftest:
        print(plan_text())
        return 0
    problems = run_controls()
    result = {"controls_ok": not problems, "problems": problems,
              "reference": {name: run(cls())["verdict"] for name, cls in
                            (("perfect", PerfectMemory), ("amnesiac", Amnesiac), ("silent", Silent), ("stale", StaleServer),
                             ("decaying", DecayingMemory))}}
    print(json.dumps(result, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
