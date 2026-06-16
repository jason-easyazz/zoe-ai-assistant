import os
import sys
import types
import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from auth import require_admin
from pi_intent_lab import compare_pi_intent_lab
from routers.pi_intent_lab import router as pi_intent_lab_router


class _Intent:
    def __init__(self, name, slots=None, confidence=0.9):
        self.name = name
        self.slots = slots or {}
        self.confidence = confidence


def _install_fake_intent_router(
    monkeypatch,
    *,
    raw=None,
    extracted=None,
    execute_response=None,
    execute_calls=None,
    execute_delay_seconds=0.0,
    execute_exception=None,
):
    module = types.ModuleType("intent_router")

    class Intent:
        def __init__(self, name, slots=None, confidence=0.9):
            self.name = name
            self.slots = slots or {}
            self.confidence = confidence

    def detect_intent(text, user_id="family-admin", context=None):
        return raw(text) if callable(raw) else raw

    async def detect_and_extract_intent(text, user_id="family-admin", context=None):
        return extracted(text) if callable(extracted) else extracted

    async def execute_intent(intent, user_id="family-admin"):
        if execute_calls is not None:
            execute_calls.append({"intent": intent, "user_id": user_id})
        if execute_delay_seconds:
            await asyncio.sleep(execute_delay_seconds)
        if execute_exception is not None:
            raise execute_exception
        return execute_response(intent) if callable(execute_response) else execute_response

    module.Intent = Intent
    module.detect_intent = detect_intent
    module.detect_and_extract_intent = detect_and_extract_intent
    module.execute_intent = execute_intent
    monkeypatch.setitem(sys.modules, "intent_router", module)


def _install_fake_pi_classifier(monkeypatch, *, result=None, seen_env=None):
    module = types.ModuleType("pi_intent_classifier")
    module.PI_INTENT_EXECUTE_THRESHOLD = 0.78

    async def classify_with_pi_intent_governor(text, *, context_turns="", env=None, config=None):
        if seen_env is not None:
            seen_env.append({"text": text, "context_turns": context_turns, "env": dict(env or os.environ)})
        return result

    module.classify_with_pi_intent_governor = classify_with_pi_intent_governor
    monkeypatch.setitem(sys.modules, "pi_intent_classifier", module)


def _install_fake_hybrid(monkeypatch):
    module = types.ModuleType("pi_hybrid_buffer")

    def pi_hybrid_buffer_status(env=None, repeat=3, include_shadow_status=False):
        return {
            "contract": {
                "mode": "shadow_buffer",
                "ready": True,
                "foreground_pi_execution_enabled": False,
                "promoted_groups": [],
                "blockers": [],
                "warnings": [],
            }
        }

    module.pi_hybrid_buffer_status = pi_hybrid_buffer_status
    monkeypatch.setitem(sys.modules, "pi_hybrid_buffer", module)



def _install_fake_voice_presence(monkeypatch):
    module = types.ModuleType("voice_presence")

    def processing_ack_event(env=None, index=None):
        return {"type": "voice:processing_ack", "text": "Let me check.", "source": "intent_buffer"}

    module.processing_ack_event = processing_ack_event
    monkeypatch.setitem(sys.modules, "voice_presence", module)

def _install_fake_zoe_agent(monkeypatch, calls):
    module = types.ModuleType("zoe_agent")

    async def run_zoe_agent(message, session_id, user_id="family-admin", **kwargs):
        calls.append({"message": message, "session_id": session_id, "user_id": user_id, "kwargs": kwargs})
        return "fallback answer"

    module.run_zoe_agent = run_zoe_agent
    monkeypatch.setitem(sys.modules, "zoe_agent", module)


@pytest.mark.asyncio
async def test_lab_compares_router_pi_and_never_dispatches(monkeypatch):
    _install_fake_intent_router(monkeypatch, raw=_Intent("weather"), extracted=_Intent("weather", {"place": "home"}))
    seen_env = []
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent="weather",
            slots={"place": "home"},
            confidence=0.91,
            task_lane="fast_tool",
            source="fake_pi",
            latency_ms=123.0,
            reason="weather signal",
        ),
        seen_env=seen_env,
    )
    _install_fake_hybrid(monkeypatch)
    _install_fake_voice_presence(monkeypatch)

    result = await compare_pi_intent_lab(
        "rain later",
        user_id="admin",
        context_turns="previous='hello'",
        allow_pi_execution=True,
        local_model_configured=True,
    )

    assert result["contract"]["admin_only"] is True
    assert result["contract"]["side_effects"] == "none"
    assert result["contract"]["intent_dispatch_enabled"] is False
    assert result["contract"]["intent_dispatch_scope"] == "none"
    assert result["contract"]["safe_read_only_fulfillment_enabled"] is False
    assert "weather" in result["contract"]["safe_read_only_fulfillment_intents"]
    assert result["contract"]["memory_writes_enabled"] is False
    assert result["contract"]["shadow_writes_enabled"] is False
    assert result["contract"]["promotion_enabled"] is False
    assert result["contract"]["pi_runtime"] == "standalone"
    assert result["zoe_router"]["intent"] == "weather"
    assert result["zoe_router"]["baseline_lane"] == "deterministic:router"
    assert result["zoe_router"]["would_execute"] is True
    assert result["pi"]["intent"] == "weather"
    assert result["pi"]["would_execute"] is False
    assert result["pi"]["would_execute_reason"] == "lab_never_dispatches_intents"
    assert result["comparison"]["agreement"] is True
    assert result["comparison"]["production_route_change"] is False
    assert result["simulated_hybrid_flow"]["cue_available"] is True
    assert result["simulated_hybrid_flow"]["cue_event"]["type"] == "voice:processing_ack"
    assert result["simulated_hybrid_flow"]["pi_completion_latency_ms"] is not None
    assert result["safe_fulfillment"]["blocked_reason"] == "not_requested"
    assert result["safe_fulfillment"]["attempted"] is False
    assert result["safe_fulfillment"]["response_chars"] == 0
    assert result["safe_fulfillment"]["response_preview"] == ""
    assert result["simulated_hybrid_flow"]["production_route_change"] is False
    assert result["hybrid_buffer"]["mode"] == "shadow_buffer"
    assert seen_env[0]["env"]["ZOE_PI_INTENT_ENABLED"] == "true"
    assert seen_env[0]["env"]["ZOE_PI_INTENT_SHADOW_ENABLED"] == "false"
    assert seen_env[0]["env"]["ZOE_PI_ALLOW_EXECUTION"] == "true"


@pytest.mark.asyncio
async def test_lab_measures_agent_baseline_only_for_fallback(monkeypatch):
    _install_fake_intent_router(monkeypatch, raw=None, extracted=None)
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent="timer_create",
            slots={"duration": "10 minutes"},
            confidence=0.95,
            task_lane="fast_tool",
            source="fake_pi",
            latency_ms=222.0,
            reason=None,
        ),
    )
    calls = []
    _install_fake_zoe_agent(monkeypatch, calls)

    result = await compare_pi_intent_lab(
        "timer for ten minutes",
        measure_zoe_agent_baseline=True,
        include_hybrid_status=False,
    )

    assert result["zoe_router"]["route_class"] == "fallback"
    assert result["zoe_agent_baseline"]["baseline_kind"] == "zoe_agent_fallback_baseline"
    assert len(calls) == 1
    assert calls[0]["user_id"] == "pi-intent-lab"
    assert result["comparison"]["pi_candidate_for_lane"] is True
    assert result["comparison"]["pi_vs_comparable_latency_delta_ms"] is not None


@pytest.mark.asyncio
async def test_lab_skips_pi_when_requested(monkeypatch):
    _install_fake_intent_router(monkeypatch, raw=None, extracted=None)

    result = await compare_pi_intent_lab("that movie was good", run_pi=False, include_hybrid_status=False)

    assert result["pi"] == {"ran": False, "intent": None, "reason": "run_pi_false", "would_execute": False}
    assert result["comparison"]["pi_candidate_for_lane"] is False
    assert result["comparison"]["pi_vs_comparable_latency_delta_ms"] is None



@pytest.mark.asyncio
async def test_lab_enforces_pi_timeout(monkeypatch):
    _install_fake_intent_router(monkeypatch, raw=None, extracted=None)
    module = types.ModuleType("pi_intent_classifier")
    module.PI_INTENT_EXECUTE_THRESHOLD = 0.78

    async def classify_with_pi_intent_governor(text, *, context_turns="", env=None, config=None):
        import asyncio

        await asyncio.sleep(1)
        return None

    module.classify_with_pi_intent_governor = classify_with_pi_intent_governor
    monkeypatch.setitem(sys.modules, "pi_intent_classifier", module)
    monkeypatch.setenv("ZOE_PI_INTENT_TIMEOUT_SECONDS", "0.01")

    result = await compare_pi_intent_lab("rain later", include_hybrid_status=False)

    assert result["pi"]["timed_out"] is True
    assert result["pi"]["intent"] is None
    assert result["comparison"]["pi_candidate_for_lane"] is False


@pytest.mark.asyncio
async def test_lab_can_fulfill_safe_read_only_pi_result(monkeypatch):
    calls = []
    _install_fake_intent_router(
        monkeypatch,
        raw=None,
        extracted=None,
        execute_response="It's 18.5 C in Perth, light jacket weather.",
        execute_calls=calls,
    )
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent="weather",
            slots={"forecast": False},
            confidence=0.93,
            task_lane="fast_tool",
            source="fake_pi",
            latency_ms=123.0,
            reason="weather signal",
        ),
    )
    _install_fake_voice_presence(monkeypatch)

    result = await compare_pi_intent_lab(
        "need a jacket tonight",
        include_hybrid_status=False,
        include_safe_fulfillment=True,
    )

    assert len(calls) == 1
    assert calls[0]["intent"].name == "weather"
    assert calls[0]["intent"].slots == {"forecast": False}
    assert calls[0]["user_id"] == "pi-intent-lab"
    assert result["contract"]["intent_dispatch_enabled"] is True
    assert result["contract"]["side_effects"] == "read_only_external_only"
    assert result["contract"]["intent_dispatch_scope"] == "read_only_allowlist_only"
    assert result["safe_fulfillment"]["attempted"] is True
    assert result["safe_fulfillment"]["allowed"] is True
    assert result["safe_fulfillment"]["would_execute"] is True
    assert result["safe_fulfillment"]["response_preview"] == "It's 18.5 C in Perth, light jacket weather."
    assert result["simulated_hybrid_flow"]["safe_fulfillment_completion_latency_ms"] is not None
    assert "18.5 C" in result["simulated_hybrid_flow"]["safe_fulfillment_response_preview"]
    assert result["simulated_hybrid_flow"]["production_route_change"] is False


@pytest.mark.asyncio
async def test_lab_counts_non_string_safe_fulfillment_response_chars(monkeypatch):
    _install_fake_intent_router(
        monkeypatch,
        raw=None,
        extracted=None,
        execute_response={"answer": "18.5 C"},
    )
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent="weather",
            slots={},
            confidence=0.93,
            task_lane="fast_tool",
            source="fake_pi",
            latency_ms=123.0,
            reason="weather signal",
        ),
    )

    result = await compare_pi_intent_lab(
        "need a jacket tonight",
        include_hybrid_status=False,
        include_safe_fulfillment=True,
    )

    assert result["safe_fulfillment"]["response_preview"] == "{'answer': '18.5 C'}"
    assert result["safe_fulfillment"]["response_chars"] == len("{'answer': '18.5 C'}")


@pytest.mark.asyncio
async def test_lab_blocks_side_effect_pi_fulfillment(monkeypatch):
    calls = []
    _install_fake_intent_router(monkeypatch, raw=None, extracted=None, execute_response="timer started", execute_calls=calls)
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent="timer_create",
            slots={"minutes": 10},
            confidence=0.95,
            task_lane="fast_tool",
            source="fake_pi",
            latency_ms=90.0,
            reason="timer signal",
        ),
    )

    result = await compare_pi_intent_lab(
        "timer for ten minutes",
        include_hybrid_status=False,
        include_safe_fulfillment=True,
    )

    assert calls == []
    assert result["safe_fulfillment"]["attempted"] is False
    assert result["safe_fulfillment"]["allowed"] is False
    assert result["safe_fulfillment"]["blocked_reason"] == "side_effect_or_unsupported_intent"
    assert result["safe_fulfillment"]["intent"] == "timer_create"
    assert result["safe_fulfillment"]["response_chars"] == 0
    assert result["safe_fulfillment"]["response_preview"] == ""
    assert result["safe_fulfillment"]["would_execute"] is False


@pytest.mark.asyncio
async def test_lab_safe_fulfillment_timeout_is_reported(monkeypatch):
    calls = []
    _install_fake_intent_router(
        monkeypatch,
        raw=None,
        extracted=None,
        execute_response="too late",
        execute_calls=calls,
        execute_delay_seconds=0.05,
    )
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent="weather",
            slots={},
            confidence=0.95,
            task_lane="fast_tool",
            source="fake_pi",
            latency_ms=90.0,
            reason="weather signal",
        ),
    )

    result = await compare_pi_intent_lab(
        "rain later",
        include_hybrid_status=False,
        include_safe_fulfillment=True,
        safe_fulfillment_timeout_seconds=0.01,
    )

    assert len(calls) == 1
    assert result["safe_fulfillment"]["attempted"] is True
    assert result["safe_fulfillment"]["allowed"] is True
    assert result["safe_fulfillment"]["timed_out"] is True
    assert result["safe_fulfillment"]["would_execute"] is False
    assert result["safe_fulfillment"]["response_chars"] == 0
    assert result["safe_fulfillment"]["response_preview"] == ""
    assert result["simulated_hybrid_flow"]["safe_fulfillment_completion_latency_ms"] is None


@pytest.mark.asyncio
async def test_lab_safe_fulfillment_error_is_reported(monkeypatch):
    calls = []
    _install_fake_intent_router(
        monkeypatch,
        raw=None,
        extracted=None,
        execute_calls=calls,
        execute_exception=RuntimeError("weather backend failed"),
    )
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent="weather",
            slots={},
            confidence=0.95,
            task_lane="fast_tool",
            source="fake_pi",
            latency_ms=90.0,
            reason="weather signal",
        ),
    )

    result = await compare_pi_intent_lab(
        "rain later",
        include_hybrid_status=False,
        include_safe_fulfillment=True,
    )

    assert len(calls) == 1
    assert result["safe_fulfillment"]["attempted"] is True
    assert result["safe_fulfillment"]["allowed"] is True
    assert result["safe_fulfillment"]["error"] == "RuntimeError"
    assert result["safe_fulfillment"]["would_execute"] is False
    assert result["safe_fulfillment"]["response_chars"] == 0
    assert result["safe_fulfillment"]["response_preview"] == ""
    assert result["simulated_hybrid_flow"]["safe_fulfillment_completion_latency_ms"] is None

def _admin_app():
    app = FastAPI()
    app.include_router(pi_intent_lab_router)

    async def fake_admin():
        return {"user_id": "admin", "role": "family-admin"}

    app.dependency_overrides[require_admin] = fake_admin
    return app


def test_pi_intent_lab_endpoint_is_admin_scoped(monkeypatch):
    app = FastAPI()
    app.include_router(pi_intent_lab_router)

    async def fake_non_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = fake_non_admin

    resp = TestClient(app).post("/api/pi-intent-lab/compare", json={"text": "rain later"})

    assert resp.status_code == 403


def test_pi_intent_lab_endpoint_returns_comparison(monkeypatch):
    _install_fake_intent_router(monkeypatch, raw=_Intent("weather"), extracted=_Intent("weather"))
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent="weather",
            slots={},
            confidence=0.9,
            task_lane="fast_tool",
            source="fake_pi",
            latency_ms=100.0,
            reason=None,
        ),
    )
    _install_fake_hybrid(monkeypatch)
    _install_fake_voice_presence(monkeypatch)
    app = _admin_app()

    resp = TestClient(app).post(
        "/api/pi-intent-lab/compare",
        json={"text": "rain later", "allow_pi_execution": True, "local_model_configured": True},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["report_kind"] == "zoe_pi_intent_lab_comparison"
    assert data["zoe_router"]["baseline_lane"] == "deterministic:router"
    assert data["pi"]["intent"] == "weather"
    assert data["contract"]["intent_dispatch_enabled"] is False
    assert data["simulated_hybrid_flow"]["cue_available"] is True
