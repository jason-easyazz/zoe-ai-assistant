"""The panel daemon's Silero feed: contiguous, per-stream, reset at the boundaries (deep dive 2 V-3/V-5).

Until 2026-10-19 ``_vad_prob`` fed each 1,280-sample chunk to Silero as two 512-sample windows
and DROPPED the remaining 256 samples, through ONE model shared by every consumer that nothing
ever reset. ``_VadStream`` carries the remainder into the next chunk and owns its model copy.
Pinned here torch-free and with SYNTHETIC audio only (a seeded sine/noise signal, never household
audio): a fake model records every window it is fed, so "no sample is dropped" is checked
sample-for-sample against the input. The real-model, real-corpus measurement is
``scripts/perf/measure_vad_feed.py`` (new feed == upstream reference, max abs diff 0.0).
"""
import contextlib
import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"


class FakeSilero:
    """Records every window; answers a probability from the window's RMS; counts resets."""

    def __init__(self):
        self.windows: list = []
        self.resets = 0

    def __call__(self, window, sample_rate):
        w = np.asarray(window, dtype=np.float32)
        assert w.shape == (512,) and sample_rate == 16000
        self.windows.append(w.copy())
        p = float(min(1.0, np.sqrt(np.mean(w * w)) * 10))
        return types.SimpleNamespace(item=lambda: p)

    def reset_states(self):
        self.resets += 1


@pytest.fixture(scope="module")
def daemon():
    fake_pyaudio = types.ModuleType("pyaudio")
    fake_pyaudio.paInt16 = 8
    fake_pyaudio.PyAudio = object
    fake_torch = types.ModuleType("torch")
    fake_torch.from_numpy = lambda a: a
    fake_torch.no_grad = contextlib.nullcontext
    stubs = {"pyaudio": fake_pyaudio, "requests": types.ModuleType("requests"), "torch": fake_torch}
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("zoe_voice_daemon_vad_stream_test", DAEMON)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        for name, prev in saved.items():
            if prev is not None:
                sys.modules[name] = prev
            else:
                sys.modules.pop(name, None)


@pytest.fixture()
def torch_stub(daemon):
    """``feed`` imports torch lazily: keep the stub installed for each test's duration."""
    fake = types.ModuleType("torch")
    fake.from_numpy = lambda a: a
    fake.no_grad = contextlib.nullcontext
    prev = sys.modules.get("torch")
    sys.modules["torch"] = fake
    yield
    if prev is not None:
        sys.modules["torch"] = prev
    else:
        sys.modules.pop("torch", None)


def synthetic(n_samples: int, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n_samples) / 16000.0
    sig = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.05 * rng.standard_normal(n_samples)
    return (sig * 32767).astype(np.int16)


def chunks(audio: np.ndarray, size: int = 1280):
    return [audio[i:i + size] for i in range(0, len(audio) - size + 1, size)]


def test_no_sample_is_dropped_between_chunks(daemon, torch_stub):
    model = FakeSilero()
    vad = daemon._VadStream(model)
    audio = synthetic(1280 * 10)
    for c in chunks(audio):
        daemon._vad_prob(vad, c)
    # 12,800 samples = 25 full windows (the old feed saw 20 and skipped 256 samples per chunk).
    assert len(model.windows) == 25
    fed = np.concatenate(model.windows)
    expected = audio[:25 * 512].astype(np.float32) / 32768.0
    assert np.array_equal(fed, expected), "the windows must be the input, contiguous, sample for sample"
    assert vad._pending.size == 0   # 12,800 is a whole number of windows


def test_remainder_waits_for_the_next_chunk(daemon, torch_stub):
    model = FakeSilero()
    vad = daemon._VadStream(model)
    audio = synthetic(1280 * 3)
    daemon._vad_prob(vad, audio[:1280])
    assert len(model.windows) == 2 and vad._pending.size == 256
    daemon._vad_prob(vad, audio[1280:2560])
    assert len(model.windows) == 5 and vad._pending.size == 0   # 256 carried + 1280 = 3 windows
    # The window that straddles the chunk boundary is the carried 256 + the next 256.
    straddle = audio[1024:1536].astype(np.float32) / 32768.0
    assert np.array_equal(model.windows[2], straddle)


def test_per_chunk_answer_is_the_max_of_its_windows(daemon, torch_stub):
    model = FakeSilero()
    vad = daemon._VadStream(model)
    quiet = np.zeros(1280, dtype=np.int16)
    loud = synthetic(1280)
    assert daemon._vad_prob(vad, quiet) == 0.0
    assert daemon._vad_prob(vad, loud) > 0.5
    # A chunk too short to complete a window answers the last window's probability.
    assert daemon._vad_prob(vad, loud[:100]) == vad.last_prob


def test_reset_clears_the_remainder_and_the_model_state(daemon, torch_stub):
    model = FakeSilero()
    vad = daemon._VadStream(model)
    daemon._vad_prob(vad, synthetic(1280))
    assert vad._pending.size == 256
    vad.reset()
    assert vad._pending.size == 0 and vad.last_prob == 0.0 and model.resets == 1


def test_each_stream_owns_its_model_copy(daemon, torch_stub, monkeypatch):
    proto = FakeSilero()
    monkeypatch.setattr(daemon, "_get_silero_vad", lambda: (proto, None))
    a, b = daemon._new_vad_stream(), daemon._new_vad_stream()
    assert a._model is not b._model and a._model is not proto
    daemon._vad_prob(a, synthetic(1280))
    assert len(a._model.windows) == 2 and b._model.windows == [] and proto.windows == []
    assert a._model.resets == 1 and b._model.resets == 1   # born reset


def test_no_silero_means_no_stream(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_get_silero_vad", lambda: (None, None))
    assert daemon._new_vad_stream() is None


def test_inference_failure_is_the_sentinel_and_resets_the_stream(daemon, torch_stub):
    class Broken(FakeSilero):
        def __call__(self, window, sample_rate):
            raise RuntimeError("boom")

    model = Broken()
    vad = daemon._VadStream(model)
    assert daemon._vad_prob(vad, synthetic(1280)) == -1.0
    assert model.resets == 1 and vad._pending.size == 0


def test_endpointer_continues_a_handed_stream_or_starts_fresh(daemon, torch_stub, monkeypatch):
    proto = FakeSilero()
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", True)
    monkeypatch.setattr(daemon, "_get_silero_vad", lambda: (proto, None))
    handed = daemon._new_vad_stream()
    assert daemon._Endpointer(spoke=True, vad=handed)._model is handed     # follow-up / barge seed: same stream
    fresh = daemon._Endpointer()._model                                    # a new recording: its own, reset
    assert isinstance(fresh, daemon._VadStream) and fresh is not handed and fresh._model.resets == 1


def test_every_consumer_feeds_its_own_stream():
    """Source pin: no consumer feeds the shared prototype any more."""
    src = DAEMON.read_text()
    assert "_vad_prob(model," not in src
    assert src.count("_new_vad_stream()") >= 5   # endpointer, barge monitor, barge queue, ambient, follow-up
