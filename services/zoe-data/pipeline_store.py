"""JSONL persistence for engineering pipeline runs."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import threading
from functools import partial
from pathlib import Path
from typing import Any, Awaitable, Callable

from pipeline_evidence import (
    EvidenceItem,
    PipelinePhase,
    PipelineState,
    block_fingerprint,
    build_scope_split_packet,
    can_complete_phase,
    issue_evidence_profile,
    missing_required_evidence,
    record_block_fingerprint,
    scope_split_required,
    transition,
    verify_validator_hash_matches,
    with_evidence,
)

_PHASE_SKILLS: dict[str, tuple[str, ...]] = {
    "scout": ("zoe-graphify", "zoe-engineering"),
    "implement": ("zoe-engineering", "zoe-graphify", "source-code-context", "code-structure-cleanup"),
    "verify": ("zoe-engineering",),
    "review": ("zoe-engineering",),
    "closeout": ("github-greptile-loop",),
    "retro": ("zoe-status-refresh",),
}

_LOCK = threading.Lock()
_TERMINAL = {"done", "archived", "blocked"}


class PipelineStateConflict(RuntimeError):
    """Raised when a caller tries to persist a stale pipeline mutation."""


def store_path() -> Path:
    override = os.environ.get("ZOE_PIPELINE_STORE_PATH", "").strip()
    if override:
        return Path(override)
    return Path(os.path.expanduser("~/.zoe/engineering_pipeline_runs.jsonl"))


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _latest_state_from_lines(lines: list[str], task_ref: str) -> PipelineState | None:
    latest: PipelineState | None = None
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("task_ref") != task_ref or "state" not in payload:
            continue
        latest = PipelineState.model_validate(payload["state"])
    return latest


def load_latest_state(task_ref: str) -> PipelineState | None:
    path = store_path()
    if not path.exists():
        return None
    with _LOCK:
        with path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                lines = handle.read().splitlines()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return _latest_state_from_lines(lines, task_ref)


async def _run_io(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args)


def save_state(
    state: PipelineState,
    *,
    event: str,
    extra: dict[str, Any] | None = None,
    allow_stale_evidence_merge: bool = False,
) -> PipelineState:
    path = store_path()
    with _LOCK:
        _ensure_parent(path)
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                latest = _latest_state_from_lines(
                    handle.read().splitlines(),
                    state.task_ref,
                )
                incoming_is_stale = bool(
                    latest and state.journal_revision < latest.journal_revision
                )
                if incoming_is_stale and not allow_stale_evidence_merge:
                    raise PipelineStateConflict(
                        f"stale pipeline state for {state.task_ref}: "
                        f"incoming revision {state.journal_revision}, "
                        f"latest revision {latest.journal_revision}"
                    )
                base_state = latest if incoming_is_stale and latest else state
                if incoming_is_stale and latest:
                    evidence_by_key = {
                        json.dumps(
                            item.model_dump(exclude={"created_at"}),
                            sort_keys=True,
                        ): item
                        for item in [*latest.evidence, *state.evidence]
                    }
                    base_state = base_state.model_copy(
                        update={
                            "evidence": sorted(
                                evidence_by_key.values(),
                                key=lambda item: item.created_at,
                            )
                        }
                    )
                state = base_state.model_copy(
                    update={
                        "journal_revision": (
                            latest.journal_revision + 1
                            if latest
                            else max(1, state.journal_revision)
                        )
                    }
                )

                record: dict[str, Any] = {
                    "event": event,
                    "task_ref": state.task_ref,
                    "phase": state.phase,
                    "status": state.status,
                    "state": state.model_dump(),
                }
                if extra:
                    record["meta"] = extra
                handle.seek(0, os.SEEK_END)
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return state


async def bootstrap_state(
    task_ref: str,
    *,
    start_phase: PipelinePhase = "implement",
    issue: dict | None = None,
) -> PipelineState:
    existing = await _run_io(load_latest_state, task_ref)
    if existing:
        return existing
    state = PipelineState(
        task_ref=task_ref,
        phase=start_phase,
        evidence_profile=issue_evidence_profile(issue),
    )
    try:
        return await _run_io(partial(save_state, state, event="bootstrap"))
    except PipelineStateConflict:
        existing = await _run_io(load_latest_state, task_ref)
        if existing is None:
            raise
        return existing


def resume_pipeline(
    task_ref: str,
    *,
    reason: str = "operator retry",
    reset_fingerprint: bool = False,
) -> PipelineState:
    """Journal an explicit retry of a blocked phase without erasing prior evidence."""
    for attempt in range(2):
        state = load_latest_state(task_ref)
        if state is None:
            raise ValueError(f"pipeline not found: {task_ref}")
        if state.status != "blocked":
            raise ValueError(f"pipeline is not blocked: {task_ref} ({state.status})")
        resumed = state.model_copy(
            update={
                "status": "todo",
                "last_block_fingerprint": None if reset_fingerprint else state.last_block_fingerprint,
                "repeated_block_count": 0 if reset_fingerprint else state.repeated_block_count,
                "block_classification": None,
                "split_packet": None,
                "history": (
                    [
                        record
                        for record in state.history
                        if not (record.reason or "").startswith("fingerprint_abort:")
                    ]
                    if reset_fingerprint
                    else state.history
                ),
            }
        )
        try:
            return save_state(
                resumed,
                event="operator_resumed",
                extra={
                    "reason": reason,
                    "phase": resumed.phase,
                    "reset_fingerprint": reset_fingerprint,
                },
            )
        except PipelineStateConflict:
            if attempt:
                raise
    raise AssertionError("unreachable")


def skip_blocked_implementation(
    task_ref: str,
    *,
    reason: str,
) -> PipelineState:
    """Journal an operator-confirmed no-code recovery into verification."""
    for attempt in range(2):
        state = load_latest_state(task_ref)
        if state is None:
            raise ValueError(f"pipeline not found: {task_ref}")
        if state.phase != "implement" or state.status != "blocked":
            raise ValueError(
                f"pipeline is not a blocked implementation: {task_ref} "
                f"({state.phase}/{state.status})"
            )
        if not any(item.kind == "tool" and item.passed is True for item in state.evidence):
            raise ValueError(
                f"pipeline lacks passed scout/tool evidence required for a no-code skip: {task_ref}"
            )
        state = state.model_copy(update={"evidence_profile": "audit"})
        skipped = transition(state, "skip_implementation", reason=reason)
        skipped = skipped.model_copy(
            update={
                "last_block_fingerprint": None,
                "repeated_block_count": 0,
                "block_classification": None,
                "split_packet": None,
            }
        )
        try:
            return save_state(
                skipped,
                event="operator_skipped_implementation",
                extra={"reason": reason, "from_phase": "implement", "to_phase": "verify"},
            )
        except PipelineStateConflict:
            if attempt:
                raise
    raise AssertionError("unreachable")


async def _append_harness_validators(state: PipelineState, phase: PipelinePhase) -> PipelineState:
    """Run repo validators when handoff lacks them; tag hash with phase."""
    if any(
        item.kind == "validator" and item.metadata.get("phase") == phase for item in state.evidence
    ):
        return state
    from pipeline_validators import run_repo_validators, validator_evidence_item

    result = await _run_io(run_repo_validators)
    return with_evidence(state, validator_evidence_item(result, phase=phase))


def _protocol_only_block(detail: dict[str, Any]) -> bool:
    """True when Hermes only failed the terminal protocol for a no-op phase."""
    protocol_seen = False

    def classify(text: str) -> bool | None:
        lowered = text.strip().lower()
        if not lowered:
            return None
        if "protocol violation" in lowered or "without calling kanban_complete" in lowered:
            return True
        if lowered in {"crashed", "failed"}:
            return None
        return False

    for event in detail.get("events") or []:
        if not isinstance(event, dict):
            continue
        if event.get("kind") == "protocol_violation":
            protocol_seen = True
        payload = event.get("payload")
        if isinstance(payload, dict):
            for value in (payload.get("error"), payload.get("trigger_outcome"), payload.get("reason")):
                verdict = classify(str(value or ""))
                if verdict is False:
                    return False
                protocol_seen = protocol_seen or verdict is True
    for run in detail.get("runs") or []:
        if not isinstance(run, dict):
            continue
        for value in (run.get("error"), run.get("outcome"), run.get("status")):
            verdict = classify(str(value or ""))
            if verdict is False:
                return False
            protocol_seen = protocol_seen or verdict is True
    return protocol_seen


def _append_audit_protocol_recovery_evidence(state: PipelineState, phase: PipelinePhase) -> PipelineState:
    kind_by_phase = {
        "scout": "tool",
        "implement": "tool",
        "verify": "validator",
        "review": "human",
        "closeout": "log",
        "retro": "log",
    }
    kind = kind_by_phase[phase]
    if any(item.kind == kind and item.metadata.get("phase") == phase for item in state.evidence):
        return state
    return with_evidence(
        state,
        EvidenceItem(
            kind=kind,  # type: ignore[arg-type]
            summary=f"audit/no-PR {phase} auto-recovered after protocol-only Hermes exit",
            passed=True,
            metadata={"source": "audit_protocol_recovery", "phase": phase},
        ),
    )


def _latest_retro_followup(state: PipelineState) -> dict[str, Any] | None:
    for item in reversed(state.evidence):
        if item.kind != "log":
            continue
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        if metadata.get("phase") != "retro":
            continue
        follow_up = metadata.get("follow_up")
        if isinstance(follow_up, dict) and follow_up.get("title"):
            return follow_up
    return None


def pipeline_summary(state: PipelineState | None) -> dict[str, Any]:
    if not state:
        return {"tracked": False}
    missing = sorted(missing_required_evidence(state))
    block_reason = None
    if state.status == "blocked":
        for record in reversed(state.history):
            if record.outcome in {"block", "verification_failed", "request_changes", "merge_blocked"}:
                block_reason = record.reason
                break
    fingerprint_abort = state.status == "blocked" and (
        state.repeated_block_count >= 2
        or any(
            (record.reason or "").startswith("fingerprint_abort:")
            for record in state.history
        )
    )
    terminal_block = state.status == "blocked" and (
        fingerprint_abort
        or state.block_classification == "scope_split_required"
    )
    hash_ok = verify_validator_hash_matches(state) if state.phase == "verify" else True
    return {
        "tracked": True,
        "phase": state.phase,
        "status": state.status,
        "evidence_profile": state.evidence_profile,
        "evidence_ok": (can_complete_phase(state) if state.status == "running" else True) and hash_ok,
        "missing_evidence": missing,
        "attempts": dict(state.attempts),
        "terminal_block": terminal_block,
        "fingerprint_abort": fingerprint_abort,
        "block_reason": block_reason,
        "block_classification": state.block_classification,
        "needs_split": state.status == "blocked" and state.block_classification == "scope_split_required",
        "split_packet": state.split_packet,
        "validator_hash_ok": hash_ok,
        "retro_followup": _latest_retro_followup(state),
    }


async def sync_pipeline_from_chain(
    task_ref: str,
    phases: dict[str, dict],
    fetch_detail: Callable[[str], Awaitable[dict[str, Any]]],
    *,
    start_phase: PipelinePhase = "implement",
    issue: dict | None = None,
) -> PipelineState:
    """Retry poll reconciliation, then defer safely to the next poll cycle."""
    for _attempt in range(3):
        try:
            return await _sync_pipeline_from_chain_once(
                task_ref,
                phases,
                fetch_detail,
                start_phase=start_phase,
                issue=issue,
            )
        except PipelineStateConflict:
            continue
    latest = await _run_io(load_latest_state, task_ref)
    if latest is None:
        raise RuntimeError(f"pipeline disappeared during reconciliation: {task_ref}")
    return latest


async def _sync_pipeline_from_chain_once(
    task_ref: str,
    phases: dict[str, dict],
    fetch_detail: Callable[[str], Awaitable[dict[str, Any]]],
    *,
    start_phase: PipelinePhase = "implement",
    issue: dict | None = None,
) -> PipelineState:
    """Advance pipeline state from terminal Kanban phase rows and parsed handoffs."""
    from pipeline_handoff import (
        audit_only_from_handoff,
        block_reason_from_handoff,
        evidence_from_handoff,
        infer_outcome,
        implementation_required_from_handoff,
        split_request_from_handoff,
    )

    state = await bootstrap_state(task_ref, start_phase=start_phase, issue=issue)
    if state.status == "done":
        return state
    if state.status == "blocked":
        terminal = pipeline_summary(state).get("terminal_block")
        if terminal:
            return state
        row = phases.get(state.phase) or {}
        if (row.get("status") or "").lower() != "blocked":
            return state

    for phase in ("scout", "implement", "verify", "review", "closeout", "retro"):
        row = phases.get(phase)
        if not row:
            continue
        row_status = (row.get("status") or "").lower()
        skills = _PHASE_SKILLS.get(phase, ())

        if phase == state.phase and row_status not in _TERMINAL and phase == "verify":
            state = await _append_harness_validators(state, "verify")

        if row_status not in _TERMINAL:
            continue

        task_id = row.get("id")
        detail: dict[str, Any] = {}
        if task_id:
            detail = await fetch_detail(task_id)

        if phase != state.phase:
            continue

        for item in evidence_from_handoff(phase, detail, skills=skills):  # type: ignore[arg-type]
            state = with_evidence(state, item)

        if phase == "implement" and audit_only_from_handoff(detail):
            state = state.model_copy(update={"evidence_profile": "audit"})

        if phase == "implement" and row_status in {"done", "archived"}:
            state = await _append_harness_validators(state, "implement")
        if phase == "verify" and row_status in {"done", "archived"}:
            state = await _append_harness_validators(state, "verify")

        outcome = infer_outcome(phase, row_status, detail)  # type: ignore[arg-type]
        if not outcome:
            continue
        if (
            phase == "scout"
            and outcome == "complete"
            and implementation_required_from_handoff(detail) is False
        ):
            outcome = "skip_implementation"
        if (
            state.evidence_profile == "audit"
            and outcome in {"block", "verification_failed", "request_changes", "merge_blocked"}
            and _protocol_only_block(detail)
        ):
            state = _append_audit_protocol_recovery_evidence(state, phase)  # type: ignore[arg-type]
            if can_complete_phase(state):
                state = await _run_io(
                    partial(
                        save_state,
                        state,
                        event="audit_protocol_recovered",
                        extra={"row_phase": phase, "outcome": outcome},
                    )
                )
                outcome = "complete"

        block_reason = None
        split_requested = False
        handoff_split_packet: dict[str, Any] | None = None
        if outcome in {"block", "verification_failed", "request_changes", "merge_blocked"}:
            block_reason = block_reason_from_handoff(
                detail, row_block_reason=row.get("block_reason")
            ) or outcome
            split_requested, handoff_split_packet = split_request_from_handoff(detail)
            fingerprint = block_fingerprint(phase, str(block_reason))  # type: ignore[arg-type]
            if state.status == "blocked" and fingerprint == state.last_block_fingerprint:
                return state
            state, should_abort = record_block_fingerprint(state, fingerprint)
            explicit_split = scope_split_required(
                phase, str(block_reason), explicit=split_requested  # type: ignore[arg-type]
            )
            if explicit_split:
                packet = build_scope_split_packet(
                    state.task_ref,
                    phase,  # type: ignore[arg-type]
                    str(block_reason),
                    source="handoff",
                    existing=handoff_split_packet,
                )
                state = transition(state, "block", reason=f"scope_split_required:{block_reason}")
                state = state.model_copy(
                    update={"block_classification": "scope_split_required", "split_packet": packet}
                )
                state = await _run_io(
                    partial(
                        save_state,
                        state,
                        event="scope_split_required",
                        extra={
                            "row_phase": phase,
                            "reason": block_reason,
                            "block_reason": block_reason,
                            "split_packet": packet,
                        },
                    )
                )
                return state
            if split_requested:
                state = await _run_io(
                    partial(
                        save_state,
                        state,
                        event="ignored_scope_split_request",
                        extra={
                            "row_phase": phase,
                            "reason": block_reason,
                            "block_reason": block_reason,
                            "split_packet": handoff_split_packet,
                        },
                    )
                )
            if should_abort:
                state = transition(state, "block", reason=f"fingerprint_abort:{fingerprint}")
                if scope_split_required(
                    phase, str(block_reason), repeated=True  # type: ignore[arg-type]
                ):
                    packet = build_scope_split_packet(
                        state.task_ref,
                        phase,  # type: ignore[arg-type]
                        str(block_reason),
                        source="fingerprint_abort",
                        existing=handoff_split_packet,
                    )
                    state = state.model_copy(
                        update={"block_classification": "scope_split_required", "split_packet": packet}
                    )
                state = await _run_io(
                    partial(
                        save_state,
                        state,
                        event="fingerprint_abort",
                        extra={
                            "row_phase": phase,
                            "fingerprint": fingerprint,
                            "reason": block_reason,
                            "block_reason": block_reason,
                            "block_classification": state.block_classification,
                            "needs_split": state.block_classification == "scope_split_required",
                            "split_packet": state.split_packet,
                        },
                    )
                )
                return state

        if outcome in {"complete", "skip_implementation"} and not can_complete_phase(state):
            extra: dict[str, Any] = {
                "row_phase": phase,
                "missing": sorted(missing_required_evidence(state)),
            }
            if state.phase == "verify" and not verify_validator_hash_matches(state):
                extra["validator_hash_mismatch"] = True
            block_reason = "GATE_BLOCKED: missing required evidence " + ",".join(extra["missing"])
            state, _should_abort = record_block_fingerprint(
                state,
                block_fingerprint(phase, block_reason),  # type: ignore[arg-type]
            )
            state = transition(state, "block", reason=block_reason)
            state = await _run_io(
                partial(
                    save_state,
                    state,
                    event="gate_blocked",
                    extra=extra,
                    allow_stale_evidence_merge=True,
                )
            )
            return state

        try:
            if outcome == "skip_implementation":
                trans_reason = "scout proved implementation not required"
            elif outcome in {
                "block",
                "verification_failed",
                "request_changes",
                "merge_blocked",
            }:
                trans_reason = block_reason
            else:
                trans_reason = None
            state = transition(state, outcome, reason=trans_reason)  # type: ignore[arg-type]
        except ValueError as exc:
            state = await _run_io(
                partial(
                    save_state,
                    state,
                    event="transition_rejected",
                    extra={"row_phase": phase, "reason": str(exc)},
                )
            )
            return state

        state = await _run_io(
            partial(
                save_state,
                state,
                event="transition",
                extra={"outcome": outcome, "from_phase": phase, "block_reason": block_reason},
            )
        )
        if state.status in {"blocked", "done"}:
            break

    return state
