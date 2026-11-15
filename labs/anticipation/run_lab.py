#!/usr/bin/env python3
"""The anticipation lab: run the engine's pure rule pack over the hand-labelled household-days and print THE NUMBERS.

    python3 labs/anticipation/run_lab.py [--json]

  * precision / recall of the offers against the labels (all days, and the DEV / HELD split);
  * privacy violations, counted by a checker that does NOT use the engine's clamp (must be 0);
  * offers per person per day (made, and after the attention budget of ``BUDGET`` a day);
  * the learning loop: a simulated person answers every surfaced offer - yes when a thoughtful assistant would have made it
    (it is in the labels), no otherwise - one household-day per simulated day; precision and recall over all days are then
    re-measured with what was learned after 30, 90 and 180 days; and the same for a second simulated household that says
    no to every leave-by reminder (does the loop stop offering what is never wanted, and how fast).

Pure and offline: no database, no model, no network. Deterministic: two runs print the same numbers.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import days as D  # noqa: E402  (puts services/zoe-data on sys.path)

import anticipation_learning as L  # noqa: E402
import anticipation_rules as R  # noqa: E402
import anticipation_situations as S  # noqa: E402

PICKY = frozenset({"leave"})       # a second simulated household: it says no to every leave-by reminder
BUDGET = 2                         # offers surfaced per person per day (anticipation.PER_DAY)
PEOPLE = [m.id for m in D.MEMBERS]


def _words(text: str) -> set:
    return {w.lower() for w in re.findall(r"[A-Za-z]{4,}", text or "")}


def privacy_violations(offer: R.Offer, facts: S.Facts) -> list:
    """Why this offer leaks, judged from the RAW facts (independent of ``anticipation_situations.view``)."""
    p, why = offer.person, []
    member = next((m for m in facts.members if m.id == p), None)
    if member is None or member.guest:
        return [f"an offer for a non-member ({p})"]
    events = {e.id: e for e in facts.events}
    hidden = [e for e in facts.events if e.owner != p and e.visibility != "family"]
    visible_words = set().union(*[_words(e.title) | _words(e.place) for e in facts.events if e not in hidden] or [set()])
    said = _words(offer.text(facts.now)) | _words(json.dumps(offer.args))
    for src in offer.sources:
        kind, _, ident = src.partition(":")
        if kind == "event" and events[ident].owner != p and events[ident].visibility == "personal":
            why.append(f"built from another member's personal event {ident}")
        if kind == "birthday" and next(b for b in facts.birthdays if b.id == ident).owner != p:
            why.append(f"built from another member's contact {ident}")
        if kind == "alarm" and next(a for a in facts.alarms if a.id == ident).owner != p:
            why.append(f"built from another member's alarm {ident}")
        if kind == "reminder" and next(r for r in facts.reminders if r.id == ident).owner != p:
            why.append(f"built from another member's reminder {ident}")
    for e in hidden:
        leaked = (_words(e.title) | _words(e.place)) - visible_words
        if leaked & said:
            why.append(f"says a word of another member's hidden event {e.id}")
    return why


def predict(day: dict, tune_for=None, budget: int = 0) -> list:
    out = []
    for p in PEOPLE:
        made = R.offers(day["facts"], p, tune=tune_for(p) if tune_for else None)
        out += made[:budget] if budget else made
    return out


def score(days: list, tune_for=None, budget: int = 0) -> dict:
    tp = fp = fn = leaks = 0
    per_person_day, fp_rules, fn_labels = [], Counter(), Counter()
    for day in days:
        offers = predict(day, tune_for, budget)
        leaks += sum(len(privacy_violations(o, day["facts"])) for o in offers)
        got, want = Counter((o.person, o.rule) for o in offers), Counter(day["expect"])
        hit = sum((got & want).values())
        tp, fp, fn = tp + hit, fp + sum(got.values()) - hit, fn + sum(want.values()) - hit
        fp_rules.update(rule for (_, rule), n in (got - want).items() for _ in range(n))
        fn_labels.update(label for (_, label), n in (want - got).items() for _ in range(n))
        per_person_day += [sum(1 for o in offers if o.person == m.id) for m in D.MEMBERS if not m.guest]
    return {"days": len(days), "offers": tp + fp, "labels": tp + fn, "tp": tp, "fp": fp, "fn": fn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "privacy_violations": leaks, "offers_per_person_per_day": round(sum(per_person_day) / len(per_person_day), 3),
            "max_offers_one_person_one_day": max(per_person_day), "false_offers_by_rule": dict(sorted(fp_rules.items())),
            "missed_by_label": dict(sorted(fn_labels.items()))}


def _wanted(day: dict, dislikes: frozenset) -> list:
    return [x for x in day["expect"] if x[1] not in dislikes]


def simulate(sim_days: int, dislikes: frozenset = frozenset()) -> dict:
    """One household-day per simulated day, in order, round and round. Returns each person's outcome history.
    ``dislikes``: rule ids this simulated household never wants (they say no to them even where the labels say yes)."""
    history = {p: [] for p in PEOPLE}
    for i in range(sim_days):
        day = D.DAYS[i % len(D.DAYS)]
        at = day["facts"].now + timedelta(days=i)
        for p in PEOPLE:
            made = R.offers(day["facts"], p, tune=L.tuner(_shift(history[p], day["facts"].now, at), day["facts"].now))
            for o in made[:BUDGET]:
                history[p].append(L.Outcome(o.rule, "acted" if (p, o.rule) in _wanted(day, dislikes) else "dismissed", at))
    return history


def _shift(history: list, fixture_now, real_now) -> list:
    """The fixtures all live on one calendar date: move the history into the fixture's frame so ages are the simulated ones."""
    return [L.Outcome(o.rule, o.outcome, o.at - (real_now - fixture_now)) for o in history]


def learned(sim_days: int, dislikes: frozenset = frozenset()) -> dict:
    history = simulate(sim_days, dislikes)
    end = lambda day: day["facts"].now + timedelta(days=sim_days)  # noqa: E731

    def tune_for_day(day):
        return lambda p: L.tuner(_shift(history[p], day["facts"].now, end(day)), day["facts"].now)

    tp = fp = fn = 0
    for day in D.DAYS:
        offers = predict(day, tune_for_day(day))
        got, want = Counter((o.person, o.rule) for o in offers), Counter(_wanted(day, dislikes))
        hit = sum((got & want).values())
        tp, fp, fn = tp + hit, fp + sum(got.values()) - hit, fn + sum(want.values()) - hit
    last = D.DAYS[0]["facts"].now
    quiet = sorted({f"{p}:{r.id}" for p in PEOPLE for r in R.RULES
                    if L.state(_shift(history[p], last, last + timedelta(days=sim_days)), r.id, last).tuning.quiet})
    return {"simulated_days": sim_days, "answers": sum(len(h) for h in history.values()),
            "yes": sum(1 for h in history.values() for o in h if o.outcome == "acted"),
            "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "offers": tp + fp, "quiet_rules": quiet}


def main() -> int:
    report = {"all": score(D.DAYS), "dev": score(D.DEV), "held_out": score(D.HELD), "after_budget": score(D.DAYS, budget=BUDGET),
              "learning": [learned(n) for n in (30, 90, 180)],
              "learning_household_that_never_wants_leave_by": [learned(n, PICKY) for n in (0, 30, 90, 180)]}
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
        return 0
    for name in ("all", "dev", "held_out", "after_budget"):
        r = report[name]
        print(f"{name:13s} days={r['days']:2d} offers={r['offers']:2d} labels={r['labels']:2d} precision={r['precision']} recall={r['recall']} "
              f"privacy_violations={r['privacy_violations']} offers/person/day={r['offers_per_person_per_day']} "
              f"max={r['max_offers_one_person_one_day']}")
    print("false offers by rule:", report["all"]["false_offers_by_rule"])
    print("missed, by label:   ", report["all"]["missed_by_label"])
    base = report["all"]
    print(f"learning (precision / recall over all days; before: {base['precision']} / {base['recall']}):")
    for r in report["learning"]:
        print(f"  after {r['simulated_days']:3d} days ({r['answers']} answers, {r['yes']} yes): precision={r['precision']} recall={r['recall']} "
              f"offers={r['offers']} quiet={r['quiet_rules']}")
    print("learning, a household that never wants a leave-by (scored against its own wishes):")
    for r in report["learning_household_that_never_wants_leave_by"]:
        print(f"  after {r['simulated_days']:3d} days ({r['answers']} answers, {r['yes']} yes): precision={r['precision']} recall={r['recall']} "
              f"offers={r['offers']} quiet={r['quiet_rules']}")
    return 0 if base["privacy_violations"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
