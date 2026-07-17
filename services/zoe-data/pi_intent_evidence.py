"""Structured evidence writers for Pi-vs-Zoe intent evaluation.

These helpers are intentionally small and fail-closed. They collect sanitized
candidate evidence only; labels still require review before promotion scoring.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Sequence

from zoe_pi_promotion import PiRouteSample, intent_group_for_intent

_DEFAULT_MISS_PATH = "~/.zoe/data/pi-intent-miss-evidence.jsonl"
_DEFAULT_PRODUCTION_PATH = "~/.zoe/data/pi-hybrid-production-evidence.jsonl"
_DEFAULT_PRODUCTION_LABELS_PATH = "~/.zoe/data/pi-hybrid-production-labels.jsonl"
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
_URL_RE = re.compile(r"https?://\S+")
_PHONE_RE = re.compile(r"\b\d[\d\s\-]{5,}\d\b")
_NAME_RE = re.compile(r"(^|\s)(?!Call\b|Email\b|Tell\b|Ask\b|Remind\b|Show\b|Set\b|Add\b|What\b|Please\b)([A-Z][a-z]+ [A-Z][a-z]+\b)")
_SPACE_RE = re.compile(r"\s+")
_SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|\btoken\b|\bsecret\b|password|authorization|\bbearer\b)")
_SECRET_TEXT_RE = re.compile(r"(?i)(api[\s_-]?key|authorization|bearer\s+[a-z0-9._\-]+|password\s*(is|=)|secret\s*(is|=)|token\s*(is|=))")
_ALLOWED_ROUTE_CLASSES = {"deterministic", "fallback", "extraction_failed"}
_ALLOWED_BASELINE_KINDS = {
    "operator_extraction_failed_override",
    "operator_fallback_override",
    "router",
    "router_extraction_failed_not_comparable",
    "router_only_not_comparable",
    "zoe_agent_extraction_failed_baseline",
    "zoe_agent_extraction_failed_error",
    "zoe_agent_extraction_failed_timeout",
    "zoe_agent_fallback_baseline",
    "zoe_agent_fallback_error",
    "zoe_agent_fallback_timeout",
}
_ALLOWED_LABEL_SOURCES = {"admin_review", "operator_override"}
_DEFAULT_PRODUCTION_RECORD_LIMIT = 1000
_DEFAULT_PRODUCTION_EVIDENCE_MAX_RECORDS = 10000


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
    preview = sanitize_evidence_text(text)
    if not preview or has_secret_evidence_text(text) or _SECRET_TEXT_RE.search(preview):
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
        "pi_intent": _optional_str(pi.get("intent")) or _optional_str(decision.get("pi_intent")),
        "pi_intent_group": _optional_str(pi.get("intent_group")) or _optional_str(decision.get("pi_intent_group")),
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
    _append_jsonl(
        values.get("ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH") or _DEFAULT_PRODUCTION_PATH,
        record,
        max_records=_positive_int_env(
            values.get("ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_MAX_RECORDS"),
            default=_DEFAULT_PRODUCTION_EVIDENCE_MAX_RECORDS,
        ),
    )
    return record


def load_pi_hybrid_production_records(
    path: str = _DEFAULT_PRODUCTION_PATH,
    *,
    limit: int = _DEFAULT_PRODUCTION_RECORD_LIMIT,
) -> list[dict[str, Any]]:
    """Load recent production Pi hybrid evidence records from JSONL."""
    target = Path(path or _DEFAULT_PRODUCTION_PATH).expanduser()
    if not target.exists():
        return []
    with target.open("r", encoding="utf-8", errors="replace") as handle:
        rows = deque(handle, maxlen=max(1, int(limit)))
    records: list[dict[str, Any]] = []
    for line in rows:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def load_pi_hybrid_production_labels(path: str = _DEFAULT_PRODUCTION_LABELS_PATH) -> dict[str, dict[str, Any]]:
    """Load latest valid append-only labels keyed by production evidence text_hash."""
    target = Path(path or _DEFAULT_PRODUCTION_LABELS_PATH).expanduser()
    if not target.exists():
        return {}
    labels: dict[str, dict[str, Any]] = {}
    with target.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, Mapping):
                continue
            text_hash = _optional_str(row.get("text_hash"))
            if not text_hash:
                continue
            label = _production_label_from_row(row)
            if label:
                labels[text_hash] = label
    return labels


def apply_pi_hybrid_production_labels(
    records: Sequence[Mapping[str, Any]], labels: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Return production records with label sidecar values applied."""
    output: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        text_hash = _optional_str(item.get("text_hash"))
        label = labels.get(text_hash or "")
        if label:
            item.update(label)
            item["outcome_label_source"] = "production_label_sidecar"
        output.append(item)
    return output


def build_pi_hybrid_production_label_queue(
    records: Sequence[Mapping[str, Any]],
    *,
    groups: Sequence[str] | None = None,
    include_labeled: bool = False,
    include_rejected: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """Build a sanitized review queue from production Pi hybrid evidence.

    The queue is read-only. Rows include label examples suitable for the
    existing production-label sidecar, but do not write labels or promote routes.
    """
    selected_groups = _selected_label_groups(groups)
    latest = _latest_production_by_text_hash(records)
    rows: list[dict[str, Any]] = []
    skipped_labeled = 0
    skipped_rejected = 0
    skipped_group = 0
    for record in latest:
        labeled = bool(record.get("outcome_label") is not None or record.get("negative"))
        accepted = bool(record.get("accepted"))
        skip_labeled = labeled and not include_labeled
        skip_rejected = not accepted and not include_rejected
        if skip_labeled:
            skipped_labeled += 1
        if skip_rejected:
            skipped_rejected += 1
        if skip_labeled or skip_rejected:
            continue
        row = _production_queue_row(record, labeled=labeled)
        if selected_groups and row["intent_group"] not in selected_groups:
            skipped_group += 1
            continue
        rows.append(row)
    rows.sort(key=_production_queue_sort_key)
    limited = rows[: max(0, int(limit))]
    return {
        "summary": {
            "raw_record_count": len(records),
            "unique_text_count": len(latest),
            "queue_count": len(limited),
            "available_queue_count": len(rows),
            "skipped_labeled_count": skipped_labeled,
            "skipped_rejected_count": skipped_rejected,
            "skipped_group_count": skipped_group,
            "selected_groups": selected_groups,
            "queue_count_by_group": _count_production_queue_by_group(rows),
        },
        "queue": limited,
    }


def _selected_label_groups(groups: Sequence[str] | None) -> list[str]:
    if not groups:
        return []
    allowed = {"chat"}
    for intent in ("weather", "daily_briefing", "list_show", "greeting", "time_query", "date_query", "calculate", "timer_create"):
        group = intent_group_for_intent(intent)
        if group:
            allowed.add(group)
    selected: list[str] = []
    for raw in groups:
        for part in str(raw).split(","):
            group = part.strip()
            if not group:
                continue
            if group not in allowed:
                raise ValueError(f"unsupported production label group: {group}")
            selected.append(group)
    return sorted(set(selected))


def _latest_production_by_text_hash(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    by_hash: dict[str, Mapping[str, Any]] = {}
    anonymous: list[Mapping[str, Any]] = []
    for record in records:
        text_hash = _optional_str(record.get("text_hash"))
        if not text_hash:
            anonymous.append(record)
            continue
        existing = by_hash.get(text_hash)
        if existing is None or _record_ts(record) >= _record_ts(existing):
            by_hash[text_hash] = record
    keyed = sorted(by_hash.values(), key=_record_ts, reverse=True)
    anonymous.sort(key=_record_ts, reverse=True)
    return [*keyed, *anonymous]


def _record_ts(record: Mapping[str, Any]) -> float:
    try:
        return float(record.get("ts") or 0)
    except (TypeError, ValueError):
        return 0.0


def _production_queue_row(record: Mapping[str, Any], *, labeled: bool) -> dict[str, Any]:
    suggested = _suggested_production_label(record)
    group = intent_group_for_intent(suggested) or _optional_str(record.get("intent_group"))
    if not group:
        group = intent_group_for_intent(_optional_str(record.get("pi_intent"))) or "chat"
    return {
        "text_hash": _optional_str(record.get("text_hash")),
        "text_preview": _optional_str(record.get("text_preview")),
        "intent_group": group,
        "suggested_outcome_label": suggested,
        "suggested_negative": suggested is None,
        "already_labeled": labeled,
        "accepted": bool(record.get("accepted")),
        "reason": _optional_str(record.get("reason")),
        "intent": _optional_str(record.get("intent")),
        "pi_intent": _optional_str(record.get("pi_intent")),
        "agreement_kind": _optional_str(record.get("agreement_kind")),
        "route_class": _optional_str(record.get("route_class")) or "fallback",
        "baseline_kind": _optional_str(record.get("baseline_kind")),
        "baseline_comparable": _bool_or_none(record.get("baseline_comparable")),
        "zoe_latency_ms": _float_or_none(record.get("zoe_latency_ms")),
        "pi_latency_ms": _float_or_none(record.get("pi_latency_ms")),
        "safe_fulfillment_latency_ms": _float_or_none(record.get("safe_fulfillment_latency_ms")),
        "production_route_change": _bool_record_value(record.get("production_route_change")),
        "label_example": _production_label_example(record, suggested),
    }


def _suggested_production_label(record: Mapping[str, Any]) -> str | None:
    for key in ("outcome_label", "intent", "pi_intent", "safe_fulfillment_intent"):
        value = _optional_str(record.get(key))
        if value and intent_group_for_intent(value):
            return value
    return None


def _production_label_example(record: Mapping[str, Any], suggested: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {"text_hash": _optional_str(record.get("text_hash")), "source": "admin_review"}
    if suggested:
        row["outcome_label"] = suggested
    else:
        row["negative"] = True
    route_class = _optional_str(record.get("route_class"))
    if route_class:
        row["route_class"] = route_class
    baseline_kind = _optional_str(record.get("baseline_kind"))
    if baseline_kind:
        row["baseline_kind"] = baseline_kind
    if record.get("baseline_comparable") is not None:
        row["baseline_comparable"] = _bool_record_value(record.get("baseline_comparable"))
    if record.get("zoe_latency_ms") is not None:
        row["zoe_latency_ms"] = _float_or_none(record.get("zoe_latency_ms"))
    return row


def _production_queue_sort_key(row: Mapping[str, Any]) -> tuple[int, float, str]:
    low_risk = bool(intent_group_for_intent(_optional_str(row.get("suggested_outcome_label"))))
    accepted = bool(row.get("accepted"))
    latency = row.get("pi_latency_ms") if isinstance(row.get("pi_latency_ms"), (int, float)) else 999999.0
    return (0 if accepted and low_risk else 1, float(latency), str(row.get("text_hash") or ""))


def _count_production_queue_by_group(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        group = str(row.get("intent_group") or "unmapped")
        counts[group] = counts.get(group, 0) + 1
    return dict(sorted(counts.items()))


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return _bool_record_value(value)


def append_pi_hybrid_production_label(
    *,
    text_hash: str,
    outcome_label: str | None = None,
    negative: bool = False,
    source: str = "admin_review",
    reviewed_by: str | None = None,
    route_class: str | None = None,
    baseline_kind: str | None = None,
    baseline_comparable: bool | None = None,
    zoe_latency_ms: float | None = None,
    evidence_path: str = _DEFAULT_PRODUCTION_PATH,
    labels_path: str = _DEFAULT_PRODUCTION_LABELS_PATH,
    production_limit: int = _DEFAULT_PRODUCTION_RECORD_LIMIT,
) -> dict[str, Any]:
    """Append one trusted admin label for an existing production evidence record."""
    normalized_hash = str(text_hash or "").strip()
    if not normalized_hash:
        raise ValueError("text_hash is required")
    records = load_pi_hybrid_production_records(evidence_path, limit=production_limit)
    matching_record = next(
        (record for record in records if str(record.get("text_hash") or "").strip() == normalized_hash),
        None,
    )
    if matching_record is None:
        raise ValueError(f"text_hash not found in the most-recent {production_limit} Pi hybrid production records")

    source_value = str(source or "admin_review").strip() or "admin_review"
    if source_value not in _ALLOWED_LABEL_SOURCES:
        raise ValueError("source must be one of: admin_review, operator_override")

    row: dict[str, Any] = {
        "text_hash": normalized_hash,
        "source": source_value,
        "labeled_at": time.time(),
    }
    if outcome_label is not None:
        row["outcome_label"] = str(outcome_label).strip()
    if negative:
        row["negative"] = True
    if reviewed_by:
        row["reviewed_by_hash"] = _hash_text(reviewed_by)
    if route_class is not None:
        row["route_class"] = route_class
    if baseline_kind is not None:
        row["baseline_kind"] = baseline_kind
    if baseline_comparable is not None:
        row["baseline_comparable"] = bool(baseline_comparable)
    if zoe_latency_ms is not None:
        row["zoe_latency_ms"] = zoe_latency_ms

    label = _production_label_from_row(row)
    if not label:
        raise ValueError("label must be a low-risk Pi intent or a negative chat/none label")

    _append_jsonl(labels_path, row)
    return {
        "ok": True,
        "text_hash": normalized_hash,
        "labels_store": "production_labels_sidecar",
        "label": label,
        "matched_record": _compact_production_record_for_label_response(matching_record),
    }


def production_records_to_route_samples(records: Sequence[Mapping[str, Any]]) -> list[PiRouteSample]:
    """Convert reviewed production Pi hybrid evidence into promotion samples.

    Only positive reviewed labels can become promotion samples. Negative labels
    are useful review evidence, but PiRouteSample is scoped to allowlisted intent
    groups rather than open chat/no-intent cases.
    """
    samples: list[PiRouteSample] = []
    for index, record in enumerate(records):
        expected_intent = _optional_str(record.get("outcome_label"))
        intent_group = intent_group_for_intent(expected_intent)
        if not expected_intent or not intent_group:
            continue
        zoe_latency_ms = _float_or_none(record.get("zoe_latency_ms"))
        pi_latency_ms = _float_or_none(record.get("pi_latency_ms"))
        if zoe_latency_ms is None or pi_latency_ms is None:
            continue
        route_class = _optional_str(record.get("route_class")) or "fallback"
        baseline_kind, baseline_comparable = _production_baseline_metadata(record, route_class=route_class)
        try:
            samples.append(
                PiRouteSample(
                    case_id=_optional_str(record.get("text_hash")) or f"pi_hybrid_production_{index}",
                    intent_group=intent_group,
                    expected_intent=expected_intent,
                    zoe_intent=_optional_str(record.get("zoe_intent")),
                    pi_intent=_optional_str(record.get("pi_intent")),
                    zoe_latency_ms=zoe_latency_ms,
                    pi_latency_ms=pi_latency_ms,
                    pi_confidence=_float_or_none(record.get("pi_confidence")) or 0.0,
                    pi_transport=_optional_str(record.get("pi_transport")) or "rpc",
                    route_class=route_class,
                    timed_out=_bool_record_value(record.get("safe_fulfillment_timed_out"))
                    or _optional_str(record.get("reason")) == "timeout",
                    user_corrected=_bool_record_value(record.get("user_corrected")),
                    rollback_blocked=_bool_record_value(record.get("rollback_blocked")),
                    metadata={
                        "source": "pi_hybrid_production",
                        "text_hash": _optional_str(record.get("text_hash")) or "",
                        "baseline_kind": baseline_kind,
                        "baseline_comparable": baseline_comparable,
                        "safe_fulfillment_latency_ms": _float_or_none(record.get("safe_fulfillment_latency_ms")),
                        "production_route_change": _bool_record_value(record.get("production_route_change")),
                        "accepted": _bool_record_value(record.get("accepted")),
                        "outcome_label_source": _optional_str(record.get("outcome_label_source")),
                    },
                )
            )
        except (TypeError, ValueError):
            continue
    return samples


def _production_baseline_metadata(record: Mapping[str, Any], *, route_class: str) -> tuple[str, bool]:
    baseline_kind = _optional_str(record.get("baseline_kind"))
    if not baseline_kind:
        baseline_kind = "router" if route_class == "deterministic" else "router_only_not_comparable"
    if record.get("baseline_comparable") is not None:
        baseline_comparable = _bool_record_value(record.get("baseline_comparable"))
    else:
        baseline_comparable = baseline_kind not in {
            "router_only_not_comparable",
            "router_extraction_failed_not_comparable",
        }
    return baseline_kind, baseline_comparable

def _production_label_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    label: dict[str, Any] = {}
    negative = _bool_record_value(row.get("negative"))
    outcome_label = _optional_str(row.get("outcome_label") or row.get("expected_intent") or row.get("label"))
    if negative and outcome_label and outcome_label not in {"chat", "none", "no_intent"}:
        return {}
    if negative or outcome_label in {"chat", "none", "no_intent"}:
        label["negative"] = True
        label["outcome_label"] = None
    elif outcome_label and intent_group_for_intent(outcome_label):
        label["outcome_label"] = outcome_label
    else:
        return {}
    for key in ("route_class", "baseline_kind", "source"):
        value = _optional_str(row.get(key))
        if value:
            if key == "route_class" and value not in _ALLOWED_ROUTE_CLASSES:
                return {}
            if key == "baseline_kind" and value not in _ALLOWED_BASELINE_KINDS:
                return {}
            label[key] = value
    for key in ("baseline_comparable", "user_corrected", "rollback_blocked"):
        if row.get(key) is not None:
            label[key] = _bool_record_value(row.get(key))
    if row.get("zoe_latency_ms") is not None:
        latency_ms = _float_or_none(row.get("zoe_latency_ms"))
        if latency_ms is None or latency_ms < 0:
            return {}
        label["zoe_latency_ms"] = latency_ms
    return label


def _compact_production_record_for_label_response(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "text_hash": _optional_str(record.get("text_hash")),
        "text_preview": _optional_str(record.get("text_preview")),
        "accepted": bool(record.get("accepted")),
        "reason": _optional_str(record.get("reason")),
        "intent": _optional_str(record.get("intent")),
        "pi_intent": _optional_str(record.get("pi_intent")),
        "intent_group": _optional_str(record.get("intent_group")),
    }


def _bool_record_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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


def _append_jsonl(path: str, record: Mapping[str, Any], *, max_records: int | None = None) -> None:
    _reject_secret_keys(record)
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    # Append + prune must be ONE critical section. `_prune_jsonl` is a
    # read-modify-write (read every row, seek(0), rewrite, truncate), so an
    # unlocked concurrent writer loses records: writer B appends, B's prune
    # reads the rows, writer A appends, then B's truncate rewrites only the
    # rows B read — A's record is gone. Real callers hit this (an awaited
    # accepted-decision write racing a fire-and-forget Pi audit write, both via
    # `asyncio.to_thread`, in `pi_hybrid_production`). An flock on the target
    # serializes writers across threads AND processes (multiple zoe-data
    # workers share the evidence path); `_prune_jsonl` deliberately uses a
    # separate unlocked handle, so it cannot self-deadlock against this hold.
    with target.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(line)
            handle.flush()
            if max_records is not None and max_records > 0:
                _prune_jsonl(target, max_records=max_records)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _prune_jsonl(target: Path, *, max_records: int) -> None:
    """Trim `target` to its last `max_records` lines.

    Callers MUST already hold the `_append_jsonl` flock on `target`: this is a
    read-modify-write and is not safe to run concurrently with any other writer.
    It intentionally does not take the lock itself — the caller holds it on a
    different open file description, and flock would block against that hold.
    """
    try:
        with target.open("r+", encoding="utf-8", errors="replace") as handle:
            rows = deque(handle, maxlen=max_records)
            handle.seek(0)
            handle.writelines(rows)
            handle.truncate()
    except FileNotFoundError:
        return


def _positive_int_env(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


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


__all__ = [
    "append_pi_hybrid_production_label",
    "build_pi_hybrid_production_label_queue",
    "apply_pi_hybrid_production_labels",
    "has_secret_evidence_text",
    "load_pi_hybrid_production_labels",
    "load_pi_hybrid_production_records",
    "production_records_to_route_samples",
    "record_intent_miss_evidence",
    "record_pi_hybrid_production_evidence",
    "sanitize_evidence_text",
]
