"""Structured evidence writers for Pi-vs-Zoe intent evaluation.

These helpers are intentionally small and fail-closed. They collect sanitized
candidate evidence only; labels still require review before promotion scoring.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping

_DEFAULT_MISS_PATH = "~/.zoe/data/pi-intent-miss-evidence.jsonl"
_DEFAULT_PRODUCTION_PATH = "~/.zoe/data/pi-hybrid-production-evidence.jsonl"
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
_URL_RE = re.compile(r"https?://\S+")
_PHONE_RE = re.compile(r"\b\d[\d\s\-]{5,}\d\b")
_NAME_RE = re.compile(r"(^|\s)(?!Call\b|Email\b|Tell\b|Ask\b|Remind\b|Show\b|Set\b|Add\b|What\b|Please\b)([A-Z][a-z]+ [A-Z][a-z]+\b)")
_SPACE_RE = re.compile(r"\s+")
_SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|\btoken\b|\bsecret\b|password|authorization|\bbearer\b)")
_SECRET_TEXT_RE = re.compile(r"(?i)(api[\s_-]?key|authorization|bearer\s+[a-z0-9._\-]+|password\s*(is|=)|secret\s*(is|=)|token\s*(is|=))")
_ALLOWED_ROUTE_CLASSES = {"deterministic", "fallback", "extraction_failed"}


def record_intent_miss_evidence(
    text: str,
    *,
    route_class: str = "fallback",
    user_id: str = "unknown",
    env: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    values = env if env is not None else os.environ
    if not _env_bool(values.get("ZOE_PI_INTENT_MISS_EVIDENCE_ENABLED"), default=False):
        return None
    preview = sanitize_evidence_text(text)
    if not preview or _SECRET_TEXT_RE.search(str(text or "")) or _SECRET_TEXT_RE.search(preview):
        return None
    active_route = route_class if route_class in _ALLOWED_ROUTE_CLASSES else "fallback"
    record = {
        "ts": time.time(),
        "source": "intent_miss",
        "route_class": active_route,
        "text": preview,
        "text_hash": _hash_text(text),
        "user_hash": _hash_text(user_id or "unknown")[:16],
        "expected_intent": None,
        "outcome_label": None,
        "negative": False,
    }
    _append_jsonl(values.get("ZOE_PI_INTENT_MISS_EVIDENCE_PATH") or _DEFAULT_MISS_PATH, record)
    return record


def record_pi_hybrid_production_evidence(
    text: str,
    *,
    user_id: str,
    decision: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Record one sanitized production Pi hybrid decision for later scoring."""
    values = env if env is not None else os.environ
    if not _env_bool(values.get("ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED"), default=False):
        return None
    if has_secret_evidence_text(text):
        return None

    lab = decision.get("lab_result") if isinstance(decision.get("lab_result"), Mapping) else {}
    pi = lab.get("pi") if isinstance(lab.get("pi"), Mapping) else {}
    router = lab.get("zoe_router") if isinstance(lab.get("zoe_router"), Mapping) else {}
    safe = lab.get("safe_fulfillment") if isinstance(lab.get("safe_fulfillment"), Mapping) else {}
    config = decision.get("config") if isinstance(decision.get("config"), Mapping) else {}
    include_preview = _env_bool(values.get("ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_INCLUDE_PREVIEW"), default=True)

    record = {
        "ts": time.time(),
        "source": "pi_hybrid_production",
        "text_hash": _hash_text(text),
        "text_preview": sanitize_evidence_text(text) if include_preview else None,
        "user_hash": _hash_text(user_id or "unknown")[:16],
        "accepted": bool(decision.get("accepted")),
        "reason": _optional_str(decision.get("reason")),
        "production_route_change": bool(decision.get("production_route_change")),
        "intent": _optional_str(decision.get("intent")),
        "intent_group": _optional_str(decision.get("intent_group")),
        "agreement_kind": _optional_str(decision.get("agreement_kind")),
        "pi_intent": _optional_str(pi.get("intent")),
        "pi_intent_group": _optional_str(pi.get("intent_group")),
        "pi_confidence": _float_or_none(pi.get("confidence")),
        "pi_latency_ms": _float_or_none(pi.get("latency_ms")),
        "pi_transport": _optional_str(pi.get("transport")) or _optional_str(config.get("transport")),
        "zoe_intent": _optional_str(router.get("intent")),
        "route_class": _optional_str(router.get("route_class")),
        "baseline_kind": _optional_str(router.get("baseline_kind")),
        "zoe_latency_ms": _float_or_none(router.get("latency_ms")),
        "safe_fulfillment_intent": _optional_str(safe.get("intent")),
        "safe_fulfillment_latency_ms": _float_or_none(safe.get("latency_ms")),
        "safe_fulfillment_timed_out": bool(safe.get("timed_out")),
        "safe_fulfillment_error": _optional_str(safe.get("error")),
        "response_chars": _int_or_none(safe.get("response_chars")),
        "enabled_groups": list(config.get("groups") or ()),
        "outcome_label": None,
    }
    _append_jsonl(values.get("ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH") or _DEFAULT_PRODUCTION_PATH, record)
    return record


def sanitize_evidence_text(text: str) -> str:
    clean = _EMAIL_RE.sub("[EMAIL]", str(text or ""))
    clean = _URL_RE.sub("[URL]", clean)
    clean = _PHONE_RE.sub("[NUMBER]", clean)
    clean = _NAME_RE.sub(lambda match: f"{match.group(1)}[NAME]", clean)
    clean = _SPACE_RE.sub(" ", clean).strip()
    return clean[:160]


def has_secret_evidence_text(text: str) -> bool:
    return bool(_SECRET_TEXT_RE.search(str(text or "")))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _append_jsonl(path: str, record: Mapping[str, Any]) -> None:
    _reject_secret_keys(record)
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def _reject_secret_keys(value: Mapping[str, Any], path: str = "") -> None:
    for key, nested in value.items():
        full_key = f"{path}.{key}" if path else str(key)
        if _SECRET_KEY_RE.search(str(key)):
            raise ValueError(f"intent evidence may not contain secret field {full_key}")
        if isinstance(nested, Mapping):
            _reject_secret_keys(nested, full_key)


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def _env_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["has_secret_evidence_text", "record_intent_miss_evidence", "record_pi_hybrid_production_evidence", "sanitize_evidence_text"]
