"""Skybridge fast path may not claim declaratives, and defers to the router.

Live over-claim, 2026-09-28 18:26:27 (a real panel turn Jason flagged): the user
said "…and just get updates from the family." The two-stage router logged
``actual_routed: chat`` (head_conf 0.9185, not gated) — yet the skybridge fast
path answered deterministically with "I found 0 contacts.", because its people
branch claimed ANY utterance containing " family"/" friends"/" person"/…

Two fixes, both pinned here:

1. SHAPE — the people matchers claim only a command/question (imperative or
   interrogative anchored at the start, the noun as the verb's object), never a
   bare keyword inside a statement (``skybridge_service._PEOPLE_DIRECTORY_ASK_RE``).
2. ROUTER VETO — when the two-stage router ran for a voice turn and did not pick
   a compatible domain (above all: said ``chat``), Skybridge declines and the turn
   goes on to the brain; logged ``SKYBRIDGE_GATE … decision=veto``. Flag
   ``ZOE_SKYBRIDGE_ROUTER_VETO`` (default on; false/0/off = old behaviour).

Negative controls (run by hand when this was written, both went red): reverting
the people matcher to the bare-keyword ``any(term in text …)`` fails
``test_live_utterance_is_not_a_people_intent``; deleting the veto block in
``resolve_skybridge_request`` fails the ``*_veto_*`` tests.
"""

from __future__ import annotations

import logging
import sys
import types
from contextlib import asynccontextmanager

import pytest

pytestmark = pytest.mark.ci_safe  # pure logic; router, DB, push and TTS are faked

import intent_router
import routers.voice_tts as voice_tts
import skybridge_service as sky
from routers.voice_tts import voice_command

LIVE_UTTERANCE = "I go down and I spend the weekends with him and just get updates from the family."

# Statements that merely CONTAIN a people word — none may be claimed.
DECLARATIVES = [
    LIVE_UTTERANCE,
    "My family is visiting.",
    "I met a person today.",
    "The contact lens fell out.",
    "Friends of mine came over.",
    "I love my family so much.",
    "She is a lovely person.",
    "We had friends round for dinner.",
    "My friends think I work too hard.",
    "I want to see my family.",
    "I need to go to my family's place.",
    "Honestly people are just tired.",
    "I lost contact with him years ago.",
    "That was a very personal thing to say.",
    "My profile picture is old.",
    # real panel transcripts the old matcher answered with a contacts card
    "But it's the person who's authenticated on the panel.",
    "Hey Zoe. Can you remove all memories of a person named Sarah?",
    "Hey zoe Show me my dashboard",
    # verbs that used to open a "contact search" anywhere in the sentence
    "I went out for a show and I was tired.",
    "Find me the cheapest flights to Melbourne.",
    "Show me the news.",
    # a first-person memory is not an instruction to store a fact
    "I remember that Sarah is funny.",
]

# Commands / questions — every one must still reach the people directory.
COMMANDS = [
    ("show my contacts", ""),
    ("show my contact", ""),
    ("show my people", ""),
    ("show my family", "family"),
    ("open people", ""),
    ("show contacts to add as friends", None),
    ("Hey Zoe, show me my contacts.", ""),
    ("Showing my contacts", ""),  # Moonshine's rendering of "show me my contacts"
    ("Can you tell me about the contacts?", ""),
    ("Tell me about my family.", "family"),
    ("who is in my family", "family"),
    ("who are my friends", "friend"),
    ("what contacts do I have", ""),
    ("how many people are in my contacts", ""),
    ("do I have any contacts", ""),
    ("what's in my contacts", ""),
    ("what's my name in contacts", ""),
    ("show my work contacts", ""),
    ("pull up my contacts", ""),
    ("please open my address book", ""),
    ("find my friends", "friend"),
    ("contacts", ""),
    ("find Sarah", "sarah"),
    ("can you look up the plumber", "the plumber"),
]


def _two_stage(domain: str, *, conf: float = 0.9185, gated: bool = False, head_top: str | None = None) -> dict:
    """A ``semantic_router.route()`` result whose ACTIVE two-stage decided ``domain``."""
    return {
        "domain": domain,
        "routed": domain,
        "score": 0.5,
        "scores": {domain: 0.5},
        "ms": 1.0,
        "two_stage": {
            "tool": None if domain == "chat" else "x",
            "domain": domain,
            "args": {},
            "shortlist": ["people", "memory", "journal"],
            "head_top": head_top or ("people" if domain == "chat" else domain),
            "head_conf": conf,
            "gated": gated,
            "ms": 400.0,
        },
    }


@pytest.fixture(autouse=True)
def _flag_default(monkeypatch):
    monkeypatch.delenv("ZOE_SKYBRIDGE_ROUTER_VETO", raising=False)


# ── 1. shape ────────────────────────────────────────────────────────────────


def test_live_utterance_is_not_a_people_intent():
    intent = sky.classify_skybridge_intent(LIVE_UTTERANCE)
    assert intent is None, f"live 2026-09-28 utterance claimed by skybridge as {intent}"


@pytest.mark.parametrize("utterance", DECLARATIVES)
def test_declaratives_with_people_words_are_not_claimed(utterance):
    intent = sky.classify_skybridge_intent(utterance)
    assert intent is None, f"{utterance!r} claimed as {intent}"


@pytest.mark.parametrize("utterance,query", COMMANDS)
def test_people_commands_and_questions_still_claimed(utterance, query):
    intent = sky.classify_skybridge_intent(utterance)
    assert intent is not None and (intent.domain, intent.action) == ("people", "show"), (utterance, intent)
    if query is not None:
        assert intent.query == query, (utterance, intent.query)


def test_remember_fact_needs_a_request_shape():
    ok = sky.classify_skybridge_intent("Can you remember that Sarah likes flowers")
    assert ok is not None and ok.action == "remember_fact" and ok.person_name == "Sarah"
    assert sky.classify_skybridge_intent("I don't remember that John has a boat") is None


# ── 2. the gate (pure) ──────────────────────────────────────────────────────


def _intent(domain="people", action="show"):
    return sky.SkybridgeIntent(domain=domain, action=action)


@pytest.mark.parametrize(
    "intent,router,want",
    [
        (_intent(), _two_stage("chat"), ("veto", "router_chat")),
        (_intent(), _two_stage("people"), ("allow", "router_agrees")),
        (_intent("weather", "current"), _two_stage("lists"), ("veto", "router_disagrees")),
        (_intent("people", "remember_fact"), _two_stage("memory"), ("allow", "router_agrees")),
        (_intent("people", "show"), _two_stage("memory"), ("veto", "router_disagrees")),
        (_intent("calendar", "show"), _two_stage("reminders"), ("allow", "router_agrees")),
        (_intent("voice", "set"), _two_stage("chat"), ("allow", "no_router_class")),
        # stage 1 under its confidence gate on a real domain = unsure, not "chat"
        (_intent("lists", "overview"), _two_stage("chat", conf=0.31, gated=True, head_top="lists"), ("allow", "router_unsure")),
        # stage 1 confidently chat IS a verdict
        (_intent(), _two_stage("chat", conf=0.8, gated=True, head_top="chat"), ("veto", "router_chat")),
        # router off / similarity-only / two-stage failed → today's behaviour
        (_intent(), None, ("allow", "router_unavailable")),
        (_intent(), {"domain": "chat", "routed": "chat", "score": 0.4}, ("allow", "router_unavailable")),
    ],
)
def test_gate_decisions(intent, router, want):
    decision, reason, _router_domain, _conf = sky.skybridge_router_gate("x", intent, router)
    assert (decision, reason) == want


@pytest.mark.parametrize("value", ["false", "0", "off", "FALSE", " Off "])
def test_gate_kill_switch(monkeypatch, value):
    monkeypatch.setenv("ZOE_SKYBRIDGE_ROUTER_VETO", value)
    decision, reason, *_ = sky.skybridge_router_gate("show my family", _intent(), _two_stage("chat"))
    assert (decision, reason) == ("allow", "flag_off")


def test_gate_allows_a_context_followup_the_router_cannot_see():
    # "add eggs" only means a list add BECAUSE a list card is on screen; the
    # router sees the bare words and says chat — that verdict carries nothing.
    ctx = {"intent": {"domain": "lists", "action": "show", "list_type": "shopping"}, "cards": []}
    intent = sky.classify_skybridge_intent("add eggs", ctx)
    assert intent is not None and intent.domain == "lists"
    decision, reason, *_ = sky.skybridge_router_gate("add eggs", intent, _two_stage("chat"), context=ctx)
    assert (decision, reason) == ("allow", "context_followup")


# ── 3. resolve_skybridge_request ────────────────────────────────────────────


@pytest.fixture
def resolved(monkeypatch):
    calls: list = []

    async def fake_resolve_with_db(intent, user_id, db, *, context=None):
        calls.append((intent.domain, intent.action) if intent.action != "create_list"
                     else (intent.domain, intent.action, intent.list_name))
        return {"handled": True, "intent": {"domain": intent.domain, "action": intent.action},
                "spoken_summary": "I found 0 contacts.", "cards": []}

    @asynccontextmanager
    async def fake_ctx():
        yield object()

    monkeypatch.setattr(sky, "_resolve_with_db", fake_resolve_with_db)
    monkeypatch.setattr(sky, "get_db_ctx", fake_ctx)
    return calls


async def test_resolve_veto_router_chat_declines_before_any_side_effect(resolved, caplog):
    caplog.set_level(logging.INFO, logger="skybridge_service")
    # a GUEST, so a claimed people read would ALSO have raised a PIN challenge
    result = await sky.resolve_skybridge_request("show my family", "guest", router_decision=_two_stage("chat"))
    assert result["handled"] is False and result["vetoed"] is True
    assert "auth_required" not in result
    assert resolved == []
    assert (
        "SKYBRIDGE_GATE router=chat conf=0.9185 skybridge=people action=show decision=veto reason=router_chat"
        in caplog.text
    )


async def test_resolve_veto_even_when_a_matcher_fires_on_the_live_utterance(resolved, monkeypatch, caplog):
    # Belt and braces: had the people matcher still claimed the live utterance
    # (the pre-fix classifier), the router's chat verdict alone must stop it.
    monkeypatch.setattr(sky, "classify_skybridge_intent", lambda _m, _c=None: _intent())
    caplog.set_level(logging.INFO, logger="skybridge_service")
    result = await sky.resolve_skybridge_request(LIVE_UTTERANCE, "jason", router_decision=_two_stage("chat"))
    assert result["handled"] is False and result["vetoed"] is True
    assert resolved == []
    assert "decision=veto" in caplog.text


async def test_resolve_router_agrees_is_allowed(resolved, caplog):
    caplog.set_level(logging.INFO, logger="skybridge_service")
    result = await sky.resolve_skybridge_request("show my family", "jason", router_decision=_two_stage("people"))
    assert result["handled"] is True and resolved == [("people", "show")]
    assert "decision=allow reason=router_agrees" in caplog.text


async def test_resolve_flag_off_restores_old_behaviour(resolved, monkeypatch):
    monkeypatch.setenv("ZOE_SKYBRIDGE_ROUTER_VETO", "false")
    result = await sky.resolve_skybridge_request("show my family", "jason", router_decision=_two_stage("chat"))
    assert result["handled"] is True and resolved == [("people", "show")]


async def test_resolve_router_unavailable_is_allowed_and_logged(resolved, caplog):
    caplog.set_level(logging.INFO, logger="skybridge_service")
    result = await sky.resolve_skybridge_request("show my family", "jason", router_decision=None)
    assert result["handled"] is True
    assert "router=- conf=- skybridge=people action=show decision=allow reason=router_unavailable" in caplog.text


async def test_ungated_callers_are_unchanged(resolved, caplog):
    # touch taps / card buttons / the brain-tool card builder pass no router turn
    caplog.set_level(logging.INFO, logger="skybridge_service")
    result = await sky.resolve_skybridge_request("show my family", "jason")
    assert result["handled"] is True
    assert "SKYBRIDGE_GATE" not in caplog.text


async def test_route_on_demand_runs_the_router_only_for_a_claimed_turn(resolved, monkeypatch):
    seen: list[str] = []

    def fake_route(text):
        seen.append(text)
        return _two_stage("chat")

    monkeypatch.setitem(sys.modules, "semantic_router",
                        types.SimpleNamespace(is_enabled=lambda: True, route=fake_route))
    unclaimed = await sky.resolve_skybridge_request(LIVE_UTTERANCE, "jason", router_decision=sky.ROUTE_ON_DEMAND)
    assert unclaimed["handled"] is False and seen == []
    vetoed = await sky.resolve_skybridge_request("show my family", "jason", router_decision=sky.ROUTE_ON_DEMAND)
    assert vetoed.get("vetoed") is True and seen == ["show my family"]


# ── 3b. the new-list naming prompt: only a NAME answers it (Greptile, #1757) ──

NAMING_CTX = {"intent": {"domain": "lists", "action": "create_list", "list_type": "personal"}, "cards": []}
NOT_A_NAME = "Actually I think we should wait"


def test_naming_prompt_sentence_reply_gets_the_router_verdict():
    intent = sky.classify_skybridge_intent(NOT_A_NAME, NAMING_CTX)
    assert intent is not None and intent.action == "create_list"  # the prompt still captures it…
    decision, reason, *_ = sky.skybridge_router_gate(NOT_A_NAME, intent, _two_stage("chat"), context=NAMING_CTX)
    assert (decision, reason) == ("veto", "router_chat")  # …but the router's chat wins


@pytest.mark.parametrize("reply", ["not now", "never mind", "let's not", "cancel that", "I'll do it later"])
def test_naming_prompt_non_names_are_not_exempt(reply):
    intent = sky.SkybridgeIntent(domain="lists", action="create_list", list_name=reply)
    decision, *_ = sky.skybridge_router_gate(reply, intent, _two_stage("chat"), context=NAMING_CTX)
    assert decision == "veto", reply


async def test_naming_prompt_sentence_reply_creates_no_list(resolved):
    result = await sky.resolve_skybridge_request(
        NOT_A_NAME, "jason", context=NAMING_CTX, router_decision=_two_stage("chat"))
    assert result["handled"] is False and result["vetoed"] is True
    assert resolved == [], "a list was created named after a conversational reply"


@pytest.mark.parametrize("reply,name", [("Groceries", "Groceries"), ("weekend jobs", "weekend jobs"), ("Camping", "Camping")])
async def test_naming_prompt_name_reply_still_creates_the_list(resolved, reply, name):
    # the router cannot see the prompt, so its chat verdict on a bare name is noise
    result = await sky.resolve_skybridge_request(
        reply, "jason", context=NAMING_CTX, router_decision=_two_stage("chat"))
    assert result["handled"] is True
    assert resolved == [("lists", "create_list", name)]


# ── 4. voice_command wiring: the veto hands the turn on toward the brain ────


class _PastSkybridge(BaseException):
    """Raised by the first stage AFTER skybridge; BaseException so the voice
    path's broad ``except Exception`` blocks cannot swallow it."""


class _Audio:
    body = b"RIFF-test"
    media_type = "audio/wav"


def _wire_voice(monkeypatch, router: dict | None):
    async def _none(*_a, **_k):
        return None

    async def _audio(*_a, **_k):
        return _Audio()

    async def _cue(**_k):
        return {"available": False, "text": ""}

    async def _past_skybridge(*_a, **_k):
        raise _PastSkybridge()

    class _Broadcaster:
        async def broadcast(self, *_a, **_k):
            return None

    @asynccontextmanager
    async def _conn():
        yield object()

    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=_Broadcaster()))
    monkeypatch.setitem(sys.modules, "guest_policy",
                        types.SimpleNamespace(record_policy_decision=lambda *_a, **_k: None))
    monkeypatch.setitem(sys.modules, "semantic_router", types.SimpleNamespace(
        is_enabled=lambda: router is not None, route=lambda _t: router))
    monkeypatch.setitem(sys.modules, "pi_hybrid_production", types.SimpleNamespace(
        PiHybridProductionConfig=types.SimpleNamespace(from_env=lambda: types.SimpleNamespace(enabled=False)),
        pi_hybrid_production_eligible=lambda _t, config=None: (False, "disabled"),
        processing_cue_packet=_cue,
        try_pi_hybrid_production=None,
    ))
    monkeypatch.setitem(sys.modules, "db_pool", types.SimpleNamespace(get_db_ctx=_conn))
    # the stage right after skybridge — reaching it means the turn continued
    # toward the intent/expert/brain lanes instead of being answered here
    monkeypatch.setattr(intent_router, "detect_and_extract_intent", _past_skybridge)
    monkeypatch.setattr(voice_tts, "synthesize", _audio)
    monkeypatch.setattr(voice_tts, "_broadcast_skybridge_ui", _none)
    monkeypatch.setattr(voice_tts, "_schedule_voice_chat_save", _none)
    monkeypatch.setattr(voice_tts, "_run_voice_memory_passes", _none)
    monkeypatch.setattr(voice_tts, "_spawn_bg", lambda coro: coro.close())
    monkeypatch.setattr(voice_tts, "_resolve_recent_panel_session_user", _none)
    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", _none)
    monkeypatch.setattr(voice_tts, "_VOICE_SESSIONS", {})
    monkeypatch.setattr(voice_tts, "_PENDING_CONFIRMATIONS", {})


async def _say(text: str):
    return await voice_command(
        {"text": text, "panel_id": "panel-veto", "session_id": "session-veto"},
        caller={"user_id": "guest", "panel_id": "panel-veto"},
        stream=False,
        db=object(),
    )


async def test_voice_router_chat_vetoes_skybridge_and_continues_to_brain_lanes(monkeypatch, resolved, caplog):
    _wire_voice(monkeypatch, _two_stage("chat"))
    caplog.set_level(logging.INFO, logger="skybridge_service")
    with pytest.raises(_PastSkybridge):
        await _say("what's the weather")
    assert resolved == [], "skybridge answered a turn the router said was chat"
    assert "skybridge=weather action=current decision=veto reason=router_chat" in caplog.text


async def test_voice_live_utterance_vetoed_not_challenged_even_if_matcher_fires(monkeypatch, resolved):
    # pre-fix classifier + a guest speaker: without the veto this turn would have
    # raised a PIN challenge or said "I found 0 contacts." — it must go on instead.
    monkeypatch.setattr(sky, "classify_skybridge_intent", lambda _m, _c=None: _intent())
    _wire_voice(monkeypatch, _two_stage("chat"))
    with pytest.raises(_PastSkybridge):
        await _say(LIVE_UTTERANCE)
    assert resolved == []


async def test_voice_router_agrees_skybridge_answers(monkeypatch, resolved):
    _wire_voice(monkeypatch, _two_stage("weather"))
    response = await _say("what's the weather")
    assert response["skybridge"] is True and response["intent"] == "skybridge:weather"
    assert resolved == [("weather", "current")]


async def test_voice_flag_off_skybridge_answers_despite_router_chat(monkeypatch, resolved):
    monkeypatch.setenv("ZOE_SKYBRIDGE_ROUTER_VETO", "off")
    _wire_voice(monkeypatch, _two_stage("chat"))
    response = await _say("what's the weather")
    assert response["skybridge"] is True and resolved == [("weather", "current")]


async def test_voice_router_disabled_keeps_todays_behaviour(monkeypatch, resolved):
    _wire_voice(monkeypatch, None)
    response = await _say("what's the weather")
    assert response["skybridge"] is True and resolved == [("weather", "current")]


async def test_voice_naming_prompt_sentence_reply_goes_to_the_brain(monkeypatch, resolved):
    _wire_voice(monkeypatch, _two_stage("chat"))
    voice_tts._VOICE_SESSIONS["panel-veto"] = {"skybridge_context": NAMING_CTX}
    with pytest.raises(_PastSkybridge):
        await _say(NOT_A_NAME)
    assert resolved == []


async def test_voice_naming_prompt_name_reply_creates_the_list(monkeypatch, resolved):
    _wire_voice(monkeypatch, _two_stage("chat"))
    voice_tts._VOICE_SESSIONS["panel-veto"] = {"skybridge_context": NAMING_CTX}
    monkeypatch.setattr(sky, "_is_guest_user", lambda _u: False)  # lists need a signed-in user
    response = await _say("weekend jobs")
    assert response["skybridge"] is True
    assert resolved == [("lists", "create_list", "weekend jobs")]
