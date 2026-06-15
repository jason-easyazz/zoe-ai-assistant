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
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
_URL_RE = re.compile(r"https?://\S+")
_PHONE_RE = re.compile(r"\b\d[\d\s\-]{5,}\d\b")
_NAME_RE = re.compile(r"(?<=\s)[A-Z][a-z]+ [A-Z][a-z]+\b")
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


def sanitize_evidence_text(text: str) -> str:
    clean = _EMAIL_RE.sub("[EMAIL]", str(text or ""))
    clean = _URL_RE.sub("[URL]", clean)
    clean = _PHONE_RE.sub("[NUMBER]", clean)
    clean = _NAME_RE.sub("[NAME]", clean)
    clean = _SPACE_RE.sub(" ", clean).strip()
    return clean[:160]


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


__all__ = ["record_intent_miss_evidence", "sanitize_evidence_text"]
