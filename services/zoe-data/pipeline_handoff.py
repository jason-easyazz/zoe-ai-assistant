"""Parse Kanban worker handoffs into pipeline evidence items."""

from __future__ import annotations

import json
import re
from typing import Any

from pipeline_evidence import EvidenceItem, PipelinePhase, content_hash

_KV_RE = re.compile(r"^[ \t]*([A-Z_]+)=(.*)$", re.MULTILINE)
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
_STABLE_BLOCKER_TOKENS = (
    "WORKTREE_NOT_READY",
    "GATE_BLOCKED",
    "PROTOCOL_VIOLATION",
    "MERGE_BLOCKED",
    "VERIFICATION_FAILED",
    "HTTP_402",
    "PAYMENT_REQUIRED",
    "CREDITS_EXHAUSTED",
    "TURN_BUDGET",
    "ITERATION_BUDGET",
    "CONTEXT_LIMIT",
    "TOKEN_LIMIT",
    "SCOPE_SPLIT_REQUIRED",
    "NEEDS_SPLIT",
)
_SURFACED_BLOCKER_TOKENS = (
    "VERIFY_BUDGET",
    "REVIEW_BUDGET",
    "CLOSEOUT_BUDGET",
    "IMPLEMENT_BUDGET",
    "PR_REVIEW_REQUIRED",
    "PR_REVISION_BLOCKED",
    "PR_REVISION_CHECKOUT_FAILED",
    "WORKTREE_PREPARATION_FAILED",
    "WORKTREE_NOT_READY",
    "PROTOCOL_VIOLATION",
    "TURN_BUDGET",
    "ITERATION_BUDGET",
    "CONTEXT_LIMIT",
    "TOKEN_LIMIT",
    "HTTP_402",
    "PAYMENT_REQUIRED",
    "CREDITS_EXHAUSTED",
)
_STABLE_BLOCKER_RE = re.compile(rf"\b(?:{'|'.join(_STABLE_BLOCKER_TOKENS)})\b", re.I)
_SURFACED_BLOCKER_RE = re.compile(rf"\b(?:{'|'.join(_SURFACED_BLOCKER_TOKENS)})\b", re.I)


def _task_body(detail: dict[str, Any]) -> str:
    task = detail.get("task") if isinstance(detail.get("task"), dict) else {}
    return str(task.get("body") or "")


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
    for run in detail.get("runs") or []:
        if not isinstance(run, dict):
            continue
        run_error = run.get("error")
        if run_error:
            parts.append(
                f"ITERATION_BUDGET: {run_error}"
                if "iteration budget" in str(run_error).lower()
                else str(run_error)
            )
        run_summary = run.get("summary")
        if run_summary:
            parts.append(str(run_summary))
        run_metadata = run.get("metadata")
        if run_metadata:
            parts.append(json.dumps(run_metadata))
    return parts


def _parse_kv_fields(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in _KV_RE.finditer(text or ""):
        key, value = match.group(1), match.group(2).strip()
        if value or key == "BLOCKER":
            out[key] = value
    return out


def _reported_evidence_passed(raw: str, *, unavailable_markers: tuple[str, ...]) -> bool:
    lowered = raw.lower()
    return not (
        any(
            marker in lowered
            for marker in ("fail", "not applicable", "n/a", *unavailable_markers)
        )
        or re.search(r"\bblock(?:ed)?\b", lowered)
    )


def _reported_test_evidence_passed(raw: str, *, phase: PipelinePhase) -> bool:
    lowered = raw.lower()
    if phase == "verify" and "not applicable" in lowered:
        no_code_markers = (
            "audit-only",
            "audit only",
            "no-code",
            "scout/plan",
            "no-code planning",
            "plan deliverable",
        )
        if any(marker in lowered for marker in no_code_markers):
            return True
    return _reported_evidence_passed(
        raw,
        unavailable_markers=("no tests",),
    )


def _structured_handoff_fields(detail: dict[str, Any]) -> dict[str, str]:
    """Extract explicit KEY=value equivalents from Kanban metadata."""
    out: dict[str, str] = {}
    candidates = [detail.get("metadata")]
    candidates.extend(
        run.get("metadata")
        for run in detail.get("runs") or []
        if isinstance(run, dict)
    )
    for metadata in candidates:
        if not isinstance(metadata, dict):
            continue
        for key, value in metadata.items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Z_]+", key):
                continue
            if value is None or isinstance(value, (dict, list)):
                continue
            text = str(value).strip()
            if text:
                out[key] = text
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


def _test_from_run_metadata(detail: dict[str, Any], *, phase: PipelinePhase) -> EvidenceItem | None:
    if phase not in {"implement", "verify"}:
        return None
    latest: tuple[Any, Any] | None = None
    for run in detail.get("runs") or []:
        if not isinstance(run, dict):
            continue
        metadata = run.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        tests_run = metadata.get("tests_run")
        tests_passed = metadata.get("tests_passed")
        if tests_run in (None, "", 0, "0"):
            continue
        latest = (tests_run, tests_passed)
    if latest is not None:
        tests_run, tests_passed = latest
        passed = False
        if tests_passed is True:
            passed = True
        elif tests_passed is False:
            passed = False
        else:
            try:
                passed = int(tests_passed) >= int(tests_run)
            except (TypeError, ValueError):
                passed = str(tests_passed or "").strip().lower() in {"true", "yes", "pass", "passed"}
        summary = f"kanban run metadata: tests_run={tests_run}, tests_passed={tests_passed}"
        return EvidenceItem(
            kind="test",
            summary=summary[:500],
            content_hash=content_hash(summary),
            passed=passed,
            metadata={"source": "kanban_run_metadata", "phase": phase},
        )
    return None


def _pr_url_from_ticket_block(detail: dict[str, Any]) -> str:
    body = _task_body(detail)
    if not body:
        return ""
    try:
        from multica_ticket_contract import parse_ticket_block

        metadata = parse_ticket_block(body)
    except Exception:  # noqa: BLE001
        metadata = {}
    if isinstance(metadata, dict):
        return str(metadata.get("pr_url") or "").strip()
    return ""


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
        explicit_approved_ready = metadata.get("approved") is True and readiness == "ready"
        explicit_approval = explicit_verdict_ready or explicit_approved_ready
        if explicit_approval:
            readiness = "merge_ready"
        task = detail.get("task") if isinstance(detail.get("task"), dict) else {}
        explicit_approver = metadata.get("approver") or metadata.get("approved_by")
        approver = str(
            explicit_approver
            or (task.get("assignee") if explicit_approval else "")
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
        passed = _reported_evidence_passed(greptile_raw, unavailable_markers=())
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


def implementation_required_from_handoff(detail: dict[str, Any]) -> bool | None:
    """Return an explicit scout decision about whether code changes are required."""
    fields: dict[str, str] = {}
    for chunk in _haystacks(detail):
        fields.update(_parse_kv_fields(chunk))
    structured_fields = _structured_handoff_fields(detail)
    fields.update(structured_fields)

    raw = (fields.get("IMPLEMENTATION_REQUIRED") or "").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False

    return None


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
    text_fields: dict[str, str] = {}
    for chunk in _haystacks(detail):
        text_fields.update(_parse_kv_fields(chunk))
    task_fields = _parse_kv_fields(_task_body(detail))
    if task_fields.get("PR_URL") and not text_fields.get("PR_URL"):
        text_fields["PR_URL"] = task_fields["PR_URL"]
    fields = dict(text_fields)
    fields.update(_structured_handoff_fields(detail))

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
        passed = _reported_test_evidence_passed(tests_raw, phase=phase)
        items.append(
            EvidenceItem(
                kind="test",
                summary=tests_raw[:500],
                content_hash=content_hash(tests_raw),
                passed=passed,
                metadata={"source": "handoff"},
            )
        )
    elif phase in {"implement", "verify"}:
        metadata_test = _test_from_run_metadata(detail, phase=phase)
        if metadata_test:
            items.append(metadata_test)

    validators_raw = fields.get("VALIDATORS") or ""
    if validators_raw and phase in {"implement", "verify"}:
        passed = _reported_evidence_passed(
            validators_raw,
            unavailable_markers=("no validators",),
        )
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
        review_note = text_fields.get("SUMMARY") or text_fields.get("REVIEW") or ""
        if review_note:
            items.append(EvidenceItem(kind="human", summary=review_note[:500], passed=True))
        else:
            review_item = _human_review_from_metadata(detail)
            if review_item:
                items.append(review_item)

    ticket_pr_url = (
        _pr_url_from_ticket_block(detail)
        if phase in {"implement", "verify", "closeout"}
        else ""
    )

    if phase == "closeout":
        summary_raw = fields.get("SUMMARY") or fields.get("CLOSEOUT") or ""
        audit_only = (fields.get("AUDIT_ONLY") or "").strip().lower() in {"1", "true", "yes"}
        # Some audit/no-code closeout workers omit AUDIT_ONLY but still report an
        # audit-only summary and no PR. Treat only that explicit audit wording as inferred audit.
        inferred_audit = bool(
            summary_raw
            and "audit" in summary_raw.lower()
            and not (fields.get("PR_URL") or "").strip()
            and not ticket_pr_url
        )
        if audit_only or inferred_audit:
            items.append(
                EvidenceItem(
                    kind="log",
                    summary=(summary_raw or "audit-only closeout completed")[:500],
                    passed=True,
                    metadata={"source": "handoff", "phase": "closeout", "audit_only": audit_only or inferred_audit},
                )
            )
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

    pr_url = fields.get("PR_URL") or ticket_pr_url
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
    for chunk in _haystacks(detail):
        stable = _stable_block_reason_from_text(chunk)
        if stable:
            return stable
    return ""


def infer_outcome(phase: PipelinePhase, row_status: str, detail: dict[str, Any]) -> str | None:
    """Map a terminal Kanban row to a pipeline transition outcome when inferrable."""
    status = (row_status or "").lower()
    fields: dict[str, str] = {}
    for chunk in _haystacks(detail):
        fields.update(_parse_kv_fields(chunk))
    explicit_blocker = (fields.get("BLOCKER") or "").strip()
    blocker = explicit_blocker
    if status == "blocked" and not blocker:
        for chunk in _haystacks(detail):
            blocker = _stable_block_reason_from_text(chunk)
            if blocker:
                break

    if status == "blocked":
        if _SURFACED_BLOCKER_RE.search(blocker):
            return "block"
        if phase == "verify":
            return "verification_failed"
        if phase == "review":
            return "request_changes"
        if phase == "closeout":
            return "merge_blocked"
        return "block"
    if status not in {"done", "archived"}:
        return None

    if explicit_blocker:
        if _SURFACED_BLOCKER_RE.search(blocker):
            return "block"
        if phase == "verify":
            return "verification_failed"
        if phase == "review":
            return "request_changes"
        if phase == "closeout":
            return "merge_blocked"
        return "block"
    return "complete"
