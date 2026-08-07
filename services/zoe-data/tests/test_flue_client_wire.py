"""ZOE_FLUE_WIRE — the Flue 1.x/2.x wire switch in ``zoe_flue_client``.

This module is on the LIVE VOICE PATH, so the contract has two halves and the
first one matters more:

  1. WIRE 1 IS UNCHANGED. With the flag unset (or set to anything that is not
     exactly ``'2'``) the client must emit the same bytes it emitted before the
     switch existed. ``fixtures/flue_wire1_golden_requests.json`` was RECORDED
     from ``origin/main``'s copy of the module — same recorder, same fake
     transport, same inputs — so the golden test below is a genuine
     before/after comparison, not a restatement of the current code.

  2. WIRE 2 SPEAKS THE MEASURED 2.x SHAPES. No ``wait`` param (Flue 2.x
     REJECTS any), a top-level ``{"kind": "user", "body": …}`` DeliveredMessage
     (the migration guide's nested form is refused with HTTP 400), and a
     non-streaming turn served by reading the turn's own Seam-A NDJSON stream to
     completion — there is no whole-result call left on 2.x.

Offline by construction (canned 2.x responses, faked httpx). The LIVE proof
against the vite-built ``labs/flue-zoe-brain-2x`` sidecar is evidence for the
PR, not something CI can run — CI has no sidecar.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

import zoe_flue_client

_GOLDEN = Path(__file__).parent / "fixtures" / "flue_wire1_golden_requests.json"

_NDJSON = "application/x-ndjson"


class _FakeStreamResponse:
    def __init__(self, lines=(), content_type=_NDJSON, status_code=200, raw=b""):
        self.status_code = status_code
        self._lines = list(lines)
        self.headers = {"content-type": content_type}
        self._raw = raw

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_lines(self):
        for line in self._lines:
            if isinstance(line, Exception):
                raise line
            yield line

    async def aread(self):
        return self._raw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakePostResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class _FakeClient:
    """Records every outbound request verbatim (url, headers, body bytes)."""

    stream_response = None
    post_response = None
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, content=b"", headers=None):
        _FakeClient.calls.append({
            "kind": "stream", "method": method, "url": url,
            "headers": dict(headers or {}), "body": content.decode(),
        })
        resp = _FakeClient.stream_response
        if isinstance(resp, Exception):
            raise resp
        return resp

    async def post(self, url, content=b"", headers=None):
        _FakeClient.calls.append({
            "kind": "post", "method": "POST", "url": url,
            "headers": dict(headers or {}), "body": content.decode(),
        })
        resp = _FakeClient.post_response
        if isinstance(resp, Exception):
            raise resp
        return resp


@pytest.fixture()
def wire_env(monkeypatch):
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://127.0.0.1:3578")
    monkeypatch.setenv("ZOE_BRAIN_TOKEN", "fixture-token")
    monkeypatch.delenv("ZOE_FLUE_WIRE", raising=False)

    async def _no_recall(message, uid):
        return ""

    async def _no_offer(uid):
        return ""

    monkeypatch.setattr(zoe_flue_client, "_recall_context_block", _no_recall)
    monkeypatch.setattr(zoe_flue_client, "_pending_offer_block", _no_offer)
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.calls = []
    _FakeClient.stream_response = None
    _FakeClient.post_response = None
    return _FakeClient


async def _collect(gen):
    return [c async for c in gen]


# ── 1. Wire 1 is byte-identical to origin/main ───────────────────────────────


@pytest.mark.asyncio
async def test_wire1_requests_are_byte_identical_to_main(wire_env, monkeypatch):
    """Replay the golden recording against the CURRENT module.

    The fixture holds the exact url / headers / body bytes and the exact yields
    that origin/main's client produced for these three scenarios. Any drift in
    the query string, the body shape, the header set, or the yielded deltas
    fails here — which is the whole safety argument for merging this dark.
    """
    golden = json.loads(_GOLDEN.read_text())
    ndjson_lines = [json.dumps("hello "), json.dumps("world"), json.dumps({"done": True})]

    for name, expected in sorted(golden.items()):
        monkeypatch.setenv(
            "ZOE_FLUE_STREAM_ENABLED", "1" if name == "stream_on" else "0"
        )
        message, sid, uid = (
            ("hi", "s2", "") if name == "stream_off_no_uid"
            else ("what is the weather", "sess/1 ?x", "jason")
        )
        _FakeClient.calls = []
        _FakeClient.stream_response = _FakeStreamResponse(lines=ndjson_lines)
        _FakeClient.post_response = _FakePostResponse({"result": {"text": "whole reply"}})

        got = await _collect(zoe_flue_client.run_flue_brain_streaming(message, sid, uid))

        assert _FakeClient.calls == expected["calls"], f"{name}: request drifted from main"
        assert got == expected["yields"], f"{name}: yields drifted from main"


@pytest.mark.asyncio
async def test_wire_flag_unset_is_provably_wire_one(wire_env, monkeypatch):
    """The default is not merely 'documented as 1' — it puts ?wait=result on the wire."""
    monkeypatch.delenv("ZOE_FLUE_WIRE", raising=False)
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "0")
    wire_env.post_response = _FakePostResponse({"result": {"text": "classic"}})

    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))

    assert out == ["classic"]
    assert zoe_flue_client._wire_version() == 1
    assert wire_env.calls[0]["url"].endswith("?wait=result")
    assert json.loads(wire_env.calls[0]["body"]) == {"message": " zoe-uid:jason\nhi"}


@pytest.mark.parametrize("value", ["", "1", " 1 "])
@pytest.mark.asyncio
async def test_wire_one_values(wire_env, monkeypatch, value):
    monkeypatch.setenv("ZOE_FLUE_WIRE", value)
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "0")
    wire_env.post_response = _FakePostResponse({"result": {"text": "classic"}})
    await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))
    assert wire_env.calls[0]["url"].endswith("?wait=result")


@pytest.mark.asyncio
async def test_unknown_wire_value_degrades_to_wire_one_loudly(wire_env, monkeypatch, caplog):
    """A typo must fall back to the DEPLOYED wire, and must say so."""
    monkeypatch.setenv("ZOE_FLUE_WIRE", "v2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "0")
    wire_env.post_response = _FakePostResponse({"result": {"text": "classic"}})

    with caplog.at_level(logging.ERROR, logger="zoe_flue_client"):
        out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))

    assert out == ["classic"]
    assert wire_env.calls[0]["url"].endswith("?wait=result")
    assert any("not a known Flue wire version" in r.message for r in caplog.records)


# ── 2. Wire 2 speaks the measured 2.x shapes ─────────────────────────────────


@pytest.mark.parametrize("stream", [False, True])
def test_wire2_endpoint_never_carries_a_wait_param(monkeypatch, stream):
    """Pin the builder itself, both branches — not just the paths in use today.

    The 2.x runtime rejects ANY ``wait`` param, so the invariant belongs to
    ``_endpoint``, not to whichever caller happens to reach it. Without this,
    reinstating ``?wait=result`` for the non-stream branch is invisible: wire 2
    only ever calls ``_endpoint(stream=True)`` right now, so no end-to-end test
    would notice until someone added a plain-POST path back.
    """
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://127.0.0.1:3578")
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    url = zoe_flue_client._endpoint("s1", stream=stream)
    assert url == "http://127.0.0.1:3578/agents/zoe/s1"
    assert "wait" not in url


@pytest.mark.parametrize(
    ("stream", "expected"),
    [(False, "http://127.0.0.1:3578/agents/zoe/s1?wait=result"),
     (True, "http://127.0.0.1:3578/agents/zoe/s1")],
)
def test_wire1_endpoint_is_unchanged(monkeypatch, stream, expected):
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://127.0.0.1:3578")
    monkeypatch.delenv("ZOE_FLUE_WIRE", raising=False)
    assert zoe_flue_client._endpoint("s1", stream=stream) == expected


def test_request_payload_per_wire(monkeypatch):
    monkeypatch.delenv("ZOE_FLUE_WIRE", raising=False)
    assert zoe_flue_client._request_payload("hi") == b'{"message": "hi"}'
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    assert zoe_flue_client._request_payload("hi") == b'{"kind": "user", "body": "hi"}'


@pytest.mark.asyncio
async def test_wire2_streaming_request_shape(wire_env, monkeypatch):
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")
    wire_env.stream_response = _FakeStreamResponse(lines=[
        json.dumps("Hello"), json.dumps(" there"), json.dumps({"done": True}),
    ])

    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))

    assert out == ["Hello", " there"]
    (call,) = wire_env.calls
    assert call["kind"] == "stream"
    assert "wait" not in call["url"], "Flue 2.x REJECTS any wait param"
    assert call["headers"]["Accept"] == _NDJSON
    assert json.loads(call["body"]) == {"kind": "user", "body": " zoe-uid:jason\nhi"}


@pytest.mark.asyncio
async def test_wire2_body_is_top_level_not_nested(wire_env, monkeypatch):
    """The migration guide's nested {"message": {...}} is refused with HTTP 400.

    Measured against the built 2.x sidecar (PR #1616); this pins the shape that
    actually works so a future doc-led 'fix' cannot regress it silently.
    """
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")
    wire_env.stream_response = _FakeStreamResponse(lines=[json.dumps({"done": True})])

    await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", ""))

    body = json.loads(wire_env.calls[0]["body"])
    assert body == {"kind": "user", "body": "hi"}
    assert not isinstance(body.get("message"), dict)


@pytest.mark.asyncio
async def test_wire2_nonstreaming_reads_the_stream_to_completion(wire_env, monkeypatch):
    """Flag off on wire 2: ONE joined delta, sentinels suppressed, no wait param.

    Observably identical to what wire-1 ?wait=result yields, so flipping
    ZOE_FLUE_WIRE alone changes the wire and nothing the caller sees.
    """
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "0")
    wire_env.stream_response = _FakeStreamResponse(lines=[
        json.dumps("Hello "),
        json.dumps('__TOOL__:{"phase": "start", "id": "t1", "name": "recall_memory"}'),
        json.dumps("__THINKING__:hmm"),
        json.dumps("world."),
        json.dumps({"done": True}),
    ])

    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))

    assert out == ["Hello world."]
    (call,) = wire_env.calls
    assert call["kind"] == "stream", "the only 2.x mechanism is the stream read"
    assert "wait" not in call["url"]
    assert call["headers"]["Accept"] == _NDJSON


@pytest.mark.asyncio
async def test_wire2_nonstreaming_error_line_yields_fallback(wire_env, monkeypatch):
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "0")
    wire_env.stream_response = _FakeStreamResponse(lines=[json.dumps({"error": "boom"})])

    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))

    assert out == [zoe_flue_client._FALLBACK_TEXT]
    assert [c["kind"] for c in wire_env.calls] == ["stream"], "never re-POST an admitted turn"


@pytest.mark.asyncio
async def test_wire2_nonstreaming_keeps_partial_text_on_mid_stream_death(wire_env, monkeypatch):
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "0")
    wire_env.stream_response = _FakeStreamResponse(lines=[
        json.dumps("First sentence."), RuntimeError("connection reset"),
    ])

    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))

    assert out == ["First sentence."]
    assert [c["kind"] for c in wire_env.calls] == ["stream"]


@pytest.mark.asyncio
async def test_wire2_never_falls_back_to_wait_result(wire_env, monkeypatch, caplog):
    """Pre-admission failure on wire 2 must NOT re-POST with ?wait=result.

    On wire 1 that fallback is correct; on 2.x the runtime answers it with a
    400, so the fallback would turn one failure into two.

    The log assertion is not decoration. There are TWO layers stopping the
    re-POST — this early return, and the unreachable-by-construction guard in
    front of the wait=result block — so asserting only on the yields passes even
    with this layer deleted, and the test would silently stop testing anything.
    Naming which layer fired keeps the control honest.
    """
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")
    wire_env.stream_response = ConnectionError("refused")
    wire_env.post_response = _FakePostResponse({"result": {"text": "must never be used"}})

    with caplog.at_level(logging.DEBUG, logger="zoe_flue_client"):
        out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))

    assert out == [zoe_flue_client._FALLBACK_TEXT]
    assert [c["kind"] for c in wire_env.calls] == ["stream"]
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "no wait=result fallback exists" in joined
    assert "reached the wait=result path" not in joined, "the last-resort guard should not be what saved us"


# ── 3. Negative controls: a wire mismatch must be LOUD, never a silent parse ──


@pytest.mark.asyncio
async def test_wire2_fed_a_wire1_whole_result_errors_loudly(wire_env, monkeypatch, caplog):
    """The v:2-sidecar-under-a-v:3-client case.

    ``_text_from_body`` is deliberately shape-tolerant, so a wire-2 path that
    reused it would happily return a 1.x ``{"result": {"text": …}}`` reply and
    nobody would ever learn the flag pointed at the wrong sidecar. Wire 2 must
    refuse it and NAME it. Paired with the control below, which proves the same
    canned response IS parsed on wire 1 — so this assertion cannot pass for the
    trivial reason that the fixture was unparseable.
    """
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "0")
    body = json.dumps({"result": {"text": "whole reply from a 1.x sidecar"}}).encode()
    wire_env.stream_response = _FakeStreamResponse(
        lines=[], content_type="application/json", raw=body,
    )

    with caplog.at_level(logging.ERROR, logger="zoe_flue_client"):
        out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))

    assert out == [zoe_flue_client._FALLBACK_TEXT]
    assert "whole reply from a 1.x sidecar" not in "".join(out)
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "1.x whole-result envelope" in joined
    assert "ZOE_FLUE_WIRE=1" in joined
    assert [c["kind"] for c in wire_env.calls] == ["stream"]


@pytest.mark.asyncio
async def test_negative_control_wire1_does_parse_that_same_body(wire_env, monkeypatch):
    """The control for the test above: on wire 1 this exact body IS the answer."""
    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "0")
    wire_env.post_response = _FakePostResponse(
        {"result": {"text": "whole reply from a 1.x sidecar"}}
    )

    out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))

    assert out == ["whole reply from a 1.x sidecar"]


# ── 4. The sentinel bytes are wire-version-independent ───────────────────────


@pytest.mark.asyncio
async def test_sentinel_output_is_identical_on_both_wires(wire_env, monkeypatch):
    """Downstream sentinel parsing must not care which wire produced the turn.

    ``routers/chat.py`` and ``routers/voice_tts.py`` dispatch on the exact
    ``__TOOL__:``/``__THINKING__:`` prefixes and json.loads the payload. The 2.x
    sidecar's src/streaming.ts differs from the deployed 1.x copy by exactly one
    deleted branch (the ?wait=result short-circuit), so the same NDJSON must
    produce the same yields under either flag value.
    """
    lines = [
        json.dumps("Sure — "),
        json.dumps('__TOOL__:{"phase": "start", "id": "call_1", "name": "recall_memory"}'),
        json.dumps('__TOOL__:{"phase": "args", "id": "call_1", "name": "recall_memory", "args": {"query": "x"}}'),
        json.dumps('__TOOL__:{"phase": "result", "id": "call_1", "result": "[]"}'),
        json.dumps("__THINKING__:checking"),
        json.dumps("your code is beef42."),
        json.dumps({"done": True}),
    ]
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")

    per_wire = {}
    for wire in ("1", "2"):
        monkeypatch.setenv("ZOE_FLUE_WIRE", wire)
        _FakeClient.calls = []
        _FakeClient.stream_response = _FakeStreamResponse(lines=lines)
        per_wire[wire] = await _collect(
            zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason")
        )

    assert per_wire["1"] == per_wire["2"]
    assert per_wire["2"][1].startswith('__TOOL__:{"phase": "start", ')
    assert json.loads(per_wire["2"][1][len("__TOOL__:"):])["name"] == "recall_memory"


@pytest.mark.asyncio
async def test_run_flue_brain_strips_sentinels_on_wire2(wire_env, monkeypatch):
    """The non-streaming collector's sentinel strip is wire-independent."""
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")
    wire_env.stream_response = _FakeStreamResponse(lines=[
        json.dumps('__TOOL__:{"phase": "start", "id": "t", "name": "recall_memory"}'),
        json.dumps("Your locker code is "),
        json.dumps("beef42."),
        json.dumps({"done": True}),
    ])

    out = await zoe_flue_client.run_flue_brain("what's my locker code?", "s1", "jason")

    assert out == "Your locker code is beef42."
    assert "__TOOL__" not in out


@pytest.mark.asyncio
async def test_wire2_aggregated_400_names_the_wire_flag(wire_env, monkeypatch, caplog):
    """A 1.x sidecar 400s the wire-2 body: the log must name ZOE_FLUE_WIRE=1.

    The mirror of the wire-1-reply-on-wire-2 case: raise_for_status() alone
    would bury the diagnosis in a bare HTTPStatusError. 4xx = never admitted.
    """
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "0")
    wire_env.stream_response = _FakeStreamResponse(
        status_code=400, content_type="application/json",
        raw=b'{"error":"invalid_request","message":"Delivered messages must be { kind, body }"}',
    )
    with caplog.at_level("ERROR"):
        out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))
    assert out == [zoe_flue_client._FALLBACK_TEXT]
    assert any("ZOE_FLUE_WIRE=1" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_wire2_streaming_400_names_the_wire_flag(wire_env, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")
    wire_env.stream_response = _FakeStreamResponse(
        status_code=400, content_type="application/json", raw=b'{"error":"invalid_request"}',
    )
    with caplog.at_level("ERROR"):
        out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))
    assert out == [zoe_flue_client._FALLBACK_TEXT]
    assert any("ZOE_FLUE_WIRE=1" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_wire2_500_does_not_claim_wire_mismatch(wire_env, monkeypatch, caplog):
    """Control: a 500 is NOT diagnosed as a wire mismatch (it isn't one)."""
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "0")
    wire_env.stream_response = _FakeStreamResponse(
        status_code=500, content_type="application/json", raw=b'{"error":"boom"}',
    )
    with caplog.at_level("ERROR"):
        out = await _collect(zoe_flue_client.run_flue_brain_streaming("hi", "s1", "jason"))
    assert out == [zoe_flue_client._FALLBACK_TEXT]
    assert not any("ZOE_FLUE_WIRE=1" in r.message for r in caplog.records)
