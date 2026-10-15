"""Offline coverage for ``scripts/maintenance/stt_accuracy_report.py`` - the PAIRING logic and the score, on synthetic rows.

The report itself can only read the live box (its voice log, ``struggle_events``, the clip corpus), so this suite asserts the
instrument: which (misheard, repeat) pairs it labels, what it refuses to label, the weekly numbers, the clip match, the
keyterm A/B comparison (with a fake transcriber - no model), and that it never writes into the repository. Every positive
case is paired with a negative control.

Every transcript here is invented (public repo). Stdlib + the pure ``struggle_signals``: safe on the slim GitHub lane.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_TOOL = ROOT / "scripts" / "maintenance" / "stt_accuracy_report.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("stt_accuracy_report", _TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module          # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


rep = _load_tool()
T0 = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc).timestamp()   # a Monday (ISO week 2026-W42)


def row(dt, text, panel="hall"):
    return rep.SttRow(T0 + dt, panel, text)


def log_lines(rows, extra=()):
    out = [json.dumps({"ts": datetime.fromtimestamp(r.ts, tz=timezone.utc).isoformat(), "panel_id": r.panel,
                       "transcript": r.text, "route": "turn_stream"}) for r in rows]
    return "\n".join(list(out) + list(extra)) + "\n"


# ── the log ──────────────────────────────────────────────────────────────────

def test_load_keeps_live_turns_only(tmp_path):
    p = tmp_path / "voice_stt.jsonl"
    p.write_text(log_lines([row(0, "what time is it")], extra=[
        json.dumps({"ts": "2026-10-12T08:00:05+00:00", "panel_id": "replay-gate", "transcript": "turn on the light"}),
        json.dumps({"ts": "2026-10-12T08:00:06+00:00", "panel_id": "hall", "transcript": ""}),
        json.dumps({"ts": "2026-10-12T08:00:07+00:00", "panel_id": "hall", "transcript": "x", "error": "boom"}),
        "not json",
    ]), encoding="utf-8")
    rows = rep.load_stt_rows([p, tmp_path / "missing.jsonl"])
    assert [r.text for r in rows] == ["what time is it"]


# ── pairing from the log ─────────────────────────────────────────────────────

def test_a_corrected_repeat_is_a_mishear_pair():
    pairs = rep.pairs_from_log([row(0, "two on the kitchen light"), row(8, "turn on the kitchen light")])
    assert [(p.misheard, p.repeat, p.signal, p.source) for p in pairs] == [
        ("two on the kitchen light", "turn on the kitchen light", "MISHEAR", "log")]
    assert pairs[0].differs and pairs[0].edits == 1 and pairs[0].repeat_words == 5


def test_an_identical_repeat_is_a_pair_but_not_a_mishear():
    pairs = rep.pairs_from_log([row(0, "what's on the shopping list"), row(10, "what's on the shopping list")])
    assert [p.signal for p in pairs] == ["RE_ASK"] and not pairs[0].differs


@pytest.mark.parametrize("second,dt,panel", [
    ("turn on the kitchen light", 45, "hall"),        # outside the window
    ("turn on the kitchen light", 8, "kitchen"),      # another panel
    ("what's the weather tomorrow", 8, "hall"),       # a different request
    ("add eggs to the list", 8, "hall"),              # a different word that does not sound alike
])
def test_no_pair_for_a_different_or_late_or_elsewhere_turn(second, dt, panel):
    first = "two on the kitchen light" if "kitchen" in second else "add milk to the list"
    assert rep.pairs_from_log([row(0, first), row(dt, second, panel)]) == []


def test_the_window_is_what_drops_a_late_repeat():
    """Negative control: the same 20 s repeat is a pair at 30 s and not at 10 s."""
    rows = [row(0, "two on the kitchen light"), row(20, "turn on the kitchen light")]
    assert len(rep.pairs_from_log(rows, window_s=30)) == 1
    assert rep.pairs_from_log(rows, window_s=10) == []


# ── pairing from struggle_events ─────────────────────────────────────────────

def test_an_event_maps_back_to_its_two_transcripts():
    rows = [row(0, "set a time for five minutes"), row(9, "set a timer for five minutes"), row(40, "thanks")]
    ev = {"channel": "voice", "signal": "MISHEAR", "panel_id": "hall", "ts": T0 + 12}
    pairs = rep.pairs_from_events([ev], rows)
    assert [(p.misheard, p.repeat, p.source) for p in pairs] == [
        ("set a time for five minutes", "set a timer for five minutes", "events")]


@pytest.mark.parametrize("ev", [
    {"channel": "chat", "signal": "MISHEAR", "panel_id": "hall"},      # typed: no STT involved
    {"channel": "voice", "signal": "VERBOSE", "panel_id": "hall"},     # not a repeat label
    {"channel": "voice", "signal": "MISHEAR", "panel_id": "office"},   # no transcripts on that panel
])
def test_events_that_label_nothing(ev):
    rows = [row(0, "set a time for five minutes"), row(9, "set a timer for five minutes")]
    assert rep.pairs_from_events([{**ev, "ts": T0 + 12}], rows) == []


def test_log_and_event_labels_merge_into_one_pair():
    rows = [row(0, "two on the kitchen light"), row(8, "turn on the kitchen light")]
    ev = {"channel": "voice", "signal": "MISHEAR", "panel_id": "hall", "ts": T0 + 10}
    merged = rep.merge_pairs(rep.pairs_from_log(rows), rep.pairs_from_events([ev], rows))
    assert [p.source for p in merged] == ["both"]


# ── the score ────────────────────────────────────────────────────────────────

def test_weekly_score():
    rows = [row(0, "two on the kitchen light"), row(8, "turn on the kitchen light"),
            row(60, "what's on the shopping list"), row(70, "what's on the shopping list"),
            row(7 * 86400, "what time is it")]                                  # the next ISO week
    report, pairs = rep.build_report(rows, None, window_s=30)
    w42, w43 = report["weeks"]
    assert (w42["week"], w42["turns"], w42["pairs"], w42["mishears"]) == ("2026-W42", 4, 2, 1)
    assert w42["mishear_rate"] == 0.25 and w42["proxy_wer"] == 0.2          # 1 edit / 5 repeat words
    assert (w43["week"], w43["turns"], w43["pairs"], w43["mishear_rate"], w43["proxy_wer"]) == ("2026-W43", 1, 0, 0.0, None)
    assert report["events_read"] is None and "log labels only" in rep.render(report)


def test_word_edits():
    assert rep.word_edits("a b c".split(), "a x c".split()) == 1
    assert rep.word_edits([], "a b".split()) == 2
    assert rep.words("What's the time?") == rep.words("what is the time")    # normalised like the detector


# ── clips + the keyterm A/B ──────────────────────────────────────────────────

def test_clip_match_by_file_time(tmp_path):
    a, b, c = (tmp_path / n for n in ("a.wav", "b.wav", "c.wav"))
    for p, t in ((a, T0), (b, T0 + 100), (c, T0 + 101)):
        p.write_bytes(b"RIFF")
        os.utime(p, (t, t))
    (tmp_path / "quarantine-x").mkdir()
    idx = rep.clip_index(tmp_path)
    assert rep.clip_for(T0 + 1.5, idx) == a
    assert rep.clip_for(T0 + 50, idx) is None              # nothing close
    assert rep.clip_for(T0 + 100.5, idx) is None           # two clips equally close: neither is trusted


def test_keyterm_ab_compares_each_arm_against_the_repeat(tmp_path):
    clips = []
    for i, t in enumerate((T0, T0 + 200)):
        p = tmp_path / f"{i}.wav"
        p.write_bytes(b"RIFF")
        os.utime(p, (t, t))
        clips.append(p)
    pairs = [rep.Pair("hall", T0, T0 + 8, "two on the kitchen light", "turn on the kitchen light", "MISHEAR", "log"),
             rep.Pair("hall", T0 + 200, T0 + 208, "set a time for five minutes", "set a timer for five minutes",
                      "MISHEAR", "log")]
    calls = []

    def fake(cl, terms):
        calls.append(terms)
        if terms is None:
            return {str(clips[0]): "two on the kitchen light", str(clips[1]): "set a time for five minutes",
                    "_keyterms": {"applied": 0}}
        return {str(clips[0]): "turn on the kitchen light", str(clips[1]): "set a time for five minutes",
                "_keyterms": {"applied": 2}}

    ab = rep.keyterm_ab(pairs, "kitchen light,timer", sample_dir=tmp_path, max_clips=10, transcribe=fake)
    assert calls == [None, "kitchen light,timer"]            # unset first, then the candidate - nothing else
    assert (ab["clips"], ab["changed"], ab["closer"], ab["further"], ab["same"]) == (2, 1, 1, 0, 1)
    assert ab["keyterms_with"] == {"applied": 2} and ab["candidate_terms"] == 2
    assert (ab["edits_without"], ab["edits_with"]) == (2, 1)


def test_keyterm_ab_skips_identical_repeats_and_unmatched_clips(tmp_path):
    pairs = [rep.Pair("hall", T0, T0 + 8, "what's the time", "what's the time", "RE_ASK", "log")]
    ab = rep.keyterm_ab(pairs, "x", sample_dir=tmp_path, max_clips=10,
                        transcribe=lambda *_a: pytest.fail("nothing to transcribe"))
    assert ab["clips"] == 0 and "no labelled clip" in ab["note"]


@pytest.mark.parametrize("hh,mm,quiet", [(1, 34, False), (1, 35, True), (3, 0, True), (4, 54, True), (4, 55, False),
                                         (14, 0, False)])
def test_the_ab_keeps_out_of_the_night_brain_window(hh, mm, quiet):
    assert rep.in_quiet_hours(datetime(2026, 10, 15, hh, mm)) is quiet


def test_the_child_gets_the_list_for_itself_only():
    base = {"ZOE_MOONSHINE_KEYTERMS": "live,list", "PATH": "/bin"}
    a = rep.child_env(base, None, "/tmp/x")
    b = rep.child_env(base, "kitchen light", "/tmp/x")
    assert "ZOE_MOONSHINE_KEYTERMS" not in a and b["ZOE_MOONSHINE_KEYTERMS"] == "kitchen light"
    assert a["ZOE_VOICE_STT_LOG"].startswith("/tmp/x") and a["ZOE_VOICE_SAVE_AUDIO"] == "0"
    assert base["ZOE_MOONSHINE_KEYTERMS"] == "live,list"     # the caller's env is untouched


# ── never into the repo ──────────────────────────────────────────────────────

def test_the_report_never_lands_in_the_repository(tmp_path):
    with pytest.raises(ValueError):
        rep.safe_out_dir(ROOT / "docs")
    with pytest.raises(ValueError):
        rep.safe_out_dir(ROOT)
    assert rep.safe_out_dir(tmp_path / "out") == (tmp_path / "out").resolve()


def test_main_writes_under_the_out_dir_only(tmp_path, capsys):
    log = tmp_path / "voice_stt.jsonl"
    now = datetime.now(timezone.utc).timestamp()
    rows = [rep.SttRow(now - 100, "hall", "two on the kitchen light"), rep.SttRow(now - 92, "hall", "turn on the kitchen light")]
    log.write_text(log_lines(rows), encoding="utf-8")
    out = tmp_path / "out"
    assert rep.main(["--log", str(log), "--no-db", "--out-dir", str(out)]) == 0
    files = sorted(p.suffix for p in out.iterdir())
    assert files == [".json", ".txt"]
    data = json.loads(next(out.glob("*.json")).read_text())
    assert data["pairs"] == 1 and data["mishears"] == 1
    printed = capsys.readouterr().out
    assert "kitchen" not in printed                           # stdout carries counts, never the words


def test_main_refuses_a_repo_out_dir_before_reading(tmp_path):
    with pytest.raises(ValueError):
        rep.main(["--log", str(tmp_path / "none.jsonl"), "--no-db", "--out-dir", str(ROOT / "docs")])
