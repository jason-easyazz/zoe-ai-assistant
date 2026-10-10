"""When does a night thread END? Two readings must agree (a moment that says it finished, and the THREADS call or a dated plan about the SAME matter) - and a ``change`` is not a finish.

Reproduces what the live 4B wrote on 2026-10-10 (synthetic names, scripted replies; the model's lines and labels are the measured ones):

* K9: 'The Hollowick trip is off, we are going to Tarnholt instead.' carried ``kind: change, later: done``; the thread also held the plan 'We are going to Hollowick on the 12th' -> the old rule
  CLOSED the trip ('resolved') instead of marking it 'changed', so the cell found 1 of 2 drifts, 3 runs of 3.
* K10: 'Sorrel practised the cello for an hour today.' carried ``later: done`` and the thread held the plan 'Sorrel has a school concert at Kingsmoor on the 20th' -> 'resolved' on its second
  mention (the plan reading was satisfied by ANY earlier plan in the thread), so the benign thread could never be raised (benign_raises 0, 3 runs of 3).
* K11 (the control that must stay green): 'The quiz night is on the 20th' ... 'The quiz night went really well, we came second' -> resolved even when the THREADS call says ``open``.

Each test goes red when its rule is reverted (the three exclusions are three lines of ``night_mind.finishes``).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re

import pytest

import night_mind as nm
import night_store as ns

pytestmark = pytest.mark.ci_safe

NOW = dt.datetime(2026, 10, 9, 3, 0, tzinfo=dt.timezone.utc)
UID = "demo_bar_00000003"


def mom(text, day, kind="progress", later="open", who=()):
    return nm.Moment(turn=nm.Turn(f"t{day}", text, NOW - dt.timedelta(days=day)), quote=text, kind=kind, who=list(who), later=later, state="current")


PLAN_TRIP = mom("We are going to Hollowick on the 12th for Dune's birthday.", 15, "plan", "open", ["Dune"])
TRIP_OFF = mom("The Hollowick trip is off, we are going to Tarnholt instead.", 5, "change", "done")
#: the same shape with a matter word in common ('train'): only the ``change`` exclusion keeps this one from closing the plan
TRAIN_PLAN = mom("We are taking the train to Hollowick on the 12th.", 15, "plan", "open")
TRAIN_OFF = mom("The train is cancelled, we are driving to Tarnholt instead.", 5, "change", "done")
PLAN_CONCERT = mom("Sorrel has a school concert at Kingsmoor on the 20th.", 12, "plan", "open", ["Sorrel"])
CELLO = mom("Sorrel practised the cello for an hour today.", 2, "progress", "done", ["Sorrel"])
PLAN_QUIZ = mom("The Marlowe quiz night is on the 20th.", 12, "plan", "open")
QUIZ_DONE = mom("The Marlowe quiz night went really well, we came second.", 2, "progress", "done")


def test_a_change_is_not_a_finish_the_cancelled_trip_is_changed_not_closed_K9():
    assert nm.finishes([PLAN_TRIP, TRIP_OFF], "open") is False
    assert nm.finishes([PLAN_TRIP, TRIP_OFF], "changed") is False
    assert nm.finishes([TRAIN_PLAN, TRAIN_OFF], "open") is False                  # shares 'train' with the plan: only "a change is not a finish" keeps it open


def test_a_line_about_something_else_in_the_story_does_not_finish_the_plan_K10():
    assert nm.finishes([PLAN_CONCERT, CELLO], "open") is False                  # shares only the child's name


def test_a_line_about_the_planned_matter_finishes_it_even_when_the_threads_call_says_open_K11():
    assert nm.finishes([PLAN_QUIZ, QUIZ_DONE], "open") is True


def test_the_threads_call_calling_it_resolved_still_counts_as_the_second_reading():
    assert nm.finishes([PLAN_CONCERT, CELLO], "resolved") is True               # an explicit 'resolved' from the model plus a done moment is the other agreeing reading
    assert nm.finishes([CELLO], "open") is False and nm.finishes([PLAN_QUIZ], "open") is False       # no done moment: nothing finished, whatever else is there


def test_the_stored_plan_of_an_earlier_night_is_read_the_same_way():
    old_quiz = [{"kind": "plan", "later": "open", "said_at": (NOW - dt.timedelta(days=12)).timestamp(), "quote": PLAN_QUIZ.quote, "who": ""}]
    old_concert = [{"kind": "plan", "later": "open", "said_at": (NOW - dt.timedelta(days=12)).timestamp(), "quote": PLAN_CONCERT.quote, "who": "Sorrel"}]
    assert nm.finishes([QUIZ_DONE], "open", old_quiz) is True
    assert nm.finishes([CELLO], "open", old_concert) is False


# ── end to end: the statuses the cells read ───────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def lab():
    backend = ns.MemoryBackend()
    ns.set_backend(backend)
    nm._reset_state()
    prev = nm.set_config(nm.Config(ctx_tokens=8192, chunk_tokens=900, max_calls=7, url="http://127.0.0.1:1"))
    yield backend
    nm.set_llm(None)
    nm.set_config(prev)
    ns.set_backend(None)


FILLER = [mom("Gustav brought round more seedlings for the garden.", 9), mom("Picked the first tomatoes from the garden with Gustav.", 1, who=["Gustav"])]


def _night(backend, story, status):
    """The measured replies: every line a moment with the label the 4B gave it; the THREADS call puts the ``story`` lines in one thread (saying ``status``) and the garden lines in another.
    Two garden lines ride along only so the night is not a 'quiet day' (fewer than 3 turns)."""
    import memory_digest

    moments = sorted(story + FILLER, key=lambda m: m.turn.at)
    pairs = [(f"t{i:03d}", m.quote) for i, m in enumerate(moments)]
    tr = memory_digest.Transcript("\n".join(t for _i, t in pairs), pairs, [m.turn.at.isoformat() for m in moments])
    by_text = {m.quote: m for m in moments}
    story_texts = {m.quote for m in story}

    def model(messages, _max):
        user = messages[-1]["content"]
        if user.startswith("TASK: THREADS"):
            rows = re.findall(r'^(q\d+) \[[^\]]*\] \w+ \w+: "(.*)"$', user, re.M)
            mine = [q for q, t in rows if t in story_texts]
            rest = [q for q, t in rows if t not in story_texts]
            ops = [{"op": "create", "title": "the story", "moments": mine, "status": status, "reason": "the same matter"}]
            if rest:
                ops.append({"op": "create", "title": "the garden", "moments": rest, "status": "open", "reason": "another matter"})
            return json.dumps({"threads": ops, "unchanged": []})
        rows = re.findall(r"^\[(m\d+)\] [^:]*: (.*)$", user, re.M)
        return json.dumps({"moments": [{"ids": [a], "quote": t, "kind": by_text[t].kind, "who": by_text[t].who, "feeling": "none", "weight": 2, "later": by_text[t].later} for a, t in rows]})
    nm.set_llm(model)
    asyncio.run(nm.run_for_user(UID, tr, None, now=NOW, force_mode="enforce"))
    return {t["title"]: t for t in asyncio.run(backend.threads(UID))}


def test_end_to_end_the_cancelled_trip_thread_is_changed(lab):
    t = _night(lab, [PLAN_TRIP, TRIP_OFF], "changed")["the story"]
    assert t["status"] == "changed"


def test_end_to_end_the_concert_thread_stays_open_and_is_raisable_K10(lab):
    t = _night(lab, [PLAN_CONCERT, CELLO], "open")["the story"]
    assert t["status"] == "open" and int(t["mentions_n"]) == 2
    nm.decide_raise([t], {t["id"]: [PLAN_CONCERT.quote, CELLO.quote]}, NOW.date())
    assert t["raise_policy"] == "raise"


def test_end_to_end_the_quiz_thread_is_resolved_K11(lab):
    t = _night(lab, [PLAN_QUIZ, QUIZ_DONE], "open")["the story"]
    assert t["status"] == "resolved"
