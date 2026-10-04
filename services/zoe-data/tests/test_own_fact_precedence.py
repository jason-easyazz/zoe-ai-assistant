"""Own-fact recall questions never reach a clock/calendar/weather tool
(live 2026-10-04: "when is my birthday" -> head time -> "It's 7:50 AM.").

The class: a question about a stored fact of the user's own life (birthday,
address, age, where they live) carries a trigger word ("when", "where") that the
head reads as a tool domain. ZOE_OWN_FACT_PRECEDENCE (default OFF) re-points it
to memory on BOTH head surfaces (route() and the INTENT_GATE head_verdict) and
adds a recall-floor shape. Head + sidecar are faked as in
test_recall_shapes_event_time — no fastembed, sklearn, DB or network (ci_safe).
All phrasings are synthetic."""
import json
import logging
import pathlib

import pytest

np = pytest.importorskip("numpy")
memory_gate = pytest.importorskip("memory_gate")
semantic_router = pytest.importorskip("semantic_router")
router_two_stage = pytest.importorskip("router_two_stage")
fast_tiers = pytest.importorskip("fast_tiers")
zc = pytest.importorskip("zoe_flue_client")

pytestmark = pytest.mark.ci_safe

CLASSES = ("calendar", "chat", "memory", "people", "reminders", "time", "weather")
TIME_CALL = "call:get_time{}"
BDAY = "When is my birthday?"

POS = [BDAY, "when's my birthday again", "What's my address?", "what is my home address",
       "how old am I", "How old is my mum?", "when is mum's birthday", "when's my dad's birthday",
       "What's my phone number", "what's my email address", "where do I live",
       "which suburb do I live in", "when was I born", "what year was I born", "where am I from",
       "what's my middle name", "what's my star sign", "when is our anniversary",
       "what car do I drive", "what do I do for work", "what's my date of birth"]
NEG = ["What time is it?", "what's the time", "When is Easter?", "what's my schedule today",
       "how old is the universe", "what's the weather", "what's on my calendar",
       "what time is my dentist appointment", "when's my flight", "when is my birthday party",
       "what's my timer at", "what's the capital of France", "when is the game on",
       "what is my next meeting", "what's the project's name",
       # finding 1: "who am I <verb>ing" is a calendar question, not "who am I"
       "who am I meeting tomorrow", "who am I seeing on Friday", "who am I having lunch with",
       # finding 4: bare nouns and third-party possessives are not own-fact
       "what's my phone bill", "what's my job today", "when is my rego due", "what's Obama's age",
       "what's Sarah's phone number", "when is Nick's birthday"]


@pytest.mark.parametrize("text", POS)
def test_own_fact_positive(text):
    assert memory_gate.is_own_fact_question(text)


@pytest.mark.parametrize("text", NEG)
def test_own_fact_negative(text):
    assert not memory_gate.is_own_fact_question(text)


class _Head:
    def __init__(self, top, conf):
        self._p = np.full(len(CLASSES), (1.0 - conf) / (len(CLASSES) - 1))
        self._p[CLASSES.index(top)] = conf
        self.classes_ = np.asarray(CLASSES)

    def predict_proba(self, X):
        return self._p.reshape(1, -1)


@pytest.fixture
def head(monkeypatch, tmp_path):
    labels = np.asarray(CLASSES)
    for k, v in {"ROUTES": {d: [] for d in CLASSES}, "_MODEL": object(), "_LABELS": labels,
                 "_MATRIX": np.eye(len(CLASSES), dtype=np.float32), "_HEAD_LOG_PATH": str(tmp_path / "s.jsonl"),
                 "_DOM_IDX": {d: np.where(labels == d)[0] for d in CLASSES},
                 "embed": lambda text: np.ones(len(CLASSES), dtype=np.float32)}.items():
        monkeypatch.setattr(semantic_router, k, v)
    monkeypatch.setattr(router_two_stage, "_HEAD_FAILED", False)
    for k in ("ZOE_ROUTER_HEAD_MIN_CONF", "ZOE_ROUTER_TWO_STAGE_GATE", "ZOE_INTENT_ROUTER_GATE",
              "ZOE_ROUTER_EVENT_TIME_PRECEDENCE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "active")
    monkeypatch.setenv("ZOE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("ZOE_OWN_FACT_PRECEDENCE", "1")

    def _set(top, conf=0.9971, raw=TIME_CALL):
        monkeypatch.setattr(router_two_stage, "_HEAD", _Head(top, conf))
        monkeypatch.setattr(router_two_stage, "_post_sidecar", lambda text, grammar: raw)
    return _set


def test_exact_ask_leaves_the_clock_and_routes_to_recall(head, caplog):
    head("time")
    with caplog.at_level(logging.INFO, logger="semantic_router"):
        rr = semantic_router.route(BDAY)
    ts = rr["two_stage"]
    assert rr["domain"] == rr["routed"] == ts["domain"] == "memory"
    assert ts["tool"] == "recall_memory" and ts["reason"] == "own_fact_question"
    assert ts["head_top"] == "time" and ts["gated"] is False
    assert "INTENT_GATE own_fact_question=1 head=time conf=0.9971 routed=memory" in caplog.text


@pytest.mark.parametrize("top,call", [
    ("time", TIME_CALL), ("calendar", "call:show_calendar{}"), ("weather", "call:get_weather{}"),
    ("reminders", "call:list_reminders{}"),
])
@pytest.mark.parametrize("text", [BDAY, "what's my address", "how old am I", "where do I live",
                                  "when is mum's birthday"])
def test_no_clock_calendar_weather_tool_for_own_fact(head, text, top, call):
    head(top, 0.99, call)
    rr = semantic_router.route(text)
    assert rr["routed"] == "memory"
    assert rr["two_stage"]["tool"] == "recall_memory"


@pytest.mark.parametrize("top,want", [("people", "people"), ("memory", "memory"), ("chat", "chat")])
def test_people_memory_chat_claims_are_kept(head, top, want):
    head(top, 0.99, {"people": "call:people{name:<escape>Mum<escape>}", "memory": "call:recall_memory{}",
                     "chat": TIME_CALL}[top])
    rr = semantic_router.route("when is mum's birthday")
    assert rr["two_stage"]["domain"] == want
    assert rr["two_stage"].get("reason") != "own_fact_question"


@pytest.mark.parametrize("text,top,want", [
    ("What time is it?", "time", "time"),  # NEGATIVE CONTROL: the clock keeps the clock question
    ("what's the time", "time", "time"),
    ("what's my schedule today", "calendar", "calendar"),
    ("when is my birthday party", "calendar", "calendar"),  # event-time lane, not this rule
    ("what's the weather", "weather", "weather"),
])
def test_unrelated_questions_keep_their_tool(head, text, top, want):
    head(top, 0.99, {"time": TIME_CALL, "calendar": "call:show_calendar{}",
                     "weather": "call:get_weather{}"}[top])
    assert semantic_router.route(text)["routed"] == want


def test_negative_control_flag_off_the_clock_answers(head, monkeypatch):
    head("time")
    monkeypatch.delenv("ZOE_OWN_FACT_PRECEDENCE")
    rr = semantic_router.route(BDAY)
    assert rr["routed"] == "time" and rr["two_stage"]["tool"] == "get_time"


def test_evidence_rule_still_wins_first(head):
    head("time")
    assert semantic_router.route("When did I tell you my birthday?")["two_stage"]["reason"] == "evidence_question"


# ── the keyword lanes (INTENT_GATE) ──────────────────────────────────────────

@pytest.mark.parametrize("intent,text,allowed", [
    ("time_query", BDAY, False),
    ("date_query", BDAY, False),
    ("calendar_show", BDAY, False),
    ("weather", "where do I live", False),
    ("memory_remember", BDAY, True),
    ("people_search", "when is mum's birthday", True),
    ("time_query", "What time is it?", True),  # negative control
])
def test_keyword_gate_blocks_the_tool_and_keeps_recall(head, intent, text, allowed):
    head("time")
    assert fast_tiers.intent_gate(intent, text, lane="voice") is allowed


def test_gate_negative_control_flag_off_keyword_clock_is_not_blocked_by_this_rule(head, monkeypatch):
    head("time")
    monkeypatch.delenv("ZOE_OWN_FACT_PRECEDENCE")
    # head still says time -> the head agrees with the clock intent: allowed (today's behaviour)
    assert fast_tiers.intent_gate("time_query", BDAY, lane="voice") is True


@pytest.mark.parametrize("intent,want", [
    ("time_query", ("veto", "own_fact_question")),
    ("calendar_show", ("veto", "own_fact_question")),
    ("weather", ("veto", "own_fact_question")),
    ("memory_remember", ("allow", "own_fact_recall_intent")),
    ("people_search", ("allow", "own_fact_recall_intent")),
])
def test_gate_decision_table(monkeypatch, intent, want):
    monkeypatch.delenv("ZOE_INTENT_ROUTER_GATE", raising=False)
    verdict = {"domain": "memory", "head_top": "time", "reason": "own_fact_question", "gated": False}
    assert fast_tiers.intent_gate_decision(intent, verdict) == want


# ── Flue recall floor ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["How old is my mum?", "when is mum's birthday", "where do I live",
                                  "what's my middle name", "when was I born"])
def test_recall_floor_claims_own_fact_when_on(monkeypatch, text):
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    monkeypatch.setenv("ZOE_OWN_FACT_PRECEDENCE", "1")
    assert zc._recall_floor_shape(text)  # personal / own_fact — either way a packet is injected


@pytest.mark.parametrize("text", ["How old is my mum?", "when is mum's birthday", "how old am I"])
def test_recall_floor_flag_off_is_todays_behaviour(monkeypatch, text):
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    monkeypatch.delenv("ZOE_OWN_FACT_PRECEDENCE", raising=False)
    assert zc._recall_floor_shape(text) == ""


def test_recall_floor_own_fact_shape_name(monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    monkeypatch.setenv("ZOE_OWN_FACT_PRECEDENCE", "1")
    assert zc._recall_floor_shape("how old is my mum") == "own_fact"
    assert zc._recall_floor_shape("What time is it?") == ""
    assert zc._recall_floor_shape("how old is the universe") == ""


# ── the router corpus carries the phrasings with the right label ────────────

def test_corpus_rows_label_first_person_facts_as_memory_recall():
    repo = pathlib.Path(__file__).resolve().parents[3]
    stage1 = [json.loads(l) for l in (repo / "labs/setfit-router/data/misses.jsonl").read_text().splitlines() if l.strip()]
    stage2 = [json.loads(l) for l in (repo / "labs/functiongemma-finetune/data/train_misses.jsonl").read_text().splitlines() if l.strip()]
    frozen = {json.loads(l)["text"].strip().lower()
              for l in (repo / "labs/needle-benchmark/corpus.jsonl").read_text().splitlines() if l.strip()}
    own1 = [r for r in stage1 if r.get("source", "").startswith("own_fact_miss")]
    own2 = [r for r in stage2 if r.get("source", "").startswith("own_fact_miss")]
    assert len(own1) >= 15 and len(own2) >= 15
    for r in own1:
        assert r["label"] in ("memory", "people")
        assert r["text"].strip().lower() not in frozen
    assert any(r["text"] == "when is my birthday" and r["label"] == "memory" for r in own1)
    assert all(r["tool"] == "recall_memory" and r["args"].get("query") for r in own2)
    assert any(r["text"] == "when is my birthday" for r in own2)
    # every first-person row is also a question the guard itself recognises
    for r in own1:
        if r["label"] == "memory":
            assert memory_gate.is_own_fact_question(r["text"]), r["text"]


# ── review findings 1 and 4 ──────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["who am I meeting tomorrow", "who am I seeing on Friday"])
def test_who_am_i_verb_phrase_still_routes_to_calendar(head, text):
    head("calendar", 0.99, "call:show_calendar{}")
    assert semantic_router.route(text)["routed"] == "calendar"
    assert fast_tiers.intent_gate("calendar_show", text, lane="voice") is True


@pytest.mark.parametrize("text", ["Who am I", "who am I?", "so, who am I", "Hey Zoe, who am I."])
def test_who_am_i_alone_is_own_fact(text):
    assert memory_gate.own_fact_question_kind(text) == "self"


@pytest.mark.parametrize("text", ["what's my phone bill", "what's my job today", "when is my rego due",
                                  "what's Obama's age"])
def test_bare_nouns_and_third_party_possessives_keep_their_tool(head, text):
    head("time", 0.99, TIME_CALL)
    assert semantic_router.route(text)["routed"] == "time"  # NOT re-pointed to memory
