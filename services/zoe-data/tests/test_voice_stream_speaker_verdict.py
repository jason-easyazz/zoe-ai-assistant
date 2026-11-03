"""The speaker gate's verdict reaches the brain on the STREAMING panel path (/api/voice/turn_stream).

voice_command binds the verdict for its turn in a ContextVar (restraint.bind_verdict): the restraint rule
(restraint.decide: a sensitive row is withheld from a voice the gate did NOT confirm), the spoken-mute refusal
(restraint.handle_turn) and the risk-cue silence all read it. On /turn_stream voice_command runs as its own task
(sub_task) and its chat tier returns a LAZY StreamingResponse: the brain runs inside that body generator, and the
generator is pulled by the OUTER request task (or a _first_task copy of it for the filler race), which never saw the
binding. Before the fix every brain lane on the live panel path read current_verdict() is None: an unconfirmed voice
looked like a typed turn.

Driven through FastAPI's TestClient: the REAL voice_turn_stream -> REAL voice_command -> the REAL chat-tier body
generator; only STT / TTS / push / the Skybridge resolver / the brain itself are faked. The brain stub records what the
restraint layer sees from inside the stream, exactly where the Flue / core lanes read it.

Three speakers: (a) a member whose claim passed the server's gate (verdict True), (b) a member claim the server refused -
an unverified voice (False), (c) the daemon scored the turn and nobody matched - a guest (False).
NEGATIVE CONTROL: test_negative_control_without_propagation_the_unverified_voice_sees_sensitive_rows swaps the context
carrier for a pass-through and the (b) assertion goes red. Synthetic ids only.
"""
from __future__ import annotations

import asyncio
import base64
import sys
import types

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

from fastapi import FastAPI
from fastapi.testclient import TestClient

import restraint

MEMBER = "member-sv-1"
SENSITIVE = "The cardiologist appointment is on Friday."   # a health-class row the packet might carry
SPEAKERS = {
    # (payload extras, claim accepted by the server?, expected verdict)
    "verified_member": ({"voice_user_id": MEMBER, "voice_score": 0.91, "voice_model_id": "wespeaker_resnet34+i8qdq-u8s8+c1"}, True, True),
    "unverified_voice": ({"voice_user_id": MEMBER, "voice_score": 0.10, "voice_model_id": "wespeaker_resnet34+i8qdq-u8s8+c1"}, False, False),
    "guest": ({"speaker": {"verified": False}}, False, False),
}


def _toy_router(monkeypatch):
    import semantic_router as sr

    monkeypatch.setenv("ZOE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "off")
    monkeypatch.setattr(sr, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(sr, "ROUTES", {"weather": ["w"], "chat": ["c"]})
    monkeypatch.setattr(sr, "_MATRIX", np.eye(2, dtype=np.float32))
    monkeypatch.setattr(sr, "_DOM_IDX", {"weather": np.array([0]), "chat": np.array([1])})
    monkeypatch.setattr(sr, "embed", lambda _t: np.array([0.1, 0.95], dtype=np.float32))


def _app(monkeypatch, seen: list, *, claim_ok: bool, filler: bool):
    import brain_dispatch
    import routers.voice_tts as vt
    from database import get_db

    monkeypatch.setenv("ZOE_RESTRAINT", "enforce")
    monkeypatch.setenv("ZOE_VOICE_FILLER_ENABLED", "1" if filler else "0")
    monkeypatch.setenv("ZOE_VOICE_FILLER_AFTER_S", "0.05")
    monkeypatch.setenv("ZOE_VOICE_GREETING_ENABLED", "0")
    _toy_router(monkeypatch)
    restraint._reset_state()

    async def stt(_path, **_kw):
        return "tell me about the cardiologist"   # a TOPIC pull: a member may hear the row, an unconfirmed voice may not

    async def tts(_text):
        return b"RIFFfake"

    class _Audio:
        body = b"RIFFfake"
        media_type = "audio/wav"
        headers: dict = {}

    async def synthesize(_payload, caller=None):
        return _Audio()

    async def not_handled(*_a, **_k):
        return {"handled": False}

    async def nothing(*_a, **_k):
        return None

    class _Broadcaster:
        async def broadcast(self, *_a, **_k):
            return 1

    async def claim(payload, caller):
        return MEMBER if claim_ok and (payload or {}).get("voice_user_id") else None

    async def brain(message, session_id, user_id="", **_kw):
        # What every lane does at the top of a turn (zoe_flue_client / zoe_core_client): note the owner's words, then
        # judge the packet rows against the turn - from INSIDE the lazily-pulled stream body.
        await asyncio.sleep(0.12)   # outlive the filler budget, so the filler race's first-pull task is exercised
        restraint.note_turn(user_id, message)
        turn = restraint.turn_for(user_id, message)
        dec = restraint.decide(SENSITIVE, restraint.classify(SENSITIVE), turn, surface="packet")
        seen.append({"user_id": user_id, "verdict": restraint.current_verdict(), "turn_verdict": turn.verdict,
                     "allow_sensitive": dec.allow, "reason": dec.reason})
        yield "Here is what I know."

    import skybridge_service

    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", not_handled)
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=_Broadcaster()))
    monkeypatch.setattr(vt, "_transcribe_audio", stt)
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", tts)
    monkeypatch.setattr(vt, "synthesize", synthesize)
    monkeypatch.setattr(vt, "_accepted_voice_claim", claim)
    monkeypatch.setattr(vt, "_broadcast_skybridge_ui", nothing)
    monkeypatch.setattr(vt, "_resolve_recent_panel_session_user", nothing)
    async def save(_sid, _user_text, _reply, _uid, **_kw):
        seen.append(("save", vt._USER_TURN_SAVED.get()))

    monkeypatch.setattr(vt, "_schedule_voice_chat_save", save)
    monkeypatch.setattr(vt, "_run_voice_memory_passes", nothing)
    import routers.chat as chat_router

    monkeypatch.setattr(chat_router, "_ensure_user_and_chat_session", nothing)
    monkeypatch.setattr(chat_router, "_save_chat_message", nothing)
    monkeypatch.setattr(vt, "_VOICE_SESSIONS", {})
    monkeypatch.setattr(brain_dispatch, "brain_streaming", brain)

    app = FastAPI()
    app.include_router(vt.router)
    app.dependency_overrides[vt._require_voice_auth] = lambda: {
        "source": "device", "panel_id": "hall-panel", "user_id": "voice-daemon"}
    app.dependency_overrides[get_db] = lambda: None
    return app


def _turn(monkeypatch, speaker: str, *, filler: bool, route: str = "/api/voice/turn_stream") -> dict:
    extras, claim_ok, _ = SPEAKERS[speaker]
    seen: list = []
    app = _app(monkeypatch, seen, claim_ok=claim_ok, filler=filler)
    payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(), "panel_id": "hall-panel", **extras}
    with TestClient(app) as client:
        r = client.post(route, json=payload)
        assert r.status_code == 200, r.text
        frames = [ln for ln in r.content.split(b"\n") if ln]
    if route.endswith("turn_stream"):
        assert any(b'"done"' in f for f in frames), frames
    turns = [x for x in seen if isinstance(x, dict)]
    assert len(turns) == 1, ("the stub brain must run exactly once inside the stream", seen, frames[:6])
    return dict(turns[0], saves=[x[1] for x in seen if isinstance(x, tuple)])


@pytest.mark.parametrize("filler", [False, True], ids=["no_filler", "filler_race"])
@pytest.mark.parametrize("speaker", list(SPEAKERS))
def test_the_brain_on_the_stream_path_sees_the_turns_speaker_verdict(monkeypatch, speaker, filler):
    expected = SPEAKERS[speaker][2]
    got = _turn(monkeypatch, speaker, filler=filler)
    assert got["verdict"] is expected, got
    assert got["turn_verdict"] is expected, got
    if expected is False:
        assert got["allow_sensitive"] is False and got["reason"] == "guest", (
            "a sensitive row reached an unconfirmed voice on the panel stream path", got)
    else:
        assert got["user_id"] == MEMBER and got["allow_sensitive"] is True, got
        # the sibling ContextVar: the stream finalizer's save now finds the arrival row voice_command bound, so a failed
        # arrival insert is recovered instead of the user row being lost
        assert got["saves"] and all(a is not None and a.text for a in got["saves"]), got["saves"]


@pytest.mark.parametrize("speaker", list(SPEAKERS))
def test_the_non_stream_turn_sees_the_same_verdict(monkeypatch, speaker):
    """/api/voice/turn awaits voice_command inline (no sub-task), so it always saw the binding: the stream path must agree."""
    expected = SPEAKERS[speaker][2]
    got = _turn(monkeypatch, speaker, filler=False, route="/api/voice/turn")
    assert got["verdict"] is expected and got["turn_verdict"] is expected, got


@pytest.mark.parametrize("speaker", list(SPEAKERS))
def test_negative_control_without_propagation_the_unverified_voice_sees_sensitive_rows(monkeypatch, speaker):
    """Remove the propagation (the carrier becomes a pass-through, the code before the fix): every speaker reads as NO
    verdict, the unconfirmed voice is ALLOWED the sensitive row, and the member's finalizer save loses the arrival row - the
    gaps this file pins. Proves the assertions above can go red."""
    import routers.voice_tts as vt

    monkeypatch.setattr(vt, "_stream_in_turn_context", lambda gen: gen)
    got = _turn(monkeypatch, speaker, filler=False)
    assert got["verdict"] is None and got["turn_verdict"] is None, got
    if SPEAKERS[speaker][2] is False:
        assert got["allow_sensitive"] is True, got
    else:
        assert got["saves"] and all(a is None for a in got["saves"]), got["saves"]


# -- the carrier itself (pure asyncio) ------------------------------------------------------------------------------------

import contextvars  # noqa: E402

_PROBE: contextvars.ContextVar = contextvars.ContextVar("stream_verdict_probe", default="unset")


def _carrier(agen):
    import routers.voice_tts as vt

    return vt._stream_in_turn_context(agen)


def test_carrier_steps_run_in_one_context_and_mutations_persist_across_steps():
    """Every step of the body sees the context of its CREATION and its own later writes, whatever task pulls it."""
    seen = []

    async def body():
        seen.append(_PROBE.get())
        _PROBE.set("written-in-step-1")
        yield 1
        seen.append(_PROBE.get())
        yield 2

    async def main():
        tok = _PROBE.set("turn")
        wrapped = _carrier(body())
        _PROBE.reset(tok)
        first = await asyncio.ensure_future(wrapped.__anext__())   # a separate task pulls step 1 (the filler race)
        rest = [x async for x in wrapped]                           # the caller pulls the rest
        return [first] + rest, _PROBE.get()

    items, caller_view = asyncio.run(main())
    assert items == [1, 2]
    assert seen == ["turn", "written-in-step-1"]
    assert caller_view == "unset", "the body's writes never leak into the consumer's context"


def test_carrier_is_lockstep_no_read_ahead():
    pulled = []

    async def body():
        for i in range(3):
            pulled.append(i)
            yield i

    async def main():
        wrapped = _carrier(body())
        first = await wrapped.__anext__()
        await asyncio.sleep(0.05)
        snapshot = list(pulled)
        await wrapped.aclose()
        return first, snapshot

    first, snapshot = asyncio.run(main())
    assert first == 0 and snapshot == [0], snapshot


def test_carrier_close_between_steps_runs_the_body_finally_with_generator_exit():
    events = []

    async def body():
        try:
            yield 1
            yield 2
        except GeneratorExit:
            events.append("generator_exit")
            raise
        finally:
            events.append("finally")

    async def main():
        wrapped = _carrier(body())
        assert await wrapped.__anext__() == 1
        await wrapped.aclose()

    asyncio.run(main())
    assert events == ["generator_exit", "finally"]


def test_carrier_consumer_cancelled_mid_step_cancels_the_body_step():
    events = []

    async def body():
        try:
            await asyncio.sleep(10)
            yield 1
        except asyncio.CancelledError:
            events.append("cancelled")
            raise
        finally:
            events.append("finally")

    async def main():
        wrapped = _carrier(body())
        task = asyncio.ensure_future(wrapped.__anext__())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await wrapped.aclose()
        await asyncio.sleep(0.01)

    asyncio.run(main())
    assert events == ["cancelled", "finally"]


@pytest.mark.parametrize("exc_type", [RuntimeError, asyncio.CancelledError])
def test_carrier_reraises_the_bodys_own_exception(exc_type):
    """A body error (and a body-raised CancelledError: a speculative turn's dropped side effect) reaches the consumer."""
    async def body():
        yield 1
        raise exc_type("boom")

    async def main():
        wrapped = _carrier(body())
        assert await wrapped.__anext__() == 1
        with pytest.raises(exc_type):
            await wrapped.__anext__()

    asyncio.run(main())


def test_carrier_pump_cancelled_between_steps_still_closes_the_body():
    """A consumer cancel that lands just after a step ended finds the pump idle: the body is closed, not abandoned."""
    import routers.voice_tts as vt

    events = []

    async def body():
        try:
            yield 1
            yield 2
        finally:
            events.append("finally")

    async def main():
        before = set(vt._BG_TASKS)
        wrapped = _carrier(body())
        assert await wrapped.__anext__() == 1
        (pump,) = set(vt._BG_TASKS) - before
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        snapshot = list(events)
        await wrapped.aclose()
        return snapshot, pump in vt._BG_TASKS

    snapshot, still_held = asyncio.run(main())
    assert snapshot == ["finally"]
    assert not still_held, "the strong reference is dropped once the pump ends"


def test_carrier_body_that_swallows_the_cancel_is_still_closed():
    """A body that catches the mid-step cancel and yields again must not leave the pump waiting forever."""
    import routers.voice_tts as vt

    events = []

    async def body():
        try:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                events.append("swallowed")
            yield 1
        finally:
            events.append("finally")

    async def main():
        before = set(vt._BG_TASKS)
        wrapped = _carrier(body())
        task = asyncio.ensure_future(wrapped.__anext__())
        await asyncio.sleep(0.05)
        (pump,) = set(vt._BG_TASKS) - before
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(asyncio.gather(pump, return_exceptions=True), 2)
        return pump.done()

    assert asyncio.run(main()) is True
    assert events == ["swallowed", "finally"]
