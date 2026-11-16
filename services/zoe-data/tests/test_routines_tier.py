"""Routines stage 1, the conversation tier (``routines_tier.py``). Synthetic members only; SQLite with the real migration 0081.

The claims and their negative controls: a bare "yep" outside a walk ticks nothing; "remind me at 7 ..." stays a clock reminder; a
member with no routine gets every existing utterance back untouched; the medical floor is consulted FIRST (an ingestion is never a
tick, and forcing the floor to fire on "done" makes the tier step aside); a medicine line needs a verified voice; flags off = the tier
returns before any work, over a corpus of the repo's own synthetic utterances.
"""
from __future__ import annotations

import ast
import json
import logging
from datetime import timedelta
from pathlib import Path

import pytest

import medical_safety
import routines_store as rs
import routines_tier as rt
from routines_support import ALEX, SAM, THU, run, store  # noqa: F401 - store is a fixture

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures"

#: everyday turns that must never be taken by a member without a routine, plus the repo's synthetic labelled sets
EVERYDAY = [
    "remind me to take the bins out at 7", "remind me at 7 to take the drill", "remind me every day to take my tablets",
    "remind me to call mum tomorrow", "remind me in 20 minutes to check the oven", "add milk to the shopping list",
    "add drill to the work list", "what's the weather tomorrow", "run good night", "activate the movie scene", "good morning",
    "good night", "what's up", "not now", "yes", "yep", "done", "no", "stop", "later", "next", "what's next on my calendar",
    "turn off the kitchen lights", "set a timer for ten minutes", "what time is it", "play some music", "I'm off to bed",
    "see you later alligator", "I took my tablets with breakfast", "I took them twice by accident", "what can you do",
    "start the dishwasher", "remember that my dentist is on friday", "put the kettle on", "skip this song", "that's enough music",
    "what's left in the fridge", "I'm leaving the house at 8, remind me to lock up", "don't let me forget the milk",
]


def _corpus() -> list[str]:
    out = list(EVERYDAY)
    for name, keys in (("restraint_labelled.json", ("dev", "held_out")), ("minor_affect_utterances.json", ("dev", "held_out")),
                       ("distress_labelled.json", ("dev", "held_out_1", "blind_2"))):
        data = json.loads((FIX / name).read_text(encoding="utf-8"))
        for k in keys:
            out += [r["text"] for r in data.get(k, []) if isinstance(r, dict) and isinstance(r.get("text"), str)]
    return out


CORPUS = _corpus()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("ZOE_ROUTINES", "on")
    monkeypatch.setenv("ZOE_ROUTINE_VOICE", "on")


def say(text, member=ALEX, session="s1", *, channel="voice", verified=True, allow_writes=True):
    uid = (member or "guest") if channel == "chat" else "panel-1"            # chat: the signed-in member; voice: the panel device
    res = run(rt.tier(text, uid, session, channel=channel, allow_writes=allow_writes,
                      extra_ctx={"panel_id": "panel-1", "scope_user": member, "speaker_verified": verified}))
    return res.reply if res is not None else None


def _morning(owner=ALEX, steps=("feed the cat", "bins"), anchor="waking", **kw):
    return run(rs.create_routine(owner, "Morning", anchor, steps=steps, **kw))


def _states(store):
    return dict(store.q("SELECT text, state FROM routine_day_steps"))


# -- the words ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,line", [
    ("remind me to take the drill to work tomorrow", "take the drill"),
    ("Remind me to bring the umbrella with me on Friday", "bring the umbrella"),
    ("don't let me forget my lunch tomorrow morning", "my lunch"),
    ("put the library books on my morning routine for tomorrow", "the library books"),
    ("add the gym bag to tomorrow's morning routine", "the gym bag"),
])
def test_one_off_phrases_are_recognised_and_the_line_is_the_persons_words(text, line):
    kind, parts = rt.shape(text)
    assert kind == "one_off" and parts["line"] == line
    assert rt.one_off_cue(parts, THU.date()) is not None


@pytest.mark.parametrize("text", [
    "remind me at 7 to take the drill", "remind me to take the drill to work at 7 tomorrow", "remind me every day to take my tablets",
    "remind me to take my tablets to work every day", "remind me to take the drill tomorrow", "add drill to the work list",
    "remind me to take the bins out in 20 minutes", "remind me to take the drill to work tomorrow at half past seven",
    "remind me to take the drill to work today and tomorrow",
])
def test_near_misses_step_aside(text):
    sh = rt.shape(text)
    assert sh is None or sh[0] != "one_off" or rt.one_off_cue(sh[1], THU.date()) is None


# -- flags off: byte-identical -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("env", [{}, {"ZOE_ROUTINES": "on"}, {"ZOE_ROUTINE_VOICE": "on"}, {"ZOE_ROUTINES": "off", "ZOE_ROUTINE_VOICE": "on"}])
def test_flags_off_return_before_any_work_over_the_corpus(monkeypatch, env):
    for k in ("ZOE_ROUTINES", "ZOE_ROUTINE_VOICE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    touched = []
    monkeypatch.setattr(medical_safety, "detect", lambda t: touched.append("floor") or medical_safety.NONE)
    monkeypatch.setattr(rt, "shape", lambda t: touched.append("shape"))

    async def no_db(*a, **k):
        touched.append("db")

    monkeypatch.setattr(rs, "list_routines", no_db)
    assert len(CORPUS) > 150
    for text in CORPUS:
        assert say(text) is None and say(text, channel="chat") is None
    assert touched == []


def test_a_member_without_routines_gets_every_utterance_back_untouched(store, on):
    for text in CORPUS:
        assert say(text) is None, text
    assert store.q("SELECT COUNT(*) FROM routine_days") == [(0,)]


# -- one-offs ----------------------------------------------------------------------------------------------------------------------
def test_remind_me_to_take_the_drill_to_work_tomorrow(store, on):
    r = _morning()
    assert say("remind me to take the drill to work tomorrow") == "Added to tomorrow's morning routine: take the drill."
    assert store.q("SELECT text, origin, kind, created_via FROM routine_day_steps") == [("take the drill", "one_off", "take_with", "voice")]
    assert store.q("SELECT day FROM routine_days") == [((THU.date() + timedelta(days=1)).isoformat(),)]
    assert say("remind me at 7 to take the drill") is None                  # the clock reminder path answers, unchanged
    assert say("remind me to take the drill to work at 7 tomorrow") is None
    assert store.q("SELECT COUNT(*) FROM routine_day_steps") == [(1,)]
    assert r["id"]


def test_a_one_off_steps_aside_when_no_single_routine_fits_or_it_does_not_run_that_day(store, on):
    _morning(anchor="dinner")
    assert say("remind me to take the drill to work tomorrow") is None      # no morning-moment routine
    _morning(days_rule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH")                     # Friday is tomorrow: not a run day
    assert say("remind me to take the drill to work tomorrow") is None
    assert store.q("SELECT COUNT(*) FROM routine_day_steps WHERE origin = 'one_off'") == [(0,)]


def test_a_one_off_with_zero_one_or_two_fitting_routines(store, on):
    assert say("remind me to take the drill to work tomorrow", SAM) is None  # zero: the reminder path answers
    run(rs.create_routine(SAM, "Out the door", "leaving", steps=("keys",)))
    assert say("remind me to take the drill to work tomorrow", SAM) == "Added to tomorrow's out the door routine: take the drill."
    run(rs.create_routine(SAM, "Wake up", "waking", steps=("water",)))       # a second routine the cue fits (old code: picked leaving)
    assert say("remind me to take the umbrella to work tomorrow", SAM) == "Which routine: Out the door or Wake up?"
    assert [t for (t,) in store.q("SELECT text FROM routine_day_steps WHERE origin = 'one_off'")] == ["take the drill"]
    assert say("put the umbrella on my wake up routine for tomorrow", SAM) == "Added to tomorrow's wake up routine: the umbrella."


# -- the walk ----------------------------------------------------------------------------------------------------------------------
def test_the_walk_names_one_step_at_a_time_and_acts_only_on_it(store, on):
    _morning()
    assert say("yep") is None and say("done") is None                        # no walk: nothing is ticked
    assert store.q("SELECT COUNT(*) FROM routine_day_steps WHERE state != 'pending'") == [(0,)]
    assert say("what's next?") == "Next: feed the cat."
    assert say("done", session="other") is None                              # another session
    assert say("done") == "Ticked. Next: bins."
    assert say("done", member=SAM) is None                                   # another person: nothing, and the walk ends
    assert say("done") is None                                               # (Zoe's last reply was not to Alex any more)
    assert say("next") is None and say("what's the weather") is None
    assert _states(store) == {"feed the cat": "done", "bins": "pending"}
    assert say("no, I haven't", member=SAM) is None                          # an undo is the same person's only
    assert say("no, I haven't") == "OK, it's back on the list: feed the cat."
    assert _states(store) == {"feed the cat": "pending", "bins": "pending"}


def test_any_other_turn_ends_the_walk_so_a_later_yes_is_not_a_tick(store, on):
    _morning()
    assert say("what's next?") == "Next: feed the cat."
    assert say("what's the weather tomorrow") is None                        # another answer came in between
    assert say("yes") is None
    assert say("what's next?") == "Next: feed the cat."                      # control: a fresh naming re-opens it
    assert say("yes") == "Ticked. Next: bins."


def test_skip_later_undo_and_the_summary(store, on):
    _morning(steps=("feed the cat", "bins", "water"))
    assert say("start my morning routine") == "Morning: 3 things. First, feed the cat."
    assert say("skip") == "Skipped. Next: bins."
    assert say("later") == "OK, I'll keep it on the list. Next: water."
    assert say("what's left?") == "2 things left: water and bins."
    assert say("done") == "Ticked. Next: bins."
    assert say("untick that") == "OK, it's back on the list: water."
    assert say("done") == "Ticked. Next: bins."
    assert say("done") == "Ticked. That's your morning routine done."
    assert say("stop") is None                                               # the walk closed with the last tick


def test_a_medicine_line_needs_a_verified_voice(store, on):
    _morning(owner=SAM, steps=(("tablets", "medicine"),))
    assert say("what's next", SAM, verified=None) == "Next: tablets."
    assert say("done", SAM, verified=None) == rt.MEDICINE_UNVERIFIED
    assert say("done", SAM, verified=False) == rt.MEDICINE_UNVERIFIED
    assert _states(store) == {"tablets": "pending"}
    assert say("done", SAM, verified=True) == "Ticked. That's your morning routine done."
    assert store.q("SELECT verified, known_by FROM routine_day_steps") == [(1, "said")]


# -- the safety floor --------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "I took them twice by accident", "I accidentally took my usual tablets twice", "my sugar was 9.8",
    "what number do i ring if jonas collapses", "wren swallowed bleach",
])
def test_the_medical_floor_owns_its_turns_even_inside_a_walk(store, on, text):
    _morning(owner=SAM, steps=(("tablets", "medicine"),))
    say("what's next", SAM)
    assert medical_safety.detect(text) != medical_safety.NONE
    assert say(text, SAM) is None
    assert _states(store) == {"tablets": "pending"}


def test_the_floor_is_consulted_first(store, on, monkeypatch):
    """Negative control for the ordering: make the floor fire on a bare "done" and the tier must step aside."""
    _morning()
    say("what's next")
    monkeypatch.setattr(medical_safety, "detect", lambda t: medical_safety.Verdict("ingest_human", "x"))
    assert say("done") is None
    monkeypatch.undo()
    assert _states(store)["feed the cat"] == "pending"


# -- leaving -----------------------------------------------------------------------------------------------------------------------
def test_im_off_names_the_undone_take_with_line(store, on, monkeypatch):
    _morning()
    assert say("I'm off") is None                                            # nothing to take: the goodbye answers as today
    say("remind me to take the drill to work tomorrow")
    monkeypatch.setattr(rs, "_now_utc", lambda: THU + timedelta(days=1))
    assert say("I'm off") == "Before you go: take the drill."
    assert say("got it") == "Ticked. Next: feed the cat."
    assert _states(store)["take the drill"] == "done"


def test_im_off_with_several_lines_names_none_and_a_got_it_ticks_nothing(store, on, monkeypatch):
    _morning()
    say("remind me to take the drill to work tomorrow")
    say("remind me to take the lunchbox to work tomorrow")
    monkeypatch.setattr(rs, "_now_utc", lambda: THU + timedelta(days=1))
    assert say("what's next?") == "Next: feed the cat."                      # an older named step A ...
    assert say("I'm off") == "Before you go: take the drill and take the lunchbox."
    assert say("got it") == "Which one: take the drill or take the lunchbox?"     # ... is not what "got it" ticks
    assert say("got it") is None
    assert all(st == "pending" for st in _states(store).values())


def test_another_speaker_never_ticks_the_first_speakers_step(store, on):
    _morning()
    assert say("what's next?") == "Next: feed the cat."
    assert say("got it", member=SAM) is None
    assert say("got it") is None                                             # the walk closed on the other speaker's turn
    assert all(st == "pending" for st in _states(store).values())


def test_a_flag_off_turn_never_loads_the_routines_modules():
    """A FRESH interpreter: import fast_tiers with both flags off, run the routines step of the tier dispatch, and neither routines
    module is loaded (old code imported routines_tier, and so routines_store, on every turn)."""
    import os
    import subprocess
    import sys

    code = ("import asyncio, sys\n"
            "import fast_tiers as ft\n"
            "assert asyncio.run(ft._routines_tier('done', 'm-alex', 's1', {'channel': 'voice'})) is None\n"
            "bad = [m for m in ('routines_tier', 'routines_store') if m in sys.modules]\n"
            "assert not bad, bad\n"
            "print('CLEAN')\n")
    env = {k: v for k, v in os.environ.items() if k not in ("ZOE_ROUTINES", "ZOE_ROUTINE_VOICE")}
    env["PYTHONPATH"] = str(SVC) + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(SVC), env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "CLEAN" in out.stdout, out.stderr[-2000:]


# -- shadow, dry, identity ---------------------------------------------------------------------------------------------------------
def test_shadow_claims_nothing_and_writes_nothing(store, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_ROUTINES", "on")
    monkeypatch.setenv("ZOE_ROUTINE_VOICE", "shadow")
    _morning()
    caplog.set_level(logging.INFO, logger="routines_tier")
    assert say("what's next") is None
    assert say("remind me to take the drill to work tomorrow") is None
    assert store.q("SELECT COUNT(*) FROM routine_days") == [(0,)]
    msgs = [m.getMessage() for m in caplog.records]
    assert any("would=next" in m for m in msgs) and any("would=one_off" in m for m in msgs)


def test_dry_runs_and_unidentified_voices_take_nothing(store, on):
    _morning()
    assert say("what's next", allow_writes=False) is None
    assert say("what's next", member=None) is None
    assert say("what's next", member="guest") is None
    assert say("what's next", channel="chat") == "Next: feed the cat."         # control: the member's own chat session


# -- the registration --------------------------------------------------------------------------------------------------------------
def test_the_tier_is_registered_in_the_conversation_half_after_distress():
    tree = ast.parse((SVC / "fast_tiers.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_resolve_tiers")
    distress = next(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_distress_tier")
    lines = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_routines_tier"]
    assert len(lines) == 1 and lines[0] > distress
    assert "routines_tier" not in (SVC / "routers" / "voice_tts.py").read_text(encoding="utf-8")   # registered via fast_tiers only
