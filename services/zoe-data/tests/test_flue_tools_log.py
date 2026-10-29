"""``FLUE_TOOLS``: the brain's own tool calls are visible in zoe-data's log.

Before this, a Flue-lane turn logged nothing about the tools the model called, so
"zero memory sources" could not be told from "the model never called recall_memory".
``zoe_flue_client`` now logs ONE ``FLUE_TOOLS`` line per turn on BOTH wire-2 read paths
(streaming and aggregated), from the ``__TOOL__:`` sentinels already on the stream:
tool names in order, the ``recall_memory`` outcome class, wall-clock ms. Never
arguments, never result text. Instrumentation must never change the turn.

Negative control: stub ``_ToolTally.log`` to a no-op and the exact-line tests fail.
"""
import json
import logging
import re

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


def _tool(**ev):
    return json.dumps("__TOOL__:" + json.dumps(ev))


def _tool_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("FLUE_TOOLS")]


@pytest.fixture()
def wire2(monkeypatch):
    import httpx

    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://127.0.0.1:3579")
    monkeypatch.setenv("ZOE_BRAIN_TOKEN", "sekret")
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")

    async def _no_block(*_a, **_k):
        return ""

    monkeypatch.setattr(zoe_flue_client, "_recall_context_block", _no_block)
    monkeypatch.setattr(zoe_flue_client, "_pending_offer_block", _no_block)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.stream_response = None
    return _FakeClient


def _wrap(mid):
    return [json.dumps("Hi "), *mid, json.dumps("there."), json.dumps({"done": True})]


async def _run(wire2, monkeypatch, caplog, stream, lines):
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", stream)
    wire2.stream_response = _FakeStreamResponse(lines=lines)
    with caplog.at_level(logging.INFO, logger=zoe_flue_client.logger.name):
        out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "voice-s1", "jason"))
    return out


_LINE_RE = r"^FLUE_TOOLS session=voice-s1 tools=\[{tools}\] recall_memory={recall} ms=\d+$"


@pytest.mark.parametrize("stream", ["1", "0"], ids=["streaming", "aggregated"])
@pytest.mark.asyncio
async def test_tool_names_and_recall_outcome_logged(wire2, monkeypatch, caplog, stream):
    mid = [
        _tool(phase="start", id="c1", name="get_time"),
        _tool(phase="args", id="c1", name="get_time", args={"secret": "ARGS-LEAK"}),
        _tool(phase="start", id="c2", name="recall_memory"),
        _tool(phase="args", id="c2", name="recall_memory", args={"query": "ARGS-LEAK"}),
        _tool(phase="result", id="c2", result="[What you remember] RESULT-LEAK locker beef42"),
    ]
    out = await _run(wire2, monkeypatch, caplog, stream, _wrap(mid))
    assert out and "".join(c for c in out if not c.startswith("__TOOL__")) == "Hi there."
    lines = _tool_lines(caplog)
    assert len(lines) == 1
    assert re.match(_LINE_RE.format(tools="get_time,recall_memory", recall="called:yes result:packet"), lines[0]), lines
    assert "LEAK" not in lines[0] and "beef42" not in lines[0]


@pytest.mark.parametrize("stream", ["1", "0"], ids=["streaming", "aggregated"])
@pytest.mark.asyncio
async def test_no_tools_logs_empty_list(wire2, monkeypatch, caplog, stream):
    out = await _run(wire2, monkeypatch, caplog, stream, _wrap([]))
    assert "".join(out) == "Hi there."
    lines = _tool_lines(caplog)
    assert len(lines) == 1
    assert re.match(_LINE_RE.format(tools="", recall="called:no"), lines[0]), lines


@pytest.mark.parametrize("stream", ["1", "0"], ids=["streaming", "aggregated"])
@pytest.mark.asyncio
async def test_malformed_frames_do_not_touch_the_turn(wire2, monkeypatch, caplog, stream):
    mid = [
        json.dumps("__TOOL__:{not json"),
        json.dumps("__TOOL__:[1,2]"),
        _tool(phase="start", id=None, name=None),
        _tool(phase="start", id="c9", name="shopping_list_add"),
        _tool(phase="result", id="zzz", result=12),
    ]
    out = await _run(wire2, monkeypatch, caplog, stream, _wrap(mid))
    assert "".join(c for c in out if not c.startswith("__TOOL__")) == "Hi there."
    lines = _tool_lines(caplog)
    assert len(lines) == 1
    assert re.match(_LINE_RE.format(tools="shopping_list_add", recall="called:no"), lines[0]), lines


@pytest.mark.asyncio
async def test_recall_empty_and_error_classes(caplog):
    for result, want in (("I don't have anything stored about that yet.", "empty"),
                         ("I couldn't reach my memory right now.", "error")):
        t = zoe_flue_client._ToolTally()
        t.feed("__TOOL__:" + json.dumps({"phase": "start", "id": "a", "name": "recall_memory"}))
        t.feed("__TOOL__:" + json.dumps({"phase": "result", "id": "a", "result": result}))
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=zoe_flue_client.logger.name):
            t.log("s")
        assert f"called:yes result:{want}" in _tool_lines(caplog)[0]
    t = zoe_flue_client._ToolTally()
    t.feed("__TOOL__:" + json.dumps({"phase": "start", "id": "a", "name": "recall_memory"}))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=zoe_flue_client.logger.name):
        t.log("s")
    assert "called:yes result:unknown" in _tool_lines(caplog)[0]


@pytest.mark.parametrize("bad", [None, 5, "", "__TOOL__:", "__TOOL__:null", "x" * 10])
def test_feed_and_log_never_raise(bad, monkeypatch):
    t = zoe_flue_client._ToolTally()
    t.feed(bad)
    monkeypatch.setattr(zoe_flue_client.logger, "info", lambda *a, **k: 1 / 0)
    t.log("s")  # a failing logger must not escape
