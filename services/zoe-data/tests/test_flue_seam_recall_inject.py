"""Seam recall floor — ZOE_SEAM_RECALL_INJECT (default OFF).

BUG B (live hard-gate 2026-07-07): "my locker code is 31999" sat at the TOP of
the /api/memories/for-prompt packet, yet the flue brain answered "I don't have
that stored" — the model didn't invoke its recall_memory tool that turn (the
~97% invocation ceiling). These tests pin the deterministic floor: on a
conservative personal-question shape the seam prepends the for-prompt packet
to the outbound message, AFTER the identity envelope line so the sidecar's
stripIdentityEnvelope regex (labs/flue-zoe-brain/src/request-identity.ts,
anchored `^ zoe-uid:...\n`) still strips cleanly and the model sees
block + question.

Default OFF is a byte-for-byte no-op; the flag stays OFF until the operator
enables it post-replay-gate.
"""
from __future__ import annotations

import json
import re

import pytest

pytestmark = pytest.mark.ci_safe  # zoe_flue_client is slim (json/logging/os/re)

import zoe_flue_client as zc

LOCKER_Q = "what's my locker code?"
PACKET = (
    "Known facts about this user:\n"
    "- my locker code is 31999 [mem:abc12345]\n"
    "- User's wife is named Emma [mem:def67890]\n"
)

# Mirror of the sidecar's IDENTITY_ENVELOPE_RE (request-identity.ts) — the
# contract the injected block must not break.
SIDECAR_STRIP_RE = re.compile(r"^ zoe-uid:([^\n]*)\n")


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _FakeClient:
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        type(self).captured["url"] = url
        type(self).captured["content"] = content
        type(self).captured["headers"] = headers
        return _FakeResponse({"result": {"text": "ok"}})


async def _outbound_message(monkeypatch, message, user_id="jason", packet=PACKET):
    """Drive run_flue_brain_streaming with fakes; return the outbound message."""
    monkeypatch.setattr(_FakeClient, "captured", {})
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    async def fake_fetch(uid, msg):
        return packet

    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fake_fetch)
    out = [c async for c in zc.run_flue_brain_streaming(message, "s1", user_id)]
    assert out == ["ok"]
    return json.loads(_FakeClient.captured["content"])["message"]


# ── default OFF: byte-for-byte no-op ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_default_off_no_injection(monkeypatch):
    monkeypatch.delenv("ZOE_SEAM_RECALL_INJECT", raising=False)
    msg = await _outbound_message(monkeypatch, LOCKER_Q)
    assert msg == f" zoe-uid:jason\n{LOCKER_Q}"


@pytest.mark.asyncio
async def test_explicit_off_no_injection(monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "0")
    msg = await _outbound_message(monkeypatch, LOCKER_Q)
    assert msg == f" zoe-uid:jason\n{LOCKER_Q}"


# ── flag ON: matching turn gets the block, AFTER the identity line ───────────

@pytest.mark.asyncio
async def test_matching_turn_block_rides_after_identity_line(monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    msg = await _outbound_message(monkeypatch, LOCKER_Q)
    # Identity envelope is STILL the first line (single-line contract).
    first_line, rest = msg.split("\n", 1)
    assert first_line == " zoe-uid:jason"
    # Block after the identity line; the user's question is preserved at the end.
    assert rest.startswith(zc._RECALL_BLOCK_OPEN)
    assert "31999" in rest
    assert rest.endswith(LOCKER_Q)
    # The sidecar's strip regex removes ONLY the identity line — the model sees
    # block + question, and the id never leaks into the prompt.
    stripped = SIDECAR_STRIP_RE.sub("", msg)
    assert stripped.startswith(zc._RECALL_BLOCK_OPEN)
    assert "zoe-uid" not in stripped
    m = SIDECAR_STRIP_RE.match(msg)
    assert m and m.group(1) == "jason"


@pytest.mark.parametrize("question", [
    "what's my locker code?",
    "What is my wife's name",
    "do you remember my locker code",
    "do you remember what I said about the dentist",
    "what did I say about the dentist",
    "when's my anniversary",
    "when did I last go hiking",
    "where do I keep the spare key",
    "who is my dentist",
    "what do you know about me",
])
@pytest.mark.asyncio
async def test_personal_question_shapes_match(monkeypatch, question):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    msg = await _outbound_message(monkeypatch, question)
    assert zc._RECALL_BLOCK_OPEN in msg


@pytest.mark.parametrize("message", [
    "set a timer for 5 minutes",
    "what is the weather today",
    "who is Ada Lovelace",
    "turn off the kitchen lights",
    "tell me a joke",
    "what time is it",
    "do you remember the alamo",
    "do you remember who won the election",
])
@pytest.mark.asyncio
async def test_non_personal_turns_do_not_inject(monkeypatch, message):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    msg = await _outbound_message(monkeypatch, message)
    assert msg == f" zoe-uid:jason\n{message}"


# ── guards: uid, failure, empty packet, truncation ───────────────────────────

@pytest.mark.asyncio
async def test_no_user_id_no_injection(monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    msg = await _outbound_message(monkeypatch, LOCKER_Q, user_id="")
    assert msg == LOCKER_Q  # no envelope, no block


@pytest.mark.asyncio
async def test_fetch_failure_never_breaks_the_turn(monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    monkeypatch.setattr(_FakeClient, "captured", {})
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    async def boom(uid, msg):
        raise RuntimeError("composer down")

    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", boom)
    out = [c async for c in zc.run_flue_brain_streaming(LOCKER_Q, "s1", "jason")]
    assert out == ["ok"]  # turn proceeded
    msg = json.loads(_FakeClient.captured["content"])["message"]
    assert msg == f" zoe-uid:jason\n{LOCKER_Q}"  # just without the floor


@pytest.mark.asyncio
async def test_empty_packet_no_block(monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    msg = await _outbound_message(monkeypatch, LOCKER_Q, packet="")
    assert msg == f" zoe-uid:jason\n{LOCKER_Q}"


@pytest.mark.asyncio
async def test_oversized_packet_truncated(monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    big = "Known facts:\n" + "\n".join(
        f"- fact number {i} about the user [mem:{i:08d}]" for i in range(40)
    )
    msg = await _outbound_message(monkeypatch, LOCKER_Q, packet=big)
    block = msg.split(zc._RECALL_BLOCK_OPEN, 1)[1].split(zc._RECALL_BLOCK_CLOSE, 1)[0]
    bullets = [l for l in block.splitlines() if l.lstrip().startswith("-")]
    assert len(bullets) <= zc._RECALL_MAX_BULLETS
    assert len(block) <= zc._RECALL_MAX_CHARS + 200  # header slack only


def test_truncate_packet_char_cap():
    long_line = "- " + "x" * 400
    packet = "\n".join([long_line] * 10)
    out = zc._truncate_packet(packet)
    assert len(out) <= zc._RECALL_MAX_CHARS + 402  # first line always kept
    assert out  # never empties a non-empty packet
