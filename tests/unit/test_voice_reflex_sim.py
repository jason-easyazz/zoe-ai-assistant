"""The voice reflex projection lab (labs/voice-reflex-sim/sim.py): it drives the REAL decide(), pairs the scorecard's allowlisted
rows, and its invariants hold whatever the inputs. Synthetic turns only; no log, no network, no model."""
from __future__ import annotations

import datetime as dt
import importlib.util
import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_PATH = Path(__file__).resolve().parents[2] / "labs" / "voice-reflex-sim" / "sim.py"
_spec = importlib.util.spec_from_file_location("voice_reflex_sim_under_test", _PATH)
SIM = importlib.util.module_from_spec(_spec)
sys.modules["voice_reflex_sim_under_test"] = SIM
_spec.loader.exec_module(SIM)
T0 = dt.datetime(2026, 1, 1, 12, 0, 0)


def turns(n, *, gap_s=600, close=640, ttfa=2500, start=T0):
    return [SIM.Turn(start + dt.timedelta(seconds=gap_s * i), close, ttfa) for i in range(n)]


def test_parse_pairs_a_ttfa_with_the_preceding_close_and_drops_orphans():
    rows = ["2026-01-01 12:00:00 C 640", "2026-01-01 12:00:03 T 2.5", "2026-01-01 12:05:00 T 9.0",     # the second has no close
            "2026-01-01 12:10:00 Q quiet", "2026-01-01 12:10:02 T 3.0", "not a row", "2026-01-01 12:20:00 E",
            "2026-01-01 12:30:00 C 640", "2026-01-01 12:30:00 R", "2026-01-01 12:30:04 T 1.0"]          # R clears the pair
    got = SIM.parse_turns(rows)
    assert [(t.close_ms, t.ttfa_ms) for t in got] == [(640, 2500), (800, 3000)]


def test_night_turns_are_all_silent_and_noon_turns_are_not():
    night = SIM.simulate(turns(10, start=dt.datetime(2026, 1, 1, 23, 30)))
    assert night["plays"] == 0 and night["skips"]["night"] == 10
    noon = SIM.simulate(turns(10))
    assert noon["plays"] == 10 and "night" not in noon["skips"]            # NEGATIVE CONTROL: the same turns at noon play


def test_the_budget_allows_one_reflex_per_gap():
    r = SIM.simulate(turns(6, gap_s=10))
    assert r["plays"] == 2 and r["skips"]["budget"] == 4                   # 10 s apart: one per 30 s window (turns 0 and 3)
    assert SIM.simulate(turns(6, gap_s=40))["plays"] == 6                  # NEGATIVE CONTROL: 40 s apart, all play


def test_idle_greeting_after_a_long_gap():
    r = SIM.simulate(turns(3, gap_s=5 * 3600, start=dt.datetime(2026, 1, 1, 7, 30)))   # 07:30, 12:30, 17:30: no night
    assert r["plays"] == 1 and r["skips"]["idle_greeting"] == 2


def test_a_reply_that_would_come_first_is_dropped_and_changes_nothing():
    fast = turns(5, ttfa=100)                                              # first sound at 740 ms: before any reflex can land
    r = SIM.simulate(fast)
    assert r["plays"] == 0 and r["skips"]["reply_first"] == 5
    assert r["reflex"]["p50"] == r["today"]["p50"]


def test_under_0_6_s_is_impossible_after_a_close():
    for kw in (dict(), dict(start_ms=0.0, talk_ready_ms=0.0, verdict_p50_ms=1.0, verdict_p95_ms=2.0)):
        r = SIM.simulate(turns(50, ttfa=3000), SIM.Assumptions(**kw))
        assert r["reflex"]["under_600"] == 0.0 and r["today"]["under_600"] == 0.0


def test_the_ceiling_bounds_every_scenario_and_slower_timing_never_helps():
    ts = SIM.synthetic_turns(300)
    rows = dict(SIM.sensitivity(ts))
    ceiling = rows["ceiling (every unobservable guard passes)"]
    for name, r in rows.items():
        assert r["plays"] <= ceiling["plays"] or "fast mic" in name, name
        assert r["reflex"]["under_1000"] <= ceiling["reflex"]["under_1000"] + 1e-9 or "fast mic" in name, name
    assert rows["player start 300 ms"]["reflex"]["under_1000"] <= rows["player start 200 ms"]["reflex"]["under_1000"]


def test_the_lab_runs_the_real_gate(monkeypatch):
    assert SIM.simulate(turns(5))["plays"] == 5
    monkeypatch.setattr(SIM.RX, "decide", lambda *a, **k: SIM.RX.Decision("none", None, "guest"))
    r = SIM.simulate(turns(5))
    assert r["plays"] == 0 and r["skips"] == {"guest": 5}


def test_decision_lines_are_summarised_and_the_report_has_no_free_text():
    lines = ["REFLEX_DECISION mode=shadow kind=heard skip=- take=mm_02 decided_ms=190 talk_obs_ms=400 noise_db=-81 spk=1 media=0 win=wake bank=3811a303",
             "REFLEX_DECISION mode=shadow kind=none skip=talking take=- decided_ms=250 talk_obs_ms=0 noise_db=-70 spk=1 media=0 win=wake bank=3811a303",
             "garbage line"]
    out = SIM.summarise_decisions(lines)
    assert "2 decisions" in out and "would_play: 1" in out and "talking: 1" in out
    assert SIM.summarise_decisions(["x"]) == "no REFLEX_DECISION lines"
    rep = SIM.report(SIM.synthetic_turns(50), "synthetic")
    assert "under 1 s" in rep and "Under 0.6 s is 0 by construction" in rep


FREE = ("pineapple", "zebracrossing", "PINEAPPLE", "Zebra")       # invented free text planted in every field a line carries


def _words(text: str) -> "set[str]":
    return set(re.findall(r"[A-Za-z][A-Za-z_]+", text))


def test_no_free_text_from_a_log_line_ever_reaches_the_output():
    """The privacy property, tested for real: free text planted in every field of a REFLEX_DECISION line (the skip, the take,
    the bank, around the line), in a would-be marker line and in a would-be turn line never appears in any output. The
    output's words are only the fixed vocabulary: the skip enum, would_play / other, and the report's own wording."""
    planted = [
        "pineapple REFLEX_DECISION mode=shadow kind=none skip=zebracrossing take=pineapple decided_ms=190 talk_obs_ms=4 "
        "noise_db=-70 spk=1 media=0 win=wake bank=Zebra trailing PINEAPPLE",
        "REFLEX_DECISION mode=shadow kind=heard skip=- take=Zebra decided_ms=200 talk_obs_ms=4 noise_db=-70 spk=1 media=0 "
        "win=wake bank=pineapple",
        "REFLEX_DECISION mode=shadow kind=none skip=talking take=- decided_ms=250 talk_obs_ms=0 noise_db=-70 spk=1 media=0 "
        "win=zebracrossing bank=-",
    ]
    out = SIM.summarise_decisions(planted)
    assert not (_words(out) & set(FREE)), out
    assert "other: 1" in out and "would_play: 1" in out           # the forged skip is counted, as "other", never echoed
    allowed = set(SIM.RX.LOG_SKIPS) | {"would_play", "other", "decisions", "decided_ms"}
    assert _words(out) <= allowed, _words(out) - allowed
    marks = ["2026-01-01 12:00:20 DISTRESS_HANDOFF pineapple", "2026-01-01 12:00:20 Zebra", "zebracrossing"]
    assert not (_words(repr(SIM.join_safety(planted, marks))) & set(FREE))
    turns = SIM.parse_turns(["2026-01-01 12:00:00 C 640 pineapple", "2026-01-01 12:00:00 C 640",
                             "2026-01-01 12:00:02 T 2.1", "Zebra 2026-01-01 12:00:03 T 1.0"])
    assert len(turns) == 1
    assert not (_words(SIM.report(turns, "saved extract")) & set(FREE))


def test_the_free_text_check_can_fail():
    """NEGATIVE CONTROL for the test above: the same check flags an output that echoes a planted word."""
    assert _words("- zebracrossing: 1 (100.0%)") & set(FREE)


def test_the_safety_count_is_the_shadows_own_and_says_unavailable_while_the_server_is_silent():
    unknown = ["2026-01-01 12:00:00,1 INFO REFLEX_SAFETY mode=shadow would_play=1 class=unknown"] * 3
    out = SIM.summarise_safety(unknown)
    assert "UNAVAILABLE" in out and ": 0" not in out and "cannot see refusals" in out   # never a misleading zero
    stated = unknown[:1] + ["x REFLEX_SAFETY mode=shadow would_play=1 class=distress",
                            "x REFLEX_SAFETY mode=shadow would_play=1 class=refusal",
                            "x REFLEX_SAFETY mode=shadow would_play=0 class=medical",
                            "x REFLEX_SAFETY mode=shadow would_play=1 class=none",
                            "x REFLEX_SAFETY mode=shadow would_play=1 class=pineapple"]   # off-enum: not a row
    out = SIM.summarise_safety(stated)
    assert "4 of 5 decided turns classified" in out
    assert "would-play turns before a safety reply: 2" in out and "- distress: 1" in out and "- refusal: 1" in out
    assert "- medical: 0" in out and "pineapple" not in out
    assert SIM.summarise_safety(["nothing here"]) == "safety count: no REFLEX_SAFETY lines"


def test_main_prints_the_shadows_count_and_labels_the_marker_join_as_the_lab_measure(tmp_path, capsys):
    dec = tmp_path / "d.txt"
    dec.write_text("2026-01-01 12:00:00,123 INFO REFLEX_DECISION mode=shadow kind=heard skip=- take=mm_01 decided_ms=190 "
                   "talk_obs_ms=4 noise_db=-70 spk=1 media=0 win=wake bank=3811a303\n"
                   "2026-01-01 12:00:05,123 INFO REFLEX_SAFETY mode=shadow would_play=1 class=unknown\n")
    mk = tmp_path / "m.txt"
    mk.write_text("2026-01-01 12:00:20 DISTRESS_HANDOFF\n")
    assert SIM.main(["--decisions", str(dec), "--markers", str(mk)]) == 0
    out = capsys.readouterr().out
    assert "safety count: UNAVAILABLE" in out and "LAB measure (a time correlation" in out and "blind to refusals" in out


def test_join_safety_counts_would_play_turns_near_a_server_marker():
    dec = ["2026-01-01 12:00:00,123 INFO REFLEX_DECISION mode=shadow kind=heard skip=- take=mm_01 decided_ms=190 talk_obs_ms=4 noise_db=-70 spk=1 media=0 win=wake bank=3811a303",
           "2026-01-01 12:10:00,123 INFO REFLEX_DECISION mode=shadow kind=heard skip=- take=mm_01 decided_ms=190 talk_obs_ms=4 noise_db=-70 spk=1 media=0 win=wake bank=3811a303",
           "2026-01-01 12:20:00,123 INFO REFLEX_DECISION mode=shadow kind=none skip=night take=- decided_ms=190 talk_obs_ms=4 noise_db=-70 spk=1 media=0 win=wake bank=3811a303"]
    marks = ["2026-01-01 12:00:20 DISTRESS_HANDOFF", "2026-01-01 12:20:10 MEDICAL_SAFETY", "free text 2026-01-01 12:10:05 CHILD_SAFETY_FLOOR"]
    r = SIM.join_safety(dec, marks)
    assert r == {"would_play": 2, "near_marker": 1, "by_marker": {"DISTRESS_HANDOFF": 1}}       # the none-turn is not counted;
    assert SIM.join_safety(dec, [])["near_marker"] == 0                                         # a non-allowlisted line is ignored
    assert SIM.join_safety(dec, ["2026-01-01 11:59:00 DISTRESS_HANDOFF"])["near_marker"] == 0   # a marker BEFORE the turn is not near it
