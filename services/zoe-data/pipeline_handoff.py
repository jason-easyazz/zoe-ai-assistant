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
_SKILL_TOOL_SOURCES = {
    "zoe-graphify": "graphify",
    "source-code-context": "opensrc",
    "github-greptile-loop": "greptile",
    "zoe-engineering": "zoe-engineering",
    "code-structure-cleanup": "code-structure-cleanup",
    "zoe-status-refresh": "zoe-status-refresh",
}
_LOG_TOOL_MARKERS = (
    "TOOLS_USED",
    "graphify query",
    "opensrc path",
    "greploop_guard",
    "validate_structure",
    "validate_critical_files",
)
_STABLE_BLOCKER_RE = re.compile(
    r"\b(?:WORKTREE_NOT_READY|GATE_BLOCKED|PROTOCOL_VIOLATION|MERGE_BLOCKED|"
    r"VERIFICATION_FAILED|HTTP_402|PAYMENT_REQUIRED|CREDITS_EXHAUSTED)\b",
    re.I,
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
    logs = detail.get("logs") or detail.get("log") or detail.get("log_tail")
    if logs:
        parts.append(logs if isinstance(logs, str) else json.dumps(logs))
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


def _log_tail_snippet(detail: dict[str, Any], *, max_lines: int = 8) -> str:
    for chunk in reversed(_haystacks(detail)):
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if lines:
            return "\n".join(lines[-max_lines:])
    return ""


def _stable_block_reason_from_text(text: str) -> str:
    """Extract a stable blocker token for fingerprinting (ignore dynamic log tails)."""
    if not text:
        return ""
    match = _STABLE_BLOCKER_RE.search(text)
    if match:
        return match.group(0).upper()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        err = re.match(r"^Error:\s*([A-Z][A-Z0-9_]+)", stripped)
        if err:
            return err.group(1)
        code = re.match(r"^([A-Z][A-Z0-9_]{5,})(?::|\s|$)", stripped)
        if code:
            return code.group(1)
    return ""


def _tool_from_skills(skills: tuple[str, ...] | list[str]) -> EvidenceItem | None:
    names = [_SKILL_TOOL_SOURCES.get(skill, skill) for skill in skills if skill in _SKILL_TOOL_SOURCES]
    if not names:
        return None
    return EvidenceItem(
        kind="tool",
        summary=f"pinned skills: {', '.join(names)}",
        passed=True,
        metadata={"source": "skills"},
    )


def _tool_from_log_markers(detail: dict[str, Any]) -> EvidenceItem | None:
    for chunk in _haystacks(detail):
        if any(marker.lower() in chunk.lower() for marker in _LOG_TOOL_MARKERS):
            return EvidenceItem(
                kind="tool",
                summary="engineering tools referenced in kanban log",
                passed=True,
                metadata={"source": "log_markers"},
            )
        lowered = chunk.lower()
        if any(name in lowered for name in _TOOL_NAMES):
            return EvidenceItem(
                kind="tool",
                summary="engineering tools referenced in kanban log",
                passed=True,
                metadata={"source": "log_markers"},
            )
    return None


def _greptile_from_closeout(detail: dict[str, Any], skills: tuple[str, ...] | list[str]) -> EvidenceItem | None:
    fields: dict[str, str] = {}
    for chunk in _haystacks(detail):
        fields.update(_parse_kv_fields(chunk))

    greptile_raw = fields.get("GREPTILE") or ""
    if greptile_raw:
        passed = "fail" not in greptile_raw.lower() and "block" not in greptile_raw.lower()
        return EvidenceItem(
            kind="greptile",
            summary=greptile_raw[:500],
            passed=passed,
            metadata={"source": "handoff"},
        )

    if "github-greptile-loop" not in skills:
        return None
    for chunk in _haystacks(detail):
        lowered = chunk.lower()
        if "greploop" in lowered or "greptile" in lowered:
            passed = "fail" not in lowered and "block" not in lowered
            return EvidenceItem(
                kind="greptile",
                summary="github-greptile-loop skill used in closeout",
                passed=passed,
                metadata={"source": "skills"},
            )
    return EvidenceItem(
        kind="greptile",
        summary="github-greptile-loop pinned for closeout",
        passed=None,
        metadata={"source": "skills"},
    )


def evidence_from_handoff(
    phase: PipelinePhase,
    detail: dict[str, Any],
    *,
    skills: tuple[str, ...] | list[str] = (),
) -> list[EvidenceItem]:
    """Best-effort extraction of structured evidence from a Kanban task show payload."""
    fields: dict[str, str] = {}
    for chunk in _haystacks(detail):
        fields.update(_parse_kv_fields(chunk))

    items: list[EvidenceItem] = []

    tools_raw = fields.get("TOOLS_USED") or fields.get("TOOLS") or ""
    if tools_raw:
        items.append(
            EvidenceItem(
                kind="tool",
                summary=_tool_summary(tools_raw),
                passed=True,
                metadata={"source": "handoff"},
            )
        )
    elif phase in {"scout", "implement"}:
        skill_tool = _tool_from_skills(skills)
        if skill_tool:
            items.append(skill_tool)
        else:
            log_tool = _tool_from_log_markers(detail)
            if log_tool:
                items.append(log_tool)
            elif phase == "scout":
                for chunk in _haystacks(detail):
                    lowered = chunk.lower()
                    if any(name in lowered for name in _TOOL_NAMES):
                        items.append(
                            EvidenceItem(
                                kind="tool",
                                summary="context tools referenced in scout handoff",
                                passed=True,
                                metadata={"source": "log_markers"},
                            )
                        )
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
                metadata={"source": "handoff"},
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
                metadata={"source": "handoff", "phase": phase},
            )
        )

    if phase == "review":
        review_note = fields.get("SUMMARY") or fields.get("REVIEW") or ""
        if review_note:
            items.append(EvidenceItem(kind="human", summary=review_note[:500], passed=True))

    if phase == "closeout":
        greptile_item = _greptile_from_closeout(detail, skills)
        if greptile_item:
            items.append(greptile_item)

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
                    EvidenceItem(
                        kind="tool",
                        summary="implementation referenced engineering tools",
                        passed=True,
                        metadata={"source": "log_markers"},
                    )
                )
                break

    return items


def block_reason_from_handoff(detail: dict[str, Any], *, row_block_reason: str | None = None) -> str:
    fields: dict[str, str] = {}
    for chunk in _haystacks(detail):
        fields.update(_parse_kv_fields(chunk))
    reason = (fields.get("BLOCKER") or row_block_reason or "").strip()
    if reason:
        return reason
    tail = _log_tail_snippet(detail)
    if tail:
        stable = _stable_block_reason_from_text(tail)
        if stable:
            return stable
    return ""


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
