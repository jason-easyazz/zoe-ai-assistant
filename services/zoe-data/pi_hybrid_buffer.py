"""Read-only readiness report for Zoe's Pi + intent-buffer hybrid mode."""

from __future__ import annotations

import math
import os
import statistics
import time
from typing import Any, Callable, Mapping, Sequence

from pi_intent_classifier import PiIntentClassifierConfig, pi_intent_promotion_status
from pi_intent_shadow import PiIntentShadowConfig, pi_intent_shadow_status
from voice_presence import (
    processing_ack_event,
    wake_ack_audio_payload,
    wake_ack_variant,
    wake_presence_events,
)

DEFAULT_HYBRID_BUFFER_BUDGET_MS = 150.0
DEFAULT_HYBRID_BUFFER_REPEAT = 10


def pi_hybrid_buffer_status(
    env: Mapping[str, str] | None = None,
    *,
    repeat: int = DEFAULT_HYBRID_BUFFER_REPEAT,
    payload_construction_budget_ms: float = DEFAULT_HYBRID_BUFFER_BUDGET_MS,
    include_shadow_status: bool = True,
) -> dict[str, Any]:
    """Report whether Zoe is configured for instant-buffer + Pi evidence mode.

    This is intentionally read-only. It does not classify text, run Pi, write
    labels, promote groups, or alter live routing.
    """
    values = env if env is not None else os.environ
    active_repeat = max(1, repeat)
    wake = _measure_path("wake_ack", active_repeat, lambda index: _wake_payload(values, index))
    processing = _measure_path(
        "processing_ack",
        active_repeat,
        lambda index: _processing_payload(values, index),
    )
    presence_gate = _presence_gate(wake, processing, budget_ms=payload_construction_budget_ms)
    pi_config = _safe_pi_config(values)
    shadow_config = _safe_shadow_config(values)
    promotion = pi_intent_promotion_status(values)
    shadow = pi_intent_shadow_status(values) if include_shadow_status else None
    contract = _hybrid_contract(
        presence_gate,
        pi_config=pi_config,
        shadow_config=shadow_config,
        promotion=promotion,
        shadow=shadow,
    )
    return {
        "report_kind": "zoe_pi_hybrid_buffer_status",
        "repeat": active_repeat,
        "payload_construction_budget_ms": payload_construction_budget_ms,
        "latency_kind": "payload_construction_only",
        "presence": {
            "wake_ack": wake,
            "processing_ack": processing,
            "gate": presence_gate,
        },
        "pi": {
            "config": pi_config,
            "promotion": promotion,
            "shadow_config": shadow_config,
            "shadow": _compact_shadow_status(shadow),
        },
        "contract": contract,
    }


def _hybrid_contract(
    presence_gate: Mapping[str, Any],
    *,
    pi_config: Mapping[str, Any],
    shadow_config: Mapping[str, Any],
    promotion: Mapping[str, Any],
    shadow: Mapping[str, Any] | None,
) -> dict[str, Any]:
    active_groups = list(promotion.get("active_groups") or [])
    ignored_groups = list(promotion.get("ignored_groups") or [])
    pi_enabled = bool(pi_config.get("enabled"))
    shadow_enabled = bool(shadow_config.get("enabled"))
    foreground_pi_execution_enabled = pi_enabled and bool(active_groups)
    processing_ready = bool(presence_gate.get("processing_ack_ready"))
    wake_ready = bool(presence_gate.get("wake_ack_ready"))

    blockers: list[str] = []
    warnings: list[str] = []
    blockers.extend(str(item) for item in presence_gate.get("blockers") or [])
    warnings.extend(str(item) for item in presence_gate.get("warnings") or [])
    if ignored_groups:
        blockers.append("non_allowlisted_pi_groups_requested")
    if active_groups and not pi_enabled:
        blockers.append("promoted_groups_without_pi_classifier_enabled")
    if pi_enabled and shadow_enabled and not active_groups:
        warnings.append("pi_classifier_enabled_without_promoted_groups_runs_shadow_only")
    if not shadow_enabled and not active_groups:
        blockers.append("pi_shadow_disabled_without_promoted_groups")
    if str(pi_config.get("transport") or "print") != "rpc":
        warnings.append("pi_transport_not_rpc")
    if bool(promotion.get("auto_promote_requested")):
        warnings.append("auto_promote_requested_but_apply_path_is_guarded")
    if shadow is not None:
        label_count = int(shadow.get("label_count") or 0)
        if label_count == 0:
            warnings.append("pi_shadow_has_no_outcome_labels_yet")

    if foreground_pi_execution_enabled:
        mode = "promoted_buffer"
    elif shadow_enabled:
        mode = "shadow_buffer"
    else:
        mode = "buffer_only"

    ready = processing_ready and not blockers and mode in {"shadow_buffer", "promoted_buffer"}
    return {
        "mode": mode,
        "ready": ready,
        "wake_ack_ready": wake_ready,
        "processing_ack_ready": processing_ready,
        "pi_shadow_enabled": shadow_enabled,
        "pi_classifier_enabled": pi_enabled,
        "pi_execution_enabled": pi_enabled,
        "foreground_pi_execution_enabled": foreground_pi_execution_enabled,
        "promoted_groups": active_groups,
        "blockers": _dedupe(blockers),
        "warnings": _dedupe(warnings),
    }


def _wake_payload(env: Mapping[str, str], index: int) -> dict[str, Any]:
    variant = wake_ack_variant(env, index=index)
    audio = wake_ack_audio_payload(env, audio_path=str(variant.get("audio_path") or ""))
    events = [_safe_event(event) for event in wake_presence_events(
        ack_phrase=str(variant.get("phrase") or ""),
        ack_audio=audio,
    )]
    return {
        "configured": _events_have_visible_ack(events),
        "events": events,
    }


def _processing_payload(env: Mapping[str, str], index: int) -> dict[str, Any]:
    event = processing_ack_event(env, index=index)
    safe = _safe_event(event) if event else None
    return {"configured": bool(safe and _event_has_visible_ack(safe)), "event": safe}


def _measure_path(name: str, repeat: int, payload_factory: Callable[[int], Mapping[str, Any]]) -> dict[str, Any]:
    latencies: list[float] = []
    payloads: list[Mapping[str, Any]] = []
    errors: list[str] = []
    for index in range(repeat):
        start = time.perf_counter()
        try:
            payload = payload_factory(index)
        except Exception as exc:  # pragma: no cover - defensive status guard
            payload = {"configured": False, "error": type(exc).__name__}
            errors.append(f"{type(exc).__name__}: {exc}")
        latencies.append((time.perf_counter() - start) * 1000)
        payloads.append(payload)
    configured_count = sum(1 for payload in payloads if payload.get("configured"))
    return {
        "name": name,
        "configured": configured_count > 0,
        "configured_count": configured_count,
        "sample_count": len(payloads),
        "latency_ms": _latency_stats(latencies),
        "first_payload": dict(payloads[0]) if payloads else {},
        "errors": errors[:5],
    }


def _presence_gate(wake: Mapping[str, Any], processing: Mapping[str, Any], *, budget_ms: float) -> dict[str, Any]:
    wake_p95 = _path_p95(wake)
    processing_p95 = _path_p95(processing)
    wake_ready = bool(wake.get("configured"))
    processing_ready = bool(processing.get("configured"))
    return {
        "payload_construction_budget_ms": budget_ms,
        "latency_kind": "payload_construction_only",
        "wake_ack_ready": wake_ready,
        "processing_ack_ready": processing_ready,
        "natural_flow_buffer_ready": processing_ready,
        "full_wake_to_processing_ready": wake_ready and processing_ready,
        "blockers": _presence_blockers(wake, processing),
        "warnings": _presence_latency_warnings(wake_p95, processing_p95, budget_ms),
    }


def _presence_blockers(wake: Mapping[str, Any], processing: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not wake.get("configured"):
        blockers.append("wake_ack_not_configured")
    if not processing.get("configured"):
        blockers.append("processing_ack_not_configured")
    return blockers


def _presence_latency_warnings(wake_p95: float | None, processing_p95: float | None, budget_ms: float) -> list[str]:
    warnings: list[str] = []
    if wake_p95 is not None and wake_p95 > budget_ms:
        warnings.append(f"wake_ack_payload_construction_p95_exceeds_{budget_ms:g}ms")
    if processing_p95 is not None and processing_p95 > budget_ms:
        warnings.append(f"processing_ack_payload_construction_p95_exceeds_{budget_ms:g}ms")
    return warnings


def _compact_shadow_status(shadow: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if shadow is None:
        return None
    report = shadow.get("report") or {}
    promotion = shadow.get("promotion_report") or {}
    return {
        "record_count_window": shadow.get("record_count_window"),
        "raw_record_count_window": shadow.get("raw_record_count_window"),
        "label_count": shadow.get("label_count"),
        "accuracy_available": report.get("accuracy_available"),
        "labeled_sample_count_by_group": report.get("labeled_sample_count_by_group"),
        "candidate_groups": promotion.get("candidate_groups"),
        "promoted_groups": promotion.get("promoted_groups"),
    }


def _safe_pi_config(env: Mapping[str, str]) -> dict[str, Any]:
    try:
        return PiIntentClassifierConfig.from_env(env).to_dict()
    except Exception as exc:
        return {"error": str(exc)}


def _safe_shadow_config(env: Mapping[str, str]) -> dict[str, Any]:
    try:
        return PiIntentShadowConfig.from_env(env).to_dict()
    except Exception as exc:
        return {"error": str(exc)}


def _path_p95(path_report: Mapping[str, Any]) -> float | None:
    value = ((path_report.get("latency_ms") or {}).get("p95"))
    return float(value) if isinstance(value, (int, float)) else None


def _safe_event(event: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    safe = dict(event)
    audio_b64 = safe.pop("audio_base64", None)
    if audio_b64:
        safe["audio_base64_chars"] = len(str(audio_b64))
    return safe


def _events_have_visible_ack(events: Sequence[Mapping[str, Any]]) -> bool:
    return any(_event_has_visible_ack(event) for event in events)


def _event_has_visible_ack(event: Mapping[str, Any] | None) -> bool:
    if not event:
        return False
    return bool(
        str(event.get("text") or "").strip()
        or event.get("audio_base64_chars")
        or event.get("audio_base64")
    )


def _latency_stats(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"avg": None, "p50": None, "p95": None, "min": None, "max": None}
    ordered = sorted(values)
    return {
        "avg": sum(ordered) / len(ordered),
        "p50": statistics.median(ordered),
        "p95": _percentile(ordered, 95),
        "min": ordered[0],
        "max": ordered[-1],
    }


def _percentile(ordered_values: Sequence[float], percentile: int) -> float | None:
    if not ordered_values:
        return None
    rank = max(1, math.ceil((percentile / 100) * len(ordered_values)))
    return ordered_values[min(rank - 1, len(ordered_values) - 1)]


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


__all__ = ["pi_hybrid_buffer_status"]
