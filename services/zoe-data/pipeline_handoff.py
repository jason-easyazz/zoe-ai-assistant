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
    r"VERIFICATION_FAILED|HTTP_402|PAYMENT_REQUIRED|CREDITS_EXHAUSTED|"
    r"TURN_BUDGET|CONTEXT_LIMIT|TOKEN_LIMIT|SCOPE_SPLIT_REQUIRED|NEEDS_SPLIT)\b",
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


def _json_field_value(text: str, key: str) -> str:
    """Extract a JSON object value from ``KEY=...``, allowing multiline JSON."""
    match = re.search(rf"^{re.escape(key)}=", text or "", re.MULTILINE)
    if not match:
        return ""
    tail = (text or "")[match.end():]
    first = next((idx for idx, char in enumerate(tail) if not char.isspace()), None)
    if first is None or tail[first] != "{":
        return tail.splitlines()[0].strip() if tail else ""

    depth = 0
    in_string = False
    escaped = False
    end = first
    for idx, char in enumerate(tail[first:], start=first):
        end = idx + 1
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                break
    return tail[first:end].strip()


def _tool_summary(raw: str) -> str:
    cleaned = raw.strip()
    return cleaned[:500] if cleaned else "tools recorded in handoff"


def _retro_followup_metadata(fields: dict[str, str]) -> dict[str, Any]:
    """Return optional retro-created follow-up ticket metadata from handoff fields."""
    title = (fields.get("FOLLOW_UP_TITLE") or fields.get("FOLLOWUP_TITLE") or "").strip()
    description = (
        fields.get("FOLLOW_UP_DESCRIPTION")
        or fields.get("FOLLOWUP_DESCRIPTION")
        or ""
    ).strip()
    if not title:
        return {}
    return {
        "title": title[:140],
        "description": description[:2000] or title[:500],
        "source": "retro",
    }


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


def _review_metadata_candidates(detail: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    metadata = detail.get("metadata") or {}
    if isinstance(metadata, dict):
        candidates.append(("kanban_metadata", metadata))
    for run in detail.get("runs") or []:
        if not isinstance(run, dict):
            continue
        run_metadata = run.get("metadata") or {}
        if isinstance(run_metadata, dict):
            candidates.append(("kanban_run_metadata", run_metadata))
    return candidates


def _human_review_from_metadata(detail: dict[str, Any]) -> EvidenceItem | None:
    summary = str(detail.get("latest_summary") or "").strip()

    for source, metadata in _review_metadata_candidates(detail):
        readiness = str(metadata.get("merge_readiness") or "").strip().lower()
        verdict = str(metadata.get("verdict") or "").strip().lower()
        explicit_verdict_ready = metadata.get("merge_ready") is True and verdict in {"approve", "approved"}
        if explicit_verdict_ready:
            readiness = "merge_ready"
        task = detail.get("task") if isinstance(detail.get("task"), dict) else {}
        explicit_approver = metadata.get("approver") or metadata.get("approved_by")
        approver = str(
            explicit_approver
            or (task.get("assignee") if explicit_verdict_ready else "")
            or ""
        ).strip()

        if readiness in {"merge_ready", "approved"} and approver:
            return EvidenceItem(
                kind="human",
                summary=(summary or f"review approved by {approver}")[:500],
                passed=True,
                metadata={
                    "source": source,
                    "approver": approver,
                    "merge_readiness": readiness,
                    "verdict": verdict or None,
                },
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


def audit_only_from_handoff(detail: dict[str, Any]) -> bool:
    """True when implement/verify handoff marks the task as audit-only (no PR/tests)."""
    fields: dict[str, str] = {}
    for chunk in _haystacks(detail):
        fields.update(_parse_kv_fields(chunk))
    if fields.get("AUDIT_ONLY", "").strip().lower() in {"1", "true", "yes"}:
        return True
    metadata = detail.get("metadata") or {}
    if isinstance(metadata, dict):
        raw = metadata.get("AUDIT_ONLY") or metadata.get("audit_only") or ""
        return str(raw).strip().lower() in {"1", "true", "yes"}
    return False


def split_request_from_handoff(detail: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    """Return explicit scope-split request and optional machine packet from handoff text."""
    fields: dict[str, str] = {}
    for chunk in _haystacks(detail):
        fields.update(_parse_kv_fields(chunk))

    packet: dict[str, Any] | None = None
    raw_packet = ""
    for chunk in _haystacks(detail):
        raw_packet = _json_field_value(chunk, "SPLIT_PACKET") or raw_packet
    raw_packet = (raw_packet or fields.get("SPLIT_PACKET") or "").strip()
    if raw_packet:
        try:
            parsed = json.loads(raw_packet)
            if isinstance(parsed, dict):
                packet = parsed
            else:
                packet = {"raw": raw_packet[:1000], "parse_error": "not_object"}
        except json.JSONDecodeError:
            packet = {"raw": raw_packet[:1000], "parse_error": "invalid_json"}

    requested = packet is not None
    for key in ("NEEDS_SPLIT", "SCOPE_SPLIT_REQUIRED"):
        if fields.get(key, "").strip().lower() in {"1", "true", "yes"}:
            requested = True

    blocker = fields.get("BLOCKER") or ""
    if _STABLE_BLOCKER_RE.search(blocker) and any(
        token in blocker.upper() for token in ("SCOPE_SPLIT_REQUIRED", "NEEDS_SPLIT")
    ):
        requested = True

    metadata = detail.get("metadata") or {}
    if isinstance(metadata, dict):
        meta_packet = metadata.get("SPLIT_PACKET") or metadata.get("split_packet")
        if isinstance(meta_packet, dict):
            packet = meta_packet
            requested = True
        for key in ("NEEDS_SPLIT", "needs_split", "SCOPE_SPLIT_REQUIRED", "scope_split_required"):
            if str(metadata.get(key) or "").strip().lower() in {"1", "true", "yes"}:
                requested = True

    return requested, packet


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
        else:
            review_item = _human_review_from_metadata(detail)
            if review_item:
                items.append(review_item)

    if phase == "closeout":
        greptile_item = _greptile_from_closeout(detail, skills)
        if greptile_item:
            items.append(greptile_item)

    retro_raw = fields.get("RETRO") or fields.get("LEARNINGS") or fields.get("SUMMARY") or ""
    if retro_raw and phase == "retro":
        metadata = {"source": "handoff", "phase": "retro"}
        follow_up = _retro_followup_metadata(fields)
        if follow_up:
            metadata["follow_up"] = follow_up
        items.append(EvidenceItem(kind="log", summary=retro_raw[:500], passed=True, metadata=metadata))

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
