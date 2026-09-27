"""Speaker embedding must run OFF the event loop in /api/voice/enroll + /identify.

``_compute_resemblyzer_embedding`` is CPU-bound, and its first call imports
resemblyzer + torch (~6 s measured on the Jetson). Called inline inside the
``async def`` handlers, that froze zoe-data's event loop — every live voice
turn, WebSocket and health probe stalled behind an enrolment. The handlers now
``await asyncio.to_thread(...)``.

The check is behavioural, not a grep: a fake encoder blocks until a concurrent
lightweight coroutine proves it ran WHILE the embedding was in progress. With
the old synchronous call the loop is frozen, the coroutine cannot run, the
encoder gives up after a bounded wait, and the assertion fails (negative
control verified by reverting the call site).

No models, no network, no live DB — ``ci_safe``.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import sys
import threading
import types

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import routers.voice_tts as voice_tts

EMB = b"\x00\x00\x80\x3f" * 4  # float32 [1, 1, 1, 1]
_BLOCK_S = 3.0  # bounded: a frozen loop costs the (failing) test this long, never a hang


class _SlowEncoder:
    """Stands in for _compute_resemblyzer_embedding; blocks until released."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.thread_id = None

    def __call__(self, _wav_path):
        self.thread_id = threading.get_ident()
        self.started.set()
        self.release.wait(_BLOCK_S)
        self.finished.set()
        return EMB


async def _light_request(enc: _SlowEncoder) -> bool:
    """A concurrent cheap request: True iff it ran while the embedding was mid-flight."""
    for _ in range(int(_BLOCK_S / 0.005) + 200):
        if enc.started.is_set():
            break
        await asyncio.sleep(0.005)
    ran_during = enc.started.is_set() and not enc.finished.is_set()
    enc.release.set()
    return ran_during


class _Cur:
    def __init__(self, one=None, rows=()):
        self._one, self._rows = one, list(rows)

    async def fetchone(self):
        return self._one

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __await__(self):
        async def _done():
            return self
        return _done().__await__()


class _DB:
    def __init__(self, rows=()):
        self.rows = rows

    def execute(self, sql, params=()):
        return _Cur(one=None, rows=self.rows)

    async def commit(self):
        return None


def _wire(monkeypatch, enc, rows=()):
    db = _DB(rows)

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield db

    mod = types.ModuleType("db_compat")
    mod.get_compat_db = fake_ctx
    monkeypatch.setitem(sys.modules, "db_compat", mod)
    monkeypatch.setattr(voice_tts, "_compute_resemblyzer_embedding", enc)


_AUDIO = base64.b64encode(b"RIFF-fake-wav").decode()
_DEVICE = {"source": "device", "user_id": "voice-daemon"}


@pytest.mark.asyncio
async def test_enroll_embedding_does_not_block_the_event_loop(monkeypatch):
    enc = _SlowEncoder()
    _wire(monkeypatch, enc)
    loop_thread = threading.get_ident()

    out, ran_during = await asyncio.gather(
        voice_tts.voice_enroll(
            {"audio_base64": _AUDIO, "user_id": "jason", "display_name": "Jason"}, caller=_DEVICE
        ),
        _light_request(enc),
    )

    assert ran_during, "event loop was blocked while the speaker embedding ran"
    assert enc.thread_id is not None and enc.thread_id != loop_thread
    # Response shape unchanged.
    assert out["ok"] is True and out["user_id"] == "jason" and out["display_name"] == "Jason"
    assert set(out) == {"ok", "profile_id", "user_id", "display_name"}


@pytest.mark.asyncio
async def test_identify_embedding_does_not_block_the_event_loop(monkeypatch):
    enc = _SlowEncoder()
    _wire(monkeypatch, enc, rows=[("pid-1", "jason", "Jason", EMB)])
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.82")
    loop_thread = threading.get_ident()

    out, ran_during = await asyncio.gather(
        voice_tts.voice_identify({"audio_base64": _AUDIO, "panel_id": "p"}, caller=_DEVICE),
        _light_request(enc),
    )

    assert ran_during, "event loop was blocked while the speaker embedding ran"
    assert enc.thread_id is not None and enc.thread_id != loop_thread
    # Identical embedding → cosine 1.0 → a match; the response is unchanged.
    assert out["ok"] is True and out["identified"] is True
    assert out["user_id"] == "jason" and out["profile_id"] == "pid-1"


@pytest.mark.asyncio
async def test_unavailable_encoder_still_503s(monkeypatch):
    _wire(monkeypatch, lambda _p: None)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await voice_tts.voice_enroll({"audio_base64": _AUDIO, "user_id": "jason"}, caller=_DEVICE)
    assert exc.value.status_code == 503
    with pytest.raises(HTTPException) as exc:
        await voice_tts.voice_identify({"audio_base64": _AUDIO}, caller=_DEVICE)
    assert exc.value.status_code == 503
