"""Seam-A NDJSON streaming in zoe_flue_client (ZOE_FLUE_STREAM_ENABLED).

The sidecar has streamed since #971 (labs/flue-zoe-brain src/streaming.ts) but
the client kept ?wait=result — voice TTS waited for the WHOLE reply. These pin
the new client mode: deltas yielded as they arrive, terminal-line handling, and
the no-re-POST rules (a 2xx admission means the sidecar is executing the turn;
re-POSTing is the #1137 duplicate-write class).
"""
import json

import pytest

pytestmark = pytest.mark.ci_safe

import zoe_flue_client


class _FakeStreamResponse:
    def __init__(self, status_code=200, lines=(), content_type="application/x-ndjson"):
        self.status_code = status_code
        self._lines = list(lines)
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_lines(self):
        for line in self._lines:
            if isinstance(line, Exception):
                raise line
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    """Captures stream/post calls; scripted responses."""

    stream_response = None
    post_response = None
    calls = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, content=b"", headers=None):
        _FakeClient.calls.append(("stream", url, dict(headers or {})))
        resp = _FakeClient.stream_response
        if isinstance(resp, Exception):
            raise resp
        return resp

    async def post(self, url, content=b"", headers=None):
        _FakeClient.calls.append(("post", url, dict(headers or {})))
        return _FakeClient.post_response


class _FakePostResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


@pytest.fixture()
def flue_env(monkeypatch):
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://127.0.0.1:3578")
    monkeypatch.setenv("ZOE_BRAIN_TOKEN", "sekret")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")
    monkeypatch.setenv("ZOE_RECALL_CONTEXT_ENABLED", "0")

    async def _no_recall(message, uid):
        return ""

    monkeypatch.setattr(zoe_flue_client, "_recall_context_block", _no_recall)
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.calls = []
    _FakeClient.stream_response = None
    _FakeClient.post_response = None
    return _FakeClient


async def _collect(gen):
    return [c async for c in gen]


@pytest.mark.asyncio
async def test_stream_yields_deltas_and_stops_on_done(flue_env):
    flue_env.stream_response = _FakeStreamResponse(lines=[
        json.dumps("Hello"),
        json.dumps(" there"),
        json.dumps("__TOOL__:" + '{"phase": "start", "id": "t1", "name": "recall_memory"}'),
        json.dumps("__THINKING__:let me look that up"),
        json.dumps({"done": True}),
    ])
    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))
    assert out == [
        "Hello",
        " there",
        '__TOOL__:{"phase": "start", "id": "t1", "name": "recall_memory"}',
        "__THINKING__:let me look that up",
    ]
    # streaming endpoint (no ?wait=result) with the Accept header; no wait=result POST
    kinds = [c[0] for c in flue_env.calls]
    assert kinds == ["stream"]
    _, url, headers = flue_env.calls[0]
    assert "wait=result" not in url
    assert headers.get("Accept") == "application/x-ndjson"


@pytest.mark.asyncio
async def test_stream_error_line_before_text_yields_fallback_no_repost(flue_env):
    flue_env.stream_response = _FakeStreamResponse(lines=[json.dumps({"error": "boom"})])
    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))
    assert out == [zoe_flue_client._FALLBACK_TEXT]
    assert [c[0] for c in flue_env.calls] == ["stream"]  # never re-POSTed


@pytest.mark.asyncio
async def test_stream_dies_mid_text_ends_turn_without_repost(flue_env):
    flue_env.stream_response = _FakeStreamResponse(lines=[
        json.dumps("First sentence."),
        RuntimeError("connection reset"),
    ])
    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))
    assert out == ["First sentence."]  # tail lost, turn NOT re-run
    assert [c[0] for c in flue_env.calls] == ["stream"]


@pytest.mark.asyncio
async def test_stream_dies_after_admission_before_text_no_repost(flue_env):
    flue_env.stream_response = _FakeStreamResponse(lines=[RuntimeError("reset before first delta")])
    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))
    assert out == [zoe_flue_client._FALLBACK_TEXT]
    assert [c[0] for c in flue_env.calls] == ["stream"], "2xx admission means the turn runs — no re-POST"


@pytest.mark.asyncio
async def test_stream_connect_failure_falls_back_to_wait_result(flue_env):
    flue_env.stream_response = ConnectionError("refused")  # pre-admission
    flue_env.post_response = _FakePostResponse({"result": {"text": "whole reply"}})
    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))
    assert out == ["whole reply"]
    kinds = [c[0] for c in flue_env.calls]
    assert kinds == ["stream", "post"]
    assert "wait=result" in flue_env.calls[1][1]


@pytest.mark.asyncio
async def test_stream_misconfig_non_ndjson_yields_fallback_no_repost(flue_env):
    # Sidecar kill-switched (ZOE_BRAIN_STREAM=0): plain POST = 202 admission,
    # the turn RUNS async — re-POSTing would double-execute it.
    flue_env.stream_response = _FakeStreamResponse(status_code=202, lines=[], content_type="application/json")
    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))
    assert out == [zoe_flue_client._FALLBACK_TEXT]
    assert [c[0] for c in flue_env.calls] == ["stream"]


@pytest.mark.asyncio
async def test_flag_off_uses_wait_result_only(flue_env, monkeypatch):
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "0")
    flue_env.post_response = _FakePostResponse({"result": {"text": "classic"}})
    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))
    assert out == ["classic"]
    assert [c[0] for c in flue_env.calls] == ["post"]
