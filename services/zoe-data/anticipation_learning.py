"""anticipation_learning - the LEARNING LOOP of the anticipation engine (pure, deterministic, inspectable; no model).

Input: one person's ``Outcome`` history - (rule, ``acted`` | ``dismissed`` | ``ignored``, when) - which the store derives
from the offers themselves (every answered ``pending_suggestions`` anticipation row keeps its rule, outcome and time; there
is no second table to drift, and a forget that deletes an offer deletes its vote).

Per person and per rule, ``state`` gives:
  * ``score``: yeses minus noes minus half the ignoreds, each weighted by age (half-life ``HALF_LIFE_DAYS``), so an old
    answer fades; ``priority`` = ``PRIORITY_STEP`` x score, capped at +/- ``PRIORITY_CAP`` (added to the rule's base);
  * ``lead_h``: a rule the person keeps accepting may be said earlier - ``LEAD_STEP_H`` per yes in a row, to ``LEAD_CAP_H``;
  * ``quiet_until``: ``QUIET_AFTER`` noes in a row (an ignored offer neither counts nor breaks the run; a yes resets it)
    silence the rule for ``QUIET_DAYS`` from the last no. After that it may be offered once more: another no and it is quiet
    again from that day; a yes and the run is over.

``tuner(history, now)`` is the ``tune`` hook of ``anticipation_rules.offers``. ``explain`` answers "why did you offer this?"
and "why did you stop offering that?" with the exact stored counts and dates - nothing here is a guess.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from anticipation_rules import BY_ID, MIN_PRIORITY, Tuning

HALF_LIFE_DAYS = 30
PRIORITY_STEP = 10
PRIORITY_CAP = 30
LEAD_STEP_H = 6
LEAD_CAP_H = 18
QUIET_AFTER = 3
QUIET_DAYS = 60
IGNORED_WEIGHT = 0.5
OUTCOMES = ("acted", "dismissed", "ignored")


@dataclass(frozen=True)
class Outcome:
    rule: str
    outcome: str
    at: datetime


@dataclass(frozen=True)
class RuleState:
    rule: str
    acted: int
    dismissed: int
    ignored: int
    score: float
    yes_run: int                  # yeses in a row, most recent first
    no_run: int                   # noes in a row, most recent first
    last_no: Optional[datetime]
    quiet_until: Optional[datetime]
    tuning: Tuning


def state(history: list, rule: str, now: datetime) -> RuleState:
    mine = sorted((o for o in history if o.rule == rule and o.outcome in OUTCOMES and o.at <= now), key=lambda o: o.at)
    count = {k: sum(1 for o in mine if o.outcome == k) for k in OUTCOMES}
    weight = {"acted": 1.0, "dismissed": -1.0, "ignored": -IGNORED_WEIGHT}
    score = sum(weight[o.outcome] * 0.5 ** ((now - o.at).total_seconds() / 86400 / HALF_LIFE_DAYS) for o in mine)
    answered = [o for o in reversed(mine) if o.outcome != "ignored"]
    yes_run = next((i for i, o in enumerate(answered) if o.outcome != "acted"), len(answered))
    no_run = next((i for i, o in enumerate(answered) if o.outcome != "dismissed"), len(answered))
    last_no = answered[0].at if no_run else None
    quiet_until = last_no + timedelta(days=QUIET_DAYS) if no_run >= QUIET_AFTER and last_no else None
    quiet = quiet_until is not None and now < quiet_until
    priority = max(-PRIORITY_CAP, min(PRIORITY_CAP, round(PRIORITY_STEP * score)))
    return RuleState(rule, count["acted"], count["dismissed"], count["ignored"], round(score, 4), yes_run, no_run, last_no,
                     quiet_until, Tuning(priority=priority, lead_h=min(LEAD_CAP_H, LEAD_STEP_H * yes_run), quiet=quiet))


def tuner(history: list, now: datetime) -> Callable[[str], Tuning]:
    cache: dict = {}

    def tune(rule: str) -> Tuning:
        if rule not in cache:
            cache[rule] = state(history, rule, now).tuning
        return cache[rule]

    return tune


def explain(history: list, rule: str, now: datetime, *, disabled: bool = False) -> dict:
    """The exact reason a rule does or does not offer for this person: ``{rule, status, why, ...counts}``.
    ``status``: ``off`` (switched off by the person) | ``quiet`` | ``below_threshold`` | ``offering``."""
    st = state(history, rule, now)
    base = BY_ID[rule].priority if rule in BY_ID else 0
    tuned = max(0, min(100, base + st.tuning.priority))
    day = lambda d: d.date().isoformat()  # noqa: E731
    tally = f"yes {st.acted}, no {st.dismissed}, left unanswered {st.ignored}"
    if disabled:
        status, why = "off", "You switched this kind of offer off."
    elif st.tuning.quiet:
        status = "quiet"
        why = (f"You said no {st.no_run} times in a row (the last on {day(st.last_no)}), so I stopped offering it. "
               f"I'll offer it once more after {day(st.quiet_until)}.")
    elif tuned < MIN_PRIORITY:
        status, why = "below_threshold", f"Your answers ({tally}) put it at priority {tuned}, under the {MIN_PRIORITY} I need to offer it."
    else:
        status = "offering"
        why = (f"Its priority is {tuned} (base {base}, {st.tuning.priority:+d} from your answers: {tally})"
               + (f", and I may say it {st.tuning.lead_h} hours earlier because you said yes {st.yes_run} time{'s' if st.yes_run != 1 else ''} in a row."
                  if st.tuning.lead_h else "."))
    return {"rule": rule, "status": status, "why": why, "base_priority": base, "priority": tuned, "acted": st.acted,
            "dismissed": st.dismissed, "ignored": st.ignored, "score": st.score, "yes_run": st.yes_run, "no_run": st.no_run,
            "lead_h": st.tuning.lead_h, "quiet_until": day(st.quiet_until) if st.quiet_until else None}
