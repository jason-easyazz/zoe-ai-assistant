"""Parse Kanban worker handoffs into pipeline evidence items."""

from __future__ import annotations

import json
import re
from typing import Any

from pipeline_evidence import EvidenceItem, PipelinePhase, content_hash

_KV_RE = re.compile(r"^([A-Z_]+)=(.*)$", re.MULTILINE)
_TOOL_NAMES = (
    "graphify",
    "opensrc",
    "multica",
    "greptile",
    "greploop",
    "validator",
    "pytest",
    "zoe-engineering",
    "zoe-graphify",
    "source-code-context",
    "github-greptile-loop",
)


def _haystacks(detail: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    summary = detail.get("latest_summary")
    if summary:
        parts.append(summary if isinstance(summary, str) else json.dumps(summary))
    for comment in detail.get("comments") or []:
        body = comment.get("body") or comment.get("text") or ""
        if body:
            parts.append(str(body))
    metadata = detail.get("metadata") or {}
    if metadata:
        parts.append(json.dumps(metadata))
    return parts


def _parse_kv_fields(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in _KV_RE.finditer(text or ""):
        key, value = match.group(1), match.group(2).strip()
        if value:
            out[key] = value
    return out


def _tool_summary(raw: str) -> str:
    cleaned = raw.strip()
    return cleaned[:500] if cleaned else "tools recorded in handoff"


def evidence_from_handoff(phase: PipelinePhase, detail: dict[str, Any]) -> list[EvidenceItem]:
    """Best-effort extraction of structured evidence from a Kanban task show payload."""
    fields: dict[str, str] = {}
    for chunk in _haystacks(detail):
        fields.update(_parse_kv_fields(chunk))

    items: list[EvidenceItem] = []

    tools_raw = fields.get("TOOLS_USED") or fields.get("TOOLS") or ""
    if tools_raw:
        items.append(EvidenceItem(kind="tool", summary=_tool_summary(tools_raw), passed=True))
    elif phase == "scout":
        for chunk in _haystacks(detail):
            lowered = chunk.lower()
            if any(name in lowered for name in _TOOL_NAMES):
                items.append(EvidenceItem(kind="tool", summary="context tools referenced in scout handoff", passed=True))
                break

    tests_raw = fields.get("TESTS") or ""
    if tests_raw and phase in {"implement", "verify"}:
        passed = "fail" not in tests_raw.lower()
        items.append(
            EvidenceItem(
                kind="test",
                summary=tests_raw[:500],
                content_hash=content_hash(tests_raw),
                passed=passed,
            )
        )

    validators_raw = fields.get("VALIDATORS") or ""
    if validators_raw and phase in {"implement", "verify"}:
        passed = "fail" not in validators_raw.lower()
        items.append(
            EvidenceItem(
                kind="validator",
                summary=validators_raw[:500],
                content_hash=content_hash(validators_raw),
                passed=passed,
            )
        )

    if phase == "review":
        review_note = fields.get("SUMMARY") or fields.get("REVIEW") or ""
        if review_note:
            items.append(EvidenceItem(kind="human", summary=review_note[:500], passed=True))

    greptile_raw = fields.get("GREPTILE") or ""
    if greptile_raw and phase == "closeout":
        passed = "fail" not in greptile_raw.lower() and "block" not in greptile_raw.lower()
        items.append(EvidenceItem(kind="greptile", summary=greptile_raw[:500], passed=passed))

    retro_raw = fields.get("RETRO") or fields.get("LEARNINGS") or fields.get("SUMMARY") or ""
    if retro_raw and phase == "retro":
        items.append(EvidenceItem(kind="log", summary=retro_raw[:500], passed=True))

    pr_url = fields.get("PR_URL") or ""
    if pr_url and phase in {"implement", "verify", "closeout"}:
        items.append(EvidenceItem(kind="pr", summary=pr_url[:500], artifact=pr_url, passed=True))

    if phase == "implement" and not any(i.kind == "tool" for i in items):
        for chunk in _haystacks(detail):
            lowered = chunk.lower()
            if any(name in lowered for name in _TOOL_NAMES):
                items.append(
                    EvidenceItem(kind="tool", summary="implementation referenced engineering tools", passed=True)
                )
                break

    return items


def block_reason_from_handoff(detail: dict[str, Any], *, row_block_reason: str | None = None) -> str:
    fields: dict[str, str] = {}
    for chunk in _haystacks(detail):
        fields.update(_parse_kv_fields(chunk))
    return (fields.get("BLOCKER") or row_block_reason or "").strip()


def infer_outcome(phase: PipelinePhase, row_status: str, detail: dict[str, Any]) -> str | None:
    """Map a terminal Kanban row to a pipeline transition outcome when inferrable."""
    status = (row_status or "").lower()
    if status == "blocked":
        if phase == "verify":
            return "verification_failed"
        if phase == "review":
            return "request_changes"
        if phase == "closeout":
            return "merge_blocked"
        return "block"
    if status not in {"done", "archived"}:
        return None

    fields: dict[str, str] = {}
    for chunk in _haystacks(detail):
        fields.update(_parse_kv_fields(chunk))
    blocker = fields.get("BLOCKER") or ""
    if blocker:
        if phase == "verify":
            return "verification_failed"
        if phase == "review":
            return "request_changes"
        if phase == "closeout":
            return "merge_blocked"
        return "block"
    return "complete"
