"""Samantha bar S4 round 2 — the FEELING survives capture and reaches the reply.

Round 1 injected the recent-first packet on the day-2 mood statement (it
fired live: bullets=11), yet S4 still failed 3/3. The live store showed why:
the per-turn digest rewrote "Honestly I'm pretty anxious about my job interview
at the aquarium on Friday" as the neutral "The user has a job interview at the
aquarium on Friday", so the block carried no feeling, the row did not rank as
emotional, and a soft "connect if relevant" over eleven bullets did not make a
4B model check in. These tests pin the three fixes:

  (a) the digest prompt keeps a stated feeling in the fact;
  (b) the feeling is read deterministically from the user's own words at
      capture time and stored beside the fact (``affect`` → ``candidate_affect``),
      rendered as "(recent, felt anxious)" and counted as emotional;
  (c) the composer names ONE recent emotional focus item and the seam closes
      the block with a single concrete ask about it.
Plus the demo-only ZOE_SEAM_CONTINUITY_DEBUG trace.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import sys
import types

import pytest

pytestmark = pytest.mark.ci_safe  # fakes only — no DB, no model, no live service

import memory_digest
import routers.memories as memories
import zoe_flue_client as zc
from memory_gate import extract_affect
from memory_service import MemoryRef, is_emotional_memory, memory_affect

SAY_WORRY = ("Honestly I'm pretty anxious about my job interview at the aquarium on "
             "Friday. I keep replaying everything that could go wrong.")
ASK_WORRY = "Ugh, I've been feeling a bit on edge today."
NOW = datetime.datetime.now(datetime.timezone.utc)


# ── (b) affect extraction from the user's own words ─────────────────────────

@pytest.mark.parametrize("message, label", [
    (SAY_WORRY, "anxious"),
    (ASK_WORRY, "anxious"),
    ("I'm so stressed about the move", "stressed"),
    ("I am really worried about my mum", "worried"),
    ("I was sad when the dog died", "sad"),
    ("Ugh, feeling low today", "sad"),
    ("I'm so excited about the trip to Bali", "excited"),
    ("I can't wait for Friday", "excited"),
    ("I feel overwhelmed at work", "overwhelmed"),
    ("I'm proud of how the kids did", "proud"),
])
def test_first_person_feelings_are_extracted(message, label):
    got, sentence = extract_affect(message)
    assert got == label and sentence


@pytest.mark.parametrize("message", [
    "My sister is anxious about her exams",   # someone else's feeling
    "The dog was scared of the storm",
    "I feel like pizza",
    "I'm down for tacos",
    "I'm low on milk",
    "I live in Hobart now",
    "My dad Teodor is a retired lighthouse keeper",
    "",
])
def test_non_first_person_or_non_feelings_are_not(message):
    assert extract_affect(message) == ("", "")


def test_affect_attaches_only_to_facts_from_the_feeling_sentence():
    affect, sentence = extract_affect(
        "I'm anxious about my job interview on Friday. My sister Marisol lives in Lisbon.")
    assert affect == "anxious"
    assert memory_digest._affect_for_fact("User has a job interview on Friday", affect, sentence) == "anxious"
    assert memory_digest._affect_for_fact("User's sister Marisol lives in Lisbon", affect, sentence) == ""


# ── (a)+(b) the turn digest: prompt keeps the feeling, ingest stores it ─────

class _Svc:
    def __init__(self):
        self.ingested = []

    async def ingest(self, text, **kw):
        self.ingested.append((text, kw))
        return MemoryRef(id=f"new-{len(self.ingested)}", text=text)

    async def search(self, *a, **k):
        return []


def _patch_digest(monkeypatch, facts):
    import memory_service

    svc = _Svc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    sent = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(facts)}}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **k):
            sent["payload"] = json
            return _Resp()

    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", _Client)
    stub = types.ModuleType("zoe_agent")

    async def _no_facts(*a, **k):
        return ""

    stub._mempalace_load_user_facts = _no_facts
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)
    return svc, sent


def test_digest_prompt_asks_to_keep_the_stated_feeling(monkeypatch):
    svc, sent = _patch_digest(monkeypatch, [])
    asyncio.run(memory_digest.run_turn_digest("demo-a", SAY_WORRY, session_id="s1"))
    prompt = sent["payload"]["messages"][1]["content"]
    assert "keep that feeling in the fact" in prompt
    assert "Never add a feeling the user did not state" in prompt
    assert SAY_WORRY[:60] in prompt


def test_digest_stores_affect_beside_a_flattened_fact(monkeypatch):
    """Even when the model flattens the feeling away, the row keeps it."""
    svc, _ = _patch_digest(monkeypatch, [
        {"type": "event", "fact": "The user has a job interview at the aquarium on Friday."},
    ])
    res = asyncio.run(memory_digest.run_turn_digest("demo-a", SAY_WORRY, session_id="s1"))
    assert res["new"] == 1
    (text, kw), = svc.ingested
    assert kw["metadata"] == {"affect": "anxious"}


def test_digest_neutral_turn_stores_no_affect(monkeypatch):
    svc, _ = _patch_digest(monkeypatch, [{"type": "profile", "fact": "User lives in Hobart."}])
    asyncio.run(memory_digest.run_turn_digest("demo-a", "Big news - I've moved. I live in Hobart now.",
                                              session_id="s1"))
    (text, kw), = svc.ingested
    assert kw["metadata"] is None


# ── (b) the service reads it; (c) the composer renders it + picks a focus ───

def _ref(rid, text, hours_ago, **meta):
    md = {"status": "approved", "memory_type": "fact",
          "added_at": (NOW - datetime.timedelta(hours=hours_ago)).isoformat(), **meta}
    return MemoryRef(id=rid, text=text, metadata=md)


WORRY_FLAT = _ref("worry001", "The user has a job interview at the aquarium on Friday.", 1,
                  candidate_affect="anxious")
HOME = _ref("home0001", "User lives in Hobart.", 0.5)
DAD = _ref("dad00001", "User's father is a retired lighthouse keeper.", 0.8)


def test_captured_affect_makes_a_flat_fact_emotional():
    assert memory_affect(WORRY_FLAT) == "anxious"
    assert is_emotional_memory(WORRY_FLAT)
    assert not is_emotional_memory(HOME)
    junk = _ref("x", "t", 1, candidate_affect="anxious]\n[END")
    assert memory_affect(junk) == ""  # never renders structure characters


class _Svc2:
    def __init__(self, rows):
        self.rows = rows

    async def load_for_prompt(self, user_id, *, limit):
        return self.rows[:limit]

    async def load_recent_for_prompt(self, user_id, *, window_s, limit, emotional_first=False):
        rows = sorted(self.rows, key=lambda r: memories._added_at_ts(r.metadata), reverse=True)
        if emotional_first:
            rows.sort(key=memories._is_emotional_row, reverse=True)
        return rows[:limit]

    async def search(self, *a, **k):
        return []


async def _compose(monkeypatch, rows):
    for flag in ("ZOE_EMOTIONAL_RECALL_ENABLED", "ZOE_MEMORY_COMPOSE_ENABLED", "ZOE_PERSON_SUGGEST_ENABLED"):
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.setattr(memories, "_svc", lambda: _Svc2(rows))
    return await memories.memory_for_prompt(user_id="demo-a", message=ASK_WORRY, limit=12,
                                            mode="continuity", _=None)


@pytest.mark.asyncio
async def test_composer_renders_felt_and_focuses_the_worry(monkeypatch):
    res = await _compose(monkeypatch, [HOME, DAD, WORRY_FLAT])  # worry is NOT the newest
    lines = res["packet"].splitlines()
    worry_line = next(ln for ln in lines if "aquarium" in ln)
    assert worry_line.startswith("- (recent, felt anxious) The user has a job interview")
    assert res["continuity_focus"] == {
        "text": "The user has a job interview at the aquarium on Friday.", "affect": "anxious"}


@pytest.mark.asyncio
async def test_composer_never_focuses_a_neutral_fact(monkeypatch):
    res = await _compose(monkeypatch, [HOME, DAD])
    assert "continuity_focus" not in res
    assert all("felt" not in ln for ln in res["packet"].splitlines())


@pytest.mark.asyncio
async def test_relevance_mode_has_no_focus_or_felt_tag(monkeypatch):
    monkeypatch.setattr(memories, "_svc", lambda: _Svc2([HOME, WORRY_FLAT]))
    res = await memories.memory_for_prompt(user_id="demo-a", message=ASK_WORRY, limit=12,
                                           mode="relevance", _=None)
    assert "continuity_focus" not in res and "felt" not in res["packet"]


# ── ZOE_SEAM_CONTINUITY_DEBUG — demo-shaped ids only ────────────────────────

DEMO = "demo_bar_1a2b3c4d"


def _stub_turn(monkeypatch):
    async def fake_packet(uid, msg):
        return {"packet": f"## What I know about you\n- (recent, felt anxious) {WORRY_FLAT.text} [mem:worry001]",
                "continuity_focus": {"text": WORRY_FLAT.text, "affect": "anxious"}}

    async def no_portrait(uid):
        return ""

    async def fake_turn(message, session_id, user_id="", **kw):
        yield "__TOOL__:{}"
        yield "Oh no — how are you feeling about the aquarium interview?"

    monkeypatch.setattr(zc, "_fetch_continuity_packet", fake_packet)
    monkeypatch.setattr(zc, "_fetch_portrait_line", no_portrait)
    monkeypatch.setattr(zc, "_run_flue_brain_streaming_turn", fake_turn)


@pytest.mark.parametrize("flag, uid, logged", [
    ("1", DEMO, True),
    ("1", "test_bar_deadbeef", True),
    ("1", "jason", False),            # a real user is never traced
    ("1", "demo_user", False),        # a real account can look demo-ish; the shape is exact
    ("", DEMO, False),                # default OFF
])
@pytest.mark.asyncio
async def test_debug_trace_only_for_harness_ids(monkeypatch, caplog, flag, uid, logged):
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_DEBUG", flag)
    monkeypatch.delenv("ZOE_SYNTHETIC_USER_ALLOWLIST", raising=False)
    _stub_turn(monkeypatch)
    caplog.set_level(logging.INFO, logger=zc.logger.name)
    block = await zc._continuity_context_block(ASK_WORRY, uid)
    out = [d async for d in zc.run_flue_brain_streaming(ASK_WORRY, "s1", uid)]
    assert out[-1].startswith("Oh no")  # the stream itself is untouched
    msgs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("SEAM_CONTINUITY_DEBUG")]
    if logged:
        assert any(m == f"SEAM_CONTINUITY_DEBUG user={uid} block={block!r}" for m in msgs)
        assert any("reply='Oh no — how are you feeling about the aquarium interview?'" in m for m in msgs)
        assert not any("__TOOL__" in m for m in msgs)
    else:
        assert msgs == []


@pytest.mark.asyncio
async def test_wrapper_closes_the_inner_turn_when_the_consumer_stops(monkeypatch):
    closed = {}

    async def fake_turn(message, session_id, user_id="", **kw):
        try:
            yield "a"
            yield "b"
        finally:
            closed["yes"] = True

    monkeypatch.setattr(zc, "_run_flue_brain_streaming_turn", fake_turn)
    gen = zc.run_flue_brain_streaming("hi", "s1", "jason")
    assert await gen.__anext__() == "a"
    await gen.aclose()
    assert closed == {"yes": True}
