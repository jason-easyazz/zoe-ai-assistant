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
    ("I'm stressed and my sister said she is fine", "stressed"),
    ("I told her I'm anxious about it", "anxious"),
    # the reported span ends at its clause — the user's own clause survives
    ("My sister said she is fine, but I'm stressed about my interview", "stressed"),
    ("My sister said she is fine; I'm stressed", "stressed"),
    ("My sister said she was tired and I'm exhausted", "exhausted"),
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
    # someone else's words, quoted or reported (Greptile #1762)
    'My sister said "I\'m so stressed about her exams."',
    "My sister said 'I'm so stressed about her exams,' and laughed",
    "My sister said “I’m so stressed”",
    "My sister said I'm so stressed",
    "He thinks I'm nervous",
    "",
])
def test_non_first_person_or_non_feelings_are_not(message):
    assert extract_affect(message) == ("", "")


def test_a_shared_weekday_does_not_carry_the_feeling_to_another_sentence():
    """Greptile #1762: '…interview on Friday. My sister arrives Friday'."""
    msg = "I'm anxious about my interview on Friday. My sister arrives Friday."
    affect, sentence = extract_affect(msg)
    assert affect == "anxious"
    f = memory_digest._affect_for_fact
    assert f("User has an interview on Friday", affect, sentence, msg) == "anxious"
    assert f("User's sister arrives on Friday", affect, sentence, msg) == ""
    # the S4 shortening: the digest keeps only "interview" from the feeling
    # sentence — it must still carry the feeling (Greptile #1762)
    assert f("User has a job interview on Friday", *extract_affect(SAY_WORRY), SAY_WORRY) == "anxious"
    assert f("The user has a job interview at the aquarium on Friday.",
             *extract_affect(SAY_WORRY), SAY_WORRY) == "anxious"
    # a fact that fits BOTH sentences equally is ambiguous → nothing attached
    both = "I'm anxious about the Lisbon trip. The Lisbon trip is long."
    a2, s2 = extract_affect(both)
    assert f("User has a Lisbon trip", a2, s2, both) == ""


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


def test_digest_update_path_carries_the_new_feeling(monkeypatch):
    """Greptile #1762: an anxious turn that SUPERSEDES an existing neutral
    interview fact must carry its feeling onto the updated row."""
    import memory_quality

    svc, _ = _patch_digest(monkeypatch, [
        {"type": "event", "fact": "User is anxious about their job interview at the aquarium on Friday."},
    ])
    reviewed = []

    async def review(mem_id, **kw):
        reviewed.append((mem_id, kw))
        return MemoryRef(id="edited-1", text=kw["edits"])

    async def reconcile(_svc, fact, user_id):
        return "update", "neutral-1"

    svc.review = review
    monkeypatch.setattr(memory_quality, "reconcile_for_ingest", reconcile)
    res = asyncio.run(memory_digest.run_turn_digest("demo-a", SAY_WORRY, session_id="s1"))
    assert res["new"] == 1 and svc.ingested == []
    (mem_id, kw), = reviewed
    assert mem_id == "neutral-1" and kw["metadata"] == {"affect": "anxious"}


def test_digest_neutral_update_clears_the_old_feeling(monkeypatch):
    """Greptile #1762: a NEUTRAL turn that supersedes an anxious fact must not
    keep the old feeling — the update passes an explicit empty affect."""
    import memory_quality

    svc, _ = _patch_digest(monkeypatch, [
        {"type": "event", "fact": "User's job interview at the aquarium moved to Monday."},
    ])
    reviewed = []

    async def review(mem_id, **kw):
        reviewed.append((mem_id, kw))
        return MemoryRef(id="edited-1", text=kw["edits"])

    async def reconcile(_svc, fact, user_id):
        return "update", "anxious-1"

    svc.review = review
    monkeypatch.setattr(memory_quality, "reconcile_for_ingest", reconcile)
    asyncio.run(memory_digest.run_turn_digest(
        "demo-a", "Update: my job interview at the aquarium moved to Monday.", session_id="s1"))
    (_, kw), = reviewed
    assert kw["metadata"] == {"affect": ""}


class _Col:
    def __init__(self):
        self.rows = {}

    def upsert(self, *, ids, documents, metadatas):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, dict(m))

    def get(self, *, ids=None, include=None, **_kw):
        hit = [i for i in (ids or []) if i in self.rows]
        return {"ids": hit, "documents": [self.rows[i][0] for i in hit],
                "metadatas": [dict(self.rows[i][1]) for i in hit]}


@pytest.mark.asyncio
async def test_real_review_edit_stores_affect_and_continuity_focuses_it(monkeypatch):
    """End to end on the real MemoryService edit path: a neutral fact edited with
    the turn's feeling becomes the continuity focus."""
    from memory_service import MemoryService

    svc = MemoryService(data_dir="/nonexistent/zoe-test-continuity-affect")
    col = _Col()
    svc._collection = lambda: col

    async def no_audit(**_kw):
        return None

    svc._append_audit = no_audit

    # pinned to the explicit `optin` mode (not the household default): this member has consented
    import persona_layer

    async def consenting(_uid, db=None):
        return persona_layer.MemberMode(mode="companion", minor=False)

    monkeypatch.setattr(persona_layer, "load_member_mode", consenting)
    monkeypatch.setenv("ZOE_AFFECT_CONSENT_GATE", "optin")
    old_meta = {"user_id": "demo-a", "wing": "demo-a", "status": "approved", "memory_type": "event",
                "source": "turn_digest", "confidence": 0.82,
                "added_at": (NOW - datetime.timedelta(hours=30)).isoformat()}
    col.rows["neutral-1"] = ("The user has a job interview at the aquarium on Friday.", old_meta)
    new = await svc.review("neutral-1", decision="edit", actor="turn_digest",
                           edits="User is anxious about their job interview at the aquarium on Friday.",
                           metadata={"affect": "anxious"})
    assert new.metadata["candidate_affect"] == "anxious"
    assert col.rows["neutral-1"][1]["status"] == "superseded"
    assert memory_affect(new) == "anxious" and is_emotional_memory(new)
    res = await _compose(monkeypatch, [HOME, DAD, new])
    assert res["continuity_focus"]["affect"] == "anxious"
    assert "job interview" in res["continuity_focus"]["text"]
    # …and a later NEUTRAL edit of that anxious row clears the feeling instead of
    # carrying it forward onto the new fact
    newer = await svc.review(new.id, decision="edit", actor="turn_digest",
                             edits="User's job interview at the aquarium moved to Monday.",
                             metadata={"affect": ""})
    assert memory_affect(newer) == "" and not is_emotional_memory(newer)
    res2 = await _compose(monkeypatch, [HOME, DAD, newer])
    assert "continuity_focus" not in res2


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


@pytest.mark.parametrize("flag, uid, registered, logged", [
    ("1", DEMO, False, True),
    ("1", "test_bar_deadbeef", False, True),
    ("1", DEMO, True, False),         # a REAL account named like a demo id (Greptile #1762)
    ("1", DEMO, "raise", False),      # registration unverifiable → fail closed
    ("1", "jason", False, False),     # a real user is never traced
    ("1", "demo_user", False, False),  # a real account can look demo-ish; the shape is exact
    ("", DEMO, False, False),         # default OFF
])
@pytest.mark.asyncio
async def test_debug_trace_only_for_harness_ids(monkeypatch, caplog, flag, uid, registered, logged):
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_DEBUG", flag)
    monkeypatch.delenv("ZOE_SYNTHETIC_USER_ALLOWLIST", raising=False)
    looked_up = []

    async def fake_registered(user_id):
        looked_up.append(user_id)
        if registered == "raise":
            raise RuntimeError("auth_users unreachable")
        return registered

    monkeypatch.setattr(memories, "_registered_account", fake_registered)
    _stub_turn(monkeypatch)
    caplog.set_level(logging.INFO, logger=zc.logger.name)
    block = await zc._continuity_context_block(ASK_WORRY, uid)
    out = [d async for d in zc.run_flue_brain_streaming(ASK_WORRY, "s1", uid)]
    assert out[-1].startswith("Oh no")  # the stream itself is untouched
    msgs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("SEAM_CONTINUITY_DEBUG")]
    if flag != "1" or uid in ("jason", "demo_user"):
        assert looked_up == []  # cheap gates first: no DB read for these
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
