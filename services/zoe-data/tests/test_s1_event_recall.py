"""Samantha bar S1 round 3 — same-day recall of an EVENT the user mentioned.

Seed: "Just so you know, my sister Marisol is flying in from Lisbon on Thursday."
Ask (new session): "Who is flying in on Thursday, and where from?"

After #1767 the ask reached the brain, yet the reply named neither Marisol nor
Lisbon. Two gaps, both pinned here:

  (1) Capture: the per-turn digest sometimes kept ONLY "User's sister is named
      Marisol." — the event (flying in, from Lisbon, on Thursday) was gone
      (live 2026-09-28 23:18 / 23:36; offline, 1 of 5 samples of the old
      prompt). The digest prompt now asks for BOTH facts when a named person
      has something happening: who they are, and the event with who/what/
      where/when kept.
  (2) Recall: the ask has no "my"/"I", so the deterministic recall floor
      (ZOE_SEAM_RECALL_INJECT) never fired and recall rested on the 4B calling
      recall_memory with a good query. Event-shaped questions (a people-movement
      verb anchored by a time cue, a relation word or a he/she/they subject —
      ``memory_gate.is_event_question``) now get the same [MEMORY CONTEXT]
      packet; general knowledge never does.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

pytestmark = pytest.mark.ci_safe  # fakes only — no DB, no model, no live service

import memory_digest
import zoe_flue_client as zc
from memory_gate import is_event_question, message_needs_memory
from memory_service import MemoryRef

SAY_SISTER = "Just so you know, my sister Marisol is flying in from Lisbon on Thursday."
ASK_SISTER = "Who is flying in on Thursday, and where from?"
NAME_FACT = "User's sister is named Marisol"
EVENT_FACT = "User's sister Marisol is flying in from Lisbon on Thursday"

EVENT_QUESTIONS = [
    ASK_SISTER,
    "who is flying in thursday",
    "Who's coming over tonight?",
    "who is staying with us this weekend",
    "Who is visiting next week?",
    "when is my sister arriving",
    "where is my sister flying from",
    "what time does my dad land",
    "when are our parents arriving",
    "where is she flying from",
    "when does he land",
    # bare present tense, no auxiliary (Greptile #1770)
    "Who arrives on Thursday?",
    "Who flies in on Thursday?",
    "So who lands tomorrow?",
    "Thanks. Who comes over on Friday?",
    # a public event with the user's own people named still counts
    "who is coming to the game with my brother on Friday",
    "who is coming to the game with us on Friday",
    # an unrelated public-event word in ANOTHER sentence does not veto it
    # (Greptile #1771)
    "Who is flying in on Thursday? The game is Friday",
]
NOT_EVENT_QUESTIONS = [
    "who is the prime minister",
    "who is Ada Lovelace",
    "what is the weather today",
    "what day is it today",
    "who is playing on Sunday",
    "who won the game on Sunday",
    "who is coming to the Oscars",
    "who is flying the plane",
    "when does the train leave",
    "when do they open",
    "where is the Eiffel Tower",
    "where is my phone",
    "when is the next full moon",
    "what time is it",
    "set a timer for 5 minutes",
    # a public event / venue as the destination (Greptile #1770)
    "Who is coming to the game on Friday?",
    "who is coming to the concert tonight",
    "who flies in for the match on Sunday",
    "who is going to the Grand Prix on Sunday",
    # a relative clause in a statement is not a question
    "My cleaner, who comes on Friday, is great",
    # a personal word in ANOTHER sentence does not vouch for a public-event
    # question (Greptile #1771)
    "We're busy. Who is coming to the game on Friday?",
    # statements are not questions about the store
    SAY_SISTER,
]


# ── (2) the shape predicate ─────────────────────────────────────────────────

@pytest.mark.parametrize("q", EVENT_QUESTIONS)
def test_event_questions_match(q):
    assert is_event_question(q)
    assert zc._recall_question_shape(q) in ("event", "personal")


@pytest.mark.parametrize("q", NOT_EVENT_QUESTIONS)
def test_general_knowledge_and_statements_do_not_match(q):
    assert not is_event_question(q)


def test_s1_ask_is_an_event_shape_not_a_personal_one():
    """The S1 ask carries no my/I — the old floor could not see it."""
    assert not zc._PERSONAL_QUESTION_RE.search(ASK_SISTER)
    assert zc._recall_question_shape(ASK_SISTER) == "event"


def test_event_question_fires_the_semantic_search_gate():
    """The for-prompt composer only runs the semantic search on recall-ish
    turns; a pronoun follow-up ("where is she flying from") had no trigger word."""
    assert message_needs_memory("where is she flying from")
    assert message_needs_memory(ASK_SISTER)
    assert not message_needs_memory("when does the train leave")


# ── (2) the seam: the S1 ask gets the [MEMORY CONTEXT] block ────────────────

PACKET = (
    "Known facts about this user:\n"
    f"- {NAME_FACT} [mem:aaaa1111]\n"
    f"- {EVENT_FACT} [mem:bbbb2222]\n"
)


class _Resp:
    def raise_for_status(self):
        return None

    def json(self):
        return {"result": {"text": "ok"}}


class _Client:
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        type(self).captured["content"] = content
        return _Resp()


async def _outbound(monkeypatch, message, packet=PACKET):
    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setattr(_Client, "captured", {})
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    fetched = []

    async def fake_fetch(uid, msg):
        fetched.append(msg)
        return packet

    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fake_fetch)

    async def no_continuity(*a, **k):
        return ""

    monkeypatch.setattr(zc, "_continuity_context_block", no_continuity)
    out = [c async for c in zc.run_flue_brain_streaming(message, "s1", "demo-a")]
    assert out == ["ok"]
    return json.loads(_Client.captured["content"])["message"], fetched


@pytest.mark.asyncio
async def test_s1_ask_gets_the_memory_block_with_the_event_fact(monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    msg, fetched = await _outbound(monkeypatch, ASK_SISTER)
    assert fetched == [ASK_SISTER], "relevance packet fetched for the user's own words"
    first, rest = msg.split("\n", 1)
    assert first == " zoe-uid:demo-a"
    assert rest.startswith(zc._RECALL_BLOCK_OPEN)
    block = rest[: rest.index(zc._RECALL_BLOCK_CLOSE)]
    assert "Marisol" in block and "Lisbon" in block and "Thursday" in block
    assert rest.endswith(ASK_SISTER)


@pytest.mark.parametrize("q", ["who is Ada Lovelace", "who is the prime minister",
                               "who is playing on Sunday", "what is the weather today"])
@pytest.mark.asyncio
async def test_general_knowledge_gets_no_block(monkeypatch, q):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    msg, fetched = await _outbound(monkeypatch, q)
    assert msg == f" zoe-uid:demo-a\n{q}"
    assert fetched == []


@pytest.mark.asyncio
async def test_flag_off_event_question_is_byte_identical(monkeypatch):
    monkeypatch.delenv("ZOE_SEAM_RECALL_INJECT", raising=False)
    msg, fetched = await _outbound(monkeypatch, ASK_SISTER)
    assert msg == f" zoe-uid:demo-a\n{ASK_SISTER}"
    assert fetched == []


EMOTIONAL_EVENT = "I'm anxious about who is flying in on Thursday"


@pytest.mark.parametrize("q", [EMOTIONAL_EVENT, "I'm so anxious — who is flying in on Thursday?"])
def test_continuity_owns_an_event_shape_inside_a_feeling(monkeypatch, q):
    """Greptile #1770: an event phrase embedded in a first-person feeling is the
    user SHARING a feeling — continuity owns it, the recall floor does not."""
    assert zc._CONTINUITY_RE.search(q) and is_event_question(q)
    monkeypatch.delenv("ZOE_SEAM_CONTINUITY_INJECT", raising=False)
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    assert zc._recall_floor_shape(q) == ""
    assert zc.is_continuity_turn(q, "demo-a")
    # continuity switched off → the recall floor still serves the event shape
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    assert zc._recall_floor_shape(q) == "event"


def test_personal_question_inside_a_feeling_stays_recall(monkeypatch):
    """The existing rule is unchanged: a my/I recall question is recall even
    with a feeling beside it."""
    q = "do you remember what I said? I'm so anxious"
    monkeypatch.delenv("ZOE_SEAM_CONTINUITY_INJECT", raising=False)
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    assert zc._recall_floor_shape(q) == "personal"
    assert not zc.is_continuity_turn(q, "demo-a")


def test_plain_s1_ask_stays_recall(monkeypatch):
    monkeypatch.delenv("ZOE_SEAM_CONTINUITY_INJECT", raising=False)
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    assert zc._recall_floor_shape(ASK_SISTER) == "event"
    assert not zc.is_continuity_turn(ASK_SISTER, "demo-a")


SEPARATE_FEELING = "I'm exhausted. Who is flying in on Thursday, and where from?"


def test_a_feeling_in_another_sentence_keeps_recall_ownership(monkeypatch):
    """Greptile #1771: the question stands on its own sentence, so it is asked,
    not shared — recall owns it (and offers are not deferred)."""
    monkeypatch.delenv("ZOE_SEAM_CONTINUITY_INJECT", raising=False)
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    assert zc._CONTINUITY_RE.search(SEPARATE_FEELING)
    assert zc._recall_floor_shape(SEPARATE_FEELING) == "event"
    assert not zc.is_continuity_turn(SEPARATE_FEELING, "demo-a")


@pytest.mark.asyncio
async def test_a_feeling_in_another_sentence_gets_the_recall_block(monkeypatch):
    monkeypatch.delenv("ZOE_SEAM_CONTINUITY_INJECT", raising=False)
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    msg, fetched = await _outbound(monkeypatch, SEPARATE_FEELING)
    assert fetched == [SEPARATE_FEELING]
    rest = msg.split("\n", 1)[1]
    assert rest.startswith(zc._RECALL_BLOCK_OPEN)
    block = rest[: rest.index(zc._RECALL_BLOCK_CLOSE)]
    assert "Marisol" in block and "Lisbon" in block
    assert zc._CONTINUITY_BLOCK_OPEN not in msg


@pytest.mark.asyncio
async def test_emotional_event_statement_gets_the_continuity_block_with_the_event(monkeypatch):
    """End to end at the seam: the continuity block (not a bare recall block)
    rides after the user's words, and the event bullet is in it."""
    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.delenv("ZOE_SEAM_CONTINUITY_INJECT", raising=False)
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    monkeypatch.delenv("ZOE_SEAM_OFFER_INJECT", raising=False)
    monkeypatch.setattr(_Client, "captured", {})
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    calls = {"recall": 0, "continuity": []}

    async def fake_recall(uid, msg):
        calls["recall"] += 1
        return PACKET

    async def fake_continuity(uid, msg):
        calls["continuity"].append(msg)
        return {"packet": "## What I know about you\n"
                          f"- (recent) {EVENT_FACT} [mem:bbbb2222]\n"}

    async def fake_portrait(uid):
        return ""

    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fake_recall)
    monkeypatch.setattr(zc, "_fetch_continuity_packet", fake_continuity)
    monkeypatch.setattr(zc, "_fetch_portrait_line", fake_portrait)
    out = [c async for c in zc.run_flue_brain_streaming(EMOTIONAL_EVENT, "s1", "demo-a")]
    assert out == ["ok"]
    msg = json.loads(_Client.captured["content"])["message"]
    assert calls["recall"] == 0 and calls["continuity"] == [EMOTIONAL_EVENT]
    assert zc._RECALL_BLOCK_OPEN not in msg
    assert zc._CONTINUITY_BLOCK_OPEN in msg
    body = msg.split(EMOTIONAL_EVENT, 1)[1]
    assert body.lstrip().startswith(zc._CONTINUITY_BLOCK_OPEN)
    assert "Marisol" in body and "Lisbon" in body


@pytest.mark.asyncio
async def test_continuity_packet_carries_the_event_as_a_search_hit(monkeypatch):
    """The fold is real, not a stub: continuity mode runs the semantic search
    on the user's words, so an event row that is NOT recent (outside the 72 h
    pins) still reaches the continuity packet beside the recent rows."""
    import datetime

    import routers.memories as memories

    now = datetime.datetime.now(datetime.timezone.utc)

    def ref(rid, text, hours_ago):
        return MemoryRef(id=rid, text=text, metadata={
            "status": "approved", "memory_type": "fact",
            "added_at": (now - datetime.timedelta(hours=hours_ago)).isoformat()})

    event = ref("event001", EVENT_FACT, 24 * 5)
    recent = [ref(f"rec{i:05d}", f"recent distinct thing {i} about topic{i}", 1 + i) for i in range(8)]

    class _S:
        async def load_for_prompt(self, user_id, *, limit):
            return recent[:limit]

        async def load_recent_for_prompt(self, user_id, *, window_s, limit, emotional_first=False):
            return recent[:limit]

        async def search(self, query, *, user_id, limit=6, **_):
            return [event]

    for k in ("ZOE_EMOTIONAL_RECALL_ENABLED", "ZOE_MEMORY_COMPOSE_ENABLED", "ZOE_PERSON_SUGGEST_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(memories, "_svc", lambda: _S())
    res = await memories.memory_for_prompt(user_id="demo-a", message=EMOTIONAL_EVENT,
                                           limit=12, mode="continuity", _=None)
    assert EVENT_FACT in res["packet"]


# ── (1) capture: the digest keeps who/where/when for the event ──────────────

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

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(facts)}}]}

    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **k):
            sent["payload"] = json
            return _R()

    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", _C)
    stub = types.ModuleType("zoe_agent")

    async def _no_facts(*a, **k):
        return ""

    stub._mempalace_load_user_facts = _no_facts
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)
    return svc, sent


def test_digest_prompt_asks_for_the_person_and_the_event(monkeypatch):
    _, sent = _patch_digest(monkeypatch, [])
    asyncio.run(memory_digest.run_turn_digest("demo-a", SAY_SISTER, session_id="s1"))
    prompt = sent["payload"]["messages"][1]["content"]
    assert "return BOTH" in prompt
    assert "keeps who, what, where and when" in prompt
    assert "Never drop the place or the day" in prompt
    # the round-2 feeling instruction is untouched
    assert "keep that feeling in the fact" in prompt
    assert SAY_SISTER in prompt


def test_digest_stores_both_the_relation_and_the_event(monkeypatch):
    """The model's two facts for the S1 seed both survive post-processing
    (quality gate, the user-anchored-relationship check, reconcile) — the
    event keeps who, where and when, and no feeling is invented."""
    svc, _ = _patch_digest(monkeypatch, [
        {"type": "relationship", "fact": NAME_FACT},
        {"type": "event", "fact": EVENT_FACT},
    ])
    res = asyncio.run(memory_digest.run_turn_digest("demo-a", SAY_SISTER, session_id="s1"))
    assert res["new"] == 2
    texts = [t for t, _ in svc.ingested]
    assert texts == [NAME_FACT, EVENT_FACT]
    event = texts[1].lower()
    assert all(w in event for w in ("marisol", "sister", "lisbon", "thursday"))
    assert all(kw["metadata"] is None for _, kw in svc.ingested)
