"""Production guard for Zoe's Pi intent-buffer hybrid route.

This module turns the already-measured lab path into a narrow production lane:
instant processing cue, standalone Pi RPC classification, and read-only Zoe
fulfillment only when the Pi result is allowlisted and agrees with Zoe's router
or the intent-buffer hint. It is disabled by default and falls back closed.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pi_intent_lab import SAFE_FULFILLMENT_INTENTS, compare_pi_intent_lab
from zoe_pi_promotion import LOW_RISK_PI_INTENT_GROUPS, intent_group_for_intent

_SAFE_PRODUCTION_INTENTS = frozenset({"weather", "daily_briefing", "list_show"})
_DEFAULT_GROUPS = ("weather", "daily_briefing")
_WEATHER_SIGNAL_RE = re.compile(
    r"\b(weather|rain|forecast|temperature|storm|windy|humid|umbrella|jacket|hot|cold|degrees|celsius)\b",
    re.I,
)
_DAILY_BRIEFING_SIGNAL_RE = re.compile(
    r"(\bwhat(?:'s| is) my day\b|\bmy day looking\b|\bleft on my day\b|\bcoming up today\b|\bbriefing\b|\bagenda\b)",
    re.I,
)
_LIST_SHOW_SIGNAL_RE = re.compile(r"\b(what(?:'s| is) on|show|read|check)\b.*\b(list|shopping|grocer)", re.I)


@dataclass(frozen=True)
class PiHybridProductionConfig:
    enabled: bool = False
    groups: tuple[str, ...] = _DEFAULT_GROUPS
    max_words: int = 32
    request_timeout_seconds: float = 8.0
    safe_fulfillment_timeout_seconds: float = 6.0
    transport: str = "rpc"
    allow_pi_execution: bool = True
    local_model_configured: bool = True
    require_agreement: bool = True
    min_available_mb: int = 2048
    min_swap_free_mb: int = 256
    resource_guard_enabled: bool = True
    router_fast_accept_enabled: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PiHybridProductionConfig":
        values = env if env is not None else os.environ
        return cls(
            enabled=_env_bool(values.get("ZOE_PI_HYBRID_PRODUCTION_ENABLED"), default=False),
            groups=_parse_groups(values.get("ZOE_PI_HYBRID_PRODUCTION_GROUPS")),
            max_words=_int_env(values.get("ZOE_PI_HYBRID_PRODUCTION_MAX_WORDS"), default=32),
            request_timeout_seconds=_float_env(values.get("ZOE_PI_HYBRID_PRODUCTION_TIMEOUT_SECONDS"), default=8.0),
            safe_fulfillment_timeout_seconds=_float_env(
                values.get("ZOE_PI_HYBRID_PRODUCTION_SAFE_FULFILLMENT_TIMEOUT_SECONDS"), default=6.0
            ),
            transport=(values.get("ZOE_PI_HYBRID_PRODUCTION_TRANSPORT") or "rpc").strip() or "rpc",
            allow_pi_execution=_env_bool(values.get("ZOE_PI_HYBRID_ALLOW_PI_EXECUTION"), default=True),
            local_model_configured=_env_bool(values.get("ZOE_PI_HYBRID_LOCAL_MODEL_CONFIGURED"), default=True),
            require_agreement=_env_bool(values.get("ZOE_PI_HYBRID_REQUIRE_AGREEMENT"), default=True),
            min_available_mb=_int_env(values.get("ZOE_PI_HYBRID_MIN_AVAILABLE_MB"), default=2048),
            min_swap_free_mb=_int_env(values.get("ZOE_PI_HYBRID_MIN_SWAP_FREE_MB"), default=256),
            resource_guard_enabled=_env_bool(values.get("ZOE_PI_HYBRID_RESOURCE_GUARD_ENABLED"), default=True),
            router_fast_accept_enabled=_env_bool(
                values.get("ZOE_PI_HYBRID_ROUTER_FAST_ACCEPT_ENABLED"), default=True
            ),
        )

    def validate(self) -> None:
        if self.max_words <= 0:
            raise ValueError("ZOE_PI_HYBRID_PRODUCTION_MAX_WORDS must be positive")
        if self.request_timeout_seconds <= 0:
            raise ValueError("ZOE_PI_HYBRID_PRODUCTION_TIMEOUT_SECONDS must be positive")
        if self.safe_fulfillment_timeout_seconds <= 0:
            raise ValueError("ZOE_PI_HYBRID_PRODUCTION_SAFE_FULFILLMENT_TIMEOUT_SECONDS must be positive")
        if self.transport not in {"rpc", "print"}:
            raise ValueError("ZOE_PI_HYBRID_PRODUCTION_TRANSPORT must be rpc or print")
        unsupported = [group for group in self.groups if group not in LOW_RISK_PI_INTENT_GROUPS]
        if unsupported:
            raise ValueError(f"unsupported Pi hybrid production groups: {', '.join(unsupported)}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "enabled": self.enabled,
            "groups": list(self.groups),
            "max_words": self.max_words,
            "request_timeout_seconds": self.request_timeout_seconds,
            "safe_fulfillment_timeout_seconds": self.safe_fulfillment_timeout_seconds,
            "transport": self.transport,
            "allow_pi_execution": self.allow_pi_execution,
            "local_model_configured": self.local_model_configured,
            "require_agreement": self.require_agreement,
            "resource_guard_enabled": self.resource_guard_enabled,
            "min_available_mb": self.min_available_mb,
            "min_swap_free_mb": self.min_swap_free_mb,
            "router_fast_accept_enabled": self.router_fast_accept_enabled,
        }


async def try_pi_hybrid_production(
    text: str,
    *,
    user_id: str,
    context_turns: str = "",
    env: Mapping[str, str] | None = None,
    config: PiHybridProductionConfig | None = None,
) -> dict[str, Any]:
    """Attempt the production hybrid lane and return an auditable decision."""
    active_config = config or PiHybridProductionConfig.from_env(env)
    active_config.validate()
    stripped = (text or "").strip()

    def _with_evidence(decision: dict[str, Any]) -> dict[str, Any]:
        _record_production_evidence(stripped, user_id=user_id, decision=decision, env=env)
        return decision

    base = {
        "report_kind": "zoe_pi_hybrid_production_decision",
        "config": active_config.to_dict(),
        "accepted": False,
        "response_text": "",
        "intent": None,
        "intent_group": None,
        "reason": "not_attempted",
        "production_route_change": False,
    }
    eligible, reason = pi_hybrid_production_eligible(stripped, config=active_config)
    if not eligible:
        base["reason"] = reason
        return _with_evidence(base)

    pressure = await _resource_pressure(active_config)
    if pressure:
        base["reason"] = "resource_pressure"
        base["resource"] = pressure
        return _with_evidence(base)

    if active_config.router_fast_accept_enabled:
        fast = await _try_router_confirmed_fast_accept(
            stripped,
            user_id=user_id,
            active_config=active_config,
            env=env,
        )
        if fast.get("accepted"):
            _schedule_pi_audit_after_fast_accept(
                stripped,
                user_id=user_id,
                context_turns=context_turns,
                active_config=active_config,
                env=env,
                fast_decision=fast,
            )
            return _with_evidence({
                **base,
                **fast,
                "response_text": str(fast.get("response_text") or ""),
                "production_route_change": True,
            })

    try:
        result = await asyncio.wait_for(
            compare_pi_intent_lab(
                stripped,
                user_id=user_id,
                context_turns=context_turns,
                run_pi=True,
                pi_transport=active_config.transport,
                allow_pi_execution=active_config.allow_pi_execution,
                local_model_configured=active_config.local_model_configured,
                measure_zoe_agent_baseline=False,
                include_hybrid_status=False,
                include_safe_fulfillment=True,
                safe_fulfillment_timeout_seconds=active_config.safe_fulfillment_timeout_seconds,
                env=env,
            ),
            timeout=active_config.request_timeout_seconds,
        )
    except asyncio.TimeoutError:
        base["reason"] = "timeout"
        return _with_evidence(base)
    except ValueError as exc:
        base["reason"] = "validation_error"
        base["error"] = str(exc)
        return _with_evidence(base)
    except Exception as exc:  # pragma: no cover - production guard must fall back closed
        base["reason"] = "exception"
        base["error"] = exc.__class__.__name__
        return _with_evidence(base)

    decision = _acceptance_decision(result, active_config)
    response_text = str(decision.get("response_text") or "")
    return _with_evidence({
        **base,
        **decision,
        "response_text": response_text,
        "production_route_change": bool(decision.get("accepted")),
        "lab_result": _compact_lab_result(result),
    })


async def _try_router_confirmed_fast_accept(
    text: str,
    *,
    user_id: str,
    active_config: PiHybridProductionConfig,
    env: Mapping[str, str] | None,
) -> dict[str, Any]:
    try:
        result = await asyncio.wait_for(
            compare_pi_intent_lab(
                text,
                user_id=user_id,
                context_turns="",
                run_pi=False,
                pi_transport=active_config.transport,
                allow_pi_execution=active_config.allow_pi_execution,
                local_model_configured=active_config.local_model_configured,
                measure_zoe_agent_baseline=False,
                include_hybrid_status=False,
                include_safe_fulfillment=True,
                safe_fulfillment_timeout_seconds=active_config.safe_fulfillment_timeout_seconds,
                allow_router_safe_fulfillment=True,
                env=env,
            ),
            timeout=active_config.safe_fulfillment_timeout_seconds,
        )
    except Exception as exc:
        return {"accepted": False, "reason": "router_fast_accept_error", "error": exc.__class__.__name__}

    decision = _router_fast_acceptance_decision(result, active_config)
    response_text = str(decision.get("response_text") or "")
    return {
        **decision,
        "response_text": response_text,
        "lab_result": _compact_lab_result(result),
    }


def _router_fast_acceptance_decision(result: Mapping[str, Any], config: PiHybridProductionConfig) -> dict[str, Any]:
    router = result.get("zoe_router") or {}
    safe = result.get("safe_fulfillment") or {}
    intent = _optional_str(router.get("intent"))
    group = intent_group_for_intent(intent)
    response_text = _optional_str(safe.get("response_text")) or _optional_str(safe.get("response_preview")) or ""
    base = {
        "accepted": False,
        "intent": intent,
        "intent_group": group,
        "response_text": "",
        "agreement_kind": None,
    }
    if str(router.get("route_class") or "") != "deterministic" or not router.get("baseline_comparable"):
        return {**base, "reason": "router_not_deterministic"}
    if group not in set(config.groups):
        return {**base, "reason": "group_not_enabled"}
    if intent not in _SAFE_PRODUCTION_INTENTS or intent not in SAFE_FULFILLMENT_INTENTS:
        return {**base, "reason": "intent_not_safe_for_production"}
    if not safe.get("attempted") or not safe.get("allowed"):
        return {**base, "reason": safe.get("blocked_reason") or "safe_fulfillment_blocked"}
    if safe.get("timed_out"):
        return {**base, "reason": "safe_fulfillment_timed_out"}
    if safe.get("error"):
        return {**base, "reason": "safe_fulfillment_error", "error": safe.get("error")}
    if not response_text:
        return {**base, "reason": "empty_fulfillment_response"}
    if not safe.get("validated_by_router"):
        return {**base, "reason": "router_validation_missing"}
    return {
        **base,
        "accepted": True,
        "response_text": response_text,
        "agreement_kind": "zoe_router_fast",
        "reason": "router_confirmed_fast_accept",
        "pi_audit_scheduled": True,
        "safe_fulfillment_latency_ms": safe.get("latency_ms"),
        "speculative_safe_fulfillment": safe.get("speculative_safe_fulfillment"),
    }


def _schedule_pi_audit_after_fast_accept(
    text: str,
    *,
    user_id: str,
    context_turns: str,
    active_config: PiHybridProductionConfig,
    env: Mapping[str, str] | None,
    fast_decision: Mapping[str, Any],
) -> None:
    async def _runner() -> None:
        try:
            result = await compare_pi_intent_lab(
                text,
                user_id=user_id,
                context_turns=context_turns,
                run_pi=True,
                pi_transport=active_config.transport,
                allow_pi_execution=active_config.allow_pi_execution,
                local_model_configured=active_config.local_model_configured,
                measure_zoe_agent_baseline=False,
                include_hybrid_status=False,
                include_safe_fulfillment=False,
                env=env,
            )
            pi = result.get("pi") or {}
            if _optional_str(pi.get("intent")) != _optional_str(fast_decision.get("intent")):
                try:
                    import logging

                    logging.getLogger(__name__).warning(
                        "Pi audit disagreed after router fast accept: router=%s pi=%s",
                        fast_decision.get("intent"),
                        pi.get("intent"),
                    )
                    _record_production_evidence(
                        text,
                        user_id=user_id,
                        decision={
                            "report_kind": "zoe_pi_hybrid_production_decision",
                            "accepted": False,
                            "intent": _optional_str(fast_decision.get("intent")),
                            "intent_group": fast_decision.get("intent_group"),
                            "reason": "audit_disagreement",
                            "pi_intent": _optional_str(pi.get("intent")),
                            "pi_intent_group": pi.get("intent_group"),
                            "production_route_change": False,
                        },
                        env=env,
                    )
                except Exception:
                    return
        except Exception:
            return

    asyncio.create_task(_runner())


def pi_hybrid_production_eligible(text: str, *, config: PiHybridProductionConfig | None = None) -> tuple[bool, str]:
    active_config = config or PiHybridProductionConfig.from_env()
    if not active_config.enabled:
        return False, "disabled"
    stripped = (text or "").strip()
    if not stripped:
        return False, "empty_text"
    if len(stripped.split()) > active_config.max_words:
        return False, "too_many_words"
    try:
        from pi_intent_classifier import pi_intent_prefilter_allows

        if not pi_intent_prefilter_allows(stripped):
            return False, "prefilter_rejected"
    except Exception:
        return False, "prefilter_unavailable"
    if not _production_prefilter_allows(stripped, active_config.groups):
        return False, "production_prefilter_rejected"
    return True, "eligible"


def _production_prefilter_allows(text: str, groups: tuple[str, ...]) -> bool:
    selected = set(groups)
    if "weather" in selected and _WEATHER_SIGNAL_RE.search(text):
        return True
    if "daily_briefing" in selected and _DAILY_BRIEFING_SIGNAL_RE.search(text):
        return True
    if "lists" in selected and _LIST_SHOW_SIGNAL_RE.search(text):
        return True
    return False


def _acceptance_decision(result: Mapping[str, Any], config: PiHybridProductionConfig) -> dict[str, Any]:
    pi = result.get("pi") or {}
    router = result.get("zoe_router") or {}
    safe = result.get("safe_fulfillment") or {}
    intent = _optional_str(pi.get("intent"))
    group = intent_group_for_intent(intent)
    response_text = _optional_str(safe.get("response_text")) or _optional_str(safe.get("response_preview")) or ""
    base = {"accepted": False, "intent": intent, "intent_group": group, "response_text": "", "agreement_kind": None}
    if not pi.get("ran"):
        return {**base, "reason": "pi_not_run"}
    if not intent:
        return {**base, "reason": "pi_no_intent"}
    if pi.get("timed_out"):
        return {**base, "reason": "pi_timed_out"}
    if pi.get("error"):
        return {**base, "reason": "pi_error", "error": pi.get("error")}
    if group not in set(config.groups):
        return {**base, "reason": "group_not_enabled"}
    if intent not in _SAFE_PRODUCTION_INTENTS or intent not in SAFE_FULFILLMENT_INTENTS:
        return {**base, "reason": "intent_not_safe_for_production"}
    if not safe.get("attempted") or not safe.get("allowed"):
        return {**base, "reason": safe.get("blocked_reason") or "safe_fulfillment_blocked"}
    if safe.get("timed_out"):
        return {**base, "reason": "safe_fulfillment_timed_out"}
    if safe.get("error"):
        return {**base, "reason": "safe_fulfillment_error", "error": safe.get("error")}
    if not response_text:
        return {**base, "reason": "empty_fulfillment_response"}

    router_intent = _optional_str(router.get("intent"))
    speculative_state = _optional_str(safe.get("speculative_safe_fulfillment"))
    if router_intent == intent:
        agreement_kind = "zoe_router"
    elif speculative_state == "used" and safe.get("validated_by_pi"):
        agreement_kind = "intent_buffer_hint"
    elif not config.require_agreement:
        agreement_kind = "pi_only"
    else:
        return {**base, "reason": "pi_not_agreed"}

    return {
        **base,
        "accepted": True,
        "response_text": response_text,
        "agreement_kind": agreement_kind,
        "reason": "accepted",
        "pi_latency_ms": pi.get("latency_ms"),
        "safe_fulfillment_latency_ms": safe.get("latency_ms"),
        "speculative_safe_fulfillment": speculative_state,
    }


def _compact_lab_result(result: Mapping[str, Any]) -> dict[str, Any]:
    pi = result.get("pi") or {}
    router = result.get("zoe_router") or {}
    safe = result.get("safe_fulfillment") or {}
    flow = result.get("simulated_hybrid_flow") or {}
    return {
        "zoe_router": {
            "intent": router.get("intent"),
            "route_class": router.get("route_class"),
            "baseline_kind": router.get("baseline_kind"),
            "latency_ms": router.get("latency_ms"),
        },
        "pi": {
            "intent": pi.get("intent"),
            "confidence": pi.get("confidence"),
            "intent_group": pi.get("intent_group"),
            "latency_ms": pi.get("latency_ms"),
            "transport": pi.get("transport"),
            "timed_out": pi.get("timed_out"),
            "error": pi.get("error"),
        },
        "safe_fulfillment": {
            "attempted": safe.get("attempted"),
            "allowed": safe.get("allowed"),
            "intent": safe.get("intent"),
            "latency_ms": safe.get("latency_ms"),
            "timed_out": safe.get("timed_out"),
            "error": safe.get("error"),
            "validated_by_pi": safe.get("validated_by_pi"),
            "speculative_safe_fulfillment": safe.get("speculative_safe_fulfillment"),
            "blocked_reason": safe.get("blocked_reason"),
            "response_chars": safe.get("response_chars"),
        },
        "flow": {
            "cue_available": flow.get("cue_available"),
            "cue_latency_ms": flow.get("cue_latency_ms"),
            "final_completion_latency_ms": flow.get("final_completion_latency_ms"),
        },
    }


async def processing_cue_packet(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    try:
        from voice_presence import processing_ack_event

        event = processing_ack_event(env, index=0)
    except Exception as exc:  # pragma: no cover - cue must never break chat
        return {"available": False, "text": "", "event": None, "error": exc.__class__.__name__}
    if not event:
        return {"available": False, "text": "", "event": None}
    safe = dict(event)
    audio = safe.pop("audio_base64", None)
    if audio:
        safe["audio_base64_chars"] = len(str(audio))
    return {"available": True, "text": str(safe.get("text") or ""), "event": safe}


async def _resource_pressure(config: PiHybridProductionConfig) -> dict[str, Any] | None:
    if not config.resource_guard_enabled:
        return None
    if config.min_available_mb <= 0 and config.min_swap_free_mb <= 0:
        return None
    mem = await asyncio.to_thread(_read_meminfo_mb)
    if not mem:
        return None
    available_mb = mem.get("MemAvailable")
    swap_free_mb = mem.get("SwapFree")
    blockers: list[str] = []
    if config.min_available_mb > 0 and available_mb is not None and available_mb < config.min_available_mb:
        blockers.append("available_memory_below_threshold")
    if config.min_swap_free_mb > 0 and swap_free_mb is not None and swap_free_mb < config.min_swap_free_mb:
        blockers.append("swap_free_below_threshold")
    if not blockers:
        return None
    return {
        "blockers": blockers,
        "available_mb": available_mb,
        "swap_free_mb": swap_free_mb,
        "min_available_mb": config.min_available_mb,
        "min_swap_free_mb": config.min_swap_free_mb,
    }


def _read_meminfo_mb(path: str = "/proc/meminfo") -> dict[str, int] | None:
    try:
        target = Path(path)
        if not target.is_file():
            return None
        values: dict[str, int] = {}
        for raw in target.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = raw.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":")
                if key in {"MemAvailable", "MemFree", "Buffers", "Cached", "SwapFree"}:
                    values[key] = int(parts[1]) // 1024
        if "MemAvailable" not in values and "MemFree" in values:
            values["MemAvailable"] = values["MemFree"] + values.get("Buffers", 0) + values.get("Cached", 0)
        return values
    except (OSError, ValueError, IndexError):
        return None


def _parse_groups(value: str | None) -> tuple[str, ...]:
    if not value:
        return _DEFAULT_GROUPS
    groups = [part.strip() for part in value.split(",") if part.strip()]
    return tuple(sorted(set(groups))) or _DEFAULT_GROUPS


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(value: str | None, *, default: int) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _float_env(value: str | None, *, default: float) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _record_production_evidence(
    text: str,
    *,
    user_id: str,
    decision: Mapping[str, Any],
    env: Mapping[str, str] | None,
) -> None:
    try:
        from pi_intent_evidence import record_pi_hybrid_production_evidence

        record_pi_hybrid_production_evidence(text, user_id=user_id, decision=decision, env=env)
    except Exception:
        return


__all__ = [
    "PiHybridProductionConfig",
    "pi_hybrid_production_eligible",
    "processing_cue_packet",
    "try_pi_hybrid_production",
]
