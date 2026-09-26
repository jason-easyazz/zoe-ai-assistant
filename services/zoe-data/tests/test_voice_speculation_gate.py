"""B1.1 speculation gate on /api/voice/turn_stream (ZOE_SPECULATIVE_TURN, default OFF).

Contract (docs/architecture/b1-speculative-turn-start.md):
  (a) flag OFF  -> the wire bytes are identical whether or not the daemon sends
      ``speculative``/``turn_id``; no gate is ever registered; verdicts are 409.
  (b) flag ON   -> nothing audible is emitted after a cancel (explicit or the
      max-hold valve), and the stream ends with a ``cancelled`` done frame.
  (c) flag ON   -> a ``resolve`` whose final transcript is equivalent to the
      speculative one releases the held audio, in order, exactly once.
  (d) negative control: the assertion used by (b) goes RED when the gate is
      bypassed (a pass-through in its place), so a silent bypass cannot read green.

Pure asyncio for the gate itself, TestClient for the router wiring; STT, TTS
and the brain are fakes (no models, no network, no DB).
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest

pytestmark = pytest.mark.ci_safe

from fastapi import FastAPI
from fastapi.testclient import TestClient

import voice_speculation as vs
import routers.voice_tts as vt


# ── helpers ──────────────────────────────────────────────────────────────

def _line(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


B64 = base64.b64encode(b"RIFFfakewav") + b"\n"


def _parse(frames: list[bytes]) -> list[dict]:
    out = []
    for f in frames:
        try:
            out.append(json.loads(f))
        except Exception:
            out.append({"_raw_audio": True})
    return out


def _audible(frames: list[bytes]) -> list[bytes]:
    return [f for f in frames if vs.is_audible_frame(f)]


def _assert_nothing_audible_after_cancel(frames: list[bytes]) -> None:
    """The (b) invariant, shared with the negative control (d)."""
    parsed = _parse(frames)
    assert not _audible(frames), f"audible frame leaked through a cancelled speculation: {parsed}"
    assert parsed and parsed[-1].get("done") and parsed[-1].get("cancelled"), parsed


async def _run_gate(upstream_factory, resolver, *, max_hold_ms="5000", monkeypatch=None):
    """Drive gate_frames against an upstream generator; ``resolver(gate)`` is
    awaited concurrently and delivers the verdict."""
    if monkeypatch is not None:
        monkeypatch.setenv(vs.MAX_HOLD_FLAG, max_hold_ms)
    gate = vs.open_gate("t-" + str(id(upstream_factory)))
    gate.speculative_transcript = "turn on the kitchen light"
    upstream = upstream_factory()
    out: list[bytes] = []

    async def _consume():
        async for f in vs.gate_frames(upstream, gate):
            out.append(f)

    await asyncio.gather(_consume(), resolver(gate))
    assert vs.get_gate(gate.turn_id) is None, "gate must be unregistered when the stream ends"
    return out, gate


def _brain_stream(closed: list | None = None):
    """A realistic _wrapped(): transcript, two spoken sentences, done."""
    async def _gen():
        try:
            yield _line({"transcript": "turn on the kitchen light"})
            await asyncio.sleep(0.02)
            yield _line({"chunk": 0, "text": "Sure.", "provider": "kokoro"})
            yield B64
            await asyncio.sleep(0.02)
            yield _line({"chunk": 1, "text": "Kitchen light on.", "provider": "kokoro"})
            yield B64
            await asyncio.sleep(0.5)  # the brain is still finishing when the verdict lands
            yield _line({"done": True, "reply": "Sure. Kitchen light on."})
        finally:
            if closed is not None:
                closed.append(True)
    return _gen


# ── (c) commit / equivalent release, in order, once ──────────────────────

def test_commit_releases_held_audio_in_order_exactly_once(monkeypatch):
    async def _commit(gate):
        await asyncio.sleep(0.15)  # both sentences are held by now
        gate.resolve("commit")

    out, gate = asyncio.run(_run_gate(_brain_stream(), _commit, monkeypatch=monkeypatch))
    parsed = _parse(out)
    assert parsed[0].get("speculation") == "gated", "a gated stream leads with the ack"
    assert parsed[1] == {"transcript": "turn on the kitchen light"}, "transcript passes through before the verdict"
    assert [p.get("chunk") for p in parsed if "chunk" in p] == [0, 1]
    assert len(_audible(out)) == 4  # 2 headers + 2 b64 lines, no duplicates
    assert parsed[-1].get("done") and not parsed[-1].get("cancelled")
    assert gate.verdict() == "commit"


def test_equivalent_final_transcript_releases_held_audio(monkeypatch):
    async def _resolve_equiv(gate):
        await asyncio.sleep(0.15)
        gate.resolve("resolve", final_transcript="Turn on the kitchen light!")  # case + punctuation differ

    out, gate = asyncio.run(_run_gate(_brain_stream(), _resolve_equiv, monkeypatch=monkeypatch))
    assert gate.verdict() == "equivalent"
    assert len(_audible(out)) == 4
    assert _parse(out)[-1].get("done") and not _parse(out)[-1].get("cancelled")


def test_non_equivalent_final_transcript_cancels(monkeypatch):
    closed: list = []

    async def _resolve_diff(gate):
        await asyncio.sleep(0.15)
        gate.resolve("resolve", final_transcript="turn on the kitchen light and the fan")

    out, gate = asyncio.run(_run_gate(_brain_stream(closed), _resolve_diff, monkeypatch=monkeypatch))
    assert gate.verdict() == "cancel"
    _assert_nothing_audible_after_cancel(out)
    assert closed, "upstream must be closed on cancel (its finally cancels the brain task)"


# ── (b) cancel: nothing audible, ever ────────────────────────────────────

def test_explicit_cancel_drops_held_audio(monkeypatch):
    closed: list = []

    async def _cancel(gate):
        await asyncio.sleep(0.15)
        gate.resolve("cancel")

    out, _ = asyncio.run(_run_gate(_brain_stream(closed), _cancel, monkeypatch=monkeypatch))
    _assert_nothing_audible_after_cancel(out)
    assert closed


def test_cancel_before_any_audio_never_forwards_later_audio(monkeypatch):
    async def _early_cancel(gate):
        gate.resolve("cancel")  # before the upstream produced anything

    out, _ = asyncio.run(_run_gate(_brain_stream(), _early_cancel, monkeypatch=monkeypatch))
    _assert_nothing_audible_after_cancel(out)


def test_no_verdict_hits_max_hold_and_cancels(monkeypatch):
    async def _never(gate):
        await asyncio.sleep(0.05)

    out, gate = asyncio.run(_run_gate(_brain_stream(), _never, max_hold_ms="200", monkeypatch=monkeypatch))
    assert gate.verdict() == "hold_timeout"
    _assert_nothing_audible_after_cancel(out)


def test_late_duplicate_verdict_is_ignored(monkeypatch):
    async def _cancel_then_commit(gate):
        await asyncio.sleep(0.15)
        gate.resolve("cancel")
        gate.resolve("commit")  # a late commit must not resurrect dropped audio

    out, gate = asyncio.run(_run_gate(_brain_stream(), _cancel_then_commit, monkeypatch=monkeypatch))
    assert gate.action == "cancel"
    _assert_nothing_audible_after_cancel(out)


def test_upstream_with_no_audio_ends_without_waiting_for_a_verdict(monkeypatch):
    async def _text_only():
        yield _line({"transcript": "hmm"})
        yield _line({"done": True, "reply": ""})

    async def _never(gate):
        return None

    out, _ = asyncio.run(_run_gate(lambda: _text_only(), _never, max_hold_ms="5000", monkeypatch=monkeypatch))
    assert [p.get("done") for p in _parse(out)] == [None, None, True]


# ── review round 1 (Greptile/Copilot on #1685) ────────────────────────────

def test_cancel_before_upstream_started_still_closes_upstream(monkeypatch):
    """A cancel that lands while STT is still running resolves the gate before
    the router's generator was ever pulled. ``aclose()`` on a never-started
    async generator skips its ``finally`` — which is where the brain task gets
    cancelled — so the gate must prime the upstream before closing it."""
    monkeypatch.setenv(vs.MAX_HOLD_FLAG, "3000")
    closed = {"ran": False}

    async def upstream():
        try:
            yield _line({"transcript": "add milk"})
            yield _line({"chunk": 0, "text": "Added."})
            yield B64
        finally:
            closed["ran"] = True

    async def run():
        gate = vs.open_gate("early-cancel")
        gate.resolve("cancel")
        return [f async for f in vs.gate_frames(upstream(), gate)]

    out = asyncio.run(run())
    _assert_nothing_audible_after_cancel(out)
    assert closed["ran"], "upstream finally (brain cancel) never ran for an early cancel"
    assert vs.get_gate("early-cancel") is None


def test_open_gate_rejects_an_active_duplicate_turn_id():
    async def run():
        first = vs.open_gate("dup")
        try:
            with pytest.raises(vs.DuplicateTurn):
                vs.open_gate("dup")
            assert vs.get_gate("dup") is first, "the active gate must not be replaced"
            first.resolve("commit")
            # A resolved (finished) gate no longer blocks its id.
            second = vs.open_gate("dup")
            assert second is not first
            vs.close_gate(second)
        finally:
            vs.close_gate(first)

    asyncio.run(run())


def test_flag_on_duplicate_turn_id_is_409(monkeypatch):
    monkeypatch.setenv(vs.FLAG, "1")
    app = _app(monkeypatch, _dict_brain)
    import types
    monkeypatch.setitem(vs._GATES, "busy", types.SimpleNamespace(resolved=False))
    with TestClient(app) as client:
        payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(),
                   "speculative": True, "turn_id": "busy"}
        assert client.post("/api/voice/turn_stream", json=payload).status_code == 409


def test_flag_on_empty_speculative_transcript_ends_cancelled(monkeypatch):
    """An empty prefix transcript means the server processed NOTHING, so the
    daemon must run the full recording as a normal turn — it only does that on
    a ``cancelled`` done frame. The plain (non-speculative) empty path is
    unchanged."""
    monkeypatch.setenv(vs.FLAG, "1")
    app = _app(monkeypatch, _dict_brain)

    async def _empty_stt(_path, capture=True):
        return ""
    monkeypatch.setattr(vt, "_transcribe_audio", _empty_stt)
    with TestClient(app) as client:
        plain = _parse(_post_raw(client, {}).splitlines())
        assert plain == [{"transcript": "", "done": True, "reply": ""}]
        spec = _parse(_post_raw(client, {"speculative": True, "turn_id": "empty1"}).splitlines())
        assert spec[-1].get("done") and spec[-1].get("cancelled"), spec
        assert spec[-1].get("reason") == "empty_transcript"
        assert not _audible([_line(o) for o in spec])
        assert vs.get_gate("empty1") is None


# ── review round 2 (Greptile on 195f57cb) ─────────────────────────────────

def test_flag_on_gated_stream_leads_with_ack_carrying_turn_id(monkeypatch):
    """The ack is the daemon's proof that the server IS gating: audio on a
    speculative stream that never carried it means the server's flag is off
    (rollback / one-sided rollout). Emitted only on gated streams — the
    flag-off wire is pinned byte-identical elsewhere."""
    monkeypatch.setenv(vs.FLAG, "1")
    monkeypatch.setenv(vs.MAX_HOLD_FLAG, "3000")

    def brain(payload, caller=None, stream=True, db=None):
        async def _make():
            vs.get_gate("ack1").resolve("commit")
            return {"reply": "Noon.", "audio_base64": base64.b64encode(b"RIFFnoon").decode()}
        return _make()
    app = _app(monkeypatch, brain)
    with TestClient(app) as client:
        parsed = _parse(_post_raw(client, {"speculative": True, "turn_id": "ack1"}).splitlines())
    assert parsed[0] == {"speculation": "gated", "turn_id": "ack1"}
    assert parsed[-1].get("done") and not parsed[-1].get("cancelled")


def test_flag_on_empty_transcript_after_early_commit_still_ends_cancelled(monkeypatch):
    """fire→commit is ~320-480 ms, shorter than a Moonshine pass, so the
    commit routinely WINS the verdict slot before STT returns. An empty prefix
    transcript must still end the stream ``cancelled`` (nothing was processed;
    the daemon has to run the full recording) — it is an upstream fact, not a
    verdict, and must not lose to the slot."""
    monkeypatch.setenv(vs.FLAG, "1")
    app = _app(monkeypatch, _dict_brain)

    async def _stt_commit_wins(_path, capture=True):
        vs.get_gate("early2").resolve("commit")  # daemon's commit lands mid-STT
        return ""
    monkeypatch.setattr(vt, "_transcribe_audio", _stt_commit_wins)
    with TestClient(app) as client:
        spec = _parse(_post_raw(client, {"speculative": True, "turn_id": "early2"}).splitlines())
    assert spec[0] == {"speculation": "gated", "turn_id": "early2"}
    assert spec[-1].get("done") and spec[-1].get("cancelled"), spec
    assert spec[-1].get("reason") == "empty_transcript"
    assert not _audible([_line(o) for o in spec])
    assert vs.get_gate("early2") is None


# ── (d) negative control: the invariant must go red when the gate is bypassed ─

def test_negative_control_bypassed_gate_is_caught(monkeypatch):
    """Replace the gate with a pass-through (what a silent bypass looks like)
    and assert the (b) checker REJECTS the result. If this test ever passes
    with the checker green, the checker — not the gate — is broken."""
    async def _passthrough(upstream, gate):
        async for f in upstream:
            yield f
        vs.close_gate(gate)

    monkeypatch.setattr(vs, "gate_frames", _passthrough)

    async def _cancel(gate):
        await asyncio.sleep(0.15)
        gate.resolve("cancel")

    out, _ = asyncio.run(_run_gate(_brain_stream(), _cancel, monkeypatch=monkeypatch))
    assert _audible(out), "control precondition: the bypass must actually leak audio"
    with pytest.raises(AssertionError):
        _assert_nothing_audible_after_cancel(out)


# ── transcript equivalence ───────────────────────────────────────────────

@pytest.mark.parametrize("a,b,expected", [
    ("Turn on the light.", "turn on the light", True),
    ("turn on the light", "turn on the light and the fan", False),
    ("", "", False),
    ("hello", "", False),
    (None, "hello", False),
])
def test_transcripts_equivalent(a, b, expected):
    assert vs.transcripts_equivalent(a, b) is expected


# ── router wiring (TestClient; fakes only) ───────────────────────────────

def _app(monkeypatch, brain):
    monkeypatch.setenv("ZOE_VOICE_FILLER_ENABLED", "0")
    monkeypatch.setenv("ZOE_VOICE_GREETING_ENABLED", "0")

    async def _fake_stt(_path, capture=True):
        return "what time is it"
    monkeypatch.setattr(vt, "_transcribe_audio", _fake_stt)

    async def _fake_tts(_text):
        return b"RIFFfakewav"
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", _fake_tts)
    monkeypatch.setattr(vt, "voice_command", brain)

    app = FastAPI()
    app.include_router(vt.router)
    app.dependency_overrides[vt._require_voice_auth] = lambda: {
        "source": "device", "panel_id": "test-panel", "user_id": "voice-daemon",
    }
    from database import get_db as _real_get_db
    app.dependency_overrides[_real_get_db] = lambda: None
    return app


def _dict_brain(payload, caller=None, stream=True, db=None):
    async def _inner():
        return {"reply": "It is noon.", "audio_base64": base64.b64encode(b"RIFFnoon").decode()}
    return _inner()


def _post_raw(client, extra: dict) -> bytes:
    payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(), "panel_id": "test-panel", **extra}
    r = client.post("/api/voice/turn_stream", json=payload)
    assert r.status_code == 200
    return r.content


def test_flag_off_is_byte_identical_and_registers_no_gate(monkeypatch):
    monkeypatch.delenv(vs.FLAG, raising=False)
    app = _app(monkeypatch, _dict_brain)
    with TestClient(app) as client:
        plain = _post_raw(client, {})
        spec = _post_raw(client, {"speculative": True, "turn_id": "abc123"})
        assert plain == spec, "flag off: the speculative fields must change nothing on the wire"
        assert vs.get_gate("abc123") is None
        r = client.post("/api/voice/turn_stream/speculation", json={"turn_id": "abc123", "action": "commit"})
        assert r.status_code == 409


def test_flag_on_missing_turn_id_is_400(monkeypatch):
    monkeypatch.setenv(vs.FLAG, "1")
    app = _app(monkeypatch, _dict_brain)
    with TestClient(app) as client:
        payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(), "speculative": True}
        assert client.post("/api/voice/turn_stream", json=payload).status_code == 400


def test_flag_on_commit_from_verdict_endpoint_releases(monkeypatch):
    """End-to-end through the router: the brain's own generator posts the
    verdict once its audio is held (the daemon does this from a thread live)."""
    monkeypatch.setenv(vs.FLAG, "1")
    monkeypatch.setenv(vs.MAX_HOLD_FLAG, "3000")
    from fastapi.responses import StreamingResponse

    def brain(payload, caller=None, stream=True, db=None):
        async def _make():
            async def _gen():
                yield _line({"chunk": 0, "text": "It is noon."})
                yield B64
                await asyncio.sleep(0.05)
                gate = vs.get_gate("commit-1")
                assert gate is not None and gate.speculative_transcript == "what time is it"
                gate.resolve("commit")
                yield _line({"done": True, "reply": "It is noon."})
            return StreamingResponse(_gen(), media_type="application/x-zoe-audio-stream")
        return _make()

    app = _app(monkeypatch, brain)
    with TestClient(app) as client:
        body = _post_raw(client, {"speculative": True, "turn_id": "commit-1"})
    frames = [ln for ln in body.split(b"\n") if ln]
    parsed = _parse(frames)
    assert parsed[0] == {"speculation": "gated", "turn_id": parsed[0].get("turn_id")}
    assert parsed[1] == {"transcript": "what time is it"}
    assert len(_audible(frames)) == 2
    assert parsed[-1].get("done") and not parsed[-1].get("cancelled")
    assert vs.get_gate("commit-1") is None


def test_flag_on_hold_timeout_drops_audio_through_the_router(monkeypatch):
    monkeypatch.setenv(vs.FLAG, "1")
    monkeypatch.setenv(vs.MAX_HOLD_FLAG, "150")
    app = _app(monkeypatch, _dict_brain)
    with TestClient(app) as client:
        body = _post_raw(client, {"speculative": True, "turn_id": "timeout-1"})
    frames = [ln for ln in body.split(b"\n") if ln]
    _assert_nothing_audible_after_cancel(frames)
    assert _parse(frames)[-1].get("reason") == "hold_timeout"


def test_verdict_endpoint_resolve_transcribes_and_reports_verdict(monkeypatch):
    monkeypatch.setenv(vs.FLAG, "1")
    app = _app(monkeypatch, _dict_brain)
    with TestClient(app) as client:
        assert client.post("/api/voice/turn_stream/speculation",
                           json={"turn_id": "nope", "action": "commit"}).status_code == 404
        assert client.post("/api/voice/turn_stream/speculation",
                           json={"turn_id": "nope", "action": "explode"}).status_code == 400

        async def _register():
            g = vs.open_gate("resolve-1")
            g.speculative_transcript = "what time is it"
            return g
        gate = client.portal.call(_register)
        r = client.post("/api/voice/turn_stream/speculation", json={
            "turn_id": "resolve-1", "action": "resolve",
            "audio_base64": base64.b64encode(b"RIFF" + b"\x00" * 64).decode(),
        })
        assert r.status_code == 200, r.text
        assert r.json()["verdict"] == "equivalent"  # fake STT returns the same text
        assert gate.resolved and gate.final_transcript == "what time is it"
        vs.close_gate(gate)
