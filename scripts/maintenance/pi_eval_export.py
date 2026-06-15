#!/usr/bin/env python3
"""Export sanitized Pi intent eval cases from JSONL evidence.

Input rows may be Pi shadow records or sanitized chat/voice evidence rows. Rows
without a trusted label are skipped by default so promotion data remains accuracy
evidence, not guesswork.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from zoe_pi_promotion import PiIntentEvalCase, intent_group_for_intent, merge_pi_intent_eval_cases  # noqa: E402

_ALLOWED_SOURCES = {"intent_miss", "chat_log", "voice_log", "known_failure", "synthetic"}
_ALLOWED_ROUTE_CLASSES = {"deterministic", "fallback", "extraction_failed"}
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
_URL_RE = re.compile(r"https?://\S+")
_PHONE_RE = re.compile(r"\b\d[\d\s\-]{6,}\b")
_NAME_RE = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b")
_SPACE_RE = re.compile(r"\s+")
_SECRET_TEXT_RE = re.compile(r"(?i)(api[\s_-]?key|authorization|bearer\s+[a-z0-9._\-]+|password\s*(is|=)|secret\s*(is|=)|token\s*(is|=))")


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    rows: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{target}:{line_number}: invalid JSONL row: {exc.msg}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"{target}:{line_number}: JSONL row must be an object")
            rows.append(payload)
    return rows


def export_eval_cases(
    rows: Sequence[Mapping[str, Any]],
    *,
    source: str,
    case_prefix: str = "evidence",
    default_route_class: str = "fallback",
    max_words: int = 32,
) -> list[PiIntentEvalCase]:
    if source not in _ALLOWED_SOURCES:
        raise ValueError(f"unsupported source {source!r}")
    cases: list[PiIntentEvalCase] = []
    for index, row in enumerate(rows, start=1):
        case = _case_from_row(
            row,
            index=index,
            source=source,
            case_prefix=case_prefix,
            default_route_class=default_route_class,
            max_words=max_words,
        )
        if case is not None:
            cases.append(case)
    return merge_pi_intent_eval_cases(cases)


def _case_from_row(
    row: Mapping[str, Any],
    *,
    index: int,
    source: str,
    case_prefix: str,
    default_route_class: str,
    max_words: int,
) -> PiIntentEvalCase | None:
    raw_text = _first_text(row, ("text_preview", "text", "transcript", "message", "utterance"))
    if not raw_text:
        return None
    if _SECRET_TEXT_RE.search(raw_text) or len(str(raw_text).split()) > max_words:
        return None
    text = sanitize_eval_text(raw_text)
    if not text or _SECRET_TEXT_RE.search(text):
        return None

    expected_intent = _optional_str(row.get("expected_intent") or row.get("outcome_label") or row.get("label"))
    negative = _bool_value(row.get("negative", False)) or expected_intent in {"chat", "none", "no_intent"}
    if negative:
        expected_intent = None
        intent_group = "chat"
    else:
        intent_group = intent_group_for_intent(expected_intent)
        if not expected_intent or not intent_group:
            return None

    route_class = _optional_str(row.get("route_class")) or default_route_class
    if route_class not in _ALLOWED_ROUTE_CLASSES:
        route_class = default_route_class
    row_source = _optional_str(row.get("source")) or source
    if row_source not in _ALLOWED_SOURCES:
        row_source = source
    case_id = _case_id(row, text=text, case_prefix=case_prefix, index=index)
    try:
        return PiIntentEvalCase(
            case_id=case_id,
            text=text,
            expected_intent=expected_intent,
            intent_group=intent_group,
            route_class=route_class,
            source=row_source,
            negative=negative,
        )
    except TypeError:
        return None


def sanitize_eval_text(text: str) -> str:
    clean = _EMAIL_RE.sub("[EMAIL]", str(text or ""))
    clean = _URL_RE.sub("[URL]", clean)
    clean = _PHONE_RE.sub("[NUMBER]", clean)
    clean = _NAME_RE.sub("[NAME]", clean)
    clean = _SPACE_RE.sub(" ", clean).strip()
    return clean[:160]


def cases_to_jsonl(cases: Sequence[PiIntentEvalCase]) -> str:
    return "".join(json.dumps(case.to_dict(), sort_keys=True, separators=(",", ":")) + "\n" for case in cases)


def _case_id(row: Mapping[str, Any], *, text: str, case_prefix: str, index: int) -> str:
    existing = _optional_str(row.get("case_id"))
    if existing:
        return _safe_id(existing)
    text_hash = _optional_str(row.get("text_hash"))
    if text_hash:
        return _safe_id(f"{case_prefix}_{text_hash[:12]}")
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return _safe_id(f"{case_prefix}_{index}_{digest}")


def _first_text(row: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", value.strip())
    return safe[:96] or "evidence_case"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export sanitized Pi intent eval JSONL from evidence JSONL")
    parser.add_argument("input", help="Input JSONL evidence file")
    parser.add_argument("--output", "-o", help="Output JSONL path; defaults to stdout")
    parser.add_argument("--source", choices=sorted(_ALLOWED_SOURCES), default="intent_miss")
    parser.add_argument("--case-prefix", default="evidence")
    parser.add_argument("--route-class", choices=sorted(_ALLOWED_ROUTE_CLASSES), default="fallback")
    parser.add_argument("--max-words", type=int, default=32)
    parser.add_argument("--summary", action="store_true", help="Write summary JSON to stderr")
    args = parser.parse_args(argv)

    rows = load_jsonl(args.input)
    cases = export_eval_cases(
        rows,
        source=args.source,
        case_prefix=args.case_prefix,
        default_route_class=args.route_class,
        max_words=args.max_words,
    )
    payload = cases_to_jsonl(cases)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    if args.summary:
        print(json.dumps({"input_rows": len(rows), "exported_cases": len(cases)}, sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
