"""Tests for the listen-pace shadow replayer. Synthetic FIRST_SOUND lines only (generated below); no live reads.

Hand-run (lab, not CI-wired; deliberately no ci_safe marker):
    flock -x -w 14000 /tmp/zoe-test-lane.lock timeout 180 python3 -m pytest -p no:cacheprovider -q labs/listen-pace/test_replay.py
"""
from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

_MOD = Path(__file__).resolve().parent / "replay.py"
_spec = importlib.util.spec_from_file_location("listen_pace_replayer_under_test", _MOD)
R = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = R
_spec.loader.exec_module(R)


def fs_line(*, rule="deep", close_ms=640, quiet_ms=720, fall="clean", still=0, talk=0, obs=1500, win="wake", muted=0,
            ts=None, extra=""):
    """One FIRST_SOUND line in PR 2495's exact field order (reflex fields are its constants)."""
    pre = f"{ts} INFO zoe-voice: " if ts else "INFO zoe-voice: "
    return (f"{pre}FIRST_SOUND close={rule}={close_ms}ms post_ms=310 reflex=none skip=- reflex_at_ms=-1 reflex_dur_ms=-1 "
            f"reflex_cut=0 reflex_wait_ms=0 server_first_ms=2400 server_kind=content gap_ms=-1 still_talking={still} "
            f"talk_chunks={talk} chunks=12 underrun_max_ms=0 underrun_sum_ms=0 obs_ms={obs} win={win} quiet_ms={quiet_ms} "
            f"fall={fall} muted_ms={muted}{extra}")


def corpus(n, cut_rate, seed, *, hesitating_share=0.4, talk_first=False):
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        cut = rnd.random() < cut_rate
        fall = "hesitating" if rnd.random() < hesitating_share else "clean"
        rule = "quiet" if fall == "hesitating" and rnd.random() < 0.5 else "deep"
        q = 800 if rule == "quiet" else rnd.choice([720, 720, 800])
        extra = f" talk_first_ms={rnd.choice([40, 120, 400, 900])}" if (talk_first and cut) else ""
        out.append(fs_line(rule=rule, close_ms=q if rule == "quiet" else 640, quiet_ms=q, fall=fall,
                           still=int(cut), talk=5 if cut else rnd.choice([0, 0, 1]), extra=extra))
    return out


def test_parse_line_reads_the_s0_fields_and_ignores_other_lines():
    t, f = R.parse_line(fs_line(ts="2026-11-20 10:00:00,123"))
    assert t is not None and f["rule"] == "deep" and f["close_ms"] == "640" and f["quiet_ms"] == "720" and f["win"] == "wake"
    assert R.parse_line("INFO zoe-voice: Recorded command (speech=1.2s)") is None
    assert R.parse_line(fs_line())[0] is None


def test_a_fast_talker_with_no_cuts_shortens_and_saves_waiting():
    rep = R.replay(corpus(400, 0.0, 1), turns_per_day=10)
    assert rep["eligible"] == 400 and rep["cuts_per_100_observed"] == 0 == rep["cuts_per_100_predicted"]
    assert rep["final_level"] == -3
    assert rep["mean_wait_ms_predicted"] < rep["mean_wait_ms_observed"] and rep["wait_saved_ms_per_100_turns"] > 0
    assert min(rep["level_histogram"]) == -3 and rep["level_histogram"][0] == 40        # 40 clean turns at L0 first


def test_the_first_39_turns_change_nothing():
    rep = R.replay(corpus(39, 0.0, 2), turns_per_day=10)
    assert set(rep["level_histogram"]) == {0} and rep["wait_saved_ms_per_100_turns"] == 0


def test_a_frequent_pauser_lengthens_and_never_shortens_into_cuts():
    rep = R.replay(corpus(300, 0.10, 3), turns_per_day=10)
    assert rep["final_level"] >= 1 and max(rep["level_histogram"]) >= 1
    assert rep["mean_wait_ms_predicted"] > rep["mean_wait_ms_observed"]                  # it costs waiting, by design
    assert rep["cutoffs_avoided"] == 0 and rep["cutoffs_possibly_avoided"] > 0           # no resume time on S0's line: "maybe"


def test_with_a_resume_time_avoided_cutoffs_are_counted_exactly():
    rep = R.replay(corpus(300, 0.10, 3, talk_first=True), turns_per_day=10)
    assert rep["cutoffs_avoided"] > 0 and rep["cuts_per_100_predicted"] < rep["cuts_per_100_observed"]


def test_shorter_levels_never_change_the_cut_count():
    rep = R.replay(corpus(600, 0.01, 4), turns_per_day=10)
    shorter = [r for r in rep["rows"] if r.get("level", 0) < 0 and "cut_obs" in r]
    assert shorter and all(r["cut_pred"] == r["cut_obs"] for r in shorter)


def test_malformed_and_frozen_lines_are_skipped_and_never_move_the_level():
    lines = [fs_line(muted=240), fs_line(talk=-1), fs_line(win="barge"), fs_line(rule="cap"), fs_line(obs=300),
             fs_line().replace("still_talking=0", "still_talking=x"), "FIRST_SOUND garbage"] * 30
    rep = R.replay(lines)
    assert rep["eligible"] == 0 and rep["skipped"] == len(lines) and rep["final_level"] == 0


def test_main_prints_numbers_only(tmp_path, capsys):
    p = tmp_path / "panel.log"
    p.write_text("\n".join(corpus(120, 0.02, 5)) + "\nINFO zoe-voice: Recorded command (speech=1.0s)\n")
    assert R.main([str(p)]) == 0
    out = capsys.readouterr().out
    assert "cuts_per_100_observed" in out and "Recorded" not in out
    assert R.main([str(p), "--json"]) == 0
