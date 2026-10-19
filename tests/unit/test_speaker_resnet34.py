"""WeSpeaker ResNet34 on the panel (``scripts/setup/zoe_speaker_resnet34.py`` + the daemon's flag-dark dual-run) - the logic, with FAKE
embedders: no ONNX Runtime, no model file, no audio, no network.

What is pinned, each with a negative control (the same call with the one guard flipped behaves the other way, so green means the GUARD works):

* cross-model: a profile of one model is NEVER scored against an embedding of another (both are 256-d, so shape cannot tell);
* the start-up self-test REFUSES on drift (cosine to the reference, or too slow), and the loader refuses a wrong file / a wrong graph level;
* ``dual`` acts ONLY on the current model: Resemblyzer's claim is what is attached, ResNet34 is logged beside it, never acted on;
* the 5-clip enrolment: a stranger's clip is dropped, re-asked, and never stored; audio never leaves the panel for ResNet34;
* thresholds by length group (2 s or less / longer);
* flag at its default: the module is not imported, the sync request and the shadow rows are exactly what they were.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SETUP = REPO / "scripts" / "setup"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rn = _load("zoe_speaker_resnet34", SETUP / "zoe_speaker_resnet34.py")        # the daemon imports it by this name
em = _load("zoe_voice_enroll_mode_rn34_test", SETUP / "zoe_voice_enroll_mode.py")
MODEL = rn.MODEL_ID
DIM = rn.EMBED_DIM


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def near(base: np.ndarray, cos: float, seed: int) -> np.ndarray:
    """A unit vector at cosine ``cos`` to ``base``."""
    o = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
    o -= (o @ base) * base
    o /= np.linalg.norm(o)
    return (cos * base + np.sqrt(1 - cos * cos) * o).astype(np.float32)


def b64(v) -> str:
    return base64.b64encode(np.asarray(v, dtype=np.float32).tobytes()).decode()


# -- a fake ONNX session: a fixed random projection of the clip's mean feature frame --------------------------------------

class FakeSession:
    def __init__(self, drift: float = 0.0, seed: int = 11):
        self.W = np.random.default_rng(seed).standard_normal((DIM, 80)).astype(np.float32)
        self.drift = drift
        self.calls = 0

    def get_inputs(self):
        return [types.SimpleNamespace(name="feats")]

    def get_outputs(self):
        return [types.SimpleNamespace(name="embs")]

    def run(self, outs, feed):
        self.calls += 1
        x = feed["feats"][0].mean(axis=0)
        v = self.W @ x
        if self.drift:
            v = v + self.drift * np.linalg.norm(v) * unit(99)        # what a wrong graph level / ORT upgrade does: nudge the embedding
        return [v[None]]


def artifacts_for(session: FakeSession, mu_seed: int = 5) -> dict:
    mu = (0.05 * unit(mu_seed)).tolist()
    probe = rn.Resnet34Embedder(session, dict(centre=dict(vector=mu), selftest=dict()))
    ref = probe.embed_raw(rn.synthetic_clip(3.0))
    return dict(model_id=MODEL, centre=dict(vector=mu), selftest=dict(reference=[float(x) for x in ref], seconds=3.0, expected_ms=100.0))


def make_embedder(drift_after: float = 0.0):
    clean = FakeSession()
    art = artifacts_for(clean)
    return rn.Resnet34Embedder(FakeSession(drift=drift_after), art), art


# -- embedding, centring, front end ------------------------------------------------------------------------------------

def test_embedding_is_the_cohort_centred_unit_vector_and_the_raw_one_is_not():
    emb, art = make_embedder()
    clip = rn.synthetic_clip(2.0)
    raw, centred = emb.embed_raw(clip), emb.embed(clip)
    mu = np.asarray(art["centre"]["vector"], dtype=np.float64)
    assert abs(float(np.linalg.norm(raw)) - 1) < 1e-5 and abs(float(np.linalg.norm(centred)) - 1) < 1e-5
    want = raw.astype(np.float64) - mu
    want /= np.linalg.norm(want)
    assert np.allclose(centred, want, atol=1e-5)
    assert rn.cosine(raw, centred) < 0.9999                   # centring really moved it (negative control: not a no-op)


def test_a_clip_under_ten_feature_frames_gives_no_embedding_rather_than_a_garbage_one():
    emb, _ = make_embedder()
    assert emb.embed(np.zeros(300, dtype=np.float32)) is None
    assert emb.embed(np.zeros(160 * 20 + 400, dtype=np.float32)) is not None


def _wav(samples: np.ndarray, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def test_wav_decoding_scales_to_unit_range_averages_channels_resamples_and_rejects_other_widths():
    pcm = (np.sin(np.arange(16000) / 20.0) * 12000).astype("<i2")
    x, secs = rn.wav_to_float(_wav(pcm))
    assert secs == pytest.approx(1.0) and x.dtype == np.float32 and np.allclose(x, pcm / 32768.0)
    stereo = np.stack([pcm, pcm // 2], axis=1).reshape(-1).astype("<i2")
    xs, _ = rn.wav_to_float(_wav(stereo, channels=2))
    assert np.allclose(xs, (pcm / 32768.0 + pcm // 2 / 32768.0) / 2, atol=1e-4)
    x8, secs8 = rn.wav_to_float(_wav(pcm[:8000], rate=8000))
    assert abs(len(x8) - 16000) <= 2 and secs8 == pytest.approx(1.0, abs=1e-3)
    with pytest.raises(ValueError):
        rn.wav_to_float(_wav(np.zeros(100, dtype="<i4"), width=4))


# -- the start-up self-test refuses on drift ----------------------------------------------------------------------------

def test_selftest_passes_when_the_embedder_reproduces_the_reference():
    emb, _ = make_embedder()
    report = rn.selftest(emb, clock=iter([0.0, 0.05]).__next__)
    assert report["cosine"] == pytest.approx(1.0, abs=1e-6) and report["ms"] == pytest.approx(50.0)


@pytest.mark.parametrize("drift", [0.02, 0.1, 0.3])
def test_selftest_refuses_on_embedding_drift(drift):
    drifted, _ = make_embedder(drift_after=drift)
    with pytest.raises(rn.SelfTestFailed, match="drift"):
        rn.selftest(drifted)
    # negative control: the same reference with a drift-free session passes
    clean, _ = make_embedder()
    assert rn.selftest(clean, clock=iter([0.0, 0.05]).__next__)["cosine"] >= rn.SELFTEST_MIN_COSINE      # fake clock: a loaded CI box must not flake this


def test_selftest_refuses_a_gross_slowdown_and_accepts_the_same_embedder_when_fast_enough():
    emb, _ = make_embedder()
    with pytest.raises(rn.SelfTestFailed, match="too slow"):
        rn.selftest(emb, clock=iter([0.0, 0.5]).__next__)        # 500 ms against an expected 100 ms x 3
    assert rn.selftest(emb, clock=iter([0.0, 0.25]).__next__)["ms"] == pytest.approx(250.0)


def test_selftest_refuses_without_a_reference():
    emb, art = make_embedder()
    emb.artifacts = dict(art, selftest=dict())
    with pytest.raises(rn.SelfTestFailed, match="reference"):
        rn.selftest(emb)


# -- the loader: pins the file, pins the graph level ----------------------------------------------------------------------

def fake_ort(session):
    seen = []

    class Opts:
        def __init__(self):
            self.entries = dict()

        def add_session_config_entry(self, k, v):
            self.entries[k] = v

    class Sess:
        def __new__(cls, path, opts, providers=None):
            seen.append((path, opts, providers))
            return session

    levels = types.SimpleNamespace(**dict((n, n) for n in ("ORT_DISABLE_ALL", "ORT_ENABLE_BASIC", "ORT_ENABLE_EXTENDED", "ORT_ENABLE_ALL")))
    return types.SimpleNamespace(SessionOptions=Opts, InferenceSession=Sess, GraphOptimizationLevel=levels), seen


def test_the_session_runs_at_enable_all_on_two_threads_with_spinning_off():
    ort, seen = fake_ort(FakeSession())
    rn.make_session("/x/model.onnx", ort)
    path, opts, providers = seen[0]
    assert opts.graph_optimization_level == "ORT_ENABLE_ALL" and opts.intra_op_num_threads == 2 and opts.inter_op_num_threads == 1
    assert opts.entries["session.intra_op.allow_spinning"] == "0" and providers == ["CPUExecutionProvider"]


@pytest.mark.parametrize("level", ["ORT_DISABLE_ALL", "ORT_ENABLE_BASIC", "ORT_NOPE"])
def test_a_graph_level_that_leaves_the_int8_graph_unfused_is_refused(level):
    ort, seen = fake_ort(FakeSession())
    with pytest.raises(rn.Unavailable, match="not allowed"):
        rn.make_session("/x/model.onnx", ort, level=level)
    assert not seen                                            # refused BEFORE a session was even built
    for ok in ("ORT_ENABLE_EXTENDED", "ORT_ENABLE_ALL"):         # negative control
        rn.make_session("/x/model.onnx", ort, level=ok)
    assert len(seen) == 2


def test_the_loader_refuses_a_missing_or_wrong_model_file_and_a_drifting_one_even_when_the_pin_matches(tmp_path, monkeypatch):
    art_path = tmp_path / rn.ARTIFACTS_FILENAME
    clean = FakeSession()
    art = artifacts_for(clean)
    art_path.write_text(json.dumps(art))
    monkeypatch.setattr(rn, "centre_digest", lambda v: rn.CENTRE_SHA256)         # the real vector's pin is not this fake's
    with pytest.raises(rn.Unavailable, match="not found"):
        rn.Resnet34Embedder.load(str(tmp_path), artifacts_path=str(art_path), ort_module=fake_ort(clean)[0])
    model = tmp_path / rn.MODEL_FILENAME
    model.write_bytes(b"not the pinned model")
    with pytest.raises(rn.Unavailable, match="!= pinned"):
        rn.Resnet34Embedder.load(str(tmp_path), artifacts_path=str(art_path), ort_module=fake_ort(clean)[0])
    import hashlib
    monkeypatch.setattr(rn, "MODEL_SHA256", hashlib.sha256(model.read_bytes()).hexdigest())       # the pin now matches the bytes ...
    with pytest.raises(rn.SelfTestFailed):                                                       # ... but the model DRIFTS: refused
        rn.Resnet34Embedder.load(str(tmp_path), artifacts_path=str(art_path), ort_module=fake_ort(FakeSession(drift=0.2))[0])
    ok = rn.Resnet34Embedder.load(str(tmp_path), artifacts_path=str(art_path), ort_module=fake_ort(clean)[0],
                                  selftest_factor=100.0)                                          # negative control: a faithful model loads
    assert isinstance(ok, rn.Resnet34Embedder)


# -- cross-model: profiles of different model_ids never compare --------------------------------------------------------

def profile_row(uid, vec, model=None):
    row = dict(user_id=uid, embedding_base64=b64(vec), sample_count=5)
    if model is not None:
        row["model_id"] = model
    return row


def test_a_resemblyzer_profile_is_never_scored_against_a_resnet34_embedding_and_the_reverse():
    probe = unit(1)
    profiles = [profile_row("old", probe), profile_row("legacy_explicit", probe, "resemblyzer")]      # identical vectors, other space
    assert rn.match_profiles(probe, profiles, MODEL) == (None, None, 0)                              # nothing compared: no match, not a perfect one
    mixed = profiles + [profile_row("new", near(probe, 0.4, 2), MODEL)]
    user, score, compared = rn.match_profiles(probe, mixed, MODEL)
    assert (user, compared) == ("new", 1) and score == pytest.approx(0.4, abs=1e-3)
    # the reverse: asked in the legacy space, the ResNet34 row is invisible
    assert rn.match_profiles(probe, [profile_row("new", probe, MODEL)], "resemblyzer") == (None, None, 0)
    # negative control: the same legacy rows DO match when the query is in the legacy space
    assert rn.match_profiles(probe, profiles, "resemblyzer")[2] == 2


def test_malformed_and_wrong_dimension_rows_are_skipped_not_fatal():
    probe = unit(1)
    rows = [profile_row("bad", unit(2)[:100], MODEL), dict(user_id="x", embedding_base64="!!!", model_id=MODEL),
            dict(embedding_base64=b64(probe), model_id=MODEL), profile_row("ok", probe, MODEL)]
    assert rn.match_profiles(probe, rows, MODEL)[:2] == ("ok", pytest.approx(1.0, abs=1e-5))


# -- thresholds by length group ----------------------------------------------------------------------------------------

def test_the_panels_would_be_decision_uses_the_length_group_threshold(monkeypatch):
    for k in ("TIER", "THRESHOLD_SHORT", "THRESHOLD_LONG"):
        monkeypatch.delenv("ZOE_SPEAKER_RESNET34_" + k, raising=False)
    assert rn.length_group(2.0) == "short" and rn.length_group(2.0001) == "long" and rn.length_group(None) == "long"
    short, long_ = rn.decision(0.25, 1.4), rn.decision(0.25, 3.5)
    assert short == dict(group="short", threshold=0.233, accept=True)
    assert long_ == dict(group="long", threshold=0.265, accept=False)
    # negative control: ONE global bar cannot give both answers for this score
    for bar in (0.233, 0.265, 0.253):
        assert ((0.25 >= bar), (0.25 >= bar)) != (short["accept"], long_["accept"])
    assert rn.decision(0.31, 3.5, "far01")["accept"] is False and rn.decision(0.36, 3.5, "far01")["accept"] is True
    assert rn.decision(None, 3.0)["accept"] is False and rn.decision(float("nan"), 3.0)["accept"] is False


# -- the enrolment recipe: 5 clips, mean of L2-normalised, leave-one-out outlier rejection -----------------------------

def five_clips(stranger: bool, seed: int = 3):
    base = unit(seed)
    clips = [near(base, 0.80, seed + i) for i in range(1, 6)]
    if stranger:
        clips[2] = unit(seed + 50)                           # a different voice in the middle of the enrolment
    return base, clips


def test_five_clean_clips_make_the_mean_profile_and_nothing_is_dropped():
    base, clips = five_clips(False)
    built = rn.build_profile(clips)
    want = np.mean(np.asarray(clips), axis=0)
    assert built.ok and built.kept == [0, 1, 2, 3, 4] and built.dropped == []
    assert rn.cosine(built.vector, want) > 0.99999 and abs(float(np.linalg.norm(built.vector)) - 1) < 1e-5
    assert rn.cosine(built.vector, base) > 0.9


def test_a_strangers_clip_is_dropped_and_the_profile_is_the_other_four():
    base, clips = five_clips(True)
    built = rn.build_profile(clips)
    assert built.ok and built.dropped == [2] and built.kept == [0, 1, 3, 4]
    clean_mean = np.mean(np.asarray([clips[i] for i in (0, 1, 3, 4)]), axis=0)
    assert rn.cosine(built.vector, clean_mean) > 0.99999
    # negative control: with rejection off (tau 0) the stranger stays in and the profile is measurably worse
    naive = rn.build_profile(clips, tau=0.0)
    assert naive.dropped == [] and rn.cosine(naive.vector, base) < rn.cosine(built.vector, base) - 0.01


def test_too_few_surviving_clips_means_no_profile_at_all():
    clips = [unit(i) for i in range(5)]                      # five unrelated voices: nothing is 'the' speaker
    built = rn.build_profile(clips)
    assert built.vector is None and not built.ok
    assert rn.build_profile([]).vector is None
    two = [near(unit(9), 0.9, 1), near(unit(9), 0.9, 2)]
    assert rn.build_profile(two).vector is None              # fewer than min_keep (3) clips: refused
    assert rn.build_profile(two, min_keep=2).ok               # negative control: the recipe's own floor of 2 would accept it


# -- the guided 5-clip enrolment run (state machine with a fake mic, voice, embedder and server) -------------------------

class Rig:
    """Records everything the run does. ``clips`` are the successive captures the 'mic' returns (bytes used as markers)."""

    def __init__(self, vectors, *, legacy_upload=False, profile_ok=True, legacy_ok=True, cancel_after=None, **kw):
        self.vectors = vectors                              # marker bytes -> embedding
        self.queue = list(vectors)
        self.said, self.legacy, self.profiles, self.reports = [], [], [], []
        self.recorded = 0
        self.profile_ok, self.legacy_ok, self.cancel_after = profile_ok, legacy_ok, cancel_after
        self.kw = dict(clips=5, embed=self.embed, build=self.build, upload_profile=self.upload_profile, legacy_upload=legacy_upload)
        self.kw.update(kw)

    def say(self, text):
        self.said.append(text)
        return True

    def record(self):
        self.recorded += 1
        return self.queue.pop(0) if self.queue else None

    def embed(self, wav):
        return self.vectors.get(wav)

    def build(self, embs):
        built = rn.build_profile(embs)
        return built.vector, list(built.dropped)

    def upload_profile(self, vec, n):
        self.profiles.append((np.asarray(vec).copy(), n))
        return self.profile_ok

    def upload(self, wav):
        self.legacy.append(wav)
        return self.legacy_ok

    def cancelled(self):
        return "tapped_stop" if self.cancel_after is not None and self.recorded >= self.cancel_after else ""

    def run(self, script=None):
        run = em.EnrollmentRun(script, say=self.say, record=self.record, upload=self.upload, name="Ada",
                               assess=lambda w: (True, "ok"), report=lambda *a: self.reports.append(a),
                               cancelled=self.cancelled, **self.kw)
        return run.run()


def clip_world(stranger_index=None, extra_good=2):
    """Marker bytes -> vector: five clips of one voice (a stranger at ``stranger_index``), plus replacement takes of that voice."""
    base = unit(21)
    vectors = dict()
    for i in range(5):
        vectors[b"clip%d" % i] = unit(300 + i) if i == stranger_index else near(base, 0.80, 40 + i)
    for j in range(extra_good):
        vectors[b"retake%d" % j] = near(base, 0.80, 80 + j)
    return vectors


def order(vectors, names):
    return dict((n, vectors[n]) for n in names)


def test_five_good_clips_upload_one_profile_and_no_audio_for_resnet34_alone():
    v = clip_world()
    rig = Rig(order(v, [b"clip0", b"clip1", b"clip2", b"clip3", b"clip4"]))
    res = rig.run()
    assert res.outcome == em.DONE and res.samples == 5 and rig.recorded == 5
    assert len(rig.profiles) == 1 and rig.profiles[0][1] == 5 and rig.legacy == []        # audio never left the panel
    assert "5 short phrases" in rig.said[0] and rig.reports[-1][0] == em.DONE


def test_the_odd_clip_is_re_asked_replaced_and_the_strangers_audio_is_never_uploaded():
    v = clip_world(stranger_index=2)
    rig = Rig(order(v, [b"clip0", b"clip1", b"clip2", b"clip3", b"clip4", b"retake0"]), legacy_upload=True)
    res = rig.run()
    assert res.outcome == em.DONE and res.samples == 5 and rig.recorded == 6                # one extra take
    assert any("sounded different" in s for s in rig.said)                                  # and it was said, not silently redone
    assert b"clip2" not in rig.legacy and len(rig.legacy) == 5 and b"retake0" in rig.legacy  # dual: the Resemblyzer clips are the KEPT ones
    clean = np.mean([rn.l2(v[k]) for k in (b"clip0", b"clip1", b"retake0", b"clip3", b"clip4")], axis=0)
    assert rn.cosine(rig.profiles[0][0], clean) > 0.99999
    # negative control: without the re-ask the same enrolment still drops the stranger but keeps only four clips
    again = Rig(order(v, [b"clip0", b"clip1", b"clip2", b"clip3", b"clip4"]), legacy_upload=True, reask_rounds=0)
    res = again.run()
    assert res.outcome == em.DONE and res.samples == 4 and again.recorded == 5 and b"clip2" not in again.legacy


def test_dual_enrols_both_models_from_the_same_clips_and_resnet34_alone_sends_no_wav():
    v = clip_world()
    names = [b"clip0", b"clip1", b"clip2", b"clip3", b"clip4"]
    dual = Rig(order(v, names), legacy_upload=True)
    assert dual.run().outcome == em.DONE
    assert dual.legacy == names and len(dual.profiles) == 1                              # the same five clips feed both profiles
    alone = Rig(order(v, names), legacy_upload=False)
    assert alone.run().outcome == em.DONE and alone.legacy == [] and len(alone.profiles) == 1


def test_no_consistent_voice_means_nothing_is_stored():
    vectors = dict((b"clip%d" % i, unit(500 + i)) for i in range(5))
    vectors.update(dict((b"retake%d" % i, unit(600 + i)) for i in range(8)))
    rig = Rig(vectors)
    rig.queue = [b"clip0", b"clip1", b"clip2", b"clip3", b"clip4"] + [b"retake%d" % i for i in range(8)]
    res = rig.run()
    assert res.outcome == em.FAILED and res.reason == "bad_capture" and rig.profiles == [] and rig.legacy == []


def test_a_stop_part_way_stores_nothing_and_a_failed_profile_upload_with_a_good_legacy_upload_is_partial():
    v = clip_world()
    names = [b"clip0", b"clip1", b"clip2", b"clip3", b"clip4"]
    stopped = Rig(order(v, names), legacy_upload=True, cancel_after=2)
    res = stopped.run()
    assert res.outcome == em.CANCELLED and stopped.profiles == [] and stopped.legacy == []
    partial = Rig(order(v, names), legacy_upload=True, profile_ok=False)
    assert partial.run().outcome == em.PARTIAL                                            # legacy stored, ResNet34 not: said so
    failed = Rig(order(v, names), legacy_upload=False, profile_ok=False)
    assert failed.run().outcome == em.FAILED


def test_a_three_phrase_script_from_an_older_server_still_yields_five_captures():
    v = clip_world()
    rig = Rig(order(v, [b"clip0", b"clip1", b"clip2", b"clip3", b"clip4"]))
    three = dict(phrases=list(em.DEFAULT_PHRASES))
    rig.run(three)
    prompts = [s for s in rig.said if s.startswith("Phrase")]
    assert len(prompts) == 5 and prompts[3].endswith(em.DEFAULT_PHRASES_EXTRA[0]) and prompts[4].endswith(em.DEFAULT_PHRASES_EXTRA[1])
    assert "5 short phrases" in rig.said[0]


def test_the_default_run_is_the_old_three_clip_wav_upload_flow():
    rig = Rig(dict((b"c%d" % i, unit(i)) for i in range(3)), legacy_upload=True)
    rig.kw = dict(upload=None)                               # no embedder wiring: exactly the pre-ResNet34 constructor
    rig.queue = [b"c0", b"c1", b"c2"]
    res = em.EnrollmentRun(None, say=rig.say, record=rig.record, upload=rig.upload, name="Ada",
                           assess=lambda w: (True, "ok")).run()
    assert res.outcome == em.DONE and res.samples == 3 and rig.legacy == [b"c0", b"c1", b"c2"] and rig.profiles == []
    assert "three short phrases" in rig.said[0]


# -- the daemon: dual acts only on the current model ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def daemon():
    stubs = dict((n, MagicMock()) for n in ("pyaudio",) if n not in sys.modules)
    saved = dict((n, sys.modules.get(n)) for n in stubs)
    sys.modules.update(stubs)
    try:
        yield _load("zoe_voice_daemon_rn34_test", SETUP / "zoe_voice_daemon.py")
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


class _Inline:
    """threading.Thread that runs its target at start() - the scorers are daemon threads in production."""

    def __init__(self, target, args=(), daemon=None, name=None):
        self._t, self._a = target, args

    def start(self):
        self._t(*self._a)


@pytest.fixture
def rig(daemon, monkeypatch, tmp_path):
    log = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", True)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW_LOG", str(log))
    monkeypatch.setattr(daemon, "_rn34", rn)
    monkeypatch.setattr(daemon.threading, "Thread", _Inline)
    daemon._claim_ctx.source = None
    daemon._claim_ctx.rn34 = None
    state = types.SimpleNamespace(log=log, scored=[], vec=unit(7))

    # Resemblyzer's side, REAL code path (_identify_speaker_from_wav -> _match_speaker_local) over a fake encoder and a synced cache:
    # Jason's legacy profile scores 0.91 against what the fake encoder returns for any clip.
    legacy_profile = unit(61)
    fake_pkg = types.ModuleType("resemblyzer")
    fake_pkg.preprocess_wav = lambda path: np.zeros(16000 * 3, dtype=np.float32)
    monkeypatch.setitem(sys.modules, "resemblyzer", fake_pkg)
    monkeypatch.setattr(daemon, "_get_voice_encoder",
                        lambda: types.SimpleNamespace(embed_utterance=lambda wav: near(legacy_profile, 0.91, 62)))
    monkeypatch.setattr(daemon, "_sync_speaker_profiles", lambda force=False: None)
    monkeypatch.setitem(daemon._profile_cache, "profiles", [profile_row("jason", legacy_profile), profile_row("ada", unit(63), MODEL)])

    def fake_score(wav):
        state.scored.append(wav)
        return dict(model_id=MODEL, source="local", user_id="ada", score=0.31, n_compared=2, seconds=3.2, group="long",
                    threshold=0.265, accept=True, ms=104.0, _vec=state.vec)

    monkeypatch.setattr(daemon, "_score_resnet34", fake_score)
    return state


def rows_of(state):
    return [json.loads(line) for line in state.log.read_text().splitlines()] if state.log.exists() else []


def test_dual_in_shadow_mode_logs_both_decisions_on_one_row_and_attaches_nothing(daemon, rig, monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "dual")
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)
    assert daemon._speaker_claim_for_turn(b"wav") is None                  # shadow: the server never receives any claim
    (row,) = rows_of(rig)
    assert row["model_id"] == "resemblyzer" and row["user_id"] == "jason" and row["score"] == pytest.approx(0.91, abs=1e-3)   # the ACTING model's
    sec = row["secondary"]
    assert sec["model_id"] == MODEL and sec["user_id"] == "ada" and sec["score"] == 0.31 and sec["accept"] is True and sec["ms"] == 104.0
    assert "_vec" not in sec and "_vec" not in json.dumps(row)             # metadata only: never a vector
    assert len(rig.scored) == 1


def test_dual_with_shadow_off_attaches_the_resemblyzer_claim_only_and_scores_resnet34_on_its_own_row(daemon, rig, monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "dual")
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", False)
    claim = daemon._speaker_claim_for_turn(b"wav")
    assert claim[0] == "jason" and claim[1] == pytest.approx(0.91, abs=1e-3) and getattr(claim, "model_id", None) is None   # Resemblyzer's
    block = daemon._speaker_field(claim)
    assert block == dict(verified=None, member="jason", score=round(claim[1], 4))                   # no model_id: today's wire format
    (row,) = rows_of(rig)
    assert row["role"] == "secondary" and row["user_id"] is None and row["secondary"]["user_id"] == "ada"
    assert len(rig.scored) == 1                                                                       # scored off the turn's path


def test_resnet34_mode_acts_on_resnet34_and_says_which_model_and_how_long(daemon, rig, monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resnet34")
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", False)
    claim = daemon._speaker_claim_for_turn(b"wav")
    assert tuple(claim) == ("ada", 0.31) and claim.model_id == MODEL and claim.speech_s == 3.2
    assert daemon._speaker_field(claim) == dict(verified=None, member="ada", score=0.31, model_id=MODEL, speech_s=3.2)
    assert rows_of(rig) == []                                          # shadow off: acting only, no shadow rows
    assert len(rig.scored) == 1                                        # Resemblyzer was never run (no encoder is even loaded)


def test_resnet34_with_nobody_enrolled_yet_is_no_claim_and_no_verdict_not_a_guest(daemon, rig, monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resnet34")
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", False)
    monkeypatch.setattr(daemon, "_score_resnet34", lambda wav: dict(model_id=MODEL, source="no_profiles", n_compared=0, seconds=2.0))
    out = daemon._speaker_claim_for_turn(b"wav")
    assert out is None                                                  # NOT daemon.SCORED_NO_MATCH, which would mean 'verified: false'
    assert daemon._speaker_field(out) is None
    # negative control: a scored non-match IS a verdict
    monkeypatch.setattr(daemon, "_score_resnet34", lambda wav: dict(model_id=MODEL, source="local", user_id="ada", score=0.1, n_compared=1,
                                                                  seconds=2.0, group="short", threshold=0.233, accept=False, _vec=unit(1)))
    assert tuple(daemon._speaker_claim_for_turn(b"wav")) == ("ada", 0.1)


def test_the_resnet34_shadow_row_names_the_acting_model_and_carries_the_length_group_decision(daemon, rig, monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resnet34")
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)
    assert daemon._speaker_claim_for_turn(b"wav") is None
    (row,) = rows_of(rig)
    assert row["model_id"] == MODEL and row["group"] == "long" and row["threshold"] == 0.265 and row["accept"] is True
    assert row["seconds"] == 3.2 and row["ms"] == 104.0 and row["source"] == "local" and "secondary" not in row
    assert "embedding" not in json.dumps(row)


def test_the_default_mode_writes_the_rows_it_always_wrote_and_syncs_with_the_request_it_always_made(daemon, rig, monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resemblyzer")
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", True)
    daemon._speaker_claim_for_turn(b"wav")
    (row,) = rows_of(rig)
    assert set(row) == set(("boot", "seq", "ts", "panel_id", "user_id", "score", "n_profiles", "source", "truth"))
    assert rig.scored == [] and daemon._sync_request_extras() == dict()
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "dual")
    assert daemon._sync_request_extras() == dict(params=dict(models="resemblyzer," + MODEL))
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resnet34")
    assert daemon._sync_request_extras() == dict(params=dict(models=MODEL))


def test_the_legacy_matcher_skips_resnet34_profiles_even_though_they_are_256_wide(daemon, monkeypatch):
    probe = unit(4)
    monkeypatch.setitem(daemon._profile_cache, "profiles", [profile_row("new", probe, MODEL)])
    assert daemon._match_speaker_local(probe) is None                         # same vector, other space: never scored
    monkeypatch.setitem(daemon._profile_cache, "profiles", [profile_row("old", probe)])
    assert daemon._match_speaker_local(probe)[0] == "old"                     # negative control: no model_id = legacy = matched


def test_a_refused_embedder_scores_nothing_and_says_why(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_rn34", rn)
    monkeypatch.setattr(daemon, "_rn34_embedder", None)
    monkeypatch.setattr(daemon, "_rn34_error", "")
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "dual")

    def refuse(cls, *a, **k):
        raise rn.SelfTestFailed("embedding drift: cosine 0.98")

    monkeypatch.setattr(rn.Resnet34Embedder, "load", classmethod(refuse))
    assert daemon._get_rn34() is None and "drift" in daemon._rn34_error
    assert daemon._rn34_status().startswith("refused: embedding drift")
    out = daemon._score_resnet34(b"wav")
    assert out["source"] == "encoder_unavailable" and out["score"] is None
    assert daemon._get_rn34() is None                                          # refused once: not re-loaded on every turn


def test_enrolment_wiring_default_none_unavailable_and_dual_degrade(daemon, monkeypatch):
    cmd = dict(clips=5)
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resemblyzer")
    assert daemon._enroll_speaker_kwargs(cmd, "ada", "Ada") == dict()
    monkeypatch.setattr(daemon, "_rn34", rn)
    monkeypatch.setattr(daemon, "_get_rn34", lambda: None)
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resnet34")
    assert daemon._enroll_speaker_kwargs(cmd, "ada", "Ada") is None             # cannot enrol what cannot load: say so
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "dual")
    assert daemon._enroll_speaker_kwargs(cmd, "ada", "Ada") == dict()          # dual degrades to the legacy enrolment, loudly
    emb, _ = make_embedder()
    monkeypatch.setattr(daemon, "_get_rn34", lambda: emb)
    kw = daemon._enroll_speaker_kwargs(dict(clips=3), "ada", "Ada")
    assert kw["clips"] == 5 and kw["legacy_upload"] is True                   # never fewer than 5 clips, and dual keeps the WAV upload
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resnet34")
    assert daemon._enroll_speaker_kwargs(cmd, "ada", "Ada")["legacy_upload"] is False
    posts = []
    monkeypatch.setattr(daemon, "_api_post", lambda path, data, **k: posts.append((path, data)) or dict(ok=True))
    assert kw["upload_profile"](unit(3), 4) is True
    path, body = posts[0]
    assert path == "/api/voice/enroll" and body["model_id"] == MODEL and body["sample_count"] == 4 and body["consent"] is True
    assert "audio_base64" not in body and len(base64.b64decode(body["embedding_base64"])) == DIM * 4


def test_in_dual_a_failed_acting_model_upload_is_a_storage_failure_even_if_resnet34_would_have_stored():
    """Codex P2 on #2024: in ``dual`` Resemblyzer ACTS. If its WAV upload fails, the member has no profile that recognises them,
    so Zoe must not say 'I can already use it' - and the ResNet34 profile must not be stored either, so 'I haven't kept
    anything' stays true."""
    v = clip_world()
    names = [b"clip0", b"clip1", b"clip2", b"clip3", b"clip4"]
    rig = Rig(order(v, names), legacy_upload=True, legacy_ok=False, profile_ok=True)
    res = rig.run()
    assert res.outcome == em.FAILED and res.reason == "upload_failed" and res.samples == 0
    assert rig.profiles == []                                              # the shadow model is never stored without the acting one
    assert rig.said[-1] == em.DEFAULT_SCRIPT["store_failed"]               # the honest line, not 'partial'
    assert len(rig.legacy) == 2                                            # the acting upload was retried once before giving up
    # negative control: the acting upload landing makes the same enrolment a success
    ok = Rig(order(v, names), legacy_upload=True, legacy_ok=True, profile_ok=True)
    assert ok.run().outcome == em.DONE and len(ok.profiles) == 1


def test_in_resnet34_alone_the_profile_is_the_acting_upload_and_is_retried_once():
    v = clip_world()
    names = [b"clip0", b"clip1", b"clip2", b"clip3", b"clip4"]
    rig = Rig(order(v, names), legacy_upload=False, profile_ok=False)
    res = rig.run()
    assert res.outcome == em.FAILED and len(rig.profiles) == 2 and rig.said[-1] == em.DEFAULT_SCRIPT["store_failed"]
    ok = Rig(order(v, names), legacy_upload=False, profile_ok=True)       # negative control
    assert ok.run().outcome == em.DONE and len(ok.profiles) == 1


def test_resnet34_mode_without_the_module_refuses_to_enrol_instead_of_a_legacy_only_done(daemon, monkeypatch):
    """Codex P2 on #2024: ResNet34 is the ACTING model and its module did not import - a legacy-only enrolment would report DONE
    while nothing can recognise the member. The wiring must return the unavailable sentinel (None)."""
    monkeypatch.setattr(daemon, "_rn34", None)
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resnet34")
    assert daemon._enroll_speaker_kwargs(dict(clips=5), "ada", "Ada") is None
    # negative control: in dual Resemblyzer acts, so a missing ResNet34 module degrades to the legacy enrolment, which is honest
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "dual")
    assert daemon._enroll_speaker_kwargs(dict(clips=5), "ada", "Ada") == dict()
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resemblyzer")
    assert daemon._enroll_speaker_kwargs(dict(clips=5), "ada", "Ada") == dict()


def test_the_refused_enrolment_says_so_records_nothing_and_reports_failed(daemon, monkeypatch):
    said, reports, posts = [], [], []
    monkeypatch.setattr(daemon, "_rn34", None)
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resnet34")
    monkeypatch.setattr(daemon, "_enroll_say", lambda text: said.append(text) or True)
    monkeypatch.setattr(daemon, "_enroll_record_once", lambda pa: posts.append("RECORDED"))
    monkeypatch.setattr(daemon, "_api_post", lambda path, data, **k: reports.append((path, data)) or dict(ok=True))
    monkeypatch.setattr(daemon, "_enroll_request", dict(user_id="ada", name="Ada"))
    monkeypatch.setattr(daemon, "_enroll_mode", em)
    daemon._run_enrollment_mode(None)
    assert said and posts == []
    assert reports == [("/api/enrollment/result", dict(status="failed", user_id="ada", samples=0, reason="encoder_unavailable"))]


# -- health must not lie when ResNet34 is the acting model (Codex P1 on #2024) -------------------------------------------

def test_health_is_degraded_when_the_acting_resnet34_embedder_is_not_ready(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resnet34")
    monkeypatch.setattr(daemon, "_rn34", rn)
    monkeypatch.setattr(daemon, "_rn34_embedder", None)
    monkeypatch.setattr(daemon, "_rn34_error", "embedding drift: cosine 0.98")
    h = daemon._health_payload()
    assert h["status"] == "degraded" and h["speaker_model"] == "resnet34" and h["speaker_resnet34"].startswith("refused")
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "dual")                      # shadow-only: reported, not degraded
    h = daemon._health_payload()
    assert h["status"] == "ok" and h["speaker_resnet34"].startswith("refused")
    emb, _ = make_embedder()                                                    # negative control: ready -> ok
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resnet34")
    monkeypatch.setattr(daemon, "_rn34_embedder", emb)
    assert daemon._health_payload()["status"] == "ok"
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resemblyzer")                # default: no speaker-model keys
    assert set(daemon._health_payload()) == set(("status", "service", "panel_id", "uptime_s", "wake_phrase", "build",
                                                 "build_deployed_at", "code_sha256", "wake_model_sha256"))


def test_health_state_waits_for_ready_in_resnet34_mode_and_gives_up_bounded():
    answers = iter([dict(speaker_model="resnet34", speaker_resnet34="not loaded")] * 2
                   + [dict(speaker_model="resnet34", speaker_resnet34="ready")])
    t = [0.0]
    clock = lambda: t[0]                                                       # noqa: E731

    def sleep(s):
        t[0] += s

    assert rn.health_state("u", 10, fetch=lambda u: next(answers), clock=clock, sleep=sleep) == ("resnet34", "ready")
    t[0] = 0.0
    stuck = rn.health_state("u", 3, fetch=lambda u: dict(speaker_model="resnet34", speaker_resnet34="refused: x"), clock=clock, sleep=sleep)
    assert stuck == ("resnet34", "refused: x") and t[0] >= 3                 # waited, then reported the truth
    t[0] = 0.0
    assert rn.health_state("u", 30, fetch=lambda u: dict(speaker_model="dual", speaker_resnet34="refused: x"), clock=clock,
                           sleep=sleep) == ("dual", "refused: x") and t[0] == 0   # dual: answered at once, no wait
    assert rn.health_state("u", 30, fetch=lambda u: dict(status="ok"), clock=clock, sleep=sleep) == ("resemblyzer", "off")

    def down(u):
        raise OSError("refused")

    assert rn.health_state("u", 2, fetch=down, clock=clock, sleep=sleep) == ("unknown", "unreachable")


# -- the hand-run flow mirrors the daemon: resnet34 without its module refuses (Codex P2 on #2024) --------------------------

@pytest.fixture
def flow(monkeypatch):
    mod = _load("zoe_enroll_flow_rn34_test", SETUP / "zoe_enroll_flow.py")
    said, legacy = [], []
    monkeypatch.setattr(mod, "say", lambda text: said.append(text))
    monkeypatch.setattr(mod, "_load_rn34", lambda: None)
    monkeypatch.setattr(mod, "_enroll_voice_legacy", lambda user, name, svc="zoe-voice": legacy.append(user) or 0)
    mod.said, mod.legacy_calls = said, legacy
    return mod


def test_hand_run_enrolment_in_resnet34_mode_without_the_module_refuses(flow, monkeypatch):
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resnet34")
    assert flow.enroll_voice("ada", "Ada") == 1
    assert flow.legacy_calls == [] and flow.said and "Nothing was stored" in flow.said[-1]
    # negative controls: dual (Resemblyzer acts) and the default mode fall back to the legacy enrolment
    for mode in ("dual", "resemblyzer"):
        monkeypatch.setenv("ZOE_SPEAKER_MODEL", mode)
        assert flow.enroll_voice("ada", "Ada") == 0
    assert flow.legacy_calls == ["ada", "ada"]
