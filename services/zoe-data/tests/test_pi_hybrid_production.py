import asyncio
import json
import sys
import types

import pytest

import pi_hybrid_production
from pi_hybrid_production import PiHybridProductionConfig, pi_hybrid_production_eligible, try_pi_hybrid_production


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
            "intent_group": "weather" if intent == "weather" else "daily_briefing",
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
        config=PiHybridProductionConfig(enabled=True, resource_guard_enabled=True),
    )
    await asyncio.sleep(0)

    assert decision["accepted"] is True
    assert decision["reason"] == "router_confirmed_fast_accept"
    assert decision["agreement_kind"] == "zoe_router_fast"
    assert decision["pi_audit_scheduled"] is True
    assert decision["safe_fulfillment_latency_ms"] == 950.0
    assert decision["response_text"] == "It is 18.5 C."
    assert calls[0]["run_pi"] is False
    assert calls[1]["run_pi"] is True


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
