"""Runtime shadow evidence for Pi-vs-Zoe intent routing.

Shadow mode is evidence-only: Zoe's current route remains authoritative and Pi
output is never executed from this module. Records are intentionally compact,
sanitized, and JSONL-backed so the first runtime loop can be observed without a
schema migration or production chat behavior change.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from zoe_pi_promotion import (
    LOW_RISK_PI_INTENT_GROUPS,
    PiPromotionPolicy,
    PiRouteSample,
    intent_group_for_intent,
    summarize_pi_promotion as summarize_pi_route_promotion,
)

_DEFAULT_SHADOW_PATH = "~/.zoe/data/pi-intent-shadow.jsonl"
_UNSET = object()
_DEFAULT_MAX_REPORT_RECORDS = 500
_SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|\btoken\b|\bsecret\b|password|authorization|\bbearer\b)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
_URL_RE = re.compile(r"https?://\S+")
_PHONE_RE = re.compile(r"\b\d[\d\s\-]{6,}\b")
_NAME_RE = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class PiIntentShadowConfig:
    enabled: bool = False
    path: str = _DEFAULT_SHADOW_PATH
    max_words: int = 32
    include_preview: bool = True
    force_classifier_enabled: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PiIntentShadowConfig":
        values = env if env is not None else os.environ
        return cls(
            enabled=_env_bool(values.get("ZOE_PI_INTENT_SHADOW_ENABLED"), default=False),
            path=(values.get("ZOE_PI_INTENT_SHADOW_PATH") or _DEFAULT_SHADOW_PATH).strip() or _DEFAULT_SHADOW_PATH,
            max_words=_int_env(values.get("ZOE_PI_INTENT_SHADOW_MAX_WORDS"), default=32),
            include_preview=_env_bool(values.get("ZOE_PI_INTENT_SHADOW_INCLUDE_PREVIEW"), default=True),
            force_classifier_enabled=_env_bool(values.get("ZOE_PI_INTENT_SHADOW_FORCE_ENABLED"), default=True),
        )

    def validate(self) -> None:
        if self.max_words <= 0:
            raise ValueError("ZOE_PI_INTENT_SHADOW_MAX_WORDS must be positive")
        if not self.path:
            raise ValueError("ZOE_PI_INTENT_SHADOW_PATH is required")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "enabled": self.enabled,
            "path": self.path,
            "max_words": self.max_words,
            "include_preview": self.include_preview,
            "force_classifier_enabled": self.force_classifier_enabled,
        }


async def maybe_record_pi_intent_shadow(
    text: str,
    *,
    zoe_intent: str | None,
    zoe_confidence: float | None = None,
    zoe_latency_ms: float | None = None,
    route_class: str,
    user_id: str = "unknown",
    context_turns: str = "",
    env: Mapping[str, str] | None = None,
    config: PiIntentShadowConfig | None = None,
    pi_result: Any = _UNSET,
) -> dict[str, Any] | None:
    active_config = config or PiIntentShadowConfig.from_env(env)
    active_config.validate()
    if not active_config.enabled:
        return None
    if len((text or "").split()) > active_config.max_words:
        return None

    runtime_env = dict(os.environ if env is None else env)
    if active_config.force_classifier_enabled:
        runtime_env["ZOE_PI_INTENT_ENABLED"] = "true"

    started = time.perf_counter()
    error: str | None = None
    if pi_result is _UNSET:
        pi_result = None
        try:
            from pi_intent_classifier import classify_with_pi_intent_governor

            pi_result = await classify_with_pi_intent_governor(text, context_turns=context_turns, env=runtime_env)
        except Exception as exc:  # pragma: no cover - defensive: shadow must never break routing
            error = type(exc).__name__
    elapsed_ms = (time.perf_counter() - started) * 1000

    pi_intent = pi_result.intent if pi_result else None
    pi_confidence = float(pi_result.confidence) if pi_result else 0.0
    pi_latency_ms = float(pi_result.latency_ms) if pi_result else elapsed_ms
    timeout_seconds = _float_env(runtime_env.get("ZOE_PI_INTENT_TIMEOUT_SECONDS"), default=4.0)
    timed_out = pi_result is None and elapsed_ms >= (timeout_seconds * 1000 * 0.95)
    record = {
        "ts": time.time(),
        "text_hash": _hash_text(text),
        "text_preview": _sanitize_text(text) if active_config.include_preview else None,
        "user_hash": _hash_text(user_id or "unknown")[:16],
        "route_class": route_class,
        "zoe_intent": zoe_intent,
        "zoe_intent_group": intent_group_for_intent(zoe_intent),
        "zoe_confidence": zoe_confidence,
        "zoe_latency_ms": zoe_latency_ms,
        "pi_intent": pi_intent,
        "pi_intent_group": intent_group_for_intent(pi_intent),
        "pi_confidence": pi_confidence,
        "pi_latency_ms": pi_latency_ms,
        "pi_transport": _safe_env_value(runtime_env, "ZOE_PI_INTENT_TRANSPORT") or "print",
        "agreement": pi_intent == zoe_intent,
        "pi_no_result": pi_result is None,
        "timed_out": timed_out,
        "error": error,
        "outcome_label": None,
    }
    _append_jsonl(active_config.path, record)
    return record


def pi_intent_shadow_status(env: Mapping[str, str] | None = None, *, limit: int = _DEFAULT_MAX_REPORT_RECORDS) -> dict[str, Any]:
    values = env if env is not None else os.environ
    config = PiIntentShadowConfig.from_env(values)
    records = load_pi_intent_shadow_records(config.path, limit=limit)
    samples = shadow_records_to_route_samples(records)
    requested_promoted_groups = _csv_env(values.get("ZOE_PI_INTENT_PROMOTED_GROUPS"))
    promoted_groups = [group for group in requested_promoted_groups if group in LOW_RISK_PI_INTENT_GROUPS]
    ignored_promoted_groups = [group for group in requested_promoted_groups if group not in LOW_RISK_PI_INTENT_GROUPS]
    return {
        "config": config.to_dict(),
        "record_count_window": len(records),
        "report": summarize_pi_intent_shadow(records),
        "promotion_report": summarize_pi_route_promotion(samples, promoted_groups=promoted_groups),
        "ignored_promoted_groups": ignored_promoted_groups,
    }


def load_pi_intent_shadow_records(path: str, *, limit: int = _DEFAULT_MAX_REPORT_RECORDS) -> list[dict[str, Any]]:
    target = Path(path).expanduser()
    if not target.exists():
        return []
    with target.open("r", encoding="utf-8", errors="replace") as handle:
        rows = deque(handle, maxlen=max(1, limit))
    records: list[dict[str, Any]] = []
    for line in rows:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def shadow_records_to_route_samples(records: Sequence[Mapping[str, Any]]) -> list[PiRouteSample]:
    samples: list[PiRouteSample] = []
    for index, record in enumerate(records):
        expected_intent = record.get("outcome_label")
        intent_group = intent_group_for_intent(str(expected_intent) if expected_intent else None)
        if not expected_intent or not intent_group:
            continue
        zoe_latency_ms = _optional_float_value(record.get("zoe_latency_ms"))
        pi_latency_ms = _optional_float_value(record.get("pi_latency_ms"))
        if zoe_latency_ms is None or pi_latency_ms is None:
            continue
        try:
            samples.append(
                PiRouteSample(
                    case_id=str(record.get("text_hash") or f"shadow_{index}"),
                    intent_group=intent_group,
                    expected_intent=str(expected_intent),
                    zoe_intent=_optional_str(record.get("zoe_intent")),
                    pi_intent=_optional_str(record.get("pi_intent")),
                    zoe_latency_ms=zoe_latency_ms,
                    pi_latency_ms=pi_latency_ms,
                    pi_confidence=_float_value(record.get("pi_confidence"), default=0.0),
                    pi_transport=str(record.get("pi_transport") or "rpc"),
                    route_class=str(record.get("route_class") or "fallback"),
                    timed_out=bool(record.get("timed_out")),
                    metadata={"source": "pi_intent_shadow", "text_hash": str(record.get("text_hash") or "")},
                )
            )
        except (TypeError, ValueError):
            continue
    return samples


def summarize_pi_intent_shadow(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    if total == 0:
        return {
            "sample_count": 0,
            "agreement_rate": 0.0,
            "timeout_rate": 0.0,
            "no_result_rate": 0.0,
            "avg_zoe_latency_ms": None,
            "avg_pi_latency_ms": None,
            "intent_groups": {},
            "accuracy_available": False,
            "labeled_sample_count": 0,
            "promotion_ready": False,
            "promotion_ready_reason": "runtime shadow records are unlabeled agreement evidence, not accuracy labels",
        }
    groups: dict[str, dict[str, int]] = {}
    agreements = 0
    timeouts = 0
    no_results = 0
    zoe_latencies: list[float] = []
    pi_latencies: list[float] = []
    for record in records:
        group = str(record.get("zoe_intent_group") or record.get("pi_intent_group") or "unmapped")
        bucket = groups.setdefault(group, {"samples": 0, "agreements": 0, "timeouts": 0})
        bucket["samples"] += 1
        if record.get("agreement"):
            agreements += 1
            bucket["agreements"] += 1
        if record.get("timed_out"):
            timeouts += 1
            bucket["timeouts"] += 1
        if record.get("pi_no_result"):
            no_results += 1
        if isinstance(record.get("zoe_latency_ms"), (int, float)):
            zoe_latencies.append(float(record["zoe_latency_ms"]))
        if isinstance(record.get("pi_latency_ms"), (int, float)):
            pi_latencies.append(float(record["pi_latency_ms"]))
    labeled_sample_count = sum(1 for record in records if record.get("outcome_label"))
    return {
        "sample_count": total,
        "agreement_rate": agreements / total,
        "timeout_rate": timeouts / total,
        "no_result_rate": no_results / total,
        "avg_zoe_latency_ms": _avg(zoe_latencies),
        "avg_pi_latency_ms": _avg(pi_latencies),
        "intent_groups": groups,
        "accuracy_available": labeled_sample_count > 0,
        "labeled_sample_count": labeled_sample_count,
        "promotion_ready": labeled_sample_count >= PiPromotionPolicy().min_samples,
        "promotion_ready_reason": (
            "labeled outcome evidence is available for promotion scoring"
            if labeled_sample_count
            else "runtime shadow records are unlabeled agreement evidence, not accuracy labels"
        ),
    }


def _append_jsonl(path: str, record: Mapping[str, Any]) -> None:
    _reject_secret_keys(record)
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def _sanitize_text(text: str) -> str:
    clean = _EMAIL_RE.sub("[EMAIL]", text or "")
    clean = _URL_RE.sub("[URL]", clean)
    clean = _PHONE_RE.sub("[NUMBER]", clean)
    clean = _NAME_RE.sub("[NAME]", clean)
    clean = _SPACE_RE.sub(" ", clean).strip()
    return clean[:160]


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def _avg(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _float_value(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _csv_env(value: str | None) -> list[str]:
    if not value:
        return []
    return sorted({item.strip() for item in value.split(",") if item.strip()})


def _safe_env_value(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if not value or _SECRET_KEY_RE.search(key):
        return None
    return value


def _reject_secret_keys(value: Mapping[str, Any], path: str = "") -> None:
    for key, nested in value.items():
        full_key = f"{path}.{key}" if path else str(key)
        if _SECRET_KEY_RE.search(str(key)):
            raise ValueError(f"shadow record may not contain secret field {full_key}")
        if isinstance(nested, Mapping):
            _reject_secret_keys(nested, full_key)


def _float_env(value: str | None, *, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _int_env(value: str | None, *, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _env_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "PiIntentShadowConfig",
    "load_pi_intent_shadow_records",
    "maybe_record_pi_intent_shadow",
    "pi_intent_shadow_status",
    "shadow_records_to_route_samples",
    "summarize_pi_intent_shadow",
]
