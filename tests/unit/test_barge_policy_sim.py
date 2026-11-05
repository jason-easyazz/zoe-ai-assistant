"""scripts/perf/barge_policy_sim.py - the duck -> decide -> resume policy driven offline.

The simulator runs the REAL daemon `_BargeDetector` / `_BargeEpisode` / `_BargeDecider` /
`_SinkInputDucker` on scripted probabilities with a pactl volume model. These tests pin the lab's
predicted outcomes, the stuck-state checker, and - the part that makes the checker trustworthy -
a NEGATIVE CONTROL for every violation it can report.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "perf" / "barge_policy_sim.py"


@pytest.fixture(scope="module")
def sim():
    spec = importlib.util.spec_from_file_location("_barge_sim_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def daemon(sim):
    mod = sim.ste.load_daemon()
    mod.BARGE_DUCK_ENABLED = True
    return mod


def _lab(sim, daemon, n):
    b = sim.LAB_BARGES[n - 1]
    return b, sim.simulate(daemon, sim.lab_profile(b["shape"]))


def test_lab_predictions_pin_the_phase1_behaviour(sim, daemon):
    got = {b["id"]: sim.predicted_outcome(sim.simulate(daemon, sim.lab_profile(b["shape"])))
           for b in sim.LAB_BARGES}
    assert [got[i] for i in (1, 2, 3, 6, 7)] == ["resume"] * 5            # backchannels / short TV / aside
    assert got[4] == got[5] == "no-fire"                                   # thump + 3 s of non-speech
    assert got[9] == got[10] == "commit"                                   # sustained speech
    assert got[8] == "resume"                                              # KNOWN GAP: a lone "stop" is < 900 ms
    lab = sim.run_lab(daemon)
    assert (lab["predicted_correct"], lab["of"]) == (9, 10)
    assert [r["id"] for r in lab["rows"] if not r["ok"]] == [8]
    assert all(not r["violations"] for r in lab["rows"])


def test_the_lone_stop_gap_is_the_commit_threshold_not_the_detector(sim, daemon, monkeypatch):
    """Negative control for the gap: lower the commit threshold below the word's length and the
    SAME profile commits - so the gap is the 900 ms rule, and the lab's #8 measures exactly it."""
    assert sim.predicted_outcome(_lab(sim, daemon, 8)[1]) == "resume"
    monkeypatch.setattr(daemon, "BARGE_COMMIT_SPEECH_MS", 400)
    assert sim.predicted_outcome(_lab(sim, daemon, 8)[1]) == "commit"


def test_commit_lands_inside_the_documented_window(sim, daemon):
    _b, r = _lab(sim, daemon, 10)
    assert r["terminated"] and 900 <= r["commit_ms"][0] <= 1100
    assert r["volume"] == sim.BASELINE          # a commit restores the stream volume before the kill


def test_resume_restores_the_volume_exactly_and_never_kills(sim, daemon):
    _b, r = _lab(sim, daemon, 1)
    assert r["outcomes"] == ["resume"] and not r["terminated"]
    assert r["volume"] == sim.BASELINE and sim.violations(daemon, r) == []


def test_duck_actually_dipped_the_stream(sim, daemon):
    """The volume model is not vacuous: while ducked the stream sits at -15 dB (negative control
    for 'restored to baseline' passing trivially)."""
    spy = sim.PactlSpy()
    spy("set-sink-input-volume", "40", "-15.0dB")
    assert spy.volume == pytest.approx(sim.BASELINE * 10 ** (-15 / 20), abs=1)
    spy("set-sink-input-volume", "40", str(sim.BASELINE))
    assert spy.volume == sim.BASELINE


def test_sentinel_never_commits_and_resolves_at_the_ceiling(sim, daemon):
    r = sim.simulate(daemon, sim.lab_profile([0.95] * 3 + [-1.0] * 40))
    assert r["outcomes"] == ["ceiling"] and not r["terminated"] and r["volume"] == sim.BASELINE
    assert sim.violations(daemon, r) == []


def test_player_exit_mid_duck_leaks_then_the_next_player_heals(sim, daemon):
    """Reply ends while ducked: the baseline is remembered as a leak and the NEXT player's
    stream is put back (`_heal_duck_leak`). Without the heal the next reply would be ducked."""
    probs = sim.lab_profile([0.95] * 4)
    proc, spy = sim.FakeProc(), sim.PactlSpy()
    with sim._isolated(daemon, spy):
        det = daemon._BargeDetector()
        det.new_playback(1000.0)
        ep = None
        for i, p in enumerate(probs):
            cap = 1000.0 + i * sim.CHUNK_S
            if det.feed(p, cap):
                ep = daemon._BargeEpisode.open("sim", proc, det, p, cap)
                break
        assert ep is not None and ep.ducker.ducked and spy.volume < sim.BASELINE
        proc.exited = True
        ep.finish("ended", 1010.0)
        assert daemon._duck_leak == sim.BASELINE
        nxt = sim.FakeProc(pid=5555)
        spy2 = sim.PactlSpy(pid=5555, volume=spy.volume)       # stream-restore re-applied the duck
        daemon._pactl = spy2
        daemon._heal_duck_leak(nxt)
        assert spy2.volume == sim.BASELINE and daemon._duck_leak is None
        # negative control: no heal call -> the next stream stays ducked
        spy3 = sim.PactlSpy(pid=5555, volume=spy.volume)
        assert spy3.volume < sim.BASELINE


def test_fuzz_finds_no_stuck_state_in_the_real_policy(sim, daemon):
    for seed in (1, 2, 3):
        fz = sim.run_fuzz(daemon, 600, seed=seed)
        assert fz["violations"] == {}, fz
        assert fz["outcomes"].get("commit") and fz["outcomes"].get("resume")   # the fuzz reaches every branch


@pytest.mark.parametrize("name", ["stuck_mute", "open_episode", "kill_without_commit",
                                  "sentinel_commit", "multi_outcome"])
def test_every_violation_has_a_negative_control(sim, daemon, name):
    """A deliberately broken policy MUST be flagged by exactly the matching check, and the real
    policy must be clean on the same profile - otherwise the stuck-state checker is blind."""
    probs = sim.CONTROL_PROFILES[name]()
    assert name not in sim.violations(daemon, sim.simulate(daemon, probs))
    with sim.mutations(daemon)[name]():
        assert name in sim.violations(daemon, sim.simulate(daemon, probs))
    assert name not in sim.violations(daemon, sim.simulate(daemon, probs))     # mutation fully undone


def test_commit_not_terminated_and_late_outcome_checks(sim, daemon):
    """The two checks no mutation reaches are pinned on hand-made results."""
    _b, r = _lab(sim, daemon, 10)
    assert sim.violations(daemon, r) == []
    assert "commit_not_terminated" in sim.violations(daemon, {**r, "terminated": False})
    assert "late_outcome" in sim.violations(daemon, {**r, "commit_ms": [daemon.BARGE_DECIDE_MAX_MS + 500]})
    assert "stuck_mute" in sim.violations(daemon, {**sim.simulate(daemon, sim.lab_profile([0.95] * 3)),
                                                   "volume": 20000})


def test_run_controls_reports_all_true(sim, daemon):
    assert all(sim.run_controls(daemon).values())
