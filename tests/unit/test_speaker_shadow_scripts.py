"""Speaker-gate rebuild step 1 — the Pi-side embed + eval scripts (no model, no audio of value).

``scripts/voice/speaker_shadow_embed.py`` and ``speaker_shadow_eval.py`` run on the Pi
over the replay corpus; nothing here loads an embedder or touches a household clip. The
embedder is a fake, the corpus is a handful of synthetic tones, and the "embeddings" the
eval sees are planted Gaussian clusters with known geometry.

What is pinned (docs/research/speaker-gate-rebuild-2026-10-04.md §5):

* **Aggregates only / no biometrics out**: the report cannot carry a clip id; the
  manifest keys clips by a hash of the relative path, never the name; every artefact is
  written 0600 in a 0700 directory.
* **Licence rule**: a non-permissive model is refused unless explicitly allowed; a pinned
  digest mismatch is refused; a download lands only under the models dir.
* **Daemon guard**: a health failure streak or an uptime reset aborts the run with partial
  results saved (exit 3); a wall-clock deadline ends it (exit 4).
* **Instruments (verify your instruments)**: planted geometry -> the metrics recover it;
  a label shuffle gives EER ~ 50 % and the noise-embedding pipeline gives a bias floor; owner==impostor data FAILS the
  targets (the harness can go red); clusters planted at 2 recover at k=2.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import sys
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


emb_mod = _load("speaker_shadow_embed", "scripts/voice/speaker_shadow_embed.py")
ev_mod = _load("speaker_shadow_eval", "scripts/voice/speaker_shadow_eval.py")


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _write_wav(path: Path, seconds: float, rate: int = 16000, freq: float = 220.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(seconds * rate)) / rate
    pcm = (0.2 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


# ======================================================================== embed script


class FakeEmbedder:
    spec = {"rate": 16000, "dim": 8, "n_mels": 80}
    metadata = {"fake": "1"}
    ort_version = "0"

    def __init__(self, fail_over_s: float = 1e9):
        self.fail_over_s = fail_over_s
        self.calls = 0

    def embed(self, wave16):
        self.calls += 1
        if wave16.size / 16000.0 > self.fail_over_s:
            raise RuntimeError("boom")
        v = np.ones(8, dtype=np.float32) * (1 + self.calls)
        return v / np.linalg.norm(v), 1.0, 2.0


def _sidecar():
    return {"model_key": "fake", "file": "f.onnx", "sha256": "ab" * 32, "licence": "Apache-2.0", "dim": 8,
            "rate": 16000, "n_mels": 80}


def test_threads_are_hard_capped_at_two(monkeypatch):
    monkeypatch.setenv("SPEAKER_SHADOW_THREADS", "16")
    assert emb_mod._threads() == 2
    monkeypatch.setenv("SPEAKER_SHADOW_THREADS", "1")
    assert emb_mod._threads() == 1
    monkeypatch.setenv("SPEAKER_SHADOW_THREADS", "junk")
    assert emb_mod._threads() == 2


def test_fbank_shape_cmn_and_short_clip():
    x = (np.random.default_rng(0).standard_normal(16000) * 0.1).astype(np.float32)
    f = emb_mod.kaldi_fbank(x)
    assert f.shape == (1 + (16000 - 400) // 160, 80)
    assert np.allclose(f.mean(axis=0), 0.0, atol=1e-4)  # per-utterance mean normalisation
    assert emb_mod.kaldi_fbank(np.zeros(399, dtype=np.float32)).shape == (0, 80)


def test_mel_banks_are_a_valid_filterbank():
    fb = emb_mod.mel_banks()
    assert fb.shape == (80, 257)
    assert (fb >= 0).all() and (fb.max(axis=1) > 0).all()
    assert (fb[:, -1] == 0).all()  # Kaldi pads the Nyquist column with zero


def test_clip_id_is_a_hash_of_the_relative_path_not_the_name():
    cid = emb_mod.clip_id("quarantine-tv-falsewakes-20260719/123456_789.wav")
    assert len(cid) == 16 and all(c in "0123456789abcdef" for c in cid)
    assert "123456" not in cid
    assert cid == emb_mod.clip_id("quarantine-tv-falsewakes-20260719/123456_789.wav")
    assert emb_mod.clip_group("quarantine-tv-falsewakes-20260719/a.wav") == "tv"
    assert emb_mod.clip_group("quarantine-nonspeech-20260804/a.wav") == "nonspeech"
    assert emb_mod.clip_group("a.wav") == "top"


def _fake_opener(payload: bytes):
    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return lambda url: _R(payload)


def test_model_licence_rule_and_pin(tmp_path):
    reg = {
        "perm": {"file": "m.onnx", "url": "http://x/m.onnx", "sha256": None, "licence": "Apache-2.0",
                 "source": "s", "dim": 4, "rate": 16000, "n_mels": 80},
        "ccby": {"file": "c.onnx", "url": "http://x/c.onnx", "sha256": None, "licence": "CC-BY-4.0",
                 "source": "s", "dim": 4, "rate": 16000, "n_mels": 80},
    }
    models = tmp_path / "models"
    with pytest.raises(emb_mod.ModelRefused, match="not permissive"):
        emb_mod.resolve_model("ccby", models, registry=reg, opener=_fake_opener(b"x"))
    assert not (models / "c.onnx").exists()  # refused BEFORE any download
    with pytest.raises(emb_mod.ModelRefused, match="unknown"):
        emb_mod.resolve_model("nope", models, registry=reg)
    # lab-only override works and is recorded as non-permissive
    _, side = emb_mod.resolve_model("ccby", models, registry=reg, opener=_fake_opener(b"cc"), allow_nonpermissive=True)
    assert side["licence_permissive"] is False
    # first download is trust-on-first-use, private, with a sidecar carrying the observed digest
    path, side = emb_mod.resolve_model("perm", models, registry=reg, opener=_fake_opener(b"model-bytes"))
    assert _mode(path) == 0o600 and _mode(models) == 0o700
    assert side["sha256"] == emb_mod.sha256_file(path) and side["pinned_in_registry"] is False
    assert json.loads(path.with_suffix(".onnx.json").read_text())["licence"] == "Apache-2.0"
    # a pinned digest that does not match the file on disk is refused
    reg["perm"]["sha256"] = "0" * 64
    with pytest.raises(emb_mod.ModelRefused, match="pinned"):
        emb_mod.resolve_model("perm", models, registry=reg)
    # a download that does not match the pinned digest is discarded, never left on disk
    reg["perm"]["sha256"] = emb_mod.sha256_file(path)
    with pytest.raises(emb_mod.ModelRefused, match="discarded"):
        emb_mod.resolve_model("perm", tmp_path / "fresh", registry=reg, opener=_fake_opener(b"tampered"))
    assert not list((tmp_path / "fresh").glob("*"))
    # missing file + downloads disabled
    with pytest.raises(emb_mod.ModelRefused, match="disabled"):
        emb_mod.resolve_model("perm", tmp_path / "other", registry=reg, download=False)


def test_registry_candidate_is_permissive_and_pinned():
    spec = emb_mod.MODELS["campplus_en_voxceleb"]
    assert spec["licence"] in emb_mod.PERMISSIVE
    assert spec["sha256"] and len(spec["sha256"]) == 64


def test_resample_poly_path_when_scipy_present():
    pytest.importorskip("scipy")
    x = (np.random.default_rng(1).standard_normal(24000) * 0.1).astype(np.float32)
    y = emb_mod.resample(x, 24000, 16000)
    assert abs(y.size - 16000) <= 1
    assert emb_mod.resample(x, 16000, 16000) is x


def _corpus(root: Path) -> None:
    _write_wav(root / "a.wav", 1.0)
    _write_wav(root / "b.wav", 2.0)
    _write_wav(root / "tiny.wav", 0.3)  # < 0.5 s -> skipped AND counted
    _write_wav(root / "huge.wav", 4.0)  # the fake raises on this one -> counted as error
    _write_wav(root / "quarantine-tv-falsewakes-20260719" / "t.wav", 1.5)
    _write_wav(root / "quarantine-nonspeech-20260804" / "n.wav", 1.5)


def test_run_embed_counts_skips_writes_private_artifacts_and_leaks_no_names(tmp_path):
    corpus, out = tmp_path / "corpus", tmp_path / "shadow"
    _corpus(corpus)
    code, man = emb_mod.run_embed(corpus, out, FakeEmbedder(fail_over_s=3.0), sidecar=_sidecar(), warmup=2)
    assert code == 0 and man["complete"] is True
    assert man["counts"] == {"ok": 4, "short": 1, "error": 1}
    assert man["n_files_seen"] == 6
    assert _mode(out) == 0o700
    assert _mode(out / "embeddings.npz") == 0o600 and _mode(out / "manifest.json") == 0o600
    emb = np.load(out / "embeddings.npz")["emb"]
    assert emb.shape == (4, 8) and np.allclose(np.linalg.norm(emb, axis=1), 1.0, atol=1e-5)
    assert sorted(man["emb_row_of_clip"].values()) == [0, 1, 2, 3]
    blob = (out / "manifest.json").read_text()
    for name in ("a.wav", "tiny", "huge", "falsewakes", "t.wav", "n.wav"):
        assert name not in blob  # ids are hashes; no file name or quarantine-dir name survives
    groups = sorted(c["group"] for c in man["clips"])
    assert groups == ["nonspeech", "top", "top", "top", "top", "tv"]
    ok = [c for c in man["clips"] if c["status"] == "ok"]
    assert sum(bool(c["warmup"]) for c in ok) == 2  # first two inferences excluded from latency stats
    assert all(c["compute_ms"] == 3.0 for c in ok)
    assert man["peak_rss_mb"] > 0 and man["threads"] <= 2


def test_daemon_guard_trips_on_failure_streak_and_on_uptime_reset():
    t = [0.0]
    seq = iter([{"status": "ok", "uptime_s": 100}, {"status": "ok", "uptime_s": 200}, {"status": "ok", "uptime_s": 5}])
    g = emb_mod.DaemonGuard("u", 10.0, fetch=lambda u: next(seq), clock=lambda: t[0])
    assert g.poll() is None          # first poll runs
    assert g.poll() is None          # inside the period: no fetch
    t[0] = 11.0
    assert g.poll() is None and g.last_uptime == 200.0
    t[0] = 22.0
    assert "restart" in g.poll() and g.restarts_seen == 1

    def boom(u):
        raise OSError("down")

    t2 = [0.0]
    g2 = emb_mod.DaemonGuard("u", 1.0, fetch=boom, clock=lambda: t2[0])
    results = []
    for i in range(4):
        t2[0] = float(i * 2)
        results.append(g2.poll())
    assert results[0] is None and results[1] is None and results[2] and "health failed" in results[2]
    assert emb_mod.DaemonGuard("u", 0.0, fetch=boom).poll() is None  # disabled


def test_run_embed_aborts_on_guard_and_deadline_keeping_partials(tmp_path):
    corpus = tmp_path / "corpus"
    _corpus(corpus)

    class OneShotGuard:
        checks, restarts_seen = 1, 1

        def __init__(self):
            self.n = 0

        def poll(self):
            self.n += 1
            return "daemon uptime went backwards (restart)" if self.n == 3 else None

    code, man = emb_mod.run_embed(corpus, tmp_path / "o1", FakeEmbedder(), sidecar=_sidecar(), guard=OneShotGuard())
    assert code == 3 and man["complete"] is False and "restart" in man["abort_reason"]
    assert man["counts"]["ok"] == 2 and (tmp_path / "o1" / "embeddings.npz").exists()  # partials saved
    assert man["daemon_restarts_seen"] == 1
    code, man = emb_mod.run_embed(corpus, tmp_path / "o2", FakeEmbedder(), sidecar=_sidecar(),
                                  deadline=100.0, now=lambda: 200.0)
    assert code == 4 and man["abort_reason"] == "deadline reached" and man["counts"] == {}


def test_deadline_rolls_to_the_next_day_when_already_past():
    import datetime as dt

    base = dt.datetime(2026, 10, 5, 3, 45).timestamp()
    assert emb_mod._deadline_epoch("04:10", now=base) == dt.datetime(2026, 10, 5, 4, 10).timestamp()
    assert emb_mod._deadline_epoch("03:30", now=base) == dt.datetime(2026, 10, 6, 3, 30).timestamp()
    assert emb_mod._deadline_epoch(None) is None


# ========================================================================= eval script

DIM = 64


def _unit(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def _planted(rng, n_owner=240, n_b=60, n_tv=5, owner_noise=0.9, same=False):
    """Owner clips around u, cluster B around v, TV clips around w; sessions via mtimes."""
    u, v, w = _unit(rng.standard_normal((3, DIM)))
    if same:
        v = w = u
    def cloud(c, n, s):
        return _unit(c + s * rng.standard_normal((n, DIM)) / np.sqrt(DIM))
    emb = np.vstack([cloud(u, n_owner, owner_noise * 0.5), cloud(v, n_b, owner_noise * 0.5), cloud(w, n_tv, 0.3)])
    groups = ["top"] * (n_owner + n_b) + ["tv"] * n_tv
    dur = rng.uniform(0.6, 9.0, emb.shape[0])
    # ten capture days, 20+ clips each, hours apart within a day; B interleaved in time
    mt = np.concatenate([
        (rng.integers(0, 10, n_owner + n_b) * 86400.0 + rng.uniform(0, 3600, n_owner + n_b)),
        np.full(n_tv, 5 * 86400.0)])
    rms = rng.normal(-30, 6, emb.shape[0])
    return emb, groups, dur, mt, rms


def _latency(p50=111.0):
    return {"n": 10, "p50_ms": p50, "p95_ms": p50 * 2}


def test_eer_and_operating_points_recover_known_gaussians():
    rng = np.random.default_rng(3)
    tgt, non = rng.normal(0.6, 0.1, 20000), rng.normal(0.2, 0.1, 20000)
    e, thr = ev_mod.eer(tgt, non)
    assert abs(e - 0.0228) < 0.006 and abs(thr - 0.4) < 0.02  # Phi(-2)
    assert ev_mod.eer(np.array([1.0, 0.9]), np.array([0.1, 0.0]))[0] == 0.0
    assert abs(ev_mod.eer(non, rng.normal(0.2, 0.1, 20000))[0] - 0.5) < 0.02
    t = ev_mod.threshold_for_far(tgt, non, 0.01)
    far, frr = ev_mod.far_frr(tgt, non, t)
    assert far <= 0.01 and abs(far - 0.01) < 0.003
    t10 = ev_mod.threshold_for_frr(tgt, 0.10)
    assert abs(ev_mod.far_frr(tgt, non, t10)[1] - 0.10) < 0.002
    assert np.isnan(ev_mod.eer(np.zeros(0), non)[0])


def test_kmeans_recovers_two_planted_clusters_and_silhouette_discriminates():
    rng = np.random.default_rng(5)
    a, b = _unit(rng.standard_normal((2, DIM)))
    x = np.vstack([_unit(a + 0.3 * rng.standard_normal((80, DIM)) / 8), _unit(b + 0.3 * rng.standard_normal((40, DIM)) / 8)])
    lab, _ = ev_mod.spherical_kmeans(x, 2, np.random.default_rng(1))
    assert len({tuple(lab[:80])}) == 1 and set(lab[:80]) != set(lab[80:])
    assert ev_mod.silhouette_cosine(x, lab) > 0.5
    blob = _unit(a + 0.3 * rng.standard_normal((120, DIM)) / 8)
    lab_b, _ = ev_mod.spherical_kmeans(blob, 2, np.random.default_rng(1))
    assert ev_mod.silhouette_cosine(blob, lab_b) < ev_mod.silhouette_cosine(x, lab) / 2  # one blob is not two clusters


def test_session_folds_never_split_a_session():
    rng = np.random.default_rng(7)
    days = rng.integers(0, 12, 300)
    mt = days * 86400.0 + rng.uniform(0, 600, 300)
    fold, n_sessions = ev_mod.session_folds(mt, 5, 1800.0)
    assert n_sessions == len(set(days))
    for d in set(days):
        assert len(set(fold[days == d])) == 1
    assert set(fold) == set(range(5))
    # fewer sessions than folds -> contiguous time blocks instead
    f2, n2 = ev_mod.session_folds(np.arange(50.0), 5, 1800.0)
    assert n2 == 1 and set(f2) == set(range(5))


def test_evaluate_recovers_planted_geometry_and_controls_pass():
    rng = np.random.default_rng(11)
    emb, groups, dur, mt, rms = _planted(rng)
    rep = ev_mod.evaluate(emb, groups, dur, mt, rms, _latency(), boot=40)
    cl = rep["cluster"]
    assert cl["best_k_by_silhouette"] == 2 and cl["k2_sizes"]["A"] == 240 and cl["k2_sizes"]["B"] == 60
    assert cl["tv_in_B"] == 5 and cl["tv_in_A"] == 0
    ev = rep["eval"]
    assert ev["separation"]["owner_median_minus_runner_up_median"] > 0.3
    assert ev["at_far1pct_pool"]["frr"] < 0.05 and ev["at_far1pct_pool"]["tv_accepted"] == 0 and ev["zero_tv_accept_point"]["frr"] < 0.05
    assert ev["eer_pool"] < 0.02 and ev["control_ok"] is True
    assert 0.35 < ev["control_label_shuffle_eer"] < 0.65
    # selection-bias floor: the same pipeline on pure noise still scores > 0 (A/B is cut from the data
    # it is scored in), and the planted result must sit under it
    assert ev["control_noise_embeddings_eer"] > 0.02 and ev["eer_pool_below_noise_floor"] is True
    assert ev["n"]["tv"] == 5 and ev["n"]["owner"] == 240
    assert all(v["pass"] for v in rep["targets_result"].values())
    assert ev["enrol_size_curve"]["24"]["frr_at_op_mean"] <= ev["enrol_size_curve"]["1"]["frr_at_op_mean"]
    for name in ("<2s", "2-5s", ">5s"):
        assert name in ev["by_duration"]


def test_evaluate_goes_red_when_owner_and_impostors_are_the_same_voice():
    """Negative control: with no real separation the harness must FAIL the targets."""
    rng = np.random.default_rng(13)
    emb, groups, dur, mt, rms = _planted(rng, same=True)
    rep = ev_mod.evaluate(emb, groups, dur, mt, rms, _latency(), boot=0)
    t = rep["targets_result"]
    assert t["separation"]["pass"] is False
    assert not (t["frr_at_far1"]["pass"] and t["far_at_frr10"]["pass"] and t["tv_accepts"]["pass"])
    assert rep["eval"]["eer_pool"] > 0.25


def test_latency_gate_is_part_of_the_verdict():
    rng = np.random.default_rng(11)
    emb, groups, dur, mt, rms = _planted(rng)
    slow = ev_mod.evaluate(emb, groups, dur, mt, rms, _latency(p50=450.0), boot=0)
    assert slow["targets_result"]["latency_p50"]["pass"] is False


def test_latency_summary_excludes_warmup_and_errors():
    clips = [
        {"status": "ok", "warmup": True, "compute_ms": 9999.0, "duration_s": 1.0, "fbank_ms": 1, "infer_ms": 9998},
        {"status": "error"}, {"status": "short"},
    ] + [
        {"status": "ok", "warmup": False, "compute_ms": 100.0 + i, "duration_s": 1.0 + i, "fbank_ms": 4.0, "infer_ms": 96.0 + i}
        for i in range(10)
    ]
    s = ev_mod.latency_summary(clips)
    assert s["n"] == 10 and 100.0 <= s["p50_ms"] <= 110.0 and s["max_ms"] == 109.0
    assert s["by_duration"]["<2s"]["n"] == 1 and s["ms_per_second_of_audio"] == pytest.approx(1.0, abs=0.05)
    assert ev_mod.latency_summary([])["p50_ms"] is None


def test_report_is_aggregate_only_and_the_guard_catches_a_leak():
    rng = np.random.default_rng(17)
    emb, groups, dur, mt, rms = _planted(rng)
    rep = ev_mod.evaluate(emb, groups, dur, mt, rms, _latency(), boot=0)
    manifest = {"clips": [{"id": "deadbeefdeadbeef"}, {"id": "0123456789abcdef"}]}
    ev_mod.assert_aggregate_only(rep, manifest)  # clean
    blob = json.dumps(rep)
    assert "emb" not in rep and '"clips"' not in blob
    leaky = dict(rep, note="see 0123456789abcdef")
    with pytest.raises(AssertionError, match="clip id"):
        ev_mod.assert_aggregate_only(leaky, manifest)
    with pytest.raises(AssertionError, match="'clips'"):
        ev_mod.assert_aggregate_only(dict(rep, clips=[1]), manifest)


def test_embed_then_eval_end_to_end_on_a_synthetic_shadow_dir(tmp_path):
    """embed (fake model) -> manifest/npz -> eval CLI -> report files: private, aggregate-only."""
    corpus, shadow = tmp_path / "corpus", tmp_path / "shadow"
    rng = np.random.default_rng(23)
    emb, groups, dur, mt, rms = _planted(rng, n_owner=60, n_b=20, n_tv=5)
    corpus.mkdir()
    for i, g in enumerate(groups):
        sub = corpus / "quarantine-tv-falsewakes-20260719" if g == "tv" else corpus
        _write_wav(sub / f"{i:04d}.wav", 1.0)
        os.utime(sub / f"{i:04d}.wav", (mt[i], mt[i]))
    by_name = {}

    class Planted(FakeEmbedder):
        spec = {"rate": 16000, "dim": DIM, "n_mels": 80}

        def embed(self, wave16):
            v = emb[len(by_name)]
            by_name[len(by_name)] = 1
            return v.astype(np.float32), 1.0, 2.0

    # embed order is the sorted-path order: top clips then the tv subdir — same as `groups`
    sc = dict(_sidecar(), dim=DIM)
    code, man = emb_mod.run_embed(corpus, shadow, Planted(), sidecar=sc)
    assert code == 0 and man["counts"]["ok"] == len(groups)
    assert ev_mod.main(["--shadow-dir", str(shadow)]) == 0
    for name in ("report.json", "report.md"):
        assert _mode(shadow / name) == 0o600
    report = json.loads((shadow / "report.json").read_text())
    text = (shadow / "report.md").read_text()
    for c in man["clips"]:
        assert c["id"] not in json.dumps(report) and c["id"] not in text
    assert report["counts"]["tv"] == 5 and report["run"]["complete"] is True
    assert "PASS" in text and report["eval"]["control_ok"] is True


def test_incumbent_baseline_is_registered_permissive_and_uses_the_installed_package():
    spec = emb_mod.MODELS["resemblyzer_baseline"]
    assert spec["kind"] == "resemblyzer" and spec["licence"] in emb_mod.PERMISSIVE and spec["dim"] == 256
    assert spec["url"] is None  # never downloaded: weights come from the installed package
    assert callable(emb_mod.make_embedder)


def test_resemblyzer_branch_hashes_the_package_weights_without_touching_them(tmp_path, monkeypatch):
    pkg = tmp_path / "site" / "resemblyzer"
    pkg.mkdir(parents=True)
    weights = pkg / "pretrained.pt"
    weights.write_bytes(b"w" * 64)
    weights.chmod(0o644)
    monkeypatch.setattr(emb_mod, "_resemblyzer_weights", lambda: weights)
    with pytest.raises(emb_mod.ModelRefused, match="pinned"):  # the real digest is pinned: fake weights are refused
        emb_mod.resolve_model("resemblyzer_baseline", tmp_path / "models")
    reg = {"resemblyzer_baseline": dict(emb_mod.MODELS["resemblyzer_baseline"], sha256=None)}
    path, side = emb_mod.resolve_model("resemblyzer_baseline", tmp_path / "models", registry=reg)
    assert path == weights and side["sha256"] == emb_mod.sha256_file(weights) and side["licence_permissive"] is True
    assert _mode(weights) == 0o644  # the venv's own file is hashed, never chmod-ed
    assert (tmp_path / "models" / "resemblyzer_baseline.json").exists()


def test_compare_spaces_agrees_when_the_audio_structure_is_real_and_not_when_it_is_noise():
    rng = np.random.default_rng(31)
    emb, groups, dur, mt, rms = _planted(rng, n_owner=120, n_b=40, n_tv=5)
    # a second, independent "embedder": a random rotation of the same structure + new noise
    q, _ = np.linalg.qr(rng.standard_normal((DIM, DIM)))
    emb2 = _unit(emb @ q + 0.05 * rng.standard_normal(emb.shape))
    same = ev_mod.compare_spaces(emb, emb2, groups, mt)
    assert same["A_membership_agreement"] > 0.95 and same["A_jaccard"] > 0.9
    assert same["tv_accepted_at_own_far1_threshold"]["tv_total"] == 5
    # labels made by the OTHER space still separate the audio when the structure is real ...
    assert same["cross_labels"]["space1_scored_with_space2_labels"]["eer_pool"] < 0.05
    assert same["consensus"]["space2"]["frr_at_far1"] < 0.05
    noise = ev_mod.compare_spaces(emb, _unit(rng.standard_normal(emb.shape)), groups, mt)
    # ... and do NOT when the second space is noise (negative control: the harness can go red)
    assert noise["A_membership_agreement"] < 0.8
    assert noise["cross_labels"]["space1_scored_with_space2_labels"]["eer_pool"] > 0.15
    assert same["homogeneity_of_A"]["A1_seen_by_space2"]["z"] > 10  # a real set stays coherent in a different space
    assert abs(noise["homogeneity_of_A"]["A1_seen_by_space2"]["z"]) < 5  # a model-specific cut does not
    assert same["p_A2_given"]["A1"] > same["p_A2_given"]["B1"]


def test_nonspeech_attractor_and_fixed_thresholds_are_reported():
    """A gate that scores near-silence like the owner must be visible in the report (min-evidence rule)."""
    rng = np.random.default_rng(41)
    emb, groups, dur, mt, rms = _planted(rng)
    owner_dir = _unit(emb[np.array(groups) == "top"][:200].mean(axis=0))
    ns = _unit(owner_dir + 0.1 * rng.standard_normal((8, DIM)) / np.sqrt(DIM))
    emb2 = np.vstack([emb, ns])
    groups2 = list(groups) + ["nonspeech"] * 8
    dur2, mt2, rms2 = (np.concatenate([a, b]) for a, b in ((dur, np.full(8, 2.0)), (mt, np.full(8, 3 * 86400.0)), (rms, np.full(8, -54.0))))
    rep = ev_mod.evaluate(emb2, groups2, dur2, mt2, rms2, _latency(), boot=0, fixed_thresholds=(0.5, 0.9))
    n = rep["eval"]["nonspeech_vs_owner"]
    assert n["n"] == 8 and n["accepted_at_operating_threshold"] >= 6
    assert n["mean_pairwise_cosine"] > 0.8 and n["mean_vector_cosine_to_A_centroid"] > 0.8
    assert n["mean_vector_cosine_to_B_centroid"] < n["mean_vector_cosine_to_A_centroid"]
    fx = rep["eval"]["at_fixed_thresholds"]
    assert set(fx) == {"0.50", "0.90"} and fx["0.50"]["frr"] <= fx["0.90"]["frr"]
    assert fx["0.50"]["nonspeech_accepted"] >= fx["0.90"]["nonspeech_accepted"]
    assert rep["eval"]["tv_diagnostics"]["accepted_at_operating_threshold"] == 0
