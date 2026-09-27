"""Per-turn llama-server prompt-cache reuse is visible in zoe-data's app log.

The 2.x sidecar forwards each model call's ``prompt_n`` (tokens re-prefilled)
and ``cache_n`` (tokens reused) on its ``{"done": true, "prompt_cache": [...]}``
NDJSON terminal (labs/flue-zoe-brain-2x src/streaming.ts). ``zoe_flue_client``
logs one ``FLUE_PROMPT_CACHE`` INFO line per turn on BOTH wire-2 read paths —
the live streaming path (``ZOE_FLUE_STREAM_ENABLED=1``) and the aggregated
one-shot path — so a slow first token can be attributed to a cache miss per
turn instead of being inferred from the llama-server journal. An older sidecar
(no field) logs nothing, and the deltas yielded are unchanged either way.
"""
import json
import logging

import pytest

import zoe_flue_client

pytestmark = pytest.mark.ci_safe


class _FakeStreamResponse:
    def __init__(self, lines):
        self.status_code = 200
        self.headers = {"content-type": "application/x-ndjson"}
        self._lines = list(lines)

    async def aread(self):
        return b""

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    stream_response = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, content=b"", headers=None):
        return _FakeClient.stream_response


async def _collect(gen):
    return [c async for c in gen]

DONE = {"done": True, "prompt_cache": [{"prompt_n": 762, "cache_n": 2400}, {"prompt_n": 18, "cache_n": 3174}]}


def _lines(done):
    return [json.dumps("It looks "), json.dumps("sunny."), json.dumps(done)]


def _cache_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("FLUE_PROMPT_CACHE")]


@pytest.fixture()
def wire2(monkeypatch):
    import httpx

    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://127.0.0.1:3579")
    monkeypatch.setenv("ZOE_BRAIN_TOKEN", "sekret")
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")

    async def _no_block(*_a, **_k):
        return ""

    monkeypatch.setattr(zoe_flue_client, "_recall_context_block", _no_block)
    monkeypatch.setattr(zoe_flue_client, "_pending_offer_block", _no_block)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.stream_response = None
    return _FakeClient


@pytest.mark.parametrize("stream", ["1", "0"], ids=["streaming", "aggregated"])
@pytest.mark.asyncio
async def test_done_terminal_prompt_cache_is_logged_per_turn(wire2, monkeypatch, caplog, stream):
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", stream)
    wire2.stream_response = _FakeStreamResponse(lines=_lines(DONE))
    with caplog.at_level(logging.INFO, logger=zoe_flue_client.logger.name):
        out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "voice-s1", "jason"))
    assert "".join(out) == "It looks sunny."
    assert _cache_lines(caplog) == [
        "FLUE_PROMPT_CACHE session=voice-s1 rounds=2 first_prompt_n=762 first_cache_n=2400 "
        "total_prompt_n=780 per_round=762/2400,18/3174"
    ]


@pytest.mark.asyncio
async def test_older_sidecar_without_the_field_logs_nothing(wire2, caplog):
    wire2.stream_response = _FakeStreamResponse(lines=_lines({"done": True}))
    with caplog.at_level(logging.INFO, logger=zoe_flue_client.logger.name):
        out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "voice-s1", "jason"))
    assert "".join(out) == "It looks sunny."
    assert _cache_lines(caplog) == []


@pytest.mark.parametrize("bad", [None, "x", [], [{"prompt_n": "nope"}], [1, 2]])
def test_malformed_prompt_cache_never_raises(caplog, bad):
    with caplog.at_level(logging.INFO, logger=zoe_flue_client.logger.name):
        zoe_flue_client._log_prompt_cache("s", {"done": True, "prompt_cache": bad})
    assert _cache_lines(caplog) == []
