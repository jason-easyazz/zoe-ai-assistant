"""Evidence-shaped questions never route to a domain expert (live 2026-09-30:
"What exactly did I say about Marisol?" → router people @ 0.9872, no recall
floor, no quote). The live head decision is replayed with a fake head/sidecar —
no fastembed, sklearn or network (ci_safe)."""
import asyncio
import json
import logging
import sys
import types

import pytest

np = pytest.importorskip("numpy")
semantic_router = pytest.importorskip("semantic_router")
router_two_stage = pytest.importorskip("router_two_stage")
fast_tiers = pytest.importorskip("fast_tiers")
expert_dispatch = pytest.importorskip("expert_dispatch")
recall_evidence = pytest.importorskip("recall_evidence")
zc = pytest.importorskip("zoe_flue_client")

pytestmark = pytest.mark.ci_safe

ASK = "What exactly did I say about Marisol?"
MILK = "When did I add milk to my shopping list?"  # detector: list_add{"when did i add milk"}
CLASSES, DOMAINS = ("chat", "lists", "memory", "people"), ("people", "memory", "lists", "chat")
PEOPLE_CALL = "call:people{name:<escape>Marisol<escape>}"


class _Head:
    def __init__(self, top, conf):
        self._p = np.full(4, (1.0 - conf) / 3)
        self._p[CLASSES.index(top)] = conf
        self.classes_ = np.asarray(CLASSES)

    def predict_proba(self, X):
        return self._p.reshape(1, -1)


@pytest.fixture
def head(monkeypatch, tmp_path):
    """ACTIVE route() over a fake similarity matrix; returns set(top, conf, raw)."""
    labels = np.asarray(DOMAINS)
    for k, v in {"ROUTES": {d: [] for d in DOMAINS}, "_MODEL": object(), "_LABELS": labels,
                 "_MATRIX": np.eye(4, dtype=np.float32), "_HEAD_LOG_PATH": str(tmp_path / "s.jsonl"),
                 "_DOM_IDX": {d: np.where(labels == d)[0] for d in DOMAINS},
                 "embed": lambda text: np.ones(4, dtype=np.float32)}.items():
        monkeypatch.setattr(semantic_router, k, v)
    monkeypatch.setattr(router_two_stage, "_HEAD_FAILED", False)
    for k in ("ZOE_ROUTER_HEAD_MIN_CONF", "ZOE_ROUTER_TWO_STAGE_GATE", "ZOE_INTENT_ROUTER_GATE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "active")
    monkeypatch.setenv("ZOE_ROUTER_ENABLED", "1")

    def _set(top, conf=0.9872, raw=PEOPLE_CALL):
        monkeypatch.setattr(router_two_stage, "_HEAD", _Head(top, conf))
        monkeypatch.setattr(router_two_stage, "_post_sidecar", lambda text, grammar: raw)
    return _set


def test_exact_ask_routes_to_memory_despite_confident_people_head(head, caplog, tmp_path):
    head("people")
    with caplog.at_level(logging.INFO, logger="semantic_router"):
        rr = semantic_router.route(ASK)
    assert rr["domain"] == rr["routed"] == rr["two_stage"]["domain"] == "memory"
    assert rr["two_stage"]["reason"] == "evidence_question" and rr["two_stage"]["head_top"] == "people"
    assert rr["score"] == rr["scores"]["memory"]  # memory's own score, never people's
    assert "INTENT_GATE evidence_question=1 head=people conf=0.9872 routed=memory" in caplog.text
    rec = json.loads((tmp_path / "s.jsonl").read_text().splitlines()[-1])
    assert rec["two_stage_domain"] == "people" and rec["actual_routed"] == "memory"


@pytest.mark.parametrize("text,top,raw,want", [
    ("who is Marisol?", "people", PEOPLE_CALL, "people"),
    ("add Marisol to my contacts", "people", PEOPLE_CALL, "people"),
    ("what did I say about my sister", "people", PEOPLE_CALL, "memory"),
    (MILK, "lists", "call:shopping_list_add{item:<escape>milk<escape>}", "memory"),
    ("are you sure?", "people", PEOPLE_CALL, "chat"),  # a challenge → the brain
    (ASK, "chat", PEOPLE_CALL, "chat"),  # a brain decision is left alone
])
def test_route(head, text, top, raw, want):
    head(top, 0.99, raw)
    rr = semantic_router.route(text)
    assert rr["domain"] == rr["routed"] == want


def test_negative_control_without_precedence_people_claims_it(head, monkeypatch):
    head("people")
    monkeypatch.setattr(semantic_router, "evidence_target", lambda text, domain: None)
    assert semantic_router.route(ASK)["routed"] == "people"


def test_chat_tier1_dispatches_memory_not_people(head, monkeypatch):
    head("people")
    seen = []

    async def _dispatch(domain, text, ctx, *, write_ok=True):
        seen.append(domain)

    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(expert_dispatch, "dispatch", _dispatch)
    asyncio.run(fast_tiers.resolve(ASK, "demo", "s", channel="chat", run_tier0=False))
    assert seen == ["memory"]


@pytest.mark.parametrize("text,top,conf,intent,allowed", [
    (MILK, "lists", 0.99, "list_add", False),
    (MILK, "lists", 0.45, "list_add", False),  # an UNSURE head no longer lets it through
    (ASK, "people", 0.9872, "people_search", False),
    ("forget what I said about Marisol", "people", 0.9872, "memory_forget_entity", True),
    ("who is Marisol?", "people", 0.9872, "people_search", True),
])
def test_keyword_gate(head, caplog, text, top, conf, intent, allowed):
    head(top, conf)
    with caplog.at_level(logging.INFO, logger="fast_tiers"):
        assert fast_tiers.intent_gate(intent, text, lane="chat") is allowed
    if intent != "people_search" or not allowed:
        assert "head_reason=evidence_question" in caplog.text


@pytest.mark.parametrize("text,want", [
    (ASK, "evidence"), ("are you sure?", "evidence"),
    ("what did I say about my sister", "personal"), ("what's the weather like", ""),
])
def test_flue_recall_floor_claims_evidence_questions(monkeypatch, text, want):
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    assert zc._recall_floor_shape(text) == want


def test_core_memory_expert_packet_slice_carries_the_utterance(monkeypatch):
    zcc = pytest.importorskip("zoe_core_client")
    sent = {}

    async def _stream(message, session_id, **kw):
        sent["message"] = message
        yield "ok"

    async def _search(text, user_id, limit):
        return [types.SimpleNamespace(text="Marisol is flying in from Lisbon on Thursday")]

    monkeypatch.setattr(zcc, "run_zoe_core_streaming", _stream)
    monkeypatch.setitem(sys.modules, "memory_service", types.SimpleNamespace(
        get_memory_service=lambda: types.SimpleNamespace(search=_search)))
    asyncio.run(expert_dispatch._run_expert("memory", ASK + " and so on" * 30, "demo_x", "s"))
    packet_msg = sent["message"][:zcc._PACKET_MESSAGE_CHARS]  # the core lane's packet query
    assert ASK in packet_msg and recall_evidence.wants_quotes(packet_msg, "nobody")
