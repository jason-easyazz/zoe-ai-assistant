"""Admin-only Pi intent lab comparisons.

The lab compares Zoe's current intent path with standalone Pi classification
without dispatching intents, writing memory, collecting shadow evidence, or
promoting routes. It is a measurement surface for deciding whether Pi can earn
specific Zoe intent lanes.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, Mapping

from zoe_pi_promotion import LOW_RISK_PI_INTENT_GROUPS, intent_group_for_intent


_ENV_LOCK = asyncio.Lock()
SAFE_FULFILLMENT_INTENTS = frozenset(
    {
        "daily_briefing",
        "date_query",
        "list_show",
        "time_query",
        "weather",
    }
)


@contextmanager
def _temporary_env(updates: Mapping[str, str | None]):
    old_values = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def compare_pi_intent_lab(
    text: str,
    *,
    user_id: str = "pi-intent-lab",
    context_turns: str = "",
    run_pi: bool = True,
    pi_transport: str | None = "rpc",
    allow_pi_execution: bool | None = None,
    local_model_configured: bool | None = None,
    measure_zoe_agent_baseline: bool = False,
    zoe_agent_timeout_seconds: float = 12.0,
    zoe_agent_max_tokens: int = 64,
    include_hybrid_status: bool = True,
    include_safe_fulfillment: bool = False,
    safe_fulfillment_timeout_seconds: float = 8.0,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return an apples-to-apples intent comparison without side effects."""
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("text is required")
    if zoe_agent_timeout_seconds <= 0:
        raise ValueError("zoe_agent_timeout_seconds must be positive")
    if zoe_agent_max_tokens <= 0:
        raise ValueError("zoe_agent_max_tokens must be positive")
    if safe_fulfillment_timeout_seconds <= 0:
        raise ValueError("safe_fulfillment_timeout_seconds must be positive")

    zoe_router = await _run_zoe_router(stripped, user_id=user_id)
    processing_cue = _processing_cue(env)
    speculative_safe_fulfillment = _start_speculative_safe_fulfillment(
        zoe_router,
        user_id=user_id,
        enabled=include_safe_fulfillment,
        timeout_seconds=safe_fulfillment_timeout_seconds,
    )
    try:
        pi = await _run_pi(
            stripped,
            context_turns=context_turns,
            run_pi=run_pi,
            transport=pi_transport,
            allow_pi_execution=allow_pi_execution,
            local_model_configured=local_model_configured,
            env=env,
        )
        zoe_agent = None
        if measure_zoe_agent_baseline and zoe_router["intent"] is None:
            zoe_agent = await _run_zoe_agent_baseline(
                stripped,
                timeout_seconds=zoe_agent_timeout_seconds,
                max_tokens=zoe_agent_max_tokens,
            )
        safe_fulfillment = await _safe_fulfill_pi_intent(
            pi,
            user_id=user_id,
            enabled=include_safe_fulfillment,
            timeout_seconds=safe_fulfillment_timeout_seconds,
            speculative=speculative_safe_fulfillment,
        )
    except BaseException:
        await _discard_speculative_safe_fulfillment(speculative_safe_fulfillment, None)
        raise

    hybrid = None
    if include_hybrid_status:
        from pi_hybrid_buffer import pi_hybrid_buffer_status

        hybrid = pi_hybrid_buffer_status(env, repeat=3, include_shadow_status=False)

    return {
        "report_kind": "zoe_pi_intent_lab_comparison",
        "contract": {
            "admin_only": True,
            "side_effects": "read_only_external_only" if include_safe_fulfillment else "none",
            "intent_dispatch_enabled": bool(include_safe_fulfillment),
            "intent_dispatch_scope": "read_only_allowlist_only" if include_safe_fulfillment else "none",
            "safe_read_only_fulfillment_enabled": bool(include_safe_fulfillment),
            "safe_read_only_fulfillment_intents": sorted(SAFE_FULFILLMENT_INTENTS),
            "memory_writes_enabled": False,
            "shadow_writes_enabled": False,
            "promotion_enabled": False,
            "pi_runtime": "standalone",
        },
        "input": {
            "text_chars": len(stripped),
            "context_turns_chars": len(context_turns or ""),
            "user_id": user_id,
        },
        "zoe_router": zoe_router,
        "pi": pi,
        "zoe_agent_baseline": zoe_agent,
        "safe_fulfillment": safe_fulfillment,
        "hybrid_buffer": _compact_hybrid(hybrid),
        "simulated_hybrid_flow": _simulated_hybrid_flow(processing_cue, pi, zoe_agent, safe_fulfillment),
        "comparison": _comparison(zoe_router, pi, zoe_agent),
    }


async def _run_zoe_router(text: str, *, user_id: str) -> dict[str, Any]:
    from intent_router import detect_and_extract_intent, detect_intent

    async with _ENV_LOCK:
        with _temporary_env({"ZOE_PI_INTENT_ENABLED": "false", "ZOE_PI_INTENT_SHADOW_ENABLED": "false"}):
            raw_started = time.perf_counter()
            raw_intent = detect_intent(text, user_id=user_id)
            raw_latency_ms = (time.perf_counter() - raw_started) * 1000
            started = time.perf_counter()
            extracted_intent = await detect_and_extract_intent(text, user_id=user_id)
            latency_ms = (time.perf_counter() - started) * 1000

    if extracted_intent is not None:
        route_class = "deterministic"
        baseline_kind = "router"
        baseline_comparable = True
    elif raw_intent is None:
        route_class = "fallback"
        baseline_kind = "router_only_not_comparable"
        baseline_comparable = False
    else:
        route_class = "extraction_failed"
        baseline_kind = "router_extraction_failed_not_comparable"
        baseline_comparable = False

    return {
        "intent": _intent_name(extracted_intent),
        "confidence": _intent_confidence(extracted_intent),
        "slots": _intent_slots(extracted_intent),
        "raw_intent": _intent_name(raw_intent),
        "raw_confidence": _intent_confidence(raw_intent),
        "route_class": route_class,
        "baseline_kind": baseline_kind,
        "baseline_lane": f"{route_class}:{baseline_kind}",
        "baseline_comparable": baseline_comparable,
        "latency_ms": latency_ms,
        "raw_router_latency_ms": raw_latency_ms,
        "would_execute": extracted_intent is not None,
    }


async def _run_pi(
    text: str,
    *,
    context_turns: str,
    run_pi: bool,
    transport: str | None,
    allow_pi_execution: bool | None,
    local_model_configured: bool | None,
    env: Mapping[str, str] | None,
) -> dict[str, Any]:
    if not run_pi:
        return {"ran": False, "intent": None, "reason": "run_pi_false", "would_execute": False}

    from pi_intent_classifier import PI_INTENT_EXECUTE_THRESHOLD, classify_with_pi_intent_governor

    runtime_env = dict(env if env is not None else os.environ)
    updates = {
        "ZOE_PI_INTENT_ENABLED": "true",
        "ZOE_PI_INTENT_SHADOW_ENABLED": "false",
    }
    if transport:
        updates["ZOE_PI_INTENT_TRANSPORT"] = transport
    if allow_pi_execution is not None:
        updates["ZOE_PI_ALLOW_EXECUTION"] = "true" if allow_pi_execution else "false"
    if local_model_configured is not None:
        updates["ZOE_PI_LOCAL_MODEL_CONFIGURED"] = "true" if local_model_configured else "false"
    runtime_env.update(updates)

    timeout_seconds = float(runtime_env.get("ZOE_PI_INTENT_TIMEOUT_SECONDS") or 4.0)
    started = time.perf_counter()
    error = None
    result = None
    timed_out_by_guard = False
    try:
        if env is None:
            # Let the classifier perform standalone Pi runtime discovery, including NVM node/bin lookup.
            # The env lock prevents concurrent lab requests from interleaving temporary os.environ changes.
            async with _ENV_LOCK:
                with _temporary_env(updates):
                    result = await asyncio.wait_for(
                        classify_with_pi_intent_governor(text, context_turns=context_turns),
                        timeout=timeout_seconds,
                    )
        else:
            result = await asyncio.wait_for(
                classify_with_pi_intent_governor(text, context_turns=context_turns, env=runtime_env),
                timeout=timeout_seconds,
            )
    except asyncio.TimeoutError:
        timed_out_by_guard = True
    except Exception as exc:  # pragma: no cover - defensive operator surface
        error = exc.__class__.__name__
    latency_ms = (time.perf_counter() - started) * 1000
    intent = result.intent if result else None
    confidence = float(result.confidence) if result else 0.0
    group = intent_group_for_intent(intent)
    executable = bool(intent) and confidence >= PI_INTENT_EXECUTE_THRESHOLD and group in LOW_RISK_PI_INTENT_GROUPS
    timed_out = timed_out_by_guard or (result is None and error is None and latency_ms >= (timeout_seconds * 1000 * 0.95))
    return {
        "ran": True,
        "intent": intent,
        "confidence": confidence,
        "slots": dict(result.slots) if result else {},
        "task_lane": result.task_lane if result else None,
        "source": result.source if result else None,
        "reason": result.reason if result else None,
        "intent_group": group,
        "low_risk_group": group in LOW_RISK_PI_INTENT_GROUPS if group else False,
        "latency_ms": latency_ms,
        "classifier_latency_ms": float(result.latency_ms) if result else None,
        "timed_out": timed_out,
        "error": error,
        "transport": runtime_env.get("ZOE_PI_INTENT_TRANSPORT") or "print",
        "would_execute": False,
        "would_execute_reason": "lab_never_dispatches_intents" if executable else "not_executable_or_not_low_risk",
    }


async def _run_zoe_agent_baseline(text: str, *, timeout_seconds: float, max_tokens: int) -> dict[str, Any]:
    from zoe_agent import run_zoe_agent

    session_id = f"pi-intent-lab-{uuid.uuid4().hex[:8]}"
    started = time.perf_counter()
    try:
        with _temporary_env({"ZOE_PI_INTENT_ENABLED": "false", "ZOE_PI_INTENT_SHADOW_ENABLED": "false"}):
            response = await asyncio.wait_for(
                run_zoe_agent(
                    text,
                    session_id,
                    "pi-intent-lab",
                    history=[],
                    db_memory_context="",
                    portrait="",
                    max_tokens_override=max_tokens,
                ),
                timeout=timeout_seconds,
            )
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "measured": True,
            "baseline_kind": "zoe_agent_fallback_baseline",
            "baseline_comparable": True,
            "latency_ms": latency_ms,
            "timed_out": False,
            "response_chars": len(response or ""),
            "would_execute": False,
        }
    except asyncio.TimeoutError:
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "measured": True,
            "baseline_kind": "zoe_agent_fallback_timeout",
            "baseline_comparable": False,
            "latency_ms": latency_ms,
            "timed_out": True,
            "response_chars": 0,
            "would_execute": False,
        }
    except Exception as exc:  # pragma: no cover - defensive operator surface
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "measured": True,
            "baseline_kind": "zoe_agent_fallback_error",
            "baseline_comparable": False,
            "latency_ms": latency_ms,
            "timed_out": False,
            "response_chars": 0,
            "error": exc.__class__.__name__,
            "would_execute": False,
        }


async def _safe_fulfill_pi_intent(
    pi: Mapping[str, Any],
    *,
    user_id: str,
    enabled: bool,
    timeout_seconds: float,
    speculative: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not enabled:
        return {
            "requested": False,
            "attempted": False,
            "allowed": False,
            "blocked_reason": "not_requested",
            "response_chars": 0,
            "response_preview": "",
            "would_execute": False,
        }

    async def block(reason: str, *, intent: str | None = None, error: Any = None) -> dict[str, Any]:
        return await _discard_speculative_safe_fulfillment(
            speculative,
            _blocked_fulfillment(reason, intent=intent, error=error),
        )

    intent_name = str(pi.get("intent") or "")
    if not pi.get("ran"):
        return await block("pi_not_run")
    if not intent_name:
        return await block("pi_no_intent")
    if pi.get("timed_out"):
        return await block("pi_timed_out", intent=intent_name)
    if pi.get("error"):
        return await block("pi_error", intent=intent_name, error=pi.get("error"))
    if intent_name not in SAFE_FULFILLMENT_INTENTS:
        return await block("side_effect_or_unsupported_intent", intent=intent_name)
    if not pi.get("low_risk_group"):
        return await block("not_low_risk_group", intent=intent_name)

    from pi_intent_classifier import PI_INTENT_EXECUTE_THRESHOLD

    confidence = float(pi.get("confidence") or 0.0)
    if confidence < PI_INTENT_EXECUTE_THRESHOLD:
        return await block("below_execute_threshold", intent=intent_name)

    pi_slots = dict(pi.get("slots") or {})
    discarded_speculative = None
    if speculative is not None:
        speculative_intent = str(speculative.get("intent") or "")
        speculative_slots = dict(speculative.get("slots") or {})
        speculative_task = speculative.get("task")
        if (
            speculative_intent == intent_name
            and speculative_slots == pi_slots
            and isinstance(speculative_task, asyncio.Task)
        ):
            return await _await_speculative_safe_fulfillment(speculative, timeout_seconds=timeout_seconds)
        reason = "speculative_slots_mismatch" if speculative_intent == intent_name else "speculative_pi_disagreed"
        discarded_speculative = await _discard_speculative_safe_fulfillment(
            speculative,
            _blocked_fulfillment(reason, intent=speculative_intent),
        )

    from intent_router import Intent

    intent = Intent(intent_name, pi_slots, confidence)
    result = await _execute_safe_fulfillment_intent(
        intent,
        user_id=user_id,
        timeout_seconds=timeout_seconds,
        started_before_pi=False,
    )
    if discarded_speculative is not None:
        result["speculative_safe_fulfillment"] = "discarded"
        result["speculative_intent"] = discarded_speculative.get("speculative_intent")
        result["speculative_discard_reason"] = discarded_speculative.get("blocked_reason")
    return result

def _start_speculative_safe_fulfillment(
    zoe_router: Mapping[str, Any],
    *,
    user_id: str,
    enabled: bool,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    if not enabled:
        return None
    intent_name = str(zoe_router.get("intent") or "")
    if not intent_name or intent_name not in SAFE_FULFILLMENT_INTENTS:
        return None
    if not zoe_router.get("baseline_comparable"):
        return None

    from intent_router import Intent

    confidence = float(zoe_router.get("confidence") or 0.0)
    intent = Intent(intent_name, dict(zoe_router.get("slots") or {}), confidence)
    task = asyncio.create_task(
        _execute_safe_fulfillment_intent(
            intent,
            user_id=user_id,
            timeout_seconds=timeout_seconds,
            started_before_pi=True,
        )
    )
    return {
        "intent": intent_name,
        "slots": dict(zoe_router.get("slots") or {}),
        "task": task,
        "source": "zoe_router",
    }


async def _execute_safe_fulfillment_intent(
    intent: Any,
    *,
    user_id: str,
    timeout_seconds: float,
    started_before_pi: bool,
) -> dict[str, Any]:
    from intent_router import execute_intent

    intent_name = str(getattr(intent, "name", "") or "")
    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(execute_intent(intent, user_id=user_id), timeout=timeout_seconds)
        response_text = _response_text(response)
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "requested": True,
            "attempted": True,
            "allowed": True,
            "intent": intent_name,
            "latency_ms": latency_ms,
            "timed_out": False,
            "error": None,
            "response_chars": len(response_text),
            "response_preview": _preview_response(response_text),
            "would_execute": True,
            "execution_scope": "read_only_allowlist",
            "started_before_pi": started_before_pi,
            "validated_by_pi": False,
        }
    except asyncio.TimeoutError:
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "requested": True,
            "attempted": True,
            "allowed": True,
            "intent": intent_name,
            "latency_ms": latency_ms,
            "timed_out": True,
            "error": None,
            "response_chars": 0,
            "response_preview": "",
            "would_execute": False,
            "execution_scope": "read_only_allowlist",
            "started_before_pi": started_before_pi,
            "validated_by_pi": False,
        }
    except Exception as exc:  # pragma: no cover - defensive operator surface
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "requested": True,
            "attempted": True,
            "allowed": True,
            "intent": intent_name,
            "latency_ms": latency_ms,
            "timed_out": False,
            "error": exc.__class__.__name__,
            "response_chars": 0,
            "response_preview": "",
            "would_execute": False,
            "execution_scope": "read_only_allowlist",
            "started_before_pi": started_before_pi,
            "validated_by_pi": False,
        }


async def _await_speculative_safe_fulfillment(speculative: Mapping[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
    task = speculative.get("task")
    if not isinstance(task, asyncio.Task):
        return _blocked_fulfillment("speculative_task_missing", intent=str(speculative.get("intent") or ""))
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        return {
            "requested": True,
            "attempted": True,
            "allowed": True,
            "intent": str(speculative.get("intent") or ""),
            "latency_ms": None,
            "timed_out": True,
            "error": None,
            "response_chars": 0,
            "response_preview": "",
            "would_execute": False,
            "execution_scope": "read_only_allowlist",
            "started_before_pi": True,
            "validated_by_pi": True,
            "speculative_safe_fulfillment": "timed_out",
        }
    result = dict(result)
    result["validated_by_pi"] = True
    result["speculative_safe_fulfillment"] = "used"
    return result


async def _discard_speculative_safe_fulfillment(
    speculative: Mapping[str, Any] | None,
    fallback: dict[str, Any] | None,
) -> dict[str, Any]:
    if speculative is None:
        return fallback or _blocked_fulfillment("speculative_not_available")
    task = speculative.get("task")
    if isinstance(task, asyncio.Task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    result = fallback or _blocked_fulfillment("speculative_pi_disagreed", intent=str(speculative.get("intent") or ""))
    result["speculative_safe_fulfillment"] = "discarded"
    result["speculative_intent"] = str(speculative.get("intent") or "")
    return result


def _blocked_fulfillment(reason: str, *, intent: str | None = None, error: Any = None) -> dict[str, Any]:
    result = {
        "requested": True,
        "attempted": False,
        "allowed": False,
        "blocked_reason": reason,
        "intent": intent,
        "response_chars": 0,
        "response_preview": "",
        "would_execute": False,
    }
    if error is not None:
        result["error"] = error
    return result


def _response_text(response: Any) -> str:
    return "" if response is None else str(response)


def _preview_response(response: Any, *, limit: int = 240) -> str:
    text = " ".join(_response_text(response).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."



def _processing_cue(env: Mapping[str, str] | None) -> dict[str, Any]:
    from voice_presence import processing_ack_event

    started = time.perf_counter()
    event = processing_ack_event(env, index=0)
    latency_ms = (time.perf_counter() - started) * 1000
    safe_event = _safe_voice_event(event) if event else None
    return {
        "available": safe_event is not None,
        "latency_ms": latency_ms,
        "event": safe_event,
        "text": str((safe_event or {}).get("text") or ""),
    }


def _simulated_hybrid_flow(
    processing_cue: Mapping[str, Any],
    pi: Mapping[str, Any],
    zoe_agent: Mapping[str, Any] | None,
    safe_fulfillment: Mapping[str, Any],
) -> dict[str, Any]:
    pi_latency = _float_or_none(pi.get("latency_ms"))
    agent_latency = _float_or_none((zoe_agent or {}).get("latency_ms"))
    fulfillment_latency = _float_or_none(safe_fulfillment.get("latency_ms"))
    fulfilled = bool(
        safe_fulfillment.get("attempted")
        and not safe_fulfillment.get("timed_out")
        and not safe_fulfillment.get("error")
    )
    if fulfilled and pi_latency is not None:
        if safe_fulfillment.get("started_before_pi"):
            final_latency = max(pi_latency, fulfillment_latency or 0.0)
        else:
            final_latency = pi_latency + (fulfillment_latency or 0.0)
    else:
        final_latency = pi_latency if pi.get("ran") else agent_latency
    return {
        "strategy": "processing_ack_then_pi_or_fallback",
        "cue_available": bool(processing_cue.get("available")),
        "cue_text": processing_cue.get("text") or "",
        "cue_latency_ms": processing_cue.get("latency_ms"),
        "cue_event": processing_cue.get("event"),
        "pi_completion_latency_ms": pi_latency,
        "fallback_completion_latency_ms": agent_latency,
        "safe_fulfillment_latency_ms": fulfillment_latency,
        "safe_fulfillment_completion_latency_ms": final_latency if fulfilled else None,
        "safe_fulfillment_response_preview": safe_fulfillment.get("response_preview") or "",
        "final_completion_latency_ms": final_latency,
        "natural_flow_candidate": bool(processing_cue.get("available") and final_latency is not None),
        "production_route_change": False,
    }


def _safe_voice_event(event: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    safe = dict(event)
    audio = safe.pop("audio_base64", None)
    if audio:
        safe["audio_base64_chars"] = len(str(audio))
    return safe

def _comparison(
    zoe_router: Mapping[str, Any],
    pi: Mapping[str, Any],
    zoe_agent: Mapping[str, Any] | None,
) -> dict[str, Any]:
    pi_latency = _float_or_none(pi.get("latency_ms"))
    router_latency = _float_or_none(zoe_router.get("latency_ms"))
    agent_latency = _float_or_none((zoe_agent or {}).get("latency_ms"))
    if zoe_agent and zoe_agent.get("baseline_comparable"):
        comparable_latency = agent_latency
    elif zoe_router.get("baseline_comparable"):
        comparable_latency = router_latency
    else:
        comparable_latency = None
    return {
        "baseline_lane": zoe_router.get("baseline_lane"),
        "zoe_router_intent": zoe_router.get("intent"),
        "pi_intent": pi.get("intent"),
        "agreement": zoe_router.get("intent") == pi.get("intent"),
        "pi_vs_router_latency_delta_ms": None if pi_latency is None or router_latency is None else router_latency - pi_latency,
        "pi_vs_comparable_latency_delta_ms": (
            None if pi_latency is None or comparable_latency is None else comparable_latency - pi_latency
        ),
        "pi_candidate_for_lane": bool(
            zoe_router.get("intent") is None
            and pi.get("intent")
            and pi.get("low_risk_group")
            and not pi.get("timed_out")
            and not pi.get("error")
        ),
        "production_route_change": False,
    }


def _compact_hybrid(hybrid: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if hybrid is None:
        return None
    contract = hybrid.get("contract") or {}
    return {
        "mode": contract.get("mode"),
        "ready": contract.get("ready"),
        "foreground_pi_execution_enabled": contract.get("foreground_pi_execution_enabled"),
        "promoted_groups": list(contract.get("promoted_groups") or []),
        "blockers": list(contract.get("blockers") or []),
        "warnings": list(contract.get("warnings") or []),
    }


def _intent_name(intent: Any) -> str | None:
    return getattr(intent, "name", None) if intent is not None else None


def _intent_confidence(intent: Any) -> float | None:
    value = getattr(intent, "confidence", None) if intent is not None else None
    return float(value) if isinstance(value, (int, float)) else None


def _intent_slots(intent: Any) -> dict[str, Any]:
    slots = getattr(intent, "slots", None) if intent is not None else None
    return dict(slots) if isinstance(slots, Mapping) else {}


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


__all__ = ["compare_pi_intent_lab"]
