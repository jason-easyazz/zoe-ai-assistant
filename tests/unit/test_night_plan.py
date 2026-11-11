"""The night session's time planner (``scripts/night/night_plan.py``) and the dreaming window in the morning report: protected stages first, optional jobs only when they fit,
the leftover to LISTEN, and anything that does not fit is carried and REPORTED."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "night"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "scripts" / "perf"))

import night_plan as np_  # noqa: E402


def test_with_a_full_night_everything_runs_and_listen_gets_the_slack():
    p = np_.plan(5 * 3600, {})
    assert p["carried"] == [] and p["fits"] and p["listen_s"] > np_.LISTEN_FLOOR_S
    assert p["stage"]["digest"] and p["stage"]["wake"] and p["stage"]["post"]
    used = sum(p["stage"].values())
    assert abs(used + p["listen_s"] - 5 * 3600) < 1.0                                  # nothing is double counted or lost


def test_history_replaces_the_defaults_with_a_rolling_median_and_a_margin():
    h = {"digest": [100, 110, 120, 4000]}
    assert np_.est(h, "digest") == 115 * np_.MARGIN                                    # the median ignores the one wild night
    assert np_.est({}, "digest") == np_.DEFAULT_S["digest"] * np_.MARGIN
    assert np_.est({"digest": list(range(1, 100))}, "digest") == 96 * np_.MARGIN       # only the last seven nights count


def test_a_short_night_carries_the_optional_jobs_never_the_essential_or_the_wake():
    p = np_.plan(2 * 3600, {})
    assert p["carried"] and "digest" in p["stage"] and "omi_day_review" in p["stage"] and "self_review" in p["stage"] and "wake" in p["stage"] and "post" in p["stage"]
    assert set(p["carried"]) <= set(np_.OPTIONAL_ORDER) and not set(p["carried"]) & set(np_.ESSENTIAL)
    kept = [n for n in np_.OPTIONAL_ORDER if n in p["stage"]]
    assert kept == list(np_.OPTIONAL_ORDER[:len(kept)])                               # optional jobs are dropped from the END of the priority list
    assert p["listen_s"] >= np_.LISTEN_FLOOR_S                                         # and LISTEN keeps its floor before another optional job is admitted


def test_a_night_too_short_for_the_protected_stages_says_so_and_reports_the_shortfall():
    p = np_.plan(1800, {})
    assert not p["fits"] and p["short_s"] > 0 and set(p["carried"]) == set(np_.OPTIONAL_ORDER) and p["listen_s"] == 0.0


def test_only_the_jobs_that_exist_tonight_are_planned():
    p = np_.plan(5 * 3600, {}, present=["dreaming"])
    assert "dreaming" in p["stage"] and "vago_extract" not in p["stage"] and p["carried"] == []


# ── the household's dreaming window in the night report ─────────────────────────────────────────────────────────────────────────────────────

def test_the_window_is_read_from_the_household_mirror_each_run_and_a_bad_one_is_loud(tmp_path, monkeypatch):
    import json
    import night_window as nw
    from test_night_window import make
    p = tmp_path / "dream-window.json"
    monkeypatch.setenv("ZOE_DREAM_WINDOW_FILE", str(p))
    monkeypatch.setenv("ZOE_DREAM_EARLIEST", "03:00")                                   # the env is only the fallback default; a stored window beats it
    p.write_text(json.dumps({"earliest": "01:00", "latest": "02:00", "awake_by": "05:00", "quiet_min": 20}))
    w, errs, src = nw.load_dream_window()
    assert (w["earliest"], w["quiet_min"], errs, src) == ("01:00", 20, [], "stored")
    p.write_text(json.dumps({"earliest": "01:00", "latest": "02:00", "awake_by": "04:30", "quiet_min": 20}))
    cfg_w, cfg_errs, cfg_src = nw.load_dream_window()
    assert cfg_errs and cfg_src == "default"
    wnd, _host, _ = make(tmp_path)
    wnd.cfg.dream_window, wnd.cfg.dream_errors, wnd.cfg.dream_source = cfg_w, cfg_errs, cfg_src
    lines = wnd.report_lines(0)
    assert any(ln.startswith("**ALERT (dreaming window):**") for ln in lines) and any(ln.startswith("Dreaming window (default):") for ln in lines)
    wnd.cfg.dream_errors = []
    assert not any(ln.startswith("**ALERT (dreaming window)") for ln in wnd.report_lines(0))
