"""The voice gate keeps a separate 'last main run' record.

The shared last-result file is overwritten by every probe run, so a passing PR
probe hid a failed nightly on main (2026-10-21). Pins: the main-lane file is
written only for runs of the live checkout, a PR run never touches it, the shared
file is still written for every run (deploy gate contract), and the ground-truth
helper reports the right lane with a timestamp.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vrp = _load("voice_regression_probe", "scripts/maintenance/voice_regression_probe.py")
vgr = _load("voice_gate_report", "scripts/maintenance/voice_gate_report.py")


class _Args:
    def __init__(self, tmp: Path, service_dir: Path | str):
        self.results = tmp / "voice_regression_last.json"
        self.trend = tmp / "voice_regression_trend.jsonl"
        self.baseline = tmp / "baseline.json"
        self.service_dir = str(service_dir)


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for cmd in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit",
                                 "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", "-C", str(path), *cmd], check=True, capture_output=True)
    return path


def _emit(args, status, reason=""):
    return vrp.emit_result(args, status=status, summary=dict(vrp.EMPTY_SUMMARY),
                           said_vs_did=[], speed_deltas={}, baseline={}, reason=reason)


@pytest.fixture
def live(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path / "assistant")
    monkeypatch.setenv("ZOE_VOICE_MAIN_CHECKOUT", str(repo))
    return repo


def test_main_run_writes_main_lane_and_shared(tmp_path, live):
    cache = tmp_path / "cache"
    cache.mkdir()
    args = _Args(cache, live)
    _emit(args, "fail", "speed regressed")
    main = json.loads(vrp.main_lane_path(args.results).read_text())
    assert main["status"] == "fail" and main["lane"] == "main"
    assert json.loads(args.results.read_text())["status"] == "fail"  # shared contract intact


def test_pr_run_never_overwrites_main_lane(tmp_path, live):
    cache = tmp_path / "cache"
    cache.mkdir()
    _emit(_Args(cache, live), "fail", "speed regressed")
    pr = _git_repo(tmp_path / "wt" / "voice-probe-1")
    pr_args = _Args(cache, pr)
    _emit(pr_args, "pass")
    assert json.loads(pr_args.results.read_text())["status"] == "pass"  # shared: overwritten as before
    main = json.loads(vrp.main_lane_path(pr_args.results).read_text())
    assert main["status"] == "fail" and main["reason"] == "speed regressed"


def test_pr_run_alone_creates_no_main_file(tmp_path, live):
    cache = tmp_path / "cache"
    cache.mkdir()
    pr_args = _Args(cache, _git_repo(tmp_path / "wt"))
    _emit(pr_args, "pass")
    assert not vrp.main_lane_path(pr_args.results).exists()


def test_no_service_dir_and_self_test_are_not_main(tmp_path, live):
    assert not vrp.is_main_lane_run({"revision": None})
    assert not vrp.is_main_lane_run({"revision": {"service_dir": str(live)}, "self_test": True})
    assert vrp.is_main_lane_run({"revision": {"service_dir": str(live)}})


def test_main_lane_streak_is_independent(tmp_path, live):
    cache = tmp_path / "cache"
    cache.mkdir()
    a = _Args(cache, live)
    _emit(a, "fail")
    _emit(_Args(cache, _git_repo(tmp_path / "wt")), "pass")  # PR pass resets shared streak
    _emit(a, "fail")
    assert json.loads(vrp.main_lane_path(a.results).read_text())["non_pass_streak"] == 2


def _rec(sd, status, ts, reason=""):
    return {"status": status, "timestamp": ts, "reason": reason, "revision": {"service_dir": str(sd)}}


def test_report_prefers_main_file_and_labels_pr_probe(tmp_path, live):
    cache = tmp_path
    (cache / "voice_regression_last_main.json").write_text(
        json.dumps(_rec(live, "fail", "2026-10-21T04:32:00Z", "speed")))
    (cache / "voice_regression_last.json").write_text(
        json.dumps(_rec(tmp_path / "wt", "pass", "2026-10-21T06:21:00Z")))
    out = vgr.report(cache, str(live))
    assert out[0] == "voice gate (nightly main, 2026-10-21T04:32:00Z): fail - speed"
    assert out[1] == "voice gate (latest PR probe, 2026-10-21T06:21:00Z): pass"


def test_report_falls_back_to_trend(tmp_path, live):
    (tmp_path / "voice_regression_trend.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [
            _rec(live, "fail", "2026-10-21T04:32:00Z", "speed"),
            _rec(tmp_path / "wt", "pass", "2026-10-21T06:21:00Z"),
        ]) + "\n")
    (tmp_path / "voice_regression_last.json").write_text(
        json.dumps(_rec(tmp_path / "wt", "pass", "2026-10-21T06:21:00Z")))
    out = vgr.report(tmp_path, str(live))
    assert out[0].startswith("voice gate (nightly main, 2026-10-21T04:32:00Z, from trend): fail")
    assert "latest PR probe" in out[1]


def test_report_never_presents_pr_pass_as_main(tmp_path, live):
    """Negative control: only a PR record exists -> main must say 'no record'."""
    (tmp_path / "voice_regression_last.json").write_text(
        json.dumps(_rec(tmp_path / "wt", "pass", "2026-10-21T06:21:00Z")))
    out = vgr.report(tmp_path, str(live))
    assert out[0] == "voice gate (nightly main): no record"
    assert "pass" not in out[0]


def test_report_shows_shared_main_run_once(tmp_path, live):
    (tmp_path / "voice_regression_last.json").write_text(
        json.dumps(_rec(live, "pass", "2026-10-22T04:32:00Z")))
    out = vgr.report(tmp_path, str(live))
    assert out == ["voice gate (nightly main, 2026-10-22T04:32:00Z, from shared file): pass"]


def test_ground_truth_calls_helper_not_shared_file():
    sh = (REPO / "scripts/maintenance/zoe_ground_truth.sh").read_text()
    assert "voice_gate_report.py" in sh
    assert "voice gate last:" not in sh


def test_real_shape_service_dirs(tmp_path, live):
    """The probe records <checkout>/services/zoe-data, not the checkout root."""
    zd = live / "services" / "zoe-data"
    zd.mkdir(parents=True)
    pr = tmp_path / "wt" / "voice-probe-2176" / "services" / "zoe-data"
    pr.mkdir(parents=True)
    agent = live / ".claude" / "worktrees" / "night-100" / "services" / "zoe-data"
    agent.mkdir(parents=True)
    for mod in (lambda d: vrp.is_main_lane_run({"revision": {"service_dir": str(d)}}),
                lambda d: vgr._is_main({"revision": {"service_dir": str(d)}}, str(live))):
        assert mod(zd) is True
        assert mod(live) is True
        assert mod(pr) is False
        assert mod(agent) is False
    (tmp_path / "voice_regression_trend.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [
            _rec(zd, "fail", "2026-10-25T20:30:00Z", "speed"),
            _rec(pr, "pass", "2026-10-25T22:00:00Z")]) + "\n")
    out = vgr.report(tmp_path, str(live))
    assert out[0] == "voice gate (nightly main, 2026-10-25T20:30:00Z, from trend): fail - speed"
