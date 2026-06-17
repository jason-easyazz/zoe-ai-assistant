import asyncio
import json
import sys
import types

import pytest

import pi_hybrid_production
from pi_hybrid_production import PiHybridProductionConfig, pi_hybrid_production_eligible, try_pi_hybrid_production
from zoe_pi_promotion import intent_group_for_intent


def _install_prefilter(monkeypatch, *, allowed=True):
    module = types.ModuleType("pi_intent_classifier")

    def pi_intent_prefilter_allows(_text):
        return allowed

    module.PI_INTENT_EXECUTE_THRESHOLD = 0.78
    module.pi_intent_prefilter_allows = pi_intent_prefilter_allows
    monkeypatch.setitem(sys.modules, "pi_intent_classifier", module)


def _accepted_lab_result(*, intent="weather", response="It is 18.5 C.", router_intent="weather"):
    return {
        "zoe_router": {"intent": router_intent, "route_class": "deterministic", "baseline_kind": "router"},
        "pi": {
            "ran": True,
            "intent": intent,
            "intent_group": intent_group_for_intent(intent),
            "confidence": 0.94,
            "latency_ms": 123.0,
            "timed_out": False,
            "error": None,
        },
        "safe_fulfillment": {
            "attempted": True,
            "allowed": True,
            "intent": intent,
            "timed_out": False,
            "error": None,
            "response_text": response,
            "response_preview": response,
            "response_chars": len(response),
            "validated_by_pi": False,
        },
        "simulated_hybrid_flow": {"cue_available": True, "final_completion_latency_ms": 400.0},
    }


def test_production_eligibility_is_disabled_and_tightly_prefiltered(monkeypatch):
    _install_prefilter(monkeypatch)

    assert pi_hybrid_production_eligible("weather today", config=PiHybridProductionConfig(enabled=False)) == (
        False,
        "disabled",
    )
    assert pi_hybrid_production_eligible("add bread to shopping", config=PiHybridProductionConfig(enabled=True)) == (
        False,
        "production_prefilter_rejected",
    )
    assert pi_hybrid_production_eligible("will it rain later", config=PiHybridProductionConfig(enabled=True)) == (
        True,
        "eligible",
    )
    assert pi_hybrid_production_eligible("what is on my shopping list", config=PiHybridProductionConfig(enabled=True)) == (
        True,
        "eligible",
    )
    assert pi_hybrid_production_eligible(
        "what is the date", config=PiHybridProductionConfig(enabled=True, groups=("clock",))
    ) == (True, "eligible")
    assert pi_hybrid_production_eligible(
        "what is the best time to leave", config=PiHybridProductionConfig(enabled=True, groups=("clock",))
    ) == (False, "production_prefilter_rejected")
    assert pi_hybrid_production_eligible(
        "what is 12 times 8", config=PiHybridProductionConfig(enabled=True, groups=("calculations",))
    ) == (True, "eligible")
    assert pi_hybrid_production_eligible(
        "what is 12 plus", config=PiHybridProductionConfig(enabled=True, groups=("calculations",))
    ) == (False, "production_prefilter_rejected")
    assert pi_hybrid_production_eligible(
        "what is the meeting time plus travel time",
        config=PiHybridProductionConfig(enabled=True, groups=("calculations",)),
    ) == (False, "production_prefilter_rejected")



def test_production_attempt_all_requests_mode_allows_short_non_secret_text(monkeypatch):
    _install_prefilter(monkeypatch, allowed=False)

    config = PiHybridProductionConfig(enabled=True, attempt_all_requests_enabled=True)

    assert pi_hybrid_production_eligible("hi there how are you", config=config) == (True, "eligible")
    assert pi_hybrid_production_eligible("check calendar authorization", config=config) == (True, "eligible")
    assert pi_hybrid_production_eligible(
        "one two three four",
        config=PiHybridProductionConfig(enabled=True, attempt_all_requests_enabled=True, max_words=3),
    ) == (False, "too_many_words")


@pytest.mark.parametrize(
    "text",
    [
        "my api key is abc123",
        "authorization: Bearer abc123",
        "bearer abc123",
        "password is abc123",
        "secret is abc123",
        "token is abc123",
    ],
)
def test_production_attempt_all_requests_mode_rejects_secret_like_text(monkeypatch, text):
    _install_prefilter(monkeypatch, allowed=False)

    config = PiHybridProductionConfig(enabled=True, attempt_all_requests_enabled=True)

    assert pi_hybrid_production_eligible(text, config=config) == (False, "secret_like_text")


def _router_fast_lab_result(*, intent="weather", response="It is 18.5 C."):
    return {
        "zoe_router": {
            "intent": intent,
            "confidence": 0.9,
            "slots": {"forecast": False} if intent == "weather" else {},
            "route_class": "deterministic",
            "baseline_kind": "router",
            "baseline_comparable": True,
            "latency_ms": 1.0,
        },
        "pi": {"ran": False, "intent": None, "reason": "run_pi_false", "would_execute": False},
        "safe_fulfillment": {
            "attempted": True,
            "allowed": True,
            "intent": intent,
            "timed_out": False,
            "error": None,
            "response_text": response,
            "response_preview": response,
            "response_chars": len(response),
            "validated_by_pi": False,
            "validated_by_router": True,
            "latency_ms": 950.0,
            "speculative_safe_fulfillment": "router_used",
        },
        "simulated_hybrid_flow": {"cue_available": True, "final_completion_latency_ms": 950.0},
    }


@pytest.mark.asyncio
async def test_try_pi_hybrid_fast_accepts_deterministic_router_weather(monkeypatch):
    _install_prefilter(monkeypatch)
    calls = []

    async def fake_compare(text, **kwargs):
        calls.append(dict(kwargs))
        if kwargs.get("run_pi") is False:
            return _router_fast_lab_result()
        return _accepted_lab_result()

    monkeypatch.setattr(pi_hybrid_production, "compare_pi_intent_lab", fake_compare)
    monkeypatch.setattr(pi_hybrid_production, "_read_meminfo_mb", lambda: {"MemAvailable": 99999, "SwapFree": 99999})

    decision = await try_pi_hybrid_production(
        "will it rain later",
        user_id="jason",
        config=PiHybridProductionConfig(
            enabled=True,
            resource_guard_enabled=True,
            router_fast_accept_enabled=True,
        ),
    )
    await asyncio.sleep(0.01)

    assert decision["accepted"] is True
    assert decision["reason"] == "router_confirmed_fast_accept"
    assert decision["agreement_kind"] == "zoe_router_fast"
    assert decision["pi_audit_scheduled"] is True
    assert decision["safe_fulfillment_latency_ms"] == 950.0
    assert decision["response_text"] == "It is 18.5 C."
    assert len(calls) == 2
    assert calls[0]["run_pi"] is False
    assert calls[1]["run_pi"] is True


@pytest.mark.asyncio
async def test_try_pi_hybrid_fast_accept_records_audit_disagreement(tmp_path, monkeypatch):
    _install_prefilter(monkeypatch)
    evidence_path = tmp_path / "production-audit-disagreement.jsonl"

    async def fake_compare(text, **kwargs):
        if kwargs.get("run_pi") is False:
            return _router_fast_lab_result()
        return _accepted_lab_result(intent="daily_briefing", response="Here is your day.", router_intent="weather")

    monkeypatch.setattr(pi_hybrid_production, "compare_pi_intent_lab", fake_compare)
    monkeypatch.setattr(pi_hybrid_production, "_read_meminfo_mb", lambda: {"MemAvailable": 99999, "SwapFree": 99999})

    decision = await try_pi_hybrid_production(
        "will it rain later",
        user_id="jason",
        config=PiHybridProductionConfig(
            enabled=True,
            resource_guard_enabled=True,
            router_fast_accept_enabled=True,
        ),
        env={
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED": "true",
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH": str(evidence_path),
        },
    )
    await asyncio.sleep(0.01)

    assert decision["accepted"] is True
    records = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["accepted"] is True
    assert records[1]["accepted"] is False
    assert records[1]["reason"] == "audit_disagreement"
    assert records[1]["intent"] == "weather"
    assert records[1]["pi_intent"] == "daily_briefing"


def test_router_fast_accept_and_attempt_all_require_explicit_env_opt_in():
    assert PiHybridProductionConfig.from_env({}).router_fast_accept_enabled is False
    assert PiHybridProductionConfig.from_env({}).attempt_all_requests_enabled is False

    config = PiHybridProductionConfig.from_env(
        {
            "ZOE_PI_HYBRID_ROUTER_FAST_ACCEPT_ENABLED": "true",
            "ZOE_PI_HYBRID_ATTEMPT_ALL_REQUESTS_ENABLED": "true",
        }
    )

    assert config.router_fast_accept_enabled is True
    assert config.attempt_all_requests_enabled is True


@pytest.mark.asyncio
async def test_try_pi_hybrid_fast_accepts_deterministic_daily_briefing(monkeypatch):
    _install_prefilter(monkeypatch)
    calls = []

    async def fake_compare(text, **kwargs):
        calls.append(dict(kwargs))
        if kwargs.get("run_pi") is False:
            return _router_fast_lab_result(
                intent="daily_briefing",
                response="Here's your day: No events on the calendar today.",
            )
        return _accepted_lab_result(
            intent="daily_briefing",
            response="Here's your day: No events on the calendar today.",
            router_intent="daily_briefing",
        )

    monkeypatch.setattr(pi_hybrid_production, "compare_pi_intent_lab", fake_compare)
    monkeypatch.setattr(pi_hybrid_production, "_read_meminfo_mb", lambda: {"MemAvailable": 99999, "SwapFree": 99999})

    decision = await try_pi_hybrid_production(
        "give me my daily briefing",
        user_id="jason",
        config=PiHybridProductionConfig(
            enabled=True,
            resource_guard_enabled=True,
            router_fast_accept_enabled=True,
        ),
    )
    await asyncio.sleep(0.01)

    assert decision["accepted"] is True
    assert decision["intent"] == "daily_briefing"
    assert decision["reason"] == "router_confirmed_fast_accept"
    assert decision["agreement_kind"] == "zoe_router_fast"
    assert decision["response_text"] == "Here's your day: No events on the calendar today."
    assert len(calls) == 2
    assert calls[0]["run_pi"] is False
    assert calls[1]["run_pi"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", ["hi there", "hi there how are you"])
async def test_try_pi_hybrid_fast_accepts_deterministic_greeting(monkeypatch, utterance):
    _install_prefilter(monkeypatch)
    calls = []

    async def fake_compare(text, **kwargs):
        calls.append(dict(kwargs))
        if kwargs.get("run_pi") is False:
            return _router_fast_lab_result(
                intent="greeting",
                response="Hi, Jason! How can I help?",
            )
        return _accepted_lab_result(
            intent="greeting",
            response="Hi, Jason! How can I help?",
            router_intent="greeting",
        )

    monkeypatch.setattr(pi_hybrid_production, "compare_pi_intent_lab", fake_compare)
    monkeypatch.setattr(pi_hybrid_production, "_read_meminfo_mb", lambda: {"MemAvailable": 99999, "SwapFree": 99999})

    decision = await try_pi_hybrid_production(
        utterance,
        user_id="jason",
        config=PiHybridProductionConfig(
            enabled=True,
            groups=("greetings",),
            resource_guard_enabled=True,
            router_fast_accept_enabled=True,
        ),
    )
    await asyncio.sleep(0.01)

    assert decision["accepted"] is True
    assert decision["intent"] == "greeting"
    assert decision["intent_group"] == "greetings"
    assert decision["reason"] == "router_confirmed_fast_accept"
    assert decision["agreement_kind"] == "zoe_router_fast"
    assert decision["response_text"] == "Hi, Jason! How can I help?"
    assert len(calls) == 2
    assert calls[0]["run_pi"] is False
    assert calls[1]["run_pi"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("utterance", "intent", "group", "response"),
    [
        ("what time is it", "time_query", "clock", "It's 4:05 PM."),
        ("what is the date", "date_query", "clock", "Today is Wednesday, June seventeenth."),
        ("what is 12 times 8", "calculate", "calculations", "12 times 8 = 96"),
    ],
)
async def test_try_pi_hybrid_fast_accepts_deterministic_clock_and_calculation(
    monkeypatch, utterance, intent, group, response
):
    _install_prefilter(monkeypatch)
    calls = []

    async def fake_compare(text, **kwargs):
        calls.append(dict(kwargs))
        if kwargs.get("run_pi") is False:
            return _router_fast_lab_result(intent=intent, response=response)
        return _accepted_lab_result(intent=intent, response=response, router_intent=intent)

    monkeypatch.setattr(pi_hybrid_production, "compare_pi_intent_lab", fake_compare)
    monkeypatch.setattr(pi_hybrid_production, "_read_meminfo_mb", lambda: {"MemAvailable": 99999, "SwapFree": 99999})

    decision = await try_pi_hybrid_production(
        utterance,
        user_id="jason",
        config=PiHybridProductionConfig(
            enabled=True,
            groups=(group,),
            resource_guard_enabled=True,
            router_fast_accept_enabled=True,
        ),
    )
    await asyncio.sleep(0.01)

    assert decision["accepted"] is True
    assert decision["intent"] == intent
    assert decision["intent_group"] == group
    assert decision["reason"] == "router_confirmed_fast_accept"
    assert decision["agreement_kind"] == "zoe_router_fast"
    assert decision["response_text"] == response
    assert len(calls) == 2
    assert calls[0]["run_pi"] is False
    assert calls[1]["run_pi"] is True


@pytest.mark.asyncio
async def test_try_pi_hybrid_fast_accepts_deterministic_list_show(monkeypatch):
    _install_prefilter(monkeypatch)
    calls = []

    async def fake_compare(text, **kwargs):
        calls.append(dict(kwargs))
        if kwargs.get("run_pi") is False:
            return _router_fast_lab_result(
                intent="list_show",
                response="Your shopping list has milk and bread.",
            )
        return _accepted_lab_result(
            intent="list_show",
            response="Your shopping list has milk and bread.",
            router_intent="list_show",
        )

    monkeypatch.setattr(pi_hybrid_production, "compare_pi_intent_lab", fake_compare)
    monkeypatch.setattr(pi_hybrid_production, "_read_meminfo_mb", lambda: {"MemAvailable": 99999, "SwapFree": 99999})

    decision = await try_pi_hybrid_production(
        "what is on my shopping list",
        user_id="jason",
        config=PiHybridProductionConfig(
            enabled=True,
            resource_guard_enabled=True,
            router_fast_accept_enabled=True,
        ),
    )
    await asyncio.sleep(0.01)

    assert decision["accepted"] is True
    assert decision["intent"] == "list_show"
    assert decision["intent_group"] == "lists"
    assert decision["reason"] == "router_confirmed_fast_accept"
    assert decision["agreement_kind"] == "zoe_router_fast"
    assert decision["pi_audit_scheduled"] is True
    assert decision["response_text"] == "Your shopping list has milk and bread."
    assert len(calls) == 2
    assert calls[0]["run_pi"] is False
    assert calls[1]["run_pi"] is True


@pytest.mark.asyncio
async def test_try_pi_hybrid_attempt_all_casual_chat_falls_through_safely(monkeypatch):
    calls = []

    async def fake_compare(text, **kwargs):
        calls.append((text, dict(kwargs)))
        return {
            "zoe_router": {"intent": None, "route_class": "fallback", "baseline_kind": "router_only_not_comparable"},
            "pi": {
                "ran": True,
                "intent": None,
                "intent_group": None,
                "confidence": 0.2,
                "latency_ms": 50.0,
                "timed_out": False,
                "error": None,
            },
            "safe_fulfillment": {"attempted": False, "allowed": False, "blocked_reason": "no_intent"},
            "simulated_hybrid_flow": {"cue_available": True, "final_completion_latency_ms": 50.0},
        }

    monkeypatch.setattr(pi_hybrid_production, "compare_pi_intent_lab", fake_compare)
    monkeypatch.setattr(pi_hybrid_production, "_read_meminfo_mb", lambda: {"MemAvailable": 99999, "SwapFree": 99999})

    decision = await try_pi_hybrid_production(
        "tell me something interesting about oceans",
        user_id="jason",
        config=PiHybridProductionConfig(
            enabled=True,
            attempt_all_requests_enabled=True,
            resource_guard_enabled=True,
        ),
    )

    assert decision["accepted"] is False
    assert decision["reason"] == "pi_no_intent"
    assert decision["intent"] is None
    assert decision["production_route_change"] is False
    assert len(calls) == 1
    assert calls[0][0] == "tell me something interesting about oceans"
    assert calls[0][1]["run_pi"] is True


@pytest.mark.asyncio
async def test_try_pi_hybrid_accepts_safe_agreed_weather(monkeypatch):
    _install_prefilter(monkeypatch)

    async def compare(**_kwargs):
        return _accepted_lab_result()

    async def fake_compare(text, **kwargs):
        assert text == "will it rain later"
        return _accepted_lab_result()

    monkeypatch.setattr(pi_hybrid_production, "compare_pi_intent_lab", fake_compare)
    monkeypatch.setattr(pi_hybrid_production, "_read_meminfo_mb", lambda: {"MemAvailable": 99999, "SwapFree": 99999})

    decision = await try_pi_hybrid_production(
        "will it rain later",
        user_id="jason",
        config=PiHybridProductionConfig(enabled=True, resource_guard_enabled=True),
    )

    assert decision["accepted"] is True
    assert decision["reason"] == "accepted"
    assert decision["intent"] == "weather"
    assert decision["agreement_kind"] == "zoe_router"
    assert decision["response_text"] == "It is 18.5 C."
    assert decision["production_route_change"] is True


@pytest.mark.asyncio
async def test_try_pi_hybrid_records_production_evidence_when_enabled(tmp_path, monkeypatch):
    _install_prefilter(monkeypatch)
    evidence_path = tmp_path / "production.jsonl"

    async def fake_compare(text, **kwargs):
        return _accepted_lab_result()

    monkeypatch.setattr(pi_hybrid_production, "compare_pi_intent_lab", fake_compare)
    monkeypatch.setattr(pi_hybrid_production, "_read_meminfo_mb", lambda: {"MemAvailable": 99999, "SwapFree": 99999})

    decision = await try_pi_hybrid_production(
        "will it rain later",
        user_id="jason",
        config=PiHybridProductionConfig(enabled=True, resource_guard_enabled=True),
        env={
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED": "true",
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH": str(evidence_path),
        },
    )

    assert decision["accepted"] is True
    saved = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert saved["source"] == "pi_hybrid_production"
    assert saved["accepted"] is True
    assert saved["intent"] == "weather"
    assert saved["pi_intent"] == "weather"
    assert saved["production_route_change"] is True


@pytest.mark.asyncio
async def test_try_pi_hybrid_records_rejected_production_evidence_when_enabled(tmp_path, monkeypatch):
    _install_prefilter(monkeypatch)
    evidence_path = tmp_path / "production-rejected.jsonl"

    decision = await try_pi_hybrid_production(
        "will it rain later",
        user_id="jason",
        config=PiHybridProductionConfig(enabled=False),
        env={
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED": "true",
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH": str(evidence_path),
        },
    )

    assert decision["accepted"] is False
    assert decision["reason"] == "disabled"
    saved = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert saved["source"] == "pi_hybrid_production"
    assert saved["accepted"] is False
    assert saved["reason"] == "disabled"
    assert saved["production_route_change"] is False


@pytest.mark.asyncio
async def test_try_pi_hybrid_rejects_side_effect_intent(monkeypatch):
    _install_prefilter(monkeypatch)

    async def fake_compare(text, **kwargs):
        result = _accepted_lab_result(intent="list_add", response="Added bread.", router_intent="list_add")
        result["pi"]["intent_group"] = "lists"
        return result

    monkeypatch.setattr(pi_hybrid_production, "compare_pi_intent_lab", fake_compare)
    monkeypatch.setattr(pi_hybrid_production, "_read_meminfo_mb", lambda: {"MemAvailable": 99999, "SwapFree": 99999})

    decision = await try_pi_hybrid_production(
        "what is on my shopping list",
        user_id="jason",
        config=PiHybridProductionConfig(enabled=True, groups=("lists",)),
    )

    assert decision["accepted"] is False
    assert decision["reason"] == "intent_not_safe_for_production"
    assert decision["production_route_change"] is False


def test_processing_cue_index_uses_softer_variant_for_social_identity_text():
    assert pi_hybrid_production.processing_cue_index_for_text("hi there how are you") == 1
    assert pi_hybrid_production.processing_cue_index_for_text("what can you do") == 1
    assert pi_hybrid_production.processing_cue_index_for_text("who are you") == 1
    assert pi_hybrid_production.processing_cue_index_for_text("who are you?") == 1


def test_processing_cue_index_keeps_checking_variant_for_lookup_text():
    assert pi_hybrid_production.processing_cue_index_for_text("will it rain later") == 0
    assert pi_hybrid_production.processing_cue_index_for_text("what is 12 times 8") == 0


@pytest.mark.asyncio
async def test_processing_cue_packet_uses_selected_variant_text():
    env = {"ZOE_PROCESSING_ACK_PHRASES": "Let me check.|One moment."}

    social = await pi_hybrid_production.processing_cue_packet(env, text="who are you")
    lookup = await pi_hybrid_production.processing_cue_packet(env, text="will it rain later")

    assert social["text"] == "One moment."
    assert lookup["text"] == "Let me check."
