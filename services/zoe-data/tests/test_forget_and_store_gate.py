"""A forget command is never stored, and talk about the conversation is never a fact (QA 2026-11-05).

Hands-on QA of the live box, text chat, a synthetic demo user:

* "no, don't remember that! forget that" -> nothing was forgotten, and the post-turn extractor stored
  "User asked me to remember: that! forget that" (its remember template matched the NEGATED "remember").
* "please forget that I said I literally just asked what you know about me" -> the teach lane answered
  "Got it - I'll remember please forget that you said ..." and stored it.
* "um I literally just asked what you know about me" -> stored as a fact about the person.

The anchored forget intent missed a leading "no," and "forget that I said ..."; the teach lane stored anything
with "remember" or an "I" in it. Forgotten means forever, and junk is never a fact.
"""
import asyncio

import pytest

pytestmark = pytest.mark.ci_safe

memory_quality = pytest.importorskip("memory_quality")
expert_dispatch = pytest.importorskip("expert_dispatch")
memory_service = pytest.importorskip("memory_service")
intent_router = pytest.importorskip("intent_router")
memory_extractor = pytest.importorskip("memory_extractor")
fast_tiers = pytest.importorskip("fast_tiers")

NO_FORGET = "no, don't remember that! forget that"
FORGET_I_SAID = "please forget that I said I literally just asked what you know about me"
COMPLAINT = "um I literally just asked what you know about me"
FORGET_COMMANDS = [
    NO_FORGET,
    FORGET_I_SAID,
    "oh, forget that",
    "wait no, forget what I just said",
    "no don’t remember that",   # a phone keyboard's curly apostrophe
    "actually, don't save that please",
]
REAL_FACTS = [
    "remember that my sister's birthday is in May",
    "my dog is called Rex",
    "don't forget that the bins go out on Tuesday",
]
LONG_STORY = ("Yesterday I went to the market with my brother and we talked for ages about how he can never "
              "remember anything, and he said I should just forget that whole argument we had last spring")


def _run(coro):
    return asyncio.run(coro)


# ── the shared detector ──────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", FORGET_COMMANDS)
def test_forget_commands_are_recognised(text):
    assert memory_quality.is_forget_command(text)


@pytest.mark.parametrize("text", REAL_FACTS + [
    LONG_STORY,
    "oh forget it",                          # dismisses a question; must never erase a memory
    "oh forget that, what's the weather",    # trailing content: not a retraction
    "forget that I told you about Sam",      # names a subject: the entity forget's job
    "I always forget that my keys are in the car",
])
def test_non_commands_are_not_forget_commands(text):
    assert not memory_quality.is_forget_command(text)


def test_dont_forget_that_is_a_teach_not_forget_wording():
    assert not memory_quality.has_forget_wording("don't forget that the bins go out on Tuesday")
    assert memory_quality.has_teach_cue("don't forget that the bins go out on Tuesday")


# ── the intent lane routes every forget shape to the forget handler ─────────────────────────────────────────────

@pytest.mark.parametrize("text", FORGET_COMMANDS)
def test_intent_lane_routes_forget_commands(text):
    intent = intent_router.detect_intent(text, log_miss=False)
    assert intent is not None and intent.name == "memory_forget_last", (text, intent)


def test_a_long_story_mentioning_forget_is_not_hijacked():
    intent = intent_router.detect_intent(LONG_STORY, log_miss=False)
    assert intent is None or intent.name != "memory_forget_last"


VETO_CHAT = {"domain": "chat", "gated": True, "reason": "chat_top", "head_top": "chat", "head_conf": 0.83}


@pytest.mark.parametrize("text", [NO_FORGET, FORGET_I_SAID, "oh, forget that"])
def test_router_head_cannot_veto_an_opener_forget(monkeypatch, text):
    import semantic_router
    monkeypatch.setattr(semantic_router, "head_verdict", lambda t: VETO_CHAT)
    assert fast_tiers.intent_gate("memory_forget_last", text, lane="chat") is True


# ── the teach lane: forget first, then the verbatim gate ────────────────────────────────────────────────────────

class _FakeSvc:
    def __init__(self):
        self.ingested: list[str] = []

    async def ingest(self, text, **kw):
        self.ingested.append(text)

    async def search(self, *a, **k):
        return []


@pytest.fixture
def lane(monkeypatch):
    svc = _FakeSvc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    executed: list[str] = []

    async def _fake_execute(intent, user_id):
        executed.append(intent.name)
        return "FORGOT"

    async def _fake_run_expert(domain, text, user_id, session_id):
        return None

    async def _no_link(*a, **k):
        return 0

    monkeypatch.setattr(intent_router, "execute_intent", _fake_execute)
    monkeypatch.setattr(expert_dispatch, "_run_expert", _fake_run_expert)
    monkeypatch.setattr(memory_extractor, "extract_and_ingest", _no_link)
    return svc, executed


@pytest.mark.parametrize("text", [NO_FORGET, FORGET_I_SAID])
def test_teach_lane_forgets_and_stores_nothing(lane, text):
    svc, executed = lane
    out = _run(expert_dispatch.store_fact("memory", text, "demo-user"))
    assert svc.ingested == [], f"{text!r} was stored"
    assert executed == ["memory_forget_last"]
    assert out == "FORGOT"


def test_teach_lane_never_stores_the_conversation_complaint(lane):
    svc, executed = lane
    out = _run(expert_dispatch.store_fact("memory", COMPLAINT, "demo-user"))
    assert svc.ingested == []
    assert executed == []
    assert not (out or "").startswith("Got it")


@pytest.mark.parametrize("text", REAL_FACTS)
def test_teach_lane_still_stores_real_facts(lane, text):
    svc, executed = lane
    _run(expert_dispatch.store_fact("memory", text, "demo-user"))
    assert len(svc.ingested) == 1, text
    assert executed == []


def test_teach_lane_does_not_hijack_a_long_story(lane):
    svc, executed = lane
    _run(expert_dispatch.store_fact("memory", LONG_STORY, "demo-user"))
    assert executed == []


# ── the write-quality gate and the post-turn extractor ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    NO_FORGET, FORGET_I_SAID, COMPLAINT,
    "User asked me to remember: that! forget that",   # the exact row stored live
])
def test_quality_gate_rejects(text):
    ok, reason = memory_quality.is_storable_fact(text)
    assert not ok and reason in ("forget_command", "conversation_meta"), (text, reason)


@pytest.mark.parametrize("text", REAL_FACTS)
def test_quality_gate_keeps_real_facts(text):
    assert memory_quality.is_storable_fact(text) == (True, "")


@pytest.mark.parametrize("text", [NO_FORGET, "please forget that I said I live in Springfield"])
def test_extractor_mines_nothing_from_a_forget(text):
    assert memory_extractor._extract_plain(text) == []


def test_extractor_skips_a_negated_remember_inside_a_longer_turn():
    cands = memory_extractor._extract_plain("no, don't remember that I was joking about the beach house")
    assert not any(c.text.startswith("User asked me to remember") for c in cands)


def test_extractor_still_mines_an_explicit_teach():
    cands = memory_extractor._extract_plain("remember that my sister's birthday is in May")
    assert any(c.text.startswith("User asked me to remember") for c in cands)
