"""The router head is the authority over deterministic keyword claims (INTENT_GATE).

Samantha bar S1, live after #1763 (2026-09-28 23:18/23:25/23:26): the two-stage
router DID send "Who is flying in on Thursday, and where from?" to chat
(people @ 0.5371, reason low_conf). The turn was then answered in ~90 ms by the
LEGACY keyword lane in routers/chat.py: ``detect_and_extract_intent`` matched
``^who is (.+)$`` → ``people_search`` → ``No contacts found for "flying in on
thursday, and where from".`` (62 chars, sha 4dc88255 — the exact S1 reply).

Two fixes, both pinned here:

1. SHAPE — "who is <X>" claims a contacts lookup only for a NAME-shaped <X>
   (``intent_router._is_name_shaped``).
2. AUTHORITY — every deterministic keyword claim (fast_tiers Tier-0 and the
   chat.py keyword lane) asks the ACTIVE head first (stage 1 only, numpy — no
   sidecar call): chat/low_conf or a confident different domain → veto → the
   brain. Same rule as the Skybridge router gate (#1757). Logged
   ``INTENT_GATE lane=… intent=… head=<top>@<conf> … decision=allow|veto``.
   Flag ``ZOE_INTENT_ROUTER_GATE`` (default on; off = old behaviour).

The real-head tests use the SHIPPED numpy MLP head on committed bge-small
vectors (no fastembed, sklearn or network). Slim-dep-green (ci_safe).
"""
import asyncio
import json
import logging
import types
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
intent_router = pytest.importorskip("intent_router")
fast_tiers = pytest.importorskip("fast_tiers")
semantic_router = pytest.importorskip("semantic_router")
router_two_stage = pytest.importorskip("router_two_stage")
router_heads_numpy = pytest.importorskip("router_heads_numpy")

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
FIXTURE = SVC / "tests" / "fixtures" / "router_intent_gate_vectors.json"
S1 = "Who is flying in on Thursday, and where from?"
S1_REPLY = 'No contacts found for "flying in on thursday, and where from".'


@pytest.fixture(scope="module")
def vectors():
    data = json.loads(FIXTURE.read_text())
    return data, {c["text"]: np.asarray(c["vector"], dtype=np.float32) for c in data["cases"]}


@pytest.fixture
def real_head(monkeypatch, vectors):
    """ACTIVE router with the SHIPPED head; embed() served from the fixture."""
    _data, vecs = vectors
    head = router_heads_numpy.load_head(str(SVC / "models" / "router_head_mlp.joblib"))
    monkeypatch.setattr(router_two_stage, "_HEAD", head)
    monkeypatch.setattr(router_two_stage, "_HEAD_FAILED", False)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "active")
    monkeypatch.setenv("ZOE_ROUTER_ENABLED", "1")
    for k in ("ZOE_ROUTER_HEAD_MIN_CONF", "ZOE_ROUTER_TWO_STAGE_GATE", "ZOE_INTENT_ROUTER_GATE"):
        monkeypatch.delenv(k, raising=False)

    def _embed(text):
        return vecs[text].copy()

    monkeypatch.setattr(semantic_router, "embed", _embed)
    return head


# --------------------------------------------------------------------------- #
# 1. shape: "who is <X>" claims only a name-shaped X                          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    S1,
    "Who is flying in on Thursday, and which city do I live in?",
    "who is coming to dinner",
    "who is my dad and when is his birthday",
    "who is picking up the kids tomorrow",
])
def test_who_is_clause_is_not_a_contacts_lookup(text):
    got = intent_router.detect_intent(text, log_miss=False)
    assert got is None or got.name != "people_search", (text, got)


@pytest.mark.parametrize("text,query", [
    ("Who is Marisol?", "marisol"),
    ("who is my dentist", "my dentist"),
    ("who is Sarah Jones", "sarah jones"),
    ("who is john", "john"),
    ("who is my sister", "my sister"),
    ("Who is Mary-Jane Watson?", "mary-jane watson"),
])
def test_who_is_name_is_still_a_contacts_lookup(text, query):
    got = intent_router.detect_intent(text, log_miss=False)
    assert got is not None and got.name == "people_search"
    assert got.slots["query"] == query


# --------------------------------------------------------------------------- #
# 2. the rule (pure)                                                           #
# --------------------------------------------------------------------------- #
def _verdict(domain, conf=0.95, reason=None, top=None):
    return {"domain": "chat" if reason else domain, "head_top": top or domain,
            "head_conf": conf, "gated": reason is not None, "reason": reason}


@pytest.mark.parametrize("intent,verdict,want", [
    ("people_search", _verdict("people", 0.5371, "low_conf"), ("veto", "router_chat")),
    ("people_search", _verdict("chat", 0.9, "chat_top", top="chat"), ("veto", "router_chat")),
    ("list_remove", _verdict("time", 0.98), ("veto", "router_disagrees")),
    ("people_search", _verdict("people", 0.99), ("allow", "router_agrees")),
    ("people_search", _verdict("memory", 0.99), ("allow", "router_agrees")),
    ("calendar_show", _verdict("reminders", 0.9), ("allow", "router_agrees")),
    ("calendar_show", _verdict("lists", 0.31, "below_gate"), ("allow", "router_unsure")),
    ("calendar_show", None, ("allow", "router_unavailable")),
    ("greeting", _verdict("chat", 0.99, "chat_top", top="chat"), ("allow", "no_router_class")),
])
def test_gate_rule(intent, verdict, want):
    assert fast_tiers.intent_gate_decision(intent, verdict) == want


@pytest.mark.parametrize("value", ["false", "0", "off", " No "])
def test_gate_kill_switch(monkeypatch, value):
    monkeypatch.setenv("ZOE_INTENT_ROUTER_GATE", value)
    assert fast_tiers.intent_gate_decision(
        "people_search", _verdict("people", 0.5371, "low_conf")) == ("allow", "flag_off")


def test_head_off_means_no_opinion(monkeypatch):
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "off")
    assert semantic_router.head_verdict(S1) is None
    assert fast_tiers.intent_gate("people_search", S1, lane="chat") is True


# --------------------------------------------------------------------------- #
# 3. the real shipped head                                                     #
# --------------------------------------------------------------------------- #
def test_fixture_head_outputs_are_unchanged(real_head, vectors):
    data, _ = vectors
    assert [str(c) for c in real_head.classes_] == data["classes"]
    for case in data["cases"]:
        got = real_head.predict_proba(
            np.asarray(case["vector"], dtype=np.float32).reshape(1, -1))[0]
        assert np.max(np.abs(got - np.asarray(case["proba"]))) <= 1e-6, case["text"]


def test_real_head_vetoes_the_s1_contacts_claim(real_head, caplog):
    v = semantic_router.head_verdict(S1)
    assert v["head_top"] == "people" and v["reason"] == "low_conf" and v["domain"] == "chat"
    caplog.set_level(logging.INFO, logger="fast_tiers")
    assert fast_tiers.intent_gate("people_search", S1, lane="chat") is False
    assert "INTENT_GATE lane=chat intent=people_search head=people@0.5371" in caplog.text
    assert "decision=veto reason=router_chat" in caplog.text


@pytest.mark.parametrize("text,intent", [
    ("Who is Marisol?", "people_search"),
    ("who is my dentist", "people_search"),
    ("What's on my calendar Thursday?", "calendar_show"),
    ("what's the weather like", "weather"),
    ("what time is it", "time_query"),
])
def test_real_head_agrees_with_real_asks(real_head, text, intent):
    assert intent_router.detect_intent(text, log_miss=False).name == intent
    assert fast_tiers.keyword_intent_allowed(intent, text, lane="chat") is True


def test_real_head_vetoes_a_keyword_misclaim(real_head):
    # measured: the keyword lane reads "got the time on you" as a LIST removal;
    # the head is confident it is a time ask → veto (the brain answers).
    got = intent_router.detect_intent("got the time on you", log_miss=False)
    assert got is not None and got.name == "list_remove"
    assert fast_tiers.keyword_intent_allowed("list_remove", "got the time on you", lane="chat") is False


def test_context_or_classifier_intent_is_not_gated(real_head):
    # the bare detector does not claim S1 any more → not a keyword claim → the
    # gate has nothing to say (a context follow-up has the same shape)
    assert fast_tiers.keyword_intent_allowed("people_search", S1, lane="chat") is True


# --------------------------------------------------------------------------- #
# 4. Tier-0 (fast_tiers.resolve) and the chat.py keyword lane                  #
# --------------------------------------------------------------------------- #
def _patch_execute(monkeypatch, reply):
    calls = []

    async def _exec(intent, user_id):
        calls.append(intent.name)
        return reply

    monkeypatch.setattr(intent_router, "execute_intent", _exec)
    return calls


def test_tier0_calendar_ask_still_answers(real_head, monkeypatch):
    calls = _patch_execute(monkeypatch, "Nothing on Thursday.")
    res = asyncio.run(fast_tiers._tier0("What's on my calendar Thursday?", "demo"))
    assert res is not None and res.tier == "tier0" and calls == ["calendar_show"]


def test_tier0_keyword_claim_the_head_rejects_falls_through(real_head, monkeypatch):
    calls = _patch_execute(monkeypatch, "It is 9:44 PM.")
    # a Tier-0 read claim the head rejects: pretend the detector read S1 as a
    # calendar read (the head says people @ 0.5371 → low_conf → chat)
    monkeypatch.setattr(intent_router, "detect_intent",
                        lambda t, log_miss=False, **k: intent_router.Intent("calendar_show", {"qualifier": "thursday"}))
    assert asyncio.run(fast_tiers._tier0(S1, "demo")) is None
    assert calls == []


def test_s1_through_resolve_chat_returns_none(real_head, monkeypatch):
    calls = _patch_execute(monkeypatch, S1_REPLY)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: False)  # stop after Tier-0
    import expert_dispatch
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    res = asyncio.run(fast_tiers.resolve(S1, "demo", "s", channel="chat"))
    assert res is None and calls == []


class _Request:
    def __init__(self, body):
        self._body = body
        self.headers = {}

    async def json(self):
        return self._body


@pytest.fixture
def chat_lane(real_head, monkeypatch):
    """Drive routers.chat.chat(stream=False) to the keyword lane; the brain
    call is replaced by a marker so the test sees which lane answered."""
    chat_router = pytest.importorskip("routers.chat")
    executed = []

    async def _noop(*a, **k):
        return None

    async def _exec(intent, user_id):
        executed.append(intent.name)
        return S1_REPLY

    async def _no_fast_path(*a, **k):
        return None

    async def _not_accepted(*a, **k):
        return {"accepted": False}

    async def _brain(msg, *a, **k):
        return "BRAIN"

    async def _detect(text, user_id, context=None):
        # today's live claim: the keyword lane reads S1 as a contacts lookup
        return intent_router.Intent("people_search", {"query": "flying in on thursday, and where from"})

    monkeypatch.setattr(chat_router, "_ensure_user_and_chat_session", _noop)
    monkeypatch.setattr(chat_router, "_save_chat_message", _noop)
    monkeypatch.setattr(chat_router, "_GUARDED_AUTO", False, raising=False)
    monkeypatch.setattr(chat_router, "_ALL_TOOLS_ENABLED", True, raising=False)
    monkeypatch.setattr(chat_router, "classify_query", lambda m: "chat")
    monkeypatch.setattr(chat_router, "_run_chat_pi_hybrid_lane", _not_accepted)
    monkeypatch.setattr(fast_tiers, "resolve", _no_fast_path)
    monkeypatch.setattr(chat_router, "detect_and_extract_intent", _detect)
    monkeypatch.setattr(chat_router, "execute_intent", _exec)
    monkeypatch.setattr(chat_router, "_USE_LOCAL_BRAIN", True, raising=False)
    monkeypatch.setattr(chat_router, "_safe_load_portrait", _noop)
    monkeypatch.setattr(chat_router, "_brain_oneshot", _brain)
    monkeypatch.setattr(chat_router, "chat_inject_background", _noop)
    monkeypatch.setattr(chat_router, "_persist_memory_candidates", _noop)
    return chat_router, executed


def _run_chat(chat_router, message):
    req = _Request({"message": message, "session_id": "t-s1", "stream": False})
    return asyncio.run(chat_router.chat(req, user={"user_id": "demo_bar"}, stream=False))


def test_chat_keyword_lane_s1_goes_to_the_brain(chat_lane, monkeypatch):
    chat_router, executed = chat_lane
    # the bare detector still claims it (as it did live) → the gate is consulted
    monkeypatch.setattr(intent_router, "detect_intent",
                        lambda t, log_miss=False, **k: intent_router.Intent("people_search", {"query": "x"}))
    out = _run_chat(chat_router, S1)
    assert executed == [] and out["response"] == "BRAIN"


def test_chat_keyword_lane_gate_off_restores_the_live_miss(chat_lane, monkeypatch):
    chat_router, executed = chat_lane
    monkeypatch.setenv("ZOE_INTENT_ROUTER_GATE", "off")
    monkeypatch.setattr(intent_router, "detect_intent",
                        lambda t, log_miss=False, **k: intent_router.Intent("people_search", {"query": "x"}))
    out = _run_chat(chat_router, S1)
    assert executed == ["people_search"] and out["response"] == S1_REPLY


def test_chat_keyword_lane_real_name_still_answers(chat_lane, monkeypatch):
    chat_router, executed = chat_lane

    async def _detect(text, user_id, context=None):
        return intent_router.detect_intent(text, log_miss=False)

    monkeypatch.setattr(chat_router, "detect_and_extract_intent", _detect)
    out = _run_chat(chat_router, "Who is Marisol?")
    assert executed == ["people_search"] and out["response"] == S1_REPLY


def test_both_chat_keyword_lanes_are_gated():
    """The streaming lane is too wide to drive here; pin that it calls the same
    gate as the non-stream lane (tested end-to-end above) and that a veto also
    skips the Tier 0.5 LLM classifier (which could otherwise re-claim the turn)."""
    src = (SVC / "routers" / "chat.py").read_text()
    assert src.count('keyword_intent_allowed(intent.name, message_for_processing, lane="chat")') == 2
    assert "if intent is None and not _intent_vetoed and use_intent_fast_path" in src


# --------------------------------------------------------------------------- #
# follow-ups (#1767 Greptile): real "-ing" names; every detector intent mapped #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,query", [
    ("Who is King?", "king"),
    ("who is Ming", "ming"),
    ("Who is Browning?", "browning"),
    ("Who is Sterling Archer?", "sterling archer"),
    ("hey zoe who is King", "king"),
    # trailing punctuation incl. the single-character ellipsis (#1769 Greptile)
    ("Who is King\u2026?", "king"),
    ("who is Ming...", "ming"),
    ("Who is Browning?!", "browning"),
])
def test_capitalised_ing_names_are_contacts_lookups(text, query):
    got = intent_router.detect_intent(text, log_miss=False)
    assert got is not None and got.name == "people_search" and got.slots["query"] == query


@pytest.mark.parametrize("text", [
    S1,
    "who is coming to dinner",
    "who is coming",
    "Who is going out tonight?",
    "who is picking up the kids tomorrow",
    # lower-case "-ing" + a direct OBJECT, not a particle (#1769 Greptile)
    "who is bringing groceries",
    "who is making dinner tonight",
    "who is ming",  # lower-case lone "-ing": ambiguous → the brain, never a canned miss
])
def test_ing_verb_phrases_go_to_the_brain(text):
    got = intent_router.detect_intent(text, log_miss=False)
    assert got is None or got.name != "people_search", (text, got)


def _detector_intent_names() -> set[str]:
    import re

    src = (SVC / "intent_router.py").read_text()
    names = set()
    for a, b in re.findall(
            r'Intent\(\s*"([a-z_]+)"(?:\s+if\s+[^"\n]+?\s+else\s+"([a-z_]+)")?', src):
        names.update(n for n in (a, b) if n)
    assert len(names) > 50, "the Intent( literal scan broke — fix the regex, not the map"
    return names


def test_every_detector_intent_is_gated_or_explicitly_ungated():
    mapped = set(fast_tiers._INTENT_ROUTER_DOMAINS)
    ungated = set(fast_tiers._INTENT_UNGATED)
    assert not mapped & ungated, mapped & ungated
    assert all(fast_tiers._INTENT_UNGATED[n].strip() for n in ungated)
    missing = (_detector_intent_names() | set(fast_tiers._TIER0_READ_INTENTS)) - mapped - ungated
    assert not missing, (
        f"detector intents with no gate decision: {sorted(missing)} — add each to "
        "fast_tiers._INTENT_ROUTER_DOMAINS (router class) or _INTENT_UNGATED (with a reason)")
    assert set(fast_tiers._TIER0_READ_INTENTS) <= mapped


@pytest.mark.parametrize("intent", ["journal_streak", "journal_prompt"])
def test_journal_keyword_claims_are_gated(intent):
    assert fast_tiers.intent_gate_decision(
        intent, _verdict("journal", 0.62, "low_conf")) == ("veto", "router_chat")
    assert fast_tiers.intent_gate_decision(intent, _verdict("journal", 0.97)) == ("allow", "router_agrees")
