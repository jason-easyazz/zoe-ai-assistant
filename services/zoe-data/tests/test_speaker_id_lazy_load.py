"""Speaker ID must stay off zoe-data's resident footprint until it is used.

resemblyzer pulls torch (~360 MB RSS measured on the Jetson, 2026-09-27) and
server-side speaker embedding is rare (enrolment; identify only when a client
sends raw audio instead of a daemon-computed embedding). So:

* a fresh interpreter importing ``main`` (and the voice router / speaker-ID
  module it wires) must NOT have ``resemblyzer``, ``torch`` or ``transformers``
  in ``sys.modules``;
* the first embedding request loads the encoder exactly once — thread-safe —
  on CPU, and later requests reuse it;
* a missing resemblyzer still degrades to ``None`` (the endpoints' 503) and is
  not cached, so a later install works without a restart.
"""
import json
import os
import subprocess
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep: fake resemblyzer + a subprocess import

_ZOE_DATA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ZOE_DATA))

import voice_speaker_id  # noqa: E402

_HEAVY = ("resemblyzer", "torch", "transformers")


def test_importing_main_does_not_load_speaker_id_stack():
    code = (
        "import json, sys\n"
        "import main, routers.voice_tts, voice_speaker_id\n"
        f"print(json.dumps(sorted(m for m in {_HEAVY!r} if m in sys.modules)))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(_ZOE_DATA)}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_ZOE_DATA),
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert loaded == [], f"import main pulled heavy speaker-ID deps: {loaded}"


class _FakeEncoder:
    instances: list = []

    def __init__(self, device=None, verbose=True, weights_fpath=None):
        self.device = device
        _FakeEncoder.instances.append(self)

    def embed_utterance(self, wav):
        return np.full(256, 0.5, dtype=np.float64)


def _fake_resemblyzer(slow_s: float = 0.0):
    mod = types.ModuleType("resemblyzer")

    class Enc(_FakeEncoder):
        def __init__(self, *a, **k):
            if slow_s:
                threading.Event().wait(slow_s)
            super().__init__(*a, **k)

    mod.VoiceEncoder = Enc
    mod.preprocess_wav = lambda path: np.zeros(16000, dtype=np.float32)
    return mod


@pytest.fixture
def fresh_encoder(monkeypatch):
    monkeypatch.setattr(voice_speaker_id, "_ENCODER", None, raising=False)
    _FakeEncoder.instances = []
    yield
    _FakeEncoder.instances = []


def test_first_call_loads_encoder_once_on_cpu_and_reuses_it(monkeypatch, fresh_encoder):
    monkeypatch.setitem(sys.modules, "resemblyzer", _fake_resemblyzer())
    assert getattr(voice_speaker_id, "_ENCODER", None) is None

    first = voice_speaker_id._compute_resemblyzer_embedding("/nonexistent.wav")
    second = voice_speaker_id._compute_resemblyzer_embedding("/nonexistent.wav")

    expected = np.full(256, 0.5, dtype=np.float32).tobytes()
    assert first == expected and second == expected
    assert len(_FakeEncoder.instances) == 1, "encoder must be built once, then reused"
    assert _FakeEncoder.instances[0].device == "cpu", "must never default onto CUDA"


def test_concurrent_first_use_builds_one_encoder(monkeypatch, fresh_encoder):
    monkeypatch.setitem(sys.modules, "resemblyzer", _fake_resemblyzer(slow_s=0.05))
    barrier = threading.Barrier(8)
    got = []

    def worker():
        barrier.wait()
        got.append(voice_speaker_id._get_voice_encoder())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(got) == 8
    assert len(_FakeEncoder.instances) == 1
    assert all(e is got[0] for e in got)


def test_missing_resemblyzer_returns_none_and_is_not_cached(monkeypatch, fresh_encoder):
    # A None entry in sys.modules makes `import resemblyzer` raise ImportError.
    monkeypatch.setitem(sys.modules, "resemblyzer", None)
    assert voice_speaker_id._compute_resemblyzer_embedding("/x.wav") is None
    with pytest.raises(ImportError):
        voice_speaker_id._get_voice_encoder()
    assert voice_speaker_id._ENCODER is None

    # Installed later: the next call succeeds without a process restart.
    monkeypatch.setitem(sys.modules, "resemblyzer", _fake_resemblyzer())
    assert voice_speaker_id._compute_resemblyzer_embedding("/x.wav") is not None
    assert len(_FakeEncoder.instances) == 1
