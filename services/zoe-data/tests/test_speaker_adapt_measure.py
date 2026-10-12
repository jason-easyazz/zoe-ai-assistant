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


# ── the embedding cache: bound to its inputs, memory-floored on any miss, owner-only ─────────────────────────────
# (review threads on #2002: PRRT_kwDOPZpkks6rcSRy, PRRT_kwDOPZpkks6rcSR4, PRRT_kwDOPZpkks6rcSSB)

import os
import stat


def _wavs(d: Path, n: int, size: int = 100) -> list:
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        p = d / f"c{i:03d}.wav"
        p.write_bytes(bytes([i % 251]) * size)
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
        out.append(p)
    return out


@pytest.fixture
def fake_embed(monkeypatch):
    """Replace the encoder: deterministic unit embeddings per file name, and a call counter."""
    calls = []

    def fake(paths, *, limit=None):
        calls.append(len(list(paths)[:limit]))
        rows = []
        for p in list(paths)[:limit]:
            rng = np.random.default_rng(sum(p.name.encode()))
            rows.append((p, p.stat().st_mtime, _unit(rng.standard_normal(DIM)), 4.0))
        return rows

    monkeypatch.setattr(measure, "embed_dir", fake)
    monkeypatch.setattr(measure, "_ENCODER_ID", "enc-A")
    return calls


def test_cache_hits_only_when_every_input_file_is_unchanged(tmp_path, fake_embed):
    files, cache = _wavs(tmp_path / "c", 12), tmp_path / "emb.npz"
    measure._load_or_embed("owner", files, cache, None)
    assert fake_embed == [12]
    measure._load_or_embed("owner", files, cache, None)
    assert fake_embed == [12]                                   # hit: no second encode
    # same count, one clip replaced by a different-sized file -> miss
    files[3].write_bytes(b"\x07" * 101)
    os.utime(files[3], (1_700_000_003, 1_700_000_003))
    measure._load_or_embed("owner", files, cache, None)
    assert fake_embed == [12, 12]
    # same size, only mtime moved -> miss
    os.utime(files[5], (1_800_000_000, 1_800_000_000))
    measure._load_or_embed("owner", files, cache, None)
    assert fake_embed == [12, 12, 12]
    # same everything but one file renamed -> miss
    files[7] = files[7].rename(files[7].with_name("zz.wav"))
    measure._load_or_embed("owner", files, cache, None)
    assert fake_embed == [12, 12, 12, 12]
    # a different encoder (weights) is a different embedding space -> miss
    measure._load_or_embed("owner", files, cache, None)
    assert fake_embed == [12, 12, 12, 12]
    measure._ENCODER_ID = "enc-B"
    measure._load_or_embed("owner", files, cache, None)
    assert fake_embed == [12, 12, 12, 12, 12]
    # a different --limit is a different input set -> miss
    measure._load_or_embed("owner", files, cache, 5)
    assert fake_embed[-1] == 5


def test_a_count_only_cache_from_the_old_format_is_a_miss(tmp_path, fake_embed):
    files, cache = _wavs(tmp_path / "c", 12), tmp_path / "emb.npz"
    np.savez(cache, owner_emb=np.zeros((12, DIM), np.float32), owner_mtime=np.zeros(12),
             owner_speech=np.zeros(12), owner_n=np.array(12))
    os.chmod(cache, 0o600)
    measure._load_or_embed("owner", files, cache, None)
    assert fake_embed == [12]


def test_needs_encoder_is_decided_per_tag_and_per_file(tmp_path, fake_embed):
    owner, imp0 = _wavs(tmp_path / "o", 12), _wavs(tmp_path / "i0", 4)
    cache = tmp_path / "emb.npz"
    specs = [("owner", owner, None), ("imp0", imp0, None)]
    assert measure.needs_encoder(specs, cache) is True            # no cache at all
    measure._load_or_embed("owner", owner, cache, None)
    assert measure.needs_encoder(specs, cache) is True            # PARTIAL: owner cached, imp0 not
    measure._load_or_embed("imp0", imp0, cache, None)
    assert measure.needs_encoder(specs, cache) is False           # fully covered
    imp0[1].write_bytes(b"changed")
    assert measure.needs_encoder(specs, cache) is True            # one edited clip is enough


def _run_main(monkeypatch, tmp_path, corpus, imps, cache, avail):
    monkeypatch.setattr(measure, "_mem_available_mb", lambda: avail)
    monkeypatch.setattr(measure, "LOCK_PATH", str(tmp_path / "lock"))
    argv = ["x", "--corpus", str(corpus), "--cache", str(cache)]
    for d in imps:
        argv += ["--impostors", str(d)]
    monkeypatch.setattr("sys.argv", argv)
    return measure.main()


def test_memory_floor_applies_to_a_partial_cache_miss(monkeypatch, tmp_path, fake_embed, capsys):
    owner = _wavs(tmp_path / "o", 30)
    imp0, imp1 = _wavs(tmp_path / "i0", 4), _wavs(tmp_path / "i1", 4)
    cache = tmp_path / "emb.npz"
    monkeypatch.setattr(measure, "_mem_available_mb", lambda: 9999.0)
    measure._load_or_embed("owner", owner, cache, None)
    measure._load_or_embed("imp0", imp0, cache, None)
    fake_embed.clear()
    # the cache exists and covers owner + imp0, but a NEW impostor arm (imp1) is requested on a starved box
    rc = _run_main(monkeypatch, tmp_path, tmp_path / "o", [tmp_path / "i0", tmp_path / "i1"], cache, avail=100.0)
    assert rc == 3 and fake_embed == []
    assert "MemAvailable" in capsys.readouterr().err
    # a fully covered cache needs no encoder, so a starved box is fine
    measure._load_or_embed("imp1", imp1, cache, None)
    assert _run_main(monkeypatch, tmp_path, tmp_path / "o", [tmp_path / "i0", tmp_path / "i1"], cache,
                     avail=100.0) == 0
    assert fake_embed == [4]


def test_embed_dir_itself_enforces_the_floor_before_importing_the_encoder(monkeypatch, tmp_path):
    monkeypatch.setattr(measure, "_mem_available_mb", lambda: 100.0)
    monkeypatch.setattr(measure, "MIN_AVAIL_MB", 1500.0)
    with pytest.raises(measure.HeadroomError):
        measure.embed_dir(_wavs(tmp_path / "c", 2))               # would import resemblyzer/torch if it got past


def test_cache_is_written_owner_only_even_under_a_permissive_umask(tmp_path, fake_embed):
    files, cache = _wavs(tmp_path / "c", 12), tmp_path / "emb.npz"
    old = os.umask(0)
    try:
        measure._load_or_embed("owner", files, cache, None)
    finally:
        os.umask(old)
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir() if ".tmp" in p.name] == []     # atomic: no temp left behind


def test_rewriting_an_existing_cache_leaves_it_owner_only(tmp_path, fake_embed):
    files, cache = _wavs(tmp_path / "c", 12), tmp_path / "emb.npz"
    measure._load_or_embed("owner", files, cache, None)
    os.chmod(cache, 0o600)
    files[0].write_bytes(b"x")                                  # force a rewrite
    old = os.umask(0)
    try:
        measure._load_or_embed("owner", files, cache, None)
    finally:
        os.umask(old)
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660, 0o666, 0o700 | 0o010])
def test_a_group_or_world_readable_cache_is_refused(tmp_path, fake_embed, capsys, monkeypatch, mode):
    owner = _wavs(tmp_path / "o", 30)
    cache = tmp_path / "emb.npz"
    measure._load_or_embed("owner", owner, cache, None)
    os.chmod(cache, mode)
    with pytest.raises(measure.CacheError):
        measure._read_cache(cache)
    fake_embed.clear()
    assert _run_main(monkeypatch, tmp_path, tmp_path / "o", [], cache, avail=9999.0) == 2
    assert "not private" in capsys.readouterr().err
    assert fake_embed == []                                     # refused BEFORE anything was embedded or rewritten


def test_a_failed_write_leaves_the_old_cache_intact_and_no_temp(tmp_path, fake_embed, monkeypatch):
    files, cache = _wavs(tmp_path / "c", 12), tmp_path / "emb.npz"
    measure._load_or_embed("owner", files, cache, None)
    before = cache.read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(measure.np, "savez", boom)
    files[0].write_bytes(b"x")
    with pytest.raises(OSError):
        measure._load_or_embed("owner", files, cache, None)
    assert cache.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir() if ".tmp" in p.name] == []
