"""The offline measurement harness (``scripts/perf/measure_speaker_adapt.py``) on SYNTHETIC embeddings.

The real run needs resemblyzer + the owner's corpus (Jetson only). What is pinned here is the harness LOGIC, which is
what makes a real run trustworthy: the simulation uses the real policy, adaptation raises a drifted owner's scores and
not an impostor's, the poisoning arms hold the bound, and the script refuses to write embeddings inside the repo.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

import speaker_adaptation as spk

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "perf" / "measure_speaker_adapt.py"
_spec = importlib.util.spec_from_file_location("measure_speaker_adapt_under_test", _SCRIPT)
measure = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = measure       # @dataclass resolves string annotations through sys.modules
_spec.loader.exec_module(measure)

DIM = 256
T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def _world(seed=0, n_owner=240, n_imp=60):
    """An owner whose everyday voice has drifted from the enrolled one, plus unrelated voices."""
    rng = np.random.default_rng(seed)
    base = _unit(rng.standard_normal(DIM))
    perp = rng.standard_normal(DIM)
    perp = _unit(perp - np.dot(perp, base) * base)
    enrolled_centre = base
    everyday_centre = _unit(0.93 * base + np.sqrt(1 - 0.93 ** 2) * perp)   # drifted ~21 degrees
    owner = [_unit(everyday_centre + 0.024 * rng.standard_normal(DIM)) for _ in range(n_owner)]   # noise norm ~0.38
    imps = [_unit(rng.standard_normal(DIM)) for _ in range(n_imp)]
    # a few impostors that are genuinely close to the owner's region (a relative)
    imps += [_unit(0.55 * everyday_centre + 0.83 * _unit(rng.standard_normal(DIM))) for _ in range(20)]
    times = [T0 + timedelta(hours=7 * i) for i in range(n_owner)]
    return enrolled_centre, owner, times, imps


def test_enroll_anchor_matches_the_enrol_route_maths():
    a = measure.enroll_anchor([np.array([1, 0, 0], np.float32), np.array([0, 1, 0], np.float32)])
    assert np.allclose(a, [0.70710678, 0.70710678, 0.0], atol=1e-5)


def test_adaptation_raises_genuine_scores_and_not_impostor_scores():
    anchor, owner, times, imps = _world()
    cfg = spk.AdaptConfig(min_score=0.80, anchor_floor=0.75)    # the synthetic voice sits lower than a real one
    rep = measure.summarise(owner, times, [4.0] * len(owner), imps, anchor, cfg)
    pre, ho = rep["prequential"], rep["holdout"]
    assert pre["applied"] > 5
    assert pre["adapted"]["mean"] > pre["anchor_only"]["mean"]
    assert ho["adapted"]["mean"] > ho["anchor_only"]["mean"] + 0.02
    assert ho["adapted"]["p10"] > ho["anchor_only"]["p10"]
    # impostors: the adapted profile must not be closer to them than the enrolled one (beyond noise)
    assert rep["impostors"]["mean_delta"] < 0.05
    assert rep["impostors"]["adapted"]["ge_0.85"] == 0.0
    # the offered-as-owner impostors are refused (random voices never clear the strict bar)
    assert rep["impostors"]["offered_as_owner"]["applied"] == 0


def test_poisoning_arms_hold_the_bound_and_all_guards_stop_impostors():
    anchor, owner, times, imps = _world()
    cfg = spk.AdaptConfig()
    out = measure.poison(anchor, imps, cfg, rounds=600)
    assert out["impostors_all_guards_on"]["applied"] == 0
    for label in ("impostors_only_drift_bound", "synthetic_adversary_only_drift_bound"):
        assert out[label]["bound_held"], label
    assert out["synthetic_adversary_only_drift_bound"]["reasons"].get("drift_bound", 0) > 0   # it hit the wall
    assert out["synthetic_adversary_only_drift_bound"]["final_cos_to_anchor"] >= cfg.min_anchor_cos - 1e-6


def test_simulation_honours_the_rate_limit():
    anchor, owner, times, _ = _world(n_owner=120)
    cfg = spk.AdaptConfig(min_score=0.80, anchor_floor=0.75, max_per_day=2, min_interval_s=0)
    fast_times = [T0 + timedelta(minutes=30 * i) for i in range(len(owner))]       # ~48 clips a day
    r = measure.simulate(owner, fast_times, [4.0] * len(owner), anchor, cfg=cfg)
    days = (fast_times[-1] - fast_times[0]).days + 1
    assert r.applied <= 2 * days
    assert r.reasons.get("rate_daily", 0) > 0


def test_script_refuses_to_cache_embeddings_inside_the_repo(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("sys.argv", ["x", "--cache", str(Path(measure._REPO) / "emb.npz")])
    assert measure.main() == 2
    assert "inside the repo" in capsys.readouterr().err


def test_mem_guard_blocks_the_encoder_load(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(measure, "_mem_available_mb", lambda: 100.0)
    monkeypatch.setattr(measure, "LOCK_PATH", str(tmp_path / "lock"))
    monkeypatch.setattr("sys.argv", ["x", "--corpus", str(tmp_path), "--cache", str(tmp_path / "nope.npz")])
    assert measure.main() == 3
    assert "MemAvailable" in capsys.readouterr().err
