#!/usr/bin/env python3
"""Build a review queue from real Pi shadow evidence.

The queue is intentionally read-only. It helps an operator label real shadow
records so Pi promotion can move from synthetic smoke evidence to trusted
runtime evidence without dumping raw logs or guessing labels.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from pi_intent_shadow import (  # noqa: E402
    PiIntentShadowConfig,
    apply_pi_intent_shadow_labels,
    load_pi_intent_shadow_labels,
    load_pi_intent_shadow_records,
)
from zoe_pi_promotion import LOW_RISK_PI_INTENT_GROUPS, PiPromotionPolicy, intent_group_for_intent  # noqa: E402

DEFAULT_ENV_FILES = (
    ROOT / ".env",
    ROOT / "services" / "zoe-data" / ".env",
    Path("/home/zoe/assistant/.env"),
    Path("/home/zoe/assistant/services/zoe-data/.env"),
)


def load_zoe_env(env_files: Iterable[str | Path] = DEFAULT_ENV_FILES) -> dict[str, str]:
    values: dict[str, str] = {}
    for env_file in env_files:
        path = Path(env_file).expanduser()
        if path.exists():
            values.update(_parse_env_file(path))
    values.update(os.environ)
    return values


def _parse_env_file(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not key or key.startswith("#"):
            continue
        try:
            parts = shlex.split(raw_value, comments=True, posix=True)
        except ValueError:
            parts = [raw_value.strip().strip('"').strip("'")]
        parsed[key] = parts[0] if parts else ""
    return parsed


def build_label_queue(
    records: Sequence[Mapping[str, Any]],
    *,
    groups: Sequence[str] | None = None,
    include_labeled: bool = False,
    include_no_result: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    selected_groups = _selected_groups(groups)
    latest = _latest_by_text_hash(records)
    rows: list[dict[str, Any]] = []
    skipped_labeled = 0
    skipped_no_result = 0
    skipped_group = 0
    for record in latest:
        labeled = bool(record.get("outcome_label") or record.get("negative"))
        if labeled and not include_labeled:
            skipped_labeled += 1
            continue
        if record.get("pi_no_result") and not include_no_result:
            skipped_no_result += 1
            continue
        queue_row = _queue_row(record, labeled=labeled)
        if selected_groups and queue_row["intent_group"] not in selected_groups:
            skipped_group += 1
            continue
        rows.append(queue_row)
    rows.sort(key=_queue_sort_key)
    limited_rows = rows[: max(0, int(limit))]
    return {
        "summary": {
            "raw_record_count": len(records),
            "unique_text_count": len(latest),
            "queue_count": len(limited_rows),
            "available_queue_count": len(rows),
            "skipped_labeled_count": skipped_labeled,
            "skipped_no_result_count": skipped_no_result,
            "skipped_group_count": skipped_group,
            "selected_groups": selected_groups or sorted(LOW_RISK_PI_INTENT_GROUPS),
            "sample_deficit_by_group": _sample_deficit_by_group(records),
            "queue_count_by_group": _count_by_group(rows),
        },
        "queue": limited_rows,
    }


def _selected_groups(groups: Sequence[str] | None) -> list[str]:
    if not groups:
        return []
    selected: list[str] = []
    for raw in groups:
        for part in str(raw).split(","):
            group = part.strip()
            if not group:
                continue
            if group not in LOW_RISK_PI_INTENT_GROUPS and group != "chat":
                raise ValueError(f"unsupported intent group for Pi shadow labeling: {group}")
            selected.append(group)
    return sorted(set(selected))


def _latest_by_text_hash(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    by_hash: dict[str, Mapping[str, Any]] = {}
    anonymous: list[Mapping[str, Any]] = []
    for record in records:
        text_hash = _optional_str(record.get("text_hash"))
        if not text_hash:
            anonymous.append(record)
            continue
        by_hash[text_hash] = record
    keyed = sorted(by_hash.values(), key=lambda item: float(item.get("ts") or 0), reverse=True)
    anonymous.sort(key=lambda item: float(item.get("ts") or 0), reverse=True)
    return [*keyed, *anonymous]


def _queue_row(record: Mapping[str, Any], *, labeled: bool) -> dict[str, Any]:
    pi_intent = _optional_str(record.get("pi_intent"))
    zoe_intent = _optional_str(record.get("zoe_intent"))
    suggested = _suggested_label(record)
    intent_group = intent_group_for_intent(suggested) or _optional_str(record.get("pi_intent_group"))
    if not intent_group:
        intent_group = intent_group_for_intent(zoe_intent) or _optional_str(record.get("zoe_intent_group")) or "chat"
    return {
        "text_hash": _optional_str(record.get("text_hash")),
        "text_preview": _optional_str(record.get("text_preview")),
        "intent_group": intent_group,
        "suggested_outcome_label": suggested,
        "suggested_negative": suggested is None,
        "already_labeled": labeled,
        "route_class": _optional_str(record.get("route_class")) or "fallback",
        "baseline_kind": _optional_str(record.get("baseline_kind")),
        "baseline_comparable": _bool_or_none(record.get("baseline_comparable")),
        "zoe_intent": zoe_intent,
        "pi_intent": pi_intent,
        "agreement": _bool_or_none(record.get("agreement")),
        "pi_confidence": _float_or_none(record.get("pi_confidence")),
        "zoe_latency_ms": _float_or_none(record.get("zoe_latency_ms")),
        "pi_latency_ms": _float_or_none(record.get("pi_latency_ms")),
        "pi_transport": _optional_str(record.get("pi_transport")) or "rpc",
        "timed_out": bool(record.get("timed_out")),
        "pi_no_result": bool(record.get("pi_no_result")),
        "label_example": _label_example(record, suggested),
    }


def _suggested_label(record: Mapping[str, Any]) -> str | None:
    for key in ("outcome_label", "pi_intent", "zoe_intent"):
        value = _optional_str(record.get(key))
        if value and intent_group_for_intent(value) in LOW_RISK_PI_INTENT_GROUPS:
            return value
    return None


def _label_example(record: Mapping[str, Any], suggested: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "text_hash": _optional_str(record.get("text_hash")),
        "source": "admin_review",
    }
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
        row["baseline_comparable"] = _bool_or_none(record.get("baseline_comparable"))
    return row


def _queue_sort_key(row: Mapping[str, Any]) -> tuple[int, float, str]:
    low_risk = row.get("intent_group") in LOW_RISK_PI_INTENT_GROUPS
    confident_pi = row.get("suggested_outcome_label") == row.get("pi_intent")
    confidence = row.get("pi_confidence") if isinstance(row.get("pi_confidence"), (int, float)) else 0.0
    return (0 if low_risk and confident_pi else 1, -float(confidence), str(row.get("text_hash") or ""))


def _sample_deficit_by_group(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    policy = PiPromotionPolicy()
    counts = {group: 0 for group in sorted(LOW_RISK_PI_INTENT_GROUPS)}
    seen: set[str] = set()
    for record in records:
        label = _optional_str(record.get("outcome_label"))
        group = intent_group_for_intent(label)
        text_hash = _optional_str(record.get("text_hash")) or f"anonymous:{id(record)}"
        if group in counts and text_hash not in seen:
            counts[group] += 1
            seen.add(text_hash)
    return {group: max(0, policy.min_samples - count) for group, count in counts.items()}


def _count_by_group(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        group = str(row.get("intent_group") or "unmapped")
        counts[group] = counts.get(group, 0) + 1
    return dict(sorted(counts.items()))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a read-only Pi shadow labeling queue")
    parser.add_argument("--shadow-path", help="Override Pi shadow JSONL path")
    parser.add_argument("--labels-path", help="Override Pi shadow labels JSONL path")
    parser.add_argument("--env-file", action="append", default=None, help="Additional env file to load")
    parser.add_argument("--group", action="append", default=None, help="Intent group filter; may be repeated or comma-separated")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--include-labeled", action="store_true")
    parser.add_argument("--include-no-result", action="store_true")
    parser.add_argument("--format", choices=("json", "jsonl"), default="json")
    parser.add_argument("--output", "-o", help="Output path; defaults to stdout")
    args = parser.parse_args(argv)

    env_files = [*DEFAULT_ENV_FILES, *(args.env_file or [])]
    env = load_zoe_env(env_files)
    if args.shadow_path:
        env["ZOE_PI_INTENT_SHADOW_PATH"] = args.shadow_path
    if args.labels_path:
        env["ZOE_PI_INTENT_SHADOW_LABELS_PATH"] = args.labels_path
    config = PiIntentShadowConfig.from_env(env)
    records = load_pi_intent_shadow_records(config.path, limit=max(args.limit * 10, 500))
    labels = load_pi_intent_shadow_labels(config.labels_path)
    labeled_records = apply_pi_intent_shadow_labels(records, labels)
    payload = build_label_queue(
        labeled_records,
        groups=args.group,
        include_labeled=args.include_labeled,
        include_no_result=args.include_no_result,
        limit=args.limit,
    )
    text = _jsonl(payload["queue"]) if args.format == "jsonl" else json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
