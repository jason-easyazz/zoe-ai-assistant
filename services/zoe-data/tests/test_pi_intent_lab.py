import json
import os
import sys
import types
import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import auth
from routers.pi_intent_lab import require_lab_operator
from pi_intent_lab import _await_speculative_safe_fulfillment, compare_pi_intent_lab
from routers.pi_intent_lab import router as pi_intent_lab_router

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _neutralize_pi_lab_resource_guard(monkeypatch):
    """These tests assert routing/endpoint behavior, not host memory.

    The lab resource-pressure guard reads live /proc/meminfo, so under a loaded
    full-suite run available memory can dip below the default 2048MB floor and the
    endpoint 503s — making these tests flaky by machine state. Default the guard
    off here; the guard-specific tests below re-enable it via their own setenv.
    """
    monkeypatch.setenv("ZOE_PI_LAB_MIN_AVAILABLE_MB", "0")
    monkeypatch.setenv("ZOE_PI_LAB_MIN_SWAP_FREE_MB", "0")


class _Intent:
    def __init__(self, name, slots=None, confidence=0.9):
        self.name = name
        self.slots = slots or {}
        self.confidence = confidence




def _disable_pi_lab_resource_guard(monkeypatch):
    monkeypatch.setenv("ZOE_PI_LAB_RESOURCE_GUARD_ENABLED", "0")

def _install_fake_intent_router(
    monkeypatch,
    *,
    raw=None,
    extracted=None,
    execute_response=None,
    execute_calls=None,
    execute_delay_seconds=0.0,
    execute_exception=None,
    execute_started_event=None,
    execute_cancelled_event=None,
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
        if execute_started_event is not None:
            execute_started_event.set()
        try:
            if execute_delay_seconds:
                await asyncio.sleep(execute_delay_seconds)
        except asyncio.CancelledError:
            if execute_cancelled_event is not None:
                execute_cancelled_event.set()
            raise
        if execute_exception is not None:
            raise execute_exception
        return execute_response(intent) if callable(execute_response) else execute_response

    module.Intent = Intent
    module.detect_intent = detect_intent
    module.detect_and_extract_intent = detect_and_extract_intent
    module.execute_intent = execute_intent
    monkeypatch.setitem(sys.modules, "intent_router", module)


def _install_fake_pi_classifier(monkeypatch, *, result=None, seen_env=None, delay_seconds=0.0, wait_event=None):
    module = types.ModuleType("pi_intent_classifier")
    module.PI_INTENT_EXECUTE_THRESHOLD = 0.78

    async def classify_with_pi_intent_governor(text, *, context_turns="", env=None, config=None):
        if seen_env is not None:
            seen_env.append({"text": text, "context_turns": context_turns, "env": dict(env or os.environ)})
        if wait_event is not None:
            # Condition-based ordering (mirrors execute_started_event): hold the
            # verdict until the given event fires. Lets a test guarantee "the
            # speculative execute HAS started before pi returns" without racing a
            # wall-clock delay — a 50ms sleep loses that race on a starved loop,
            # where cancellation can land before the coroutine's first slice.
            await wait_event.wait()
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
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
        "conditions outside",
        include_hybrid_status=False,
        include_safe_fulfillment=True,
    )

    assert len(calls) == 1
    assert calls[0]["intent"].name == "weather"
    assert calls[0]["intent"].slots == {"forecast": False}
    assert calls[0]["user_id"] == "pi-intent-lab"
    assert result["contract"]["intent_dispatch_enabled"] is True
    assert result["contract"]["side_effects"] == "read_only_external_or_action_form_prefill"
    assert result["contract"]["intent_dispatch_scope"] == "read_only_or_action_form_prefill"
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
async def test_lab_prefills_timer_action_form_without_dispatching(monkeypatch):
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
    assert result["safe_fulfillment"]["attempted"] is True
    assert result["safe_fulfillment"]["allowed"] is True
    assert result["safe_fulfillment"]["intent"] == "timer_create"
    assert result["safe_fulfillment"]["execution_scope"] == "action_form_prefill"
    assert result["safe_fulfillment"]["action_form"] == {
        "component": "timer_create_form",
        "prefill": {"minutes": 10},
    }
    assert result["safe_fulfillment"]["response_preview"] == "Timer is ready to confirm."
    assert result["safe_fulfillment"]["would_execute"] is False


@pytest.mark.asyncio
async def test_lab_blocks_non_allowlisted_side_effect_pi_fulfillment(monkeypatch):
    calls = []
    _install_fake_intent_router(monkeypatch, raw=None, extracted=None, execute_response="reminder set", execute_calls=calls)
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent="reminder_create",
            slots={"title": "take bins out"},
            confidence=0.95,
            task_lane="fast_tool",
            source="fake_pi",
            latency_ms=90.0,
            reason="reminder signal",
        ),
    )

    result = await compare_pi_intent_lab(
        "remind me to take the bins out",
        include_hybrid_status=False,
        include_safe_fulfillment=True,
    )

    assert calls == []
    assert result["safe_fulfillment"]["attempted"] is False
    assert result["safe_fulfillment"]["allowed"] is False
    assert result["safe_fulfillment"]["blocked_reason"] == "side_effect_or_unsupported_intent"
    assert result["safe_fulfillment"]["intent"] == "reminder_create"
    assert result["safe_fulfillment"]["would_execute"] is False


@pytest.mark.asyncio
async def test_lab_safe_fulfillment_timeout_is_reported(monkeypatch):
    calls = []
    # Timing margins are deliberately WIDE on both sides of the inequality
    # (10s delay vs 0.25s timeout, was 0.05 vs 0.01). The old 10ms budget could
    # expire before the execute coroutine's FIRST scheduling slice on a starved
    # loop — `calls` stayed empty and `len(calls) == 1` failed with 0 == 1
    # (seen on a slow reviewer VM). 0.25s is enough for any loop to start the
    # task; the 10s delay never actually elapses (wait_for cancels it), so the
    # test still finishes in ~0.25s and the timeout is still genuinely proven.
    _install_fake_intent_router(
        monkeypatch,
        raw=None,
        extracted=None,
        execute_response="too late",
        execute_calls=calls,
        execute_delay_seconds=10.0,
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
        safe_fulfillment_timeout_seconds=0.25,
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


@pytest.mark.asyncio
async def test_lab_can_fulfill_deterministic_router_intent_without_pi(monkeypatch):
    calls = []
    _install_fake_intent_router(
        monkeypatch,
        raw=_Intent("weather"),
        extracted=_Intent("weather", {"forecast": False}),
        execute_response="It's 18.5 C in Perth, light jacket weather.",
        execute_calls=calls,
    )
    _install_fake_pi_classifier(monkeypatch, result=None)
    _install_fake_voice_presence(monkeypatch)

    result = await compare_pi_intent_lab(
        "will it rain later",
        run_pi=False,
        include_hybrid_status=False,
        include_safe_fulfillment=True,
        allow_router_safe_fulfillment=True,
    )

    assert result["pi"]["ran"] is False
    assert result["zoe_router"]["route_class"] == "deterministic"
    assert len(calls) == 1
    assert calls[0]["intent"].name == "weather"
    assert result["safe_fulfillment"]["validated_by_router"] is True
    assert result["safe_fulfillment"]["validated_by_pi"] is False
    assert result["safe_fulfillment"]["speculative_safe_fulfillment"] == "router_used"
    assert result["safe_fulfillment"]["response_preview"] == "It's 18.5 C in Perth, light jacket weather."


@pytest.mark.asyncio
async def test_lab_reuses_speculative_safe_fulfillment_when_pi_agrees(monkeypatch):
    calls = []
    _install_fake_intent_router(
        monkeypatch,
        raw=_Intent("weather"),
        extracted=_Intent("weather", {"forecast": False}),
        execute_response="It's 18.5 C in Perth, light jacket weather.",
        execute_calls=calls,
        execute_delay_seconds=0.01,
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
    assert result["safe_fulfillment"]["started_before_pi"] is True
    assert result["safe_fulfillment"]["validated_by_pi"] is True
    assert result["safe_fulfillment"]["speculative_safe_fulfillment"] == "used"
    assert result["safe_fulfillment"]["response_preview"] == "It's 18.5 C in Perth, light jacket weather."
    pi_latency = result["simulated_hybrid_flow"]["pi_completion_latency_ms"]
    fulfillment_latency = result["simulated_hybrid_flow"]["safe_fulfillment_latency_ms"]
    assert result["simulated_hybrid_flow"]["safe_fulfillment_completion_latency_ms"] == max(
        pi_latency,
        fulfillment_latency,
    )


@pytest.mark.asyncio
async def test_lab_reuses_fallback_hint_speculative_weather_when_pi_agrees(monkeypatch):
    calls = []
    _install_fake_intent_router(
        monkeypatch,
        raw=None,
        extracted=None,
        execute_response="It's 18.5 C in Perth, light jacket weather.",
        execute_calls=calls,
        execute_delay_seconds=0.01,
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
        delay_seconds=0.03,
    )
    _install_fake_voice_presence(monkeypatch)

    result = await compare_pi_intent_lab(
        "need a jacket tonight",
        include_hybrid_status=False,
        include_safe_fulfillment=True,
    )

    assert result["zoe_router"]["route_class"] == "fallback"
    assert len(calls) == 1
    assert calls[0]["intent"].name == "weather"
    assert calls[0]["intent"].slots == {}
    assert result["safe_fulfillment"]["started_before_pi"] is True
    assert result["safe_fulfillment"]["validated_by_pi"] is True
    assert result["safe_fulfillment"]["speculative_safe_fulfillment"] == "used"
    assert result["safe_fulfillment"]["response_preview"] == "It's 18.5 C in Perth, light jacket weather."


@pytest.mark.asyncio
async def test_lab_reuses_fallback_hint_speculative_daily_briefing_when_pi_agrees(monkeypatch):
    calls = []
    _install_fake_intent_router(
        monkeypatch,
        raw=None,
        extracted=None,
        execute_response="Here's your day: No events on the calendar today.",
        execute_calls=calls,
        execute_delay_seconds=0.01,
    )
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent="daily_briefing",
            slots={},
            confidence=0.93,
            task_lane="fast_tool",
            source="fake_pi",
            latency_ms=123.0,
            reason="daily briefing signal",
        ),
        delay_seconds=0.03,
    )
    _install_fake_voice_presence(monkeypatch)

    result = await compare_pi_intent_lab(
        "what is my day looking like",
        include_hybrid_status=False,
        include_safe_fulfillment=True,
    )

    assert result["zoe_router"]["route_class"] == "fallback"
    assert len(calls) == 1
    assert calls[0]["intent"].name == "daily_briefing"
    assert calls[0]["intent"].slots == {}
    assert result["safe_fulfillment"]["started_before_pi"] is True
    assert result["safe_fulfillment"]["validated_by_pi"] is True
    assert result["safe_fulfillment"]["speculative_safe_fulfillment"] == "used"
    assert result["safe_fulfillment"]["response_preview"] == "Here's your day: No events on the calendar today."


@pytest.mark.asyncio
async def test_lab_does_not_speculate_casual_fallback_text(monkeypatch):
    calls = []
    _install_fake_intent_router(
        monkeypatch,
        raw=None,
        extracted=None,
        execute_response="should not run",
        execute_calls=calls,
    )
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent=None,
            slots={},
            confidence=0.0,
            task_lane="chat",
            source="fake_pi",
            latency_ms=10.0,
            reason="casual chat",
        ),
    )

    result = await compare_pi_intent_lab(
        "I like the breakfast service",
        include_hybrid_status=False,
        include_safe_fulfillment=True,
    )

    assert result["zoe_router"]["route_class"] == "fallback"
    assert calls == []
    assert result["safe_fulfillment"]["blocked_reason"] == "pi_no_intent"
    assert "speculative_safe_fulfillment" not in result["safe_fulfillment"]


@pytest.mark.parametrize(
    "text",
    [
        "that cold case documentary was great",
        "can you open the temp file",
        "I had a hot dog for lunch",
        "jacket potato sounds good",
        "that umbrella clause is confusing",
    ],
)
@pytest.mark.asyncio
async def test_lab_does_not_speculate_ambiguous_weather_words(monkeypatch, text):
    calls = []
    _install_fake_intent_router(
        monkeypatch,
        raw=None,
        extracted=None,
        execute_response="should not run",
        execute_calls=calls,
    )
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent=None,
            slots={},
            confidence=0.0,
            task_lane="chat",
            source="fake_pi",
            latency_ms=10.0,
            reason="casual chat",
        ),
    )

    result = await compare_pi_intent_lab(
        text,
        include_hybrid_status=False,
        include_safe_fulfillment=True,
    )

    assert result["zoe_router"]["route_class"] == "fallback"
    assert calls == []
    assert result["safe_fulfillment"]["blocked_reason"] == "pi_no_intent"
    assert "speculative_safe_fulfillment" not in result["safe_fulfillment"]


@pytest.mark.asyncio
async def test_lab_discards_speculative_safe_fulfillment_when_pi_slots_differ(monkeypatch):
    calls = []
    # Event-ordered, not scheduler-luck (the #1413 fix, extended to this test).
    # The discard path calls `task.cancel()` on the speculative task; if that
    # lands before the task's FIRST scheduling slice, execute_intent's body
    # never runs, nothing is appended to `calls`, and the two-call assertion
    # below fails with [{'forecast': True}] != [{'forecast': False}, ...].
    # Holding the pi verdict until execute_started fires makes the speculative
    # call a fact before the discard can race it. Safe against deadlock: the
    # speculative task is created (pi_intent_lab.py:106) BEFORE pi is awaited
    # (:114), so the event is always reachable. Passed on this box and in CI
    # while failing on a slower reviewer VM — the classic starved-loop tell.
    execute_started = asyncio.Event()

    def response_for(intent):
        return f"forecast={intent.slots.get('forecast')}"

    _install_fake_intent_router(
        monkeypatch,
        raw=_Intent("weather"),
        extracted=_Intent("weather", {"forecast": False}),
        execute_response=response_for,
        execute_calls=calls,
        execute_started_event=execute_started,
    )
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent="weather",
            slots={"forecast": True},
            confidence=0.93,
            task_lane="fast_tool",
            source="fake_pi",
            latency_ms=123.0,
            reason="weather signal",
        ),
        wait_event=execute_started,
    )

    result = await compare_pi_intent_lab(
        "weather tomorrow",
        include_hybrid_status=False,
        include_safe_fulfillment=True,
    )

    # Self-describing on failure: this test has been reported failing on a
    # slower VM while green here and in CI, and a bare list-compare hides WHICH
    # half went missing (speculative never ran vs pi-validated never ran).
    assert [call["intent"].slots for call in calls] == [
        {"forecast": False},
        {"forecast": True},
    ], (
        "expected the speculative execute (forecast=False) THEN the pi-validated "
        f"one (forecast=True); actually recorded {[c['intent'].slots for c in calls]!r}"
    )
    assert result["safe_fulfillment"]["started_before_pi"] is False
    assert result["safe_fulfillment"]["validated_by_pi"] is True
    assert result["safe_fulfillment"]["speculative_safe_fulfillment"] == "discarded"
    assert result["safe_fulfillment"]["speculative_intent"] == "weather"
    assert result["safe_fulfillment"]["speculative_discard_reason"] == "speculative_slots_mismatch"
    assert result["safe_fulfillment"]["response_preview"] == "forecast=True"


@pytest.mark.asyncio
async def test_lab_discards_speculative_safe_fulfillment_below_pi_threshold(monkeypatch):
    calls = []
    # Event-ordered, not wall-clock: the pi verdict is HELD until the speculative
    # execute has actually started (wait_event=execute_started), so the discard
    # can never cancel the task before its first scheduling slice. The old 50ms
    # delay raced exactly that on a starved loop — cancellation landed before the
    # coroutine body ran, `calls` stayed empty, and `len(calls) == 1` failed with
    # 0 == 1 (seen on a slow reviewer VM). The 10s delay never elapses: the
    # below-threshold discard cancels the task, which is the behaviour under test.
    execute_started = asyncio.Event()
    _install_fake_intent_router(
        monkeypatch,
        raw=_Intent("weather"),
        extracted=_Intent("weather", {"forecast": False}),
        execute_response="should not be exposed",
        execute_calls=calls,
        execute_delay_seconds=10.0,
        execute_started_event=execute_started,
    )
    _install_fake_pi_classifier(
        monkeypatch,
        result=types.SimpleNamespace(
            intent="weather",
            slots={"forecast": False},
            confidence=0.2,
            task_lane="fast_tool",
            source="fake_pi",
            latency_ms=123.0,
            reason="weak weather signal",
        ),
        wait_event=execute_started,
    )

    result = await compare_pi_intent_lab(
        "weather maybe",
        include_hybrid_status=False,
        include_safe_fulfillment=True,
    )

    assert len(calls) == 1
    assert result["safe_fulfillment"]["attempted"] is False
    assert result["safe_fulfillment"]["blocked_reason"] == "below_execute_threshold"
    assert result["safe_fulfillment"]["speculative_safe_fulfillment"] == "discarded"
    assert result["safe_fulfillment"]["speculative_intent"] == "weather"
    assert result["safe_fulfillment"]["response_preview"] == ""


@pytest.mark.asyncio
async def test_lab_cancels_speculative_safe_fulfillment_when_comparison_is_cancelled(monkeypatch):
    execute_started = asyncio.Event()
    execute_cancelled = asyncio.Event()
    calls = []
    _install_fake_intent_router(
        monkeypatch,
        raw=_Intent("weather"),
        extracted=_Intent("weather", {"forecast": False}),
        execute_response="too late",
        execute_calls=calls,
        execute_delay_seconds=10.0,
        execute_started_event=execute_started,
        execute_cancelled_event=execute_cancelled,
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
        delay_seconds=10.0,
    )

    task = asyncio.create_task(
        compare_pi_intent_lab(
            "need a jacket tonight",
            include_hybrid_status=False,
            include_safe_fulfillment=True,
        )
    )
    await asyncio.wait_for(execute_started.wait(), timeout=1.0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.wait_for(execute_cancelled.wait(), timeout=1.0)
    pending_fulfillment_tasks = [
        candidate
        for candidate in asyncio.all_tasks()
        if candidate is not asyncio.current_task()
        and getattr(candidate.get_coro(), "__name__", "") == "_execute_safe_fulfillment_intent"
        and not candidate.done()
    ]
    assert pending_fulfillment_tasks == []
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_lab_await_speculative_timeout_cancels_shielded_task():
    cancelled = asyncio.Event()

    async def slow_fulfillment():
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(slow_fulfillment())
    result = await _await_speculative_safe_fulfillment(
        {"intent": "weather", "task": task},
        timeout_seconds=0.01,
    )

    assert result["timed_out"] is True
    assert result["validated_by_pi"] is False
    assert result["speculative_safe_fulfillment"] == "timed_out"
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    assert task.cancelled()

def _admin_app():
    app = FastAPI()
    app.include_router(pi_intent_lab_router)

    async def fake_admin():
        return {"user_id": "admin", "role": "family-admin"}

    app.dependency_overrides[require_lab_operator] = fake_admin
    return app


def test_pi_intent_lab_endpoint_is_admin_scoped(monkeypatch):
    app = FastAPI()
    app.include_router(pi_intent_lab_router)

    async def fake_non_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_lab_operator] = fake_non_admin

    resp = TestClient(app).post("/api/pi-intent-lab/compare", json={"text": "rain later"})

    assert resp.status_code == 403


def test_pi_intent_lab_endpoint_allows_internal_token(monkeypatch):
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
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "pi-lab-token")
    app = FastAPI()
    app.include_router(pi_intent_lab_router)

    resp = TestClient(app).post(
        "/api/pi-intent-lab/compare",
        json={"text": "rain later", "run_pi": True},
        headers={"X-Internal-Token": "pi-lab-token"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["input"]["user_id"] == "internal-pi-intent-lab"
    assert data["pi"]["intent"] == "weather"


def test_pi_intent_lab_endpoint_rejects_wrong_internal_token(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "pi-lab-token")
    app = FastAPI()
    app.include_router(pi_intent_lab_router)

    resp = TestClient(app).post(
        "/api/pi-intent-lab/compare",
        json={"text": "rain later", "run_pi": False},
        headers={"X-Internal-Token": "wrong-token"},
    )

    assert resp.status_code == 403
    assert "X-Internal-Token" in resp.json()["detail"]


def test_pi_intent_lab_endpoint_falls_back_to_admin_session_when_internal_token_unconfigured(monkeypatch):
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
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "")
    calls = []

    async def fake_current_user(request):
        calls.append("get_current_user")
        return {"user_id": "session-admin", "role": "family-admin"}

    async def fake_require_admin(user):
        calls.append(("require_admin", user["user_id"]))
        return user

    monkeypatch.setattr(auth, "get_current_user", fake_current_user)
    monkeypatch.setattr(auth, "require_admin", fake_require_admin)
    app = FastAPI()
    app.include_router(pi_intent_lab_router)

    resp = TestClient(app).post(
        "/api/pi-intent-lab/compare",
        json={"text": "rain later", "run_pi": True},
        headers={"X-Internal-Token": "proxy-added-but-feature-disabled"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["input"]["user_id"] == "session-admin"
    assert calls == ["get_current_user", ("require_admin", "session-admin")]


def test_pi_intent_lab_endpoint_returns_comparison(monkeypatch):
    _disable_pi_lab_resource_guard(monkeypatch)
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




@pytest.mark.asyncio
async def test_pi_lab_resource_pressure_guard_thresholds(monkeypatch):
    import routers.pi_intent_lab as route_module

    payload = route_module.PiIntentLabCompareRequest(text="rain later", run_pi=True)
    monkeypatch.setenv("ZOE_PI_LAB_MIN_AVAILABLE_MB", "2048")
    monkeypatch.setenv("ZOE_PI_LAB_MIN_SWAP_FREE_MB", "256")
    monkeypatch.setattr(
        route_module,
        "_read_meminfo_mb",
        lambda: {"MemAvailable": 1024, "SwapFree": 128},
    )

    pressure = await route_module._pi_lab_resource_pressure_blocker(payload)

    assert pressure is not None
    assert pressure["error_type"] == "resource_pressure"
    assert pressure["blockers"] == ["available_memory_below_threshold", "swap_free_below_threshold"]
    assert pressure["available_mb"] == 1024
    assert pressure["swap_free_mb"] == 128


@pytest.mark.asyncio
async def test_pi_lab_resource_pressure_guard_passes_when_healthy(monkeypatch):
    import routers.pi_intent_lab as route_module

    payload = route_module.PiIntentLabCompareRequest(text="rain later", run_pi=True)
    monkeypatch.setenv("ZOE_PI_LAB_MIN_AVAILABLE_MB", "2048")
    monkeypatch.setenv("ZOE_PI_LAB_MIN_SWAP_FREE_MB", "256")
    monkeypatch.setattr(
        route_module,
        "_read_meminfo_mb",
        lambda: {"MemAvailable": 4096, "SwapFree": 1024},
    )

    assert await route_module._pi_lab_resource_pressure_blocker(payload) is None


@pytest.mark.asyncio
async def test_pi_lab_resource_pressure_guard_can_be_disabled(monkeypatch):
    import routers.pi_intent_lab as route_module

    payload = route_module.PiIntentLabCompareRequest(text="rain later", run_pi=True)
    monkeypatch.setenv("ZOE_PI_LAB_RESOURCE_GUARD_ENABLED", "0")
    # Set real (non-zero) thresholds so the `min_*_mb <= 0` short-circuit does NOT
    # fire (the autouse fixture zeros them by default). With low meminfo + these
    # thresholds, the guard would block IF enabled — so a None result here proves
    # the GUARD_ENABLED=0 flag specifically, not the zeroed-threshold short-circuit.
    monkeypatch.setenv("ZOE_PI_LAB_MIN_AVAILABLE_MB", "2048")
    monkeypatch.setenv("ZOE_PI_LAB_MIN_SWAP_FREE_MB", "256")
    monkeypatch.setattr(route_module, "_read_meminfo_mb", lambda: {"MemAvailable": 1, "SwapFree": 1})

    assert await route_module._pi_lab_resource_pressure_blocker(payload) is None


def test_pi_lab_resource_pressure_meminfo_fallback(tmp_path):
    import routers.pi_intent_lab as route_module

    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemFree:        1048576 kB\nBuffers:         262144 kB\nCached:          524288 kB\nSwapFree:        131072 kB\n",
        encoding="utf-8",
    )

    values = route_module._read_meminfo_mb(str(meminfo))

    assert values["MemAvailable"] == 1792
    assert values["SwapFree"] == 128

def test_pi_intent_lab_compare_blocks_under_resource_pressure(monkeypatch):
    import routers.pi_intent_lab as route_module

    async def fake_pressure(payload):
        return {
            "error_type": "resource_pressure",
            "detail": "Pi intent lab blocked to avoid zoe-data OOM restart",
            "available_mb": 512,
            "min_available_mb": 2048,
            "production_route_change": False,
        }

    monkeypatch.setattr(route_module, "_pi_lab_resource_pressure_blocker", fake_pressure)
    app = _admin_app()

    resp = TestClient(app).post(
        "/api/pi-intent-lab/compare",
        json={"text": "rain later", "run_pi": True},
    )

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["error_type"] == "resource_pressure"
    assert detail["production_route_change"] is False
    assert detail["available_mb"] == 512


def test_pi_intent_lab_hybrid_stream_emits_resource_pressure_after_cue(monkeypatch):
    import routers.pi_intent_lab as route_module

    async def fake_pressure(payload):
        return {
            "error_type": "resource_pressure",
            "detail": "Pi intent lab blocked to avoid zoe-data OOM restart",
            "available_mb": 512,
            "min_available_mb": 2048,
            "production_route_change": False,
        }

    monkeypatch.setattr(route_module, "_pi_lab_resource_pressure_blocker", fake_pressure)
    monkeypatch.setattr(
        route_module,
        "_processing_cue",
        lambda: {"available": True, "latency_ms": 0.05, "event": None, "text": "Let me check."},
    )
    app = _admin_app()

    with TestClient(app).stream(
        "POST",
        "/api/pi-intent-lab/hybrid-stream",
        json={"text": "rain later", "run_pi": True},
    ) as resp:
        assert resp.status_code == 200
        events = [json.loads(line) for line in resp.iter_lines() if line]

    assert [event["event"] for event in events] == ["processing_cue", "error"]
    assert events[1]["error_type"] == "resource_pressure"
    assert events[1]["phase"] == "final"
    assert events[1]["resource"]["available_mb"] == 512
    assert events[1]["production_route_change"] is False

def test_pi_intent_lab_endpoint_times_out_stuck_comparison(monkeypatch):
    _disable_pi_lab_resource_guard(monkeypatch)
    import routers.pi_intent_lab as route_module

    async def stuck_comparison(*args, **kwargs):
        await asyncio.sleep(1)
        return {"unexpected": True}

    monkeypatch.setattr(route_module, "compare_pi_intent_lab", stuck_comparison)
    app = _admin_app()

    resp = TestClient(app).post(
        "/api/pi-intent-lab/compare",
        json={"text": "rain later", "request_timeout_seconds": 0.01},
    )

    assert resp.status_code == 504
    assert resp.json()["detail"] == "Pi intent lab comparison timed out"


def test_pi_intent_lab_hybrid_stream_emits_cue_then_final(monkeypatch):
    _disable_pi_lab_resource_guard(monkeypatch)
    import routers.pi_intent_lab as route_module

    async def fake_compare(text, **kwargs):
        await asyncio.sleep(0.01)
        return {
            "report_kind": "zoe_pi_intent_lab_comparison",
            "input": {"user_id": kwargs["user_id"]},
            "contract": {"production_route_change": False},
            "pi": {"intent": "weather", "confidence": 0.95},
            "safe_fulfillment": {"response_preview": "It is 18.5 C."},
            "simulated_hybrid_flow": {
                "cue_available": True,
                "final_completion_latency_ms": 2600.0,
                "production_route_change": False,
            },
        }

    monkeypatch.setattr(route_module, "compare_pi_intent_lab", fake_compare)
    monkeypatch.setattr(
        route_module,
        "_processing_cue",
        lambda: {
            "available": True,
            "latency_ms": 0.05,
            "event": {"type": "voice:processing_ack", "text": "Let me check."},
            "text": "Let me check.",
        },
    )
    app = _admin_app()

    with TestClient(app).stream(
        "POST",
        "/api/pi-intent-lab/hybrid-stream",
        json={"text": "rain later", "include_safe_fulfillment": True},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in resp.iter_lines() if line]

    assert [event["event"] for event in events] == ["processing_cue", "final"]
    assert events[0]["phase"] == "cue"
    assert events[0]["cue"]["text"] == "Let me check."
    assert events[0]["contract"]["production_route_change"] is False
    assert events[1]["phase"] == "final"
    assert events[1]["result"]["input"]["user_id"] == "admin"
    assert events[1]["result"]["pi"]["intent"] == "weather"
    assert events[1]["production_route_change"] is False
    assert events[1]["elapsed_ms"] >= events[0]["elapsed_ms"]


def test_pi_intent_lab_hybrid_stream_times_out_as_final_error(monkeypatch):
    _disable_pi_lab_resource_guard(monkeypatch)
    import routers.pi_intent_lab as route_module

    async def stuck_compare(*args, **kwargs):
        await asyncio.sleep(1)
        return {"unexpected": True}

    monkeypatch.setattr(route_module, "compare_pi_intent_lab", stuck_compare)
    monkeypatch.setattr(
        route_module,
        "_processing_cue",
        lambda: {"available": True, "latency_ms": 0.05, "event": None, "text": "Let me check."},
    )
    app = _admin_app()

    with TestClient(app).stream(
        "POST",
        "/api/pi-intent-lab/hybrid-stream",
        json={"text": "rain later", "request_timeout_seconds": 0.01},
    ) as resp:
        assert resp.status_code == 200
        events = [json.loads(line) for line in resp.iter_lines() if line]

    assert [event["event"] for event in events] == ["processing_cue", "error"]
    assert events[1]["error_type"] == "timeout"
    assert events[1]["phase"] == "final"
    assert events[1]["production_route_change"] is False


def test_pi_intent_lab_hybrid_stream_is_admin_scoped(monkeypatch):
    app = FastAPI()
    app.include_router(pi_intent_lab_router)

    async def fake_non_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_lab_operator] = fake_non_admin

    resp = TestClient(app).post("/api/pi-intent-lab/hybrid-stream", json={"text": "rain later"})

    assert resp.status_code == 403


def test_pi_intent_lab_hybrid_stream_emits_packet_when_cue_builder_fails(monkeypatch):
    _disable_pi_lab_resource_guard(monkeypatch)
    import routers.pi_intent_lab as route_module

    async def fake_compare(text, **kwargs):
        return {
            "report_kind": "zoe_pi_intent_lab_comparison",
            "input": {"user_id": kwargs["user_id"]},
            "pi": {"intent": "weather"},
        }

    def broken_cue():
        raise RuntimeError("cue builder exploded with a long but non-sensitive lab message")

    monkeypatch.setattr(route_module, "compare_pi_intent_lab", fake_compare)
    monkeypatch.setattr(route_module, "_processing_cue", broken_cue)
    app = _admin_app()

    with TestClient(app).stream(
        "POST",
        "/api/pi-intent-lab/hybrid-stream",
        json={"text": "rain later"},
    ) as resp:
        assert resp.status_code == 200
        events = [json.loads(line) for line in resp.iter_lines() if line]

    assert [event["event"] for event in events] == ["processing_cue", "final"]
    assert events[0]["cue"]["available"] is False
    assert events[0]["cue"]["error_type"] == "RuntimeError"
    assert "cue builder exploded" in events[0]["cue"]["error"]
    assert events[1]["result"]["pi"]["intent"] == "weather"


def test_pi_intent_lab_hybrid_stream_unexpected_compare_error_terminates_cleanly(monkeypatch):
    _disable_pi_lab_resource_guard(monkeypatch)
    import routers.pi_intent_lab as route_module

    async def broken_compare(*args, **kwargs):
        raise RuntimeError("backend vanished " + "x" * 300)

    monkeypatch.setattr(route_module, "compare_pi_intent_lab", broken_compare)
    monkeypatch.setattr(
        route_module,
        "_processing_cue",
        lambda: {"available": True, "latency_ms": 0.05, "event": None, "text": "Let me check."},
    )
    app = _admin_app()

    with TestClient(app).stream(
        "POST",
        "/api/pi-intent-lab/hybrid-stream",
        json={"text": "rain later"},
    ) as resp:
        assert resp.status_code == 200
        events = [json.loads(line) for line in resp.iter_lines() if line]

    assert [event["event"] for event in events] == ["processing_cue", "error"]
    assert events[1]["error_type"] == "exception"
    assert events[1]["exception_class"] == "RuntimeError"
    assert events[1]["phase"] == "final"
    assert len(events[1]["error"]) == 200
    assert events[1]["production_route_change"] is False
