"""scripts/perf/turn_taking_metrics.py: cut-offs and over-waits per 100 turns, counted from daemon log lines (counts only).

Neutral synthetic log lines in the daemon's real format. Negative controls: a log with none of the events scores zero, an unrelated line
scores nothing, a non-turn is not a turn, and an empty log is None (never 0).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "perf"))
import turn_taking_metrics as tm  # noqa: E402

pytestmark = pytest.mark.ci_safe


def L(msg: str, day: str = "2026-11-04", lvl: str = "INFO") -> str:
    return f"{day} 19:00:00,123 [voice-daemon] {lvl} {msg}"


REC = "Recorded command: duration=2.10s chunks=26 stop=vad endpoint=vad tail=clean silence_timeout=1.50s"
FUP = "Recorded follow-up: duration=1.40s chunks=17 stop=vad endpoint=vad tail=deep silence_timeout=1.50s"


def test_counts_turns_cutoffs_and_overwaits():
    lines = [L(REC)] * 6 + [L(FUP)] * 4 + [
        L("Continuation merged: one turn of 4.2s"),
        L("Recording ended on the hard cap mid-speech - listening 3.0s for the rest (merge 1/2)"),
        L("No continuation - sending the held clip as is"),
    ]
    r = tm.count_events(lines)
    assert (r["turns"], r["cutoffs"], r["overwaits"]) == (10, 2, 1)
    assert r["cutoffs_per_100"] == 20.0 and r["overwaits_per_100"] == 10.0 and r["meets_target"] is False


def test_a_clean_log_meets_the_target_and_zero_events_is_zero_not_none():
    r = tm.count_events([L(REC)] * 100)
    assert r["cutoffs_per_100"] == 0.0 and r["overwaits_per_100"] == 0.0 and r["meets_target"] is True


def test_control_unrelated_lines_and_a_too_short_turn_count_nothing():
    lines = [L("Barge-in detected during playback (x)"), L("Follow-up listening (turn 1/3, 6.0s window)..."),
             L(REC), L("Command too short, ignoring."), "Traceback (most recent call last):", ""]
    r = tm.count_events(lines)
    assert r["turns"] == 0 and r["cutoffs"] == 0 and r["overwaits"] == 0
    assert r["unparsed_lines"] == 1   # the traceback line; the blank line is not counted


def test_an_unmatched_too_short_line_does_not_cancel_an_unrelated_turn():
    # a "too short" with no counted recording before it (rotated log start) cancels nothing...
    assert tm.count_events([L("Command too short, ignoring."), L(REC)])["turns"] == 1
    # ...and a second one after the pair is spent cancels nothing either (never negative, never eats a later turn)
    r = tm.count_events([L(REC), L("Command too short, ignoring."), L("Command too short, ignoring."), L(REC)])
    assert r["turns"] == 1


def test_control_an_empty_log_has_no_rate_rather_than_zero():
    r = tm.count_events([])
    assert r["turns"] == 0 and r["cutoffs_per_100"] is None and r["overwaits_per_100"] is None and r["meets_target"] is None


def test_since_and_until_filter_by_day():
    lines = [L(REC, "2026-11-01"), L("Continuation merged: one turn of 3.0s", "2026-11-01"),
             L(REC, "2026-11-04"), L(REC, "2026-11-04")]
    assert tm.count_events(lines, since="2026-11-03")["turns"] == 2
    assert tm.count_events(lines, until="2026-11-02")["cutoffs"] == 1
    assert tm.count_events(lines, since="2026-11-03")["cutoffs"] == 0


def test_cli_reads_a_file_and_reports_unreadable(tmp_path, capsys):
    p = tmp_path / "voice.log"
    p.write_text(L(REC) + "\n" + L("Continuation merged: one turn of 3.0s") + "\n")
    assert tm.main([str(p), "--json"]) == 0
    assert '"cutoffs": 1' in capsys.readouterr().out
    assert tm.main([str(tmp_path / "missing.log")]) == 2


def test_the_matched_phrases_still_exist_in_the_daemon():
    """A renamed daemon log line would silently turn every rate to zero: pin the phrases to the daemon source."""
    import re
    src = (Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py").read_text()
    for phrase in tm.TURN_PATTERNS + tm.CUTOFF_PATTERNS + tm.OVERWAIT_PATTERNS + tm.NOT_A_TURN + tm.BOUNDARY_PATTERNS:
        # the phrase must open an actual log.info / log.warning call (a comment or dead constant does not count)
        pat = r"log\.(?:info|warning)\(\s*\n?\s*\"" + re.escape(phrase.rstrip(":"))
        assert re.search(pat, src), phrase
    assert not re.search(r"log\.info\(\s*\"Renamed phrase", src)   # control: the pattern does not match everything


def test_a_merged_continuation_clip_is_not_a_second_turn():
    """Codex #2295: 100 turns with five held-turn continuations must stay 100 turns (and five cut-offs), not 105."""
    lines = [L(REC)] * 95
    for _ in range(5):
        lines += [L(REC), L("Turn held: transcript ends mid-clause - listening 3.0s for the rest"), L(FUP),
                  L("Continuation merged: one turn of 4.2s")]
    r = tm.count_events(lines)
    assert (r["turns"], r["cutoffs"]) == (100, 5)
    # hard-cap continuation: the clip recorded after the cap line is the rest of the same turn
    cap = [L(REC), L("Recording ended on the hard cap mid-speech - listening 3.0s for the rest (merge 1/2)"), L(FUP)]
    assert tm.count_events(cap)["turns"] == 1
    # negative control: a follow-up with no hold / cap line before it is a real turn
    assert tm.count_events([L(REC), L(FUP)])["turns"] == 2
    # a hold that heard nothing does not swallow the NEXT real follow-up turn
    held_none = [L(REC), L("Turn held: transcript ends mid-clause - listening 3.0s for the rest"),
                 L("No continuation - sending the held clip as is"), L(FUP)]
    assert tm.count_events(held_none)["turns"] == 2


def test_a_hard_cap_listen_that_hears_nothing_does_not_swallow_the_next_follow_up():
    """Codex #2295: command -> hard cap -> silence (no log line of its own) -> the turn is answered -> a genuine follow-up.
    That is TWO turns; the "Transcript:" of the capped turn closes the open listen."""
    cap = L("Recording ended on the hard cap mid-speech - listening 3.0s for the rest (merge 1/2)")
    seq = [L(REC), cap, L("Transcript: len=40 utt=abc123abc123"), L(FUP)]
    r = tm.count_events(seq)
    assert (r["turns"], r["cutoffs"]) == (2, 1)
    # negative control: when the listen DID hear the rest, the clip is logged before the Transcript and is still not a turn
    assert tm.count_events([L(REC), cap, L(FUP), L("Transcript: len=60 utt=abc123abc123")])["turns"] == 1
