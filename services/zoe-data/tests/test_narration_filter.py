"""ZOE_STRIP_NARRATION (default OFF) — never narrate the lookup.

Live 2026-10-04: "Who am I" opened with "I'll check what I've got on file about
you." before the answer. The post-filter drops a LEADING lookup-announcing
sentence when the real answer follows; the recall block carries the same rule at
the source. Negative controls: a promise ("…and get back to you"), an
announcement with nothing after it, and flag-off bytes are left alone.
Synthetic phrasings only (ci_safe)."""
from __future__ import annotations

import asyncio
import json

import pytest

import memory_gate
import narration_filter as nf
import zoe_flue_client as zc

pytestmark = pytest.mark.ci_safe

ANSWER = "You're someone who likes a morning swim."


def _run(coro):
    return asyncio.run(coro)


# five narration openers -> stripped
@pytest.mark.parametrize("opener", [
    "I'll check what I've got on file about you.",
    "Let me look that up for you.",
    "Checking my notes...",
    "One moment while I look that up.",
    "Okay, I'll see what I remember about you.",
    "Sure! Let me pull up what I have.",
    "Let me check my records.",
])
def test_opener_is_stripped_when_the_answer_follows(opener):
    assert nf.strip_leading_narration(f"{opener} {ANSWER}") == ANSWER
    assert nf.strip_leading_narration(f"{opener}\n{ANSWER}") == ANSWER


def test_two_stacked_announcements_both_go():
    text = f"I'll check. Let me look at what I have. {ANSWER}"
    assert nf.strip_leading_narration(text) == ANSWER


@pytest.mark.parametrize("text", [
    # NEGATIVE CONTROL: a real promise with a deferral is kept
    "I'll check the weather and get back to you. It's sunny at the moment.",
    "I'll check with you tomorrow. Anything else?",
    "Let me check the forecast and let you know later. Sit tight.",
    # nothing follows -> the announcement IS the reply; dropping it would blank it
    "I'll check what I've got on file about you.",
    "I'll check what I've got on file about you.\n",
    "I'll check what I've got on file about you. ",
    # not lookup announcements
    "I'll see you there. Bring snacks.",
    "Let me know if you need anything. Bye.",
    "I'll look forward to it. See you soon.",
    # the announcement is not the first sentence
    f"{ANSWER} I'll check again later if you like.",
    ANSWER,
    "",
])
def test_kept_unchanged(text):
    assert nf.strip_leading_narration(text) == text


def test_real_tool_call_with_a_promise_is_kept_end_to_end():
    async def src():
        yield "I'll check the weather and get back to you. "
        yield '__TOOL__:{"phase": "start", "id": "1", "name": "get_weather"}'
        yield "It is 16 degrees and clear."

    async def collect():
        return [d async for d in nf.filter_stream(src())]

    out = _run(collect())
    assert "".join(d for d in out if not d.startswith("__TOOL__")) == \
        "I'll check the weather and get back to you. It is 16 degrees and clear."
    assert any(d.startswith("__TOOL__:") for d in out)


def test_flag_default_off():
    assert nf.enabled() is False


# stream form == pure form at any chunking
@pytest.mark.parametrize("text", [
    f"I'll check what I've got on file about you.\n{ANSWER}",
    f"Sure! Let me pull up what I have. {ANSWER}",
    "I'll check what I've got on file about you.",
    "I'll check the weather and get back to you. It's sunny.",
    f"I'll check. Let me look at it. Hmm, let me search. {ANSWER}",
    ANSWER,
])
@pytest.mark.parametrize("size", [1, 2, 5, 11, 400])
def test_stream_equals_pure(text, size):
    st = nf.NarrationStripper()
    out = "".join(st.feed(text[i:i + size]) for i in range(0, len(text), size)) + st.finish()
    assert out == nf.strip_leading_narration(text)


def test_stream_passes_sentinels_immediately_and_closes_inner():
    closed = []

    async def src():
        try:
            yield "__THINKING__:hm"
            yield "Let me check my notes. "
            yield '__TOOL__:{"phase": "start", "id": "1", "name": "recall_memory"}'
            yield ANSWER
        finally:
            closed.append(True)

    async def collect():
        return [d async for d in nf.filter_stream(src())]

    out = _run(collect())
    assert out[0] == "__THINKING__:hm" and out[1].startswith("__TOOL__:")
    assert "".join(out[2:]) == ANSWER and closed == [True]


# ── the seam, end to end (wire-1 doubles) ────────────────────────────────────

class _Resp:
    def __init__(self, text):
        self._t = text

    def raise_for_status(self):
        return None

    def json(self):
        return {"result": {"text": self._t}}


def _client(reply, captured):
    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def post(self, url, content=None, headers=None):
            captured["content"] = content
            return _Resp(reply)
    return _C


@pytest.fixture
def seam(monkeypatch):
    import httpx

    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    for k in ("ZOE_STRIP_NARRATION", "ZOE_SEAM_RECALL_INJECT", "ZOE_VERIFY_ON_CHALLENGE", "ZOE_TRIVIA_HEDGE"):
        monkeypatch.delenv(k, raising=False)

    async def turn(message, reply):
        cap = {}
        monkeypatch.setattr(httpx, "AsyncClient", _client(reply, cap))
        out = [c async for c in zc.run_flue_brain_streaming(message, "s1", "jason")]
        return "".join(out), json.loads(cap["content"])["message"]
    return turn


NARRATED = f"I'll check what I've got on file about you.\n{ANSWER}"


def test_seam_flag_off_keeps_the_narration_and_the_bytes(seam):
    out, sent = _run(seam("Who am I", NARRATED))
    assert out == NARRATED and sent == " zoe-uid:jason\nWho am I"


def test_seam_flag_on_strips_it(seam, monkeypatch):
    monkeypatch.setenv("ZOE_STRIP_NARRATION", "1")
    out, sent = _run(seam("Who am I", NARRATED))
    assert out == ANSWER and sent == " zoe-uid:jason\nWho am I"  # no recall flag -> no block


def test_seam_flag_on_keeps_a_promise(seam, monkeypatch):
    monkeypatch.setenv("ZOE_STRIP_NARRATION", "1")
    reply = "I'll check the weather and get back to you. It is 16 degrees."
    out, _ = _run(seam("whats the weather", reply))
    assert out == reply


# ── the rule at the source: the recall block says "answer directly" ──────────

def test_recall_block_open_line_default_unchanged(monkeypatch):
    monkeypatch.delenv("ZOE_STRIP_NARRATION", raising=False)
    assert zc._recall_block_open() == zc._RECALL_BLOCK_OPEN
    assert "never say you are checking" not in zc._RECALL_BLOCK_OPEN


def test_recall_block_open_line_carries_the_rule_when_on(monkeypatch):
    monkeypatch.setenv("ZOE_STRIP_NARRATION", "1")
    line = zc._recall_block_open()
    assert "never say you are checking or looking anything up" in line
    # still the registered [MEMORY CONTEXT open (the sidecar elides stale copies by it)
    assert line.startswith("[MEMORY CONTEXT ") and "[MEMORY CONTEXT" == zc._FLUE_CONTEXT_BLOCKS[0][0]


@pytest.mark.asyncio
async def test_recall_floor_block_uses_the_directive_line_when_on(monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    monkeypatch.setenv("ZOE_STRIP_NARRATION", "1")

    async def fake(uid, msg):
        return "Known facts about this user:\n- likes a morning swim"

    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fake)
    block = await zc._recall_context_block("what's my favourite colour", "jason")
    assert block.startswith(zc._RECALL_BLOCK_OPEN_DIRECT)
    monkeypatch.delenv("ZOE_STRIP_NARRATION")
    assert (await zc._recall_context_block("what's my favourite colour", "jason")).startswith(zc._RECALL_BLOCK_OPEN)


# ── "who am I" is an own-fact / recall shape ─────────────────────────────────

@pytest.mark.parametrize("text", ["Who am I", "who am i?", "tell me about myself", "what do you remember about me"])
def test_self_questions_are_own_fact(text):
    assert memory_gate.own_fact_question_kind(text) == "self"


@pytest.mark.parametrize("text", ["who am I to argue", "who is she", "who are you"])
def test_self_negative(text):
    assert memory_gate.own_fact_question_kind(text) != "self"
