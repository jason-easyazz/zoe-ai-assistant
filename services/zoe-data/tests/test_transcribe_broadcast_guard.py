"""The replay harness must be able to transcribe without touching the panels.

websocket-sync.js applies NO panel_id filter to voice events — every kiosk flips
its orb and resets its auto-home timer on each voice:thinking. The guard is
server-side: callers that are instruments, not users, carry the replay- prefix
and the endpoint stays silent. This was originally claimed to be handled by
client-side filtering that does not exist (caught by Copilot on #1572).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _guard():
    # Import lazily: routers.voice_tts is heavy, but the predicate is module-level.
    from routers.voice_tts import _suppress_ui_broadcast
    return _suppress_ui_broadcast


def test_replay_harness_is_suppressed():
    assert _guard()("replay-harness") is True
    assert _guard()("replay-anything") is True


def test_real_panels_still_broadcast():
    for pid in ("zoe-panel", "kitchen", "unknown", ""):
        assert _guard()(pid) is False


def test_none_is_not_suppressed():
    assert _guard()(None) is False


@pytest.mark.asyncio
async def test_capture_false_never_touches_the_corpus(monkeypatch):
    """Instrument callers must not feed the corpus.

    The replay gate POSTs EXISTING corpus WAVs through /transcribe; with capture
    on, each run re-saved its own inputs as "new" samples — 62 byte-identical
    duplicates in one day (quarantine-replay-dups-20260727), a feedback loop
    where the nightly newest-20 becomes replays of replays.
    """
    import routers.voice_tts as vt
    calls = []
    monkeypatch.setattr(vt, "_transcribe_audio_impl", _fake_impl)
    monkeypatch.setattr(vt, "_maybe_capture_stt", _spy(calls))
    assert await vt._transcribe_audio("/tmp/x.wav", capture=False) == "hello"
    assert calls == [], "capture=False must never reach _maybe_capture_stt"
    assert await vt._transcribe_audio("/tmp/x.wav") == "hello"
    assert len(calls) == 1, "real user turns (default) must still capture"


async def _fake_impl(wav_path):
    return "hello"


def _spy(calls):
    async def _rec(wav_path, primary):
        calls.append((wav_path, primary))
    return _rec
