"""ZOE_BRAIN_BACKEND cutover seam — additive, default-OFF.

Proves:
(a) default (env unset / 'core') → dispatch uses the EXISTING core client,
    byte-identical to today; the flue client is never touched.
(b) ZOE_BRAIN_BACKEND='flue' → dispatch selects the flue client, which hits the
    sidecar with the right URL + bearer + body, and yields the core stream shape.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.ci_safe


# ── (a) default → existing core path, flue untouched ──────────────────────────

def test_use_flue_brain_default_off(monkeypatch):
    import brain_dispatch as bd

    monkeypatch.delenv("ZOE_BRAIN_BACKEND", raising=False)
    assert bd.use_flue_brain() is False  # default OFF
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "core")
    assert bd.use_flue_brain() is False
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    assert bd.use_flue_brain() is True
    # Anything that isn't exactly 'flue' stays on core (fail-safe).
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "FLUE")  # case-insensitive opt-in
    assert bd.use_flue_brain() is True
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "something-else")
    assert bd.use_flue_brain() is False


@pytest.mark.asyncio
async def test_oneshot_default_uses_core_not_flue(monkeypatch):
    import brain_dispatch as bd
    import zoe_core_client
    import zoe_flue_client

    monkeypatch.delenv("ZOE_BRAIN_BACKEND", raising=False)  # default
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "true")

    async def fake_core(msg, sid, uid="", **kw):
        return f"core:{msg}:{uid}"

    async def boom_flue(msg, sid, uid="", **kw):  # must NOT be called
        raise AssertionError("flue client used while ZOE_BRAIN_BACKEND default/OFF")

    monkeypatch.setattr(zoe_core_client, "run_zoe_core", fake_core)
    monkeypatch.setattr(zoe_flue_client, "run_flue_brain", boom_flue)
    assert await bd.brain_oneshot("hi", "s", "jason") == "core:hi:jason"


@pytest.mark.asyncio
async def test_streaming_default_uses_core_not_flue(monkeypatch):
    import brain_dispatch as bd
    import zoe_core_client
    import zoe_flue_client

    monkeypatch.delenv("ZOE_BRAIN_BACKEND", raising=False)  # default
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "true")

    async def fake_core_stream(msg, sid, uid="", **kw):
        yield "a"
        yield "b"

    async def boom_flue_stream(msg, sid, uid="", **kw):  # must NOT be called
        raise AssertionError("flue stream used while default/OFF")
        yield  # pragma: no cover

    monkeypatch.setattr(zoe_core_client, "run_zoe_core_streaming", fake_core_stream)
    monkeypatch.setattr(zoe_flue_client, "run_flue_brain_streaming", boom_flue_stream)
    chunks = [c async for c in bd.brain_streaming("hi", "s", "jason")]
    assert chunks == ["a", "b"]


# ── (b) ZOE_BRAIN_BACKEND='flue' → flue client selected + correct HTTP ────────

class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _FakeClient:
    """Captures the single POST and returns a canned {result:{text}} body."""

    captured: dict = {}

    def __init__(self, *args, **kwargs):
        type(self).captured["client_kwargs"] = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        type(self).captured["url"] = url
        type(self).captured["content"] = content
        type(self).captured["headers"] = headers
        return _FakeResponse({"result": {"text": "hello from flue"}})


@pytest.mark.asyncio
async def test_flue_selected_and_hits_sidecar(monkeypatch):
    import brain_dispatch as bd
    import zoe_core_client

    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://127.0.0.1:3578")
    monkeypatch.setenv("ZOE_BRAIN_TOKEN", "sekret")

    # Core must NOT be reached when flue is selected.
    async def boom_core(msg, sid, uid="", **kw):
        raise AssertionError("core used while ZOE_BRAIN_BACKEND=flue")

    monkeypatch.setattr(zoe_core_client, "run_zoe_core", boom_core)

    _FakeClient.captured = {}
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    text = await bd.brain_oneshot("ping", "sess-7", "jason")
    assert text == "hello from flue"

    cap = _FakeClient.captured
    # Right endpoint: base + /agents/zoe/<session>?wait=result
    assert cap["url"] == "http://127.0.0.1:3578/agents/zoe/sess-7?wait=result"
    # Bearer token sent (sidecar fails closed without it).
    assert cap["headers"]["Authorization"] == "Bearer sekret"
    assert cap["headers"]["Content-Type"] == "application/json"
    # Body carries ONLY {message}: the acting user identity rides an envelope
    # prefix on the message (" zoe-uid:<id>\n<msg>"), NOT a separate body field,
    # because the sidecar's Flue payload schema drops any field other than
    # {message, images}. The sidecar reads + strips this prefix before the model.
    assert json.loads(cap["content"]) == {"message": " zoe-uid:jason\nping"}


@pytest.mark.asyncio
async def test_flue_stream_shape_matches_core(monkeypatch):
    """Streaming flue path yields plain text deltas — same shape as core
    (async iterator of str), so chat.py's sentinel handlers work unchanged."""
    import brain_dispatch as bd

    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.delenv("ZOE_BRAIN_TOKEN", raising=False)  # no token -> no auth header

    _FakeClient.captured = {}
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    chunks = [c async for c in bd.brain_streaming("hi", "s", "jason")]
    assert chunks == ["hello from flue"]
    assert all(isinstance(c, str) for c in chunks)
    # No bearer header when ZOE_BRAIN_TOKEN unset.
    assert "Authorization" not in _FakeClient.captured["headers"]


@pytest.mark.asyncio
async def test_flue_error_does_not_crash_turn(monkeypatch):
    """A sidecar failure yields a graceful error string, never raises."""
    import zoe_flue_client

    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")

    class _BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("connection refused")

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _BoomClient)

    # Must not raise; returns a non-empty graceful string.
    out = await zoe_flue_client.run_flue_brain("hi", "s", "jason")
    assert isinstance(out, str)
    assert out  # graceful fallback, not blank


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_body", [{"result": {}}, {"result": {"text": ""}}, {}])
async def test_flue_empty_success_yields_fallback(monkeypatch, empty_body):
    """An HTTP 200 with no usable text must NOT end the stream blank — it yields
    the same graceful fallback as a transport error, so the assistant turn is
    never rendered empty."""
    import zoe_flue_client

    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")

    class _EmptyClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            return _FakeResponse(empty_body)

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _EmptyClient)

    chunks = [c async for c in zoe_flue_client.run_flue_brain_streaming("hi", "s", "jason")]
    assert chunks == [zoe_flue_client._FALLBACK_TEXT]
    # And the oneshot collector returns the same non-blank text.
    out = await zoe_flue_client.run_flue_brain("hi", "s", "jason")
    assert out == zoe_flue_client._FALLBACK_TEXT


@pytest.mark.asyncio
async def test_chat_router_helper_selects_flue(monkeypatch):
    """The chat.py dispatch helpers honor the same default-OFF seam."""
    import routers.chat as chat

    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    assert chat._use_flue_brain() is True
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "core")
    assert chat._use_flue_brain() is False
    monkeypatch.delenv("ZOE_BRAIN_BACKEND", raising=False)
    assert chat._use_flue_brain() is False


def test_identity_envelope_strips_embedded_newlines():
    """A newline inside the user_id must not break the single-line identity
    envelope (which would leak the remainder into the model prompt)."""
    import zoe_flue_client as zc

    for raw in ("ali\nce", "ali\rce", "ali\r\nce", "\nalice\n"):
        wrapped = zc._wrap_message_with_identity("what do you know about me?", raw)
        first_line = wrapped.split("\n", 1)[0]
        # Envelope stays one clean line; the id carries no CR/LF.
        assert "\r" not in first_line and "\n" not in first_line
        assert first_line == f"{zc._IDENTITY_ENVELOPE_PREFIX}alice"
    # An injection attempt is flattened, not split across lines.
    wrapped = zc._wrap_message_with_identity("hi", "alice\ninjected")
    assert wrapped.split("\n", 1)[0] == f"{zc._IDENTITY_ENVELOPE_PREFIX}aliceinjected"
    # Empty/whitespace id leaves the message untouched.
    assert zc._wrap_message_with_identity("hi", "  ") == "hi"
