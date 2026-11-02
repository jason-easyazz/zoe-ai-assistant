"""The streaming turn route stamps household voice activity itself (codex P2, #2234).

The deploy quiet-wait reads ``voice_activity``. ``voice_command`` stamps, but /turn_stream answers the conversation opener ("let's talk")
and the mishear clarification from canned paths that return BEFORE voice_command, so a household conversation could start while a
deploy was waiting for quiet. The stamp now sits at the top of the route, once per accepted turn, behind the traffic-class filter.
Driven through the REAL voice_turn_stream; synthetic phrases only.
"""
from __future__ import annotations

import base64

import pytest

pytestmark = pytest.mark.ci_safe

from fastapi.testclient import TestClient

import test_voice_stream_speaker_verdict as base
import voice_activity


def _post(monkeypatch, *, text="let's talk", extra=None):
    import routers.voice_tts as vt

    seen: list = []
    app = base._app(monkeypatch, seen, claim_ok=False, filler=False)

    async def stt(_path, **_kw):
        return text

    monkeypatch.setattr(vt, "_transcribe_audio", stt)
    monkeypatch.setenv("ZOE_CONVERSATION_OPENER_ENABLED", "1")
    monkeypatch.setattr(voice_activity, "_LAST_VOICE_ACTIVITY_MONO", None)
    payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(), "panel_id": "hall-panel", **(extra or {})}
    with TestClient(app) as client:
        r = client.post("/api/voice/turn_stream", json=payload)
        assert r.status_code == 200, r.text
        frames = [ln for ln in r.content.split(b"\n") if ln]
    return seen, frames


def test_the_conversation_opener_fast_path_stamps_household_activity(monkeypatch):
    seen, frames = _post(monkeypatch)
    assert any(b'"conversation_mode"' in f for f in frames), frames        # it WAS the canned opener: voice_command never ran
    assert [x for x in seen if isinstance(x, dict)] == [], "the brain ran for the opener"
    age = voice_activity.last_activity_age_s()
    assert age is not None and 0.0 <= age < 5.0, "the opener turn left no household activity stamp"


def test_a_probe_or_harness_turn_does_not_stamp(monkeypatch):
    import traffic_class

    monkeypatch.setattr(traffic_class, "is_household", lambda: False)
    _seen, frames = _post(monkeypatch)
    assert any(b'"conversation_mode"' in f for f in frames), frames
    assert voice_activity.last_activity_age_s() is None


def test_an_invalid_request_is_not_an_accepted_turn_and_does_not_stamp(monkeypatch):
    import routers.voice_tts as vt

    app = base._app(monkeypatch, [], claim_ok=False, filler=False)
    monkeypatch.setattr(voice_activity, "_LAST_VOICE_ACTIVITY_MONO", None)
    assert vt  # app is wired against the real router
    with TestClient(app) as client:
        assert client.post("/api/voice/turn_stream", json={"panel_id": "hall-panel"}).status_code == 400
        assert client.post("/api/voice/turn_stream",
                           json={"audio_base64": "!!notbase64!!", "panel_id": "hall-panel"}).status_code == 400
    assert voice_activity.last_activity_age_s() is None
