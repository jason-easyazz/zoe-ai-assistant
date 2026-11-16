"""Listen pace: the pure decision core (services/zoe-data/listen_pace.py). Synthetic numbers only, no I/O.

The four owner-facing properties are pinned here: the level never leaves [floor, ceiling]; one "talked after the close" always
beats any clean history; 39 clean turns never shorten; a household level is used when the speaker is unknown. Plus: unknown or
malformed input never changes anything.
"""
from __future__ import annotations

import random

import pytest

import listen_pace as lp

pytestmark = pytest.mark.ci_safe

T0 = 1_800_000_000.0
DAY = lp.DAY_S

# level_used=4 + pause_max_ms=0: a turn that PROVES every shorter close clean (seen at the longest level, no in-turn pause)
CLEAN = dict(close="deep=640ms", win="wake", still_talking="0", talk_chunks="0", obs_ms="1500", muted_ms="0", level_used="4",
             pause_max_ms="0")
CUT = dict(CLEAN, still_talking="1", talk_chunks="5")


def out(**kw):
    o = lp.outcome_from_fields(dict(CLEAN, **kw))
    assert o is not None
    return o


def feed(s, outcomes, *, t=T0, dt=60.0, **kw):
    for o in outcomes:
        s, _ = lp.step(s, o, t, **kw)
        t += dt
    return s, t


# ── the table ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_profile_table_floors_and_ceiling():
    assert sorted(lp.PROFILES) == list(range(lp.LEVEL_MIN, lp.LEVEL_MAX + 1))
    for lvl, (deep, quiet, clean) in lp.PROFILES.items():
        assert deep >= 640 and quiet <= 1200 and deep < quiet          # never under 640 otherwise; dead air capped
        assert clean == 0 or clean >= 560                                # never faster than 560 on a clean stop
        assert all(v % lp.STEP_MS == 0 for v in (deep, quiet, clean))
        assert (clean != 0) == (lvl < 0)                                 # only SHORTER levels touch the clean gate
    assert lp.PROFILES[0] == (640, 800, 0)                               # L0 = today's household default
    assert lp.profile(-99) == lp.profile(lp.LEVEL_MIN) and lp.profile(99) == lp.profile(lp.LEVEL_MAX)


# ── shorten slow ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_39_clean_turns_never_shorten_and_the_40th_does():
    s, t = feed(lp.new_state(), [out()] * 39)
    assert s.level == 0 and s.run == 39
    s, ch = lp.step(s, out(), t)
    assert (s.level, ch.reason) == (-1, "shorten_run")


@pytest.mark.parametrize("seed", range(40))
def test_39_clean_turns_never_shorten_from_any_level(seed):
    rnd = random.Random(seed)
    start = rnd.randint(lp.LEVEL_MIN, lp.LEVEL_MAX)
    s = lp.State(level=start, run=0)
    s, _ = feed(s, [out(win=rnd.choice(["wake", "followup", "conversation"])) for _ in range(39)])
    assert s.level == start


def test_one_cut_beats_any_clean_history():
    s, t = feed(lp.new_state(), [out()] * 39)
    s, ch = lp.step(s, lp.outcome_from_fields(CUT), t)
    assert s.level == 0 and s.run == 0 and ch.reason == "none"       # never shorter; the clean run is gone
    s, t = feed(s, [out()] * 39, t=t + 60)
    assert s.level == 0                                               # a fresh full run is needed
    s, _ = lp.step(s, out(), t)
    assert s.level == -1


@pytest.mark.parametrize("seed", range(60))
def test_a_cut_never_shortens_whatever_the_state(seed):
    rnd = random.Random(1000 + seed)
    s = lp.State(level=rnd.randint(-3, 4), run=rnd.randint(0, 500), ring="".join(rnd.choice("012") for _ in range(rnd.randint(0, 20))),
                 after_lengthen=rnd.random() < 0.5, last_step_at=None if rnd.random() < 0.5 else T0 - rnd.uniform(0, 5 * DAY),
                 pin=rnd.choice(["", "longer", "quicker"]), pin_until=T0 + rnd.uniform(-DAY, 90 * DAY), quicker_guard=rnd.randint(0, 20))
    for cut in (CUT, dict(CLEAN, held="1")):
        s2, _ = lp.step(s, lp.outcome_from_fields(cut), T0)
        assert s2.level >= lp.effective_level(s, T0) and s2.run == 0


def test_after_a_lengthen_the_run_is_80_and_one_step_a_day():
    s, t = feed(lp.new_state(), [lp.outcome_from_fields(CUT)] * 2)
    assert s.level == 1 and s.after_lengthen and s.bad[-1][0] == 0
    s, t = feed(s, [out()] * 119, t=t + DAY)
    assert s.level == 1                                               # L0 caused that lengthen: back to it needs 120
    s, _ = lp.step(s, out(), t)
    assert s.level == 0
    s = lp.State(level=1, after_lengthen=True, last_step_at=T0 - 2 * DAY)   # a lengthen, no bad level in the way
    s, t = feed(s, [out()] * 79, t=T0)
    assert s.level == 1
    s, _ = lp.step(s, out(), t)
    assert s.level == 0
    s2, _ = feed(s, [out()] * 200, t=t + 60, dt=1.0)                 # 200 clean turns inside a few minutes
    assert s2.level == 0                                              # ... still at most one automatic step per day


def test_returning_to_a_level_that_caused_a_lengthen_needs_120():
    s = lp.State(level=0, bad=((-1, T0),), last_step_at=T0 - 2 * DAY)
    s2, t = feed(s, [out()] * 119, t=T0 + DAY)
    assert s2.level == 0
    s2, _ = lp.step(s2, out(), t)
    assert s2.level == -1
    s3, _ = feed(lp.State(level=0, bad=((-1, T0 - 31 * DAY),)), [out()] * 40, t=T0)   # older than 30 days: back to 40
    assert s3.level == -1


def test_the_reflex_doubles_the_run():
    s, _ = feed(lp.new_state(), [out()] * 79, reflex_on=True)
    assert s.level == 0
    s, _ = feed(s, [out()], t=T0 + 1e5, reflex_on=True)
    assert s.level == -1


# ── lengthen fast ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_two_cuts_in_twenty_lengthen_two_cuts_twenty_one_apart_do_not():
    cut = lp.outcome_from_fields(CUT)
    s, _ = feed(lp.new_state(), [cut] + [out()] * 18 + [cut])
    assert s.level == 1
    s, _ = feed(lp.new_state(), [cut] + [out()] * 20 + [cut])
    assert s.level == 0


def test_a_merged_continuation_weighs_half():
    held = out(held="1")
    s, _ = feed(lp.new_state(), [held] * 3)
    assert s.level == 0
    s, _ = lp.step(s, held, T0 + 1e4)
    assert s.level == 1


def test_one_cut_after_a_reflex_lengthens_at_once():
    s, ch = lp.step(lp.new_state(), lp.outcome_from_fields(dict(CUT, reflex="heard")), T0)
    assert (s.level, ch.reason) == (1, "lengthen_reflex")


def test_lengthening_is_not_rate_limited():
    cut = lp.outcome_from_fields(CUT)
    s, _ = feed(lp.new_state(), [cut] * 8, dt=1.0)
    assert s.level == 4                                               # every 2 cuts is one more level, up to the ceiling


# ── never leave [floor, ceiling] ─────────────────────────────────────────────────────────────────────────────────────────────
def _random_fields(rnd):
    f = dict(CLEAN)
    f["still_talking"] = rnd.choice(["0", "1", "0", "0", "x", ""])
    f["talk_chunks"] = rnd.choice(["0", "4", "-1", "abc", "9"])
    f["obs_ms"] = rnd.choice(["1500", "300", "1300", "-5"])
    f["muted_ms"] = rnd.choice(["0", "0", "-1", "240"])
    f["close"] = rnd.choice(["deep=640ms", "quiet=800ms", "clean=560ms", "cap=0ms", "no_speech=0ms", "nonsense"])
    f["win"] = rnd.choice(["wake", "followup", "conversation", "barge", "tv"])
    if rnd.random() < 0.2:
        f["reflex"] = rnd.choice(["heard", "none", "1"])
    if rnd.random() < 0.2:
        f["level_used"] = str(rnd.randint(-5, 6))
    if rnd.random() < 0.1:
        f.pop(rnd.choice(list(f)))
    return f


@pytest.mark.parametrize("child", [False, True])
@pytest.mark.parametrize("seed", range(25))
def test_the_level_never_leaves_floor_and_ceiling(seed, child):
    rnd = random.Random(seed)
    s, t = lp.new_state(child=child), T0
    for _ in range(600):
        t += rnd.choice([1.0, 60.0, 3600.0, DAY, 10 * DAY])
        r = rnd.random()
        if r < 0.05:
            s, _ = lp.override(s, rnd.choice(["longer", "quicker", "normal", "bogus"]), t)
        else:
            s, _ = lp.step(s, lp.outcome_from_fields(_random_fields(rnd)), t, reflex_on=rnd.random() < 0.3)
        lo = lp.CHILD_FLOOR if child else lp.LEVEL_MIN
        assert lo <= s.level <= lp.LEVEL_MAX
        assert lo <= lp.effective_level(s, t) <= lp.LEVEL_MAX
        assert len(s.ring) <= lp.WINDOW


# ── unknown or malformed never changes anything ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    {"still_talking": "maybe"}, {"talk_chunks": "-1"}, {"talk_chunks": "1.5"}, {"muted_ms": "-1"}, {"muted_ms": "240"},
    {"obs_ms": "300"}, {"close": "cap=0ms"}, {"close": "no_speech=0ms"}, {"win": "barge"}, {"win": "tv"},
    {"frozen": "media"}, {"frozen": "weird"}, {"level_used": "9"}, {"speech_ms": "200"}, {"obs_ms": "nan"},
])
def test_an_unknown_or_malformed_turn_leaves_the_state_untouched(bad):
    s = lp.State(level=-2, run=39, ring="0" * 19 + "2", last_step_at=T0 - 5 * DAY, last_heard_at=T0 - 60)
    s2, ch = lp.step(s, lp.outcome_from_fields(dict(CLEAN, **bad)), T0)
    assert s2 is s and ch.reason.startswith("skip") and ch.after == ch.before


@pytest.mark.parametrize("missing", ["still_talking", "talk_chunks", "obs_ms", "muted_ms", "win", "close"])
def test_a_missing_field_is_malformed(missing):
    f = dict(CLEAN)
    f.pop(missing)
    assert lp.outcome_from_fields(f) is None
    assert lp.outcome_from_fields({"still_talking": True, "talk_chunks": True, "obs_ms": 1500, "muted_ms": 0,
                                   "win": "wake", "rule": "deep"}) is None   # a bool is not a count


@pytest.mark.parametrize("bad", [
    {"still_talking": "0", "talk_chunks": "5"},     # 3+ chunks after the close IS still talking (the daemon's own definition)
    {"still_talking": "0", "talk_chunks": "3"},
    {"still_talking": "1", "talk_chunks": "2"},
    {"still_talking": "1", "talk_chunks": "-1"},    # "talking" with no monitor listening
])
def test_still_talking_that_contradicts_the_chunk_count_is_malformed(bad):
    assert lp.outcome_from_fields(dict(CLEAN, **bad)) is None
    s = lp.State(level=0, run=39)                   # one clean turn from shortening
    s2, ch = lp.step(s, lp.outcome_from_fields(dict(CLEAN, **bad)), T0)
    assert s2 is s and ch.reason == "skip:malformed" and ch.after == 0
    assert lp.outcome_from_fields(dict(CLEAN, talk_chunks="2")) is not None          # the consistent neighbours still parse
    assert lp.outcome_from_fields(dict(CLEAN, still_talking="1", talk_chunks="3")) is not None


# ── only PROVEN evidence shortens: unknown, contradictory or unproven is neutral ─────────────────────────────────────────────
def test_a_pause_resume_finish_turn_never_counts_toward_a_shorter_level_it_would_have_cut():
    """Seen at L0 with a 600 ms pause mid-turn, then a resume and a clean finish. L-3's clean gate (560 ms) would have closed
    on that pause: the turn proves nothing for L-3, so the run toward it does not move."""
    s = lp.State(level=-2, run=39)
    s2, ch = lp.step(s, out(level_used="0", pause_max_ms="600"), T0)
    assert s2.level == -2 and s2.run == 39 and ch.reason == "none"
    s3, ch = lp.step(s, out(level_used="0", pause_max_ms="200"), T0)           # a pause L-3 would have survived: it counts
    assert (s3.level, ch.reason) == (-3, "shorten_run")
    s4, ch = lp.step(lp.State(level=0, run=39), out(level_used="2", pause_max_ms="700"), T0)    # 700 > L-1's 640
    assert s4.level == 0 and s4.run == 39


def test_a_clean_turn_with_no_pause_measure_is_neutral():
    s = lp.State(level=0, run=39)
    f = dict(CLEAN, level_used="0")
    f.pop("pause_max_ms")
    s2, ch = lp.step(s, lp.outcome_from_fields(f), T0)
    assert s2.level == 0 and s2.run == 39 and ch.reason == "none"


def test_a_clean_turn_at_an_unknown_level_never_shortens():
    s = lp.State(level=1, run=39)
    f = dict(CLEAN)
    f.pop("level_used")
    s2, ch = lp.step(s, lp.outcome_from_fields(f), T0)
    assert s2 is s and ch.reason == "skip:level_unknown"
    s3, _ = lp.step(s, lp.outcome_from_fields({k: v for k, v in CUT.items() if k != "level_used"}), T0)
    assert s3.run == 0 and s3.events_half == lp.HALF                           # a cut still counts: lengthening needs less proof


def test_a_pause_seen_at_a_longer_level_only_proves_the_current_one_if_it_would_have_survived_it():
    s = lp.State(level=-3, run=0)
    s2, ch = lp.step(s, out(level_used="0", pause_max_ms="600"), T0)           # L-3 would have closed on that pause
    assert s2 is s and ch.reason == "skip:unproven"


def _good():
    return lp.Outcome(rule="deep", win="wake", still_talking=False, talk_chunks=0, obs_ms=1500, muted_ms=0, level_used=4,
                      pause_max_ms=0)


@pytest.mark.parametrize("bad", [
    {"win": "invalid"}, {"still_talking": False, "talk_chunks": 5}, {"still_talking": True, "talk_chunks": 1},
    {"still_talking": "0"}, {"still_talking": 0}, {"talk_chunks": True}, {"talk_chunks": -2}, {"talk_chunks": 10001},
    {"talk_chunks": 1.0}, {"obs_ms": -1}, {"obs_ms": 60001}, {"muted_ms": -2}, {"frozen": "weird"}, {"frozen": None},
    {"reflex": "yes"}, {"held": 1}, {"speech_ms": -5}, {"level_used": 9}, {"level_used": -4}, {"pause_max_ms": -1},
    {"rule": 5},
])
def test_step_validates_a_hand_built_outcome_like_the_parser(bad):
    import dataclasses
    o = dataclasses.replace(_good(), **bad)
    s = lp.State(level=0, run=39)
    s2, ch = lp.step(s, o, T0)
    assert s2 is s and ch.reason == "skip:malformed"
    assert lp.step(s, _good(), T0)[1].reason == "shorten_run"                    # the untouched outcome would have shortened


def test_garbage_state_or_clock_never_raises_or_moves():
    s = lp.new_state()
    assert lp.step(s, out(), float("nan"))[0] is s
    assert lp.step("not a state", out(), T0)[1].reason == "error"
    assert lp.override(s, "longer", float("inf"))[0] is s


def test_a_clean_turn_counts_only_when_observed_at_this_level_or_longer():
    s, ch = lp.step(lp.State(level=-1, run=39), out(level_used="0"), T0)   # shadow: observed at L0, proves L-1 and L-2 clean
    assert (s.level, ch.reason) == (-2, "shorten_run")
    s = lp.State(level=1, run=39)
    s2, ch = lp.step(s, out(level_used="0"), T0)                        # observed SHORTER: says nothing about L+1 -> L0
    assert s2 is s and ch.reason == "skip:level_mismatch"
    s3, _ = lp.step(s, lp.outcome_from_fields(dict(CUT, level_used="0")), T0)   # a cut always counts (the safe direction)
    assert s3.run == 0 and s3.level == 1


# ── who ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_household_level_is_used_when_the_speaker_is_unknown():
    states = {"panel-a|*": lp.State(level=-2), "panel-a|member-1": lp.State(level=3), "panel-b|*": lp.State(level=1)}
    assert lp.level_for(states, "panel-a", None, T0) == (-2, "panel-a|*", "household")
    assert lp.level_for(states, "panel-a", "guest-7", T0) == (-2, "panel-a|*", "household")   # a guest is never a member row
    assert lp.level_for(states, "panel-a", "member-1", T0) == (3, "panel-a|member-1", "member")
    assert lp.level_for(states, "panel-a", "member-2", T0) == (-2, "panel-a|member-2", "household")   # seeded from the panel
    assert lp.level_for(states, "panel-a", "child-1", T0, child=True)[0] == lp.CHILD_DEFAULT      # a child never starts faster
    assert lp.level_for(states, "panel-b", "member-1", T0)[0] == 1                                # per PANEL
    assert lp.level_for({}, "panel-c", None, T0) == (0, "panel-c|*", "default")
    assert lp.level_for(states, "bad|id", None, T0) == (0, "", "default")


def test_a_stored_row_never_bypasses_the_current_child_floor():
    """A row created before a role correction (with the adult floor) is read at the CURRENT role's floor."""
    states = {"panel-a|member-1": lp.State(level=-2), "panel-a|member-2": lp.State(level=3)}
    assert lp.level_for(states, "panel-a", "member-1", T0, child=True) == (lp.CHILD_FLOOR, "panel-a|member-1", "member")
    assert lp.level_for(states, "panel-a", "member-1", T0) == (-2, "panel-a|member-1", "member")     # an adult: unchanged
    assert lp.level_for(states, "panel-a", "member-2", T0, child=True)[0] == 3                       # longer is kept


def test_a_child_never_goes_below_the_household_default():
    s, _ = feed(lp.new_state(child=True), [out()] * 400, dt=DAY)
    assert s.level == lp.CHILD_FLOOR
    s, _ = lp.override(s, "quicker", T0 + 500 * DAY)
    assert s.level == lp.CHILD_FLOOR


# ── the spoken override ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_give_me_more_time_is_two_longer_and_sticks():
    s, ch = lp.override(lp.State(level=-1), "longer", T0)
    assert (s.level, ch.reason, s.events_half) == (1, "pin_longer", 2 * lp.HALF)
    s2, _ = feed(s, [out()] * 300, t=T0 + 60, dt=DAY / 4)           # 75 days of clean turns: pinned, no shortening
    assert s2.level == 1
    s3, _ = feed(s2, [out()] * 200, t=T0 + 91 * DAY, dt=DAY)          # after the pin it shortens again, slowly
    assert s3.level < 1


def test_you_can_be_quicker_is_one_shorter_and_a_cut_undoes_it():
    s, ch = lp.override(lp.State(level=0), "quicker", T0)
    assert (s.level, ch.reason) == (-1, "pin_quicker")
    s2, _ = feed(s, [out()] * 5 + [lp.outcome_from_fields(CUT)], t=T0 + 60)
    assert s2.level == 0 and s2.pin == ""
    s3, _ = lp.override(lp.State(level=lp.LEVEL_MIN), "quicker", T0)
    assert s3.level == lp.LEVEL_MIN                                   # never below the floor


def test_go_back_to_normal_resets():
    s, _ = lp.override(lp.State(level=3, pin="longer", pin_until=T0 + DAY, ring="22"), "normal", T0)
    assert (s.level, s.pin, s.ring) == (0, "", "")
    assert lp.override(lp.new_state(child=True, level=4), "normal", T0)[0].level == lp.CHILD_DEFAULT


def test_momentary_is_two_longer_for_this_turn_only():
    assert lp.momentary(0) == lp.profile(2) and lp.momentary(4) == lp.profile(4)


# ── decay ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_shorter_level_drifts_back_up_after_three_weeks_a_longer_one_never_drifts_down():
    short = lp.State(level=-3, last_heard_at=T0)
    assert lp.effective_level(short, T0 + 20 * DAY) == -3
    assert lp.effective_level(short, T0 + 22 * DAY) == -2
    assert lp.effective_level(short, T0 + 29 * DAY) == -1
    assert lp.effective_level(short, T0 + 400 * DAY) == 0
    long_ = lp.State(level=3, last_heard_at=T0)
    assert lp.effective_level(long_, T0 + 400 * DAY) == 3


def test_mode_reads_the_registered_flag(monkeypatch):
    monkeypatch.delenv("ZOE_LISTEN_PACE", raising=False)
    assert lp.mode() == "off"
    monkeypatch.setenv("ZOE_LISTEN_PACE", "Shadow")
    assert lp.mode() == "shadow"
    monkeypatch.setenv("ZOE_LISTEN_PACE", "yes-please")
    assert lp.mode() == "off"


@pytest.mark.parametrize("blank", ["", " ", "\t"])
def test_a_blank_flag_is_malformed_never_a_clean_close(blank):
    """Review of #2597: an empty or whitespace still_talking read as False, so a line with the field blank counted as clean."""
    with pytest.raises(ValueError):
        lp._flag01(blank)
    assert lp._flag01("0") is False and lp._flag01("no") is False and lp._flag01("1") is True
