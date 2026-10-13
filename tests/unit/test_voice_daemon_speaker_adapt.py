"""Panel-daemon side of guarded speaker adaptation (`scripts/setup/zoe_voice_daemon.py`, "adaptation offer" section).

The daemon already holds each turn's embedding; when ``SPEAKER_ADAPT_SEND`` is on it OFFERS a high-certainty turn to
``POST /api/voice/adapt``. Policy is the server's (``services/zoe-data/speaker_adaptation.py``) - the daemon only
pre-filters so it does not POST an embedding for every turn. Pinned here:

* off by default: with the flag unset NOTHING is posted, no thread is started;
* the payload carries an embedding and metadata only - NEVER audio;
* the pre-filter (score, speech seconds, send gap) and the throttle;
* an offer can never raise into the turn, and the hook sits on the local-match branch of
  ``_identify_speaker_from_wav``.

No mic, no network, no models: the daemon is loaded with ``pyaudio`` stubbed (same recipe as the cache test).
"""
from __future__ import annotations

import base64
import importlib.util
import inspect
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"


@pytest.fixture(scope="module")
def daemon():
    stubs = {n: MagicMock() for n in ("pyaudio",) if n not in sys.modules}
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("zoe_voice_daemon_adapt_under_test", _DAEMON_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


@pytest.fixture
def sent(daemon, monkeypatch):
    """Capture POSTs synchronously (run the worker thread inline) and reset the throttle."""
    posts: list = []

    def fake_post(path, data, timeout=60, retries=None):
        posts.append((path, data))
        return {"ok": True, "applied": False, "reason": "shadow", "would_apply": True, "mode": "shadow"}

    class _Inline:
        def __init__(self, target, args=(), daemon=None, name=None):
            self._t, self._a = target, args

        def start(self):
            self._t(*self._a)

    monkeypatch.setattr(daemon, "_api_post", fake_post)
    monkeypatch.setattr(daemon.threading, "Thread", _Inline)
    monkeypatch.setattr(daemon, "_adapt_last_sent", 0.0)
    return posts


EMB = (np.arange(256, dtype=np.float32) / 1000.0)


def test_off_by_default_posts_nothing(daemon, sent):
    assert daemon.SPEAKER_ADAPT_SEND is False
    daemon._adapt_offer("jason", 0.99, EMB, 9.0)
    assert sent == []


def test_qualifying_turn_posts_embedding_and_metadata_never_audio(daemon, sent, monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_ADAPT_SEND", True)
    daemon._adapt_offer("jason", 0.9123456, EMB, 3.456)
    assert len(sent) == 1
    path, body = sent[0]
    assert path == "/api/voice/adapt"
    assert set(body) == {"voice_user_id", "voice_score", "embedding_base64", "speech_s", "corroboration"}
    assert not any("audio" in k or "wav" in k for k in body)
    assert np.allclose(np.frombuffer(base64.b64decode(body["embedding_base64"]), dtype=np.float32), EMB)
    assert body["voice_user_id"] == "jason" and body["voice_score"] == 0.9123 and body["speech_s"] == 3.46


@pytest.mark.parametrize("score,speech_s,why", [
    (0.80, 5.0, "score below the pre-filter"),
    (0.95, 1.0, "clip too short"),
    (float("nan"), 5.0, "non-finite score"),
    (0.95, float("nan"), "non-finite length"),
])
def test_prefilter_blocks(daemon, sent, monkeypatch, score, speech_s, why):
    monkeypatch.setattr(daemon, "SPEAKER_ADAPT_SEND", True)
    daemon._adapt_offer("jason", score, EMB, speech_s)
    assert sent == [], why


def test_send_gap_throttles_back_to_back_offers(daemon, sent, monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_ADAPT_SEND", True)
    daemon._adapt_offer("jason", 0.95, EMB, 5.0)
    daemon._adapt_offer("jason", 0.95, EMB, 5.0)
    assert len(sent) == 1
    monkeypatch.setattr(daemon, "_adapt_last_sent", 0.0)        # gap elapsed
    daemon._adapt_offer("jason", 0.95, EMB, 5.0)
    assert len(sent) == 2


def test_a_failing_post_never_raises_into_the_turn(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_ADAPT_SEND", True)
    monkeypatch.setattr(daemon, "_adapt_last_sent", 0.0)

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(daemon, "_api_post", boom)
    done = threading.Event()
    real_thread = threading.Thread

    class _Run(real_thread):
        def run(self):
            try:
                super().run()
            finally:
                done.set()

    monkeypatch.setattr(daemon.threading, "Thread", _Run)
    daemon._adapt_offer("jason", 0.95, EMB, 5.0)      # must return normally
    assert done.wait(2)
    daemon._adapt_offer("jason", 0.95, object(), 5.0)  # an un-encodable embedding is swallowed too


def test_hook_is_on_the_local_match_branch_and_sends_speech_seconds(daemon):
    src = inspect.getsource(daemon._identify_speaker_from_wav)
    local_branch = src.split('_claim_ctx.source = "local"', 1)[1].split("return local", 1)[0]
    assert "_adapt_offer(local[0], local[1], embedding, speech_s)" in local_branch
    assert "speech_s = float(len(wav)) / 16000.0" in src
