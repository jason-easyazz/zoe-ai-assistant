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
    PipelinePhase,
    PipelineState,
    block_fingerprint,
    can_complete_phase,
    issue_evidence_profile,
    missing_required_evidence,
    record_block_fingerprint,
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


def store_path() -> Path:
    override = os.environ.get("ZOE_PIPELINE_STORE_PATH", "").strip()
    if override:
        return Path(override)
    return Path(os.path.expanduser("~/.zoe/engineering_pipeline_runs.jsonl"))


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_record(record: dict[str, Any]) -> None:
    path = store_path()
    with _LOCK:
        _ensure_parent(path)
        with path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_latest_state(task_ref: str) -> PipelineState | None:
    path = store_path()
    if not path.exists():
        return None
    latest: PipelineState | None = None
    with _LOCK:
        with path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                lines = handle.read().splitlines()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("task_ref") != task_ref:
                continue
            if "state" in payload:
                latest = PipelineState.model_validate(payload["state"])
    return latest


async def _run_io(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args)


def save_state(state: PipelineState, *, event: str, extra: dict[str, Any] | None = None) -> PipelineState:
    record: dict[str, Any] = {
        "event": event,
        "task_ref": state.task_ref,
        "phase": state.phase,
        "status": state.status,
        "state": state.model_dump(),
    }
    if extra:
        record["meta"] = extra
    append_record(record)
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
    await _run_io(partial(save_state, state, event="bootstrap"))
    return state


async def _append_harness_validators(state: PipelineState, phase: PipelinePhase) -> PipelineState:
    """Run repo validators when handoff lacks them; tag hash with phase."""
    if any(
        item.kind == "validator" and item.metadata.get("phase") == phase for item in state.evidence
    ):
        return state
    from pipeline_validators import run_repo_validators, validator_evidence_item

    result = await _run_io(run_repo_validators)
    return with_evidence(state, validator_evidence_item(result, phase=phase))


def pipeline_summary(state: PipelineState | None) -> dict[str, Any]:
    if not state:
        return {"tracked": False}
    missing = sorted(missing_required_evidence(state))
    terminal_block = state.status == "blocked" and (
        state.repeated_block_count >= 2
        or any(
            (rec.reason or "").startswith("fingerprint_abort:")
            for rec in state.history
        )
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
        "fingerprint_abort": terminal_block,
        "validator_hash_ok": hash_ok,
    }


async def sync_pipeline_from_chain(
    task_ref: str,
    phases: dict[str, dict],
    fetch_detail: Callable[[str], Awaitable[dict[str, Any]]],
    *,
    start_phase: PipelinePhase = "implement",
    issue: dict | None = None,
) -> PipelineState:
    """Advance pipeline state from terminal Kanban phase rows and parsed handoffs."""
    from pipeline_handoff import block_reason_from_handoff, evidence_from_handoff, infer_outcome

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

        if phase == "implement" and row_status in {"done", "archived"}:
            state = await _append_harness_validators(state, "implement")
        if phase == "verify" and row_status in {"done", "archived"}:
            state = await _append_harness_validators(state, "verify")

        outcome = infer_outcome(phase, row_status, detail)  # type: ignore[arg-type]
        if not outcome:
            continue

        block_reason = None
        if outcome in {"block", "verification_failed", "request_changes", "merge_blocked"}:
            block_reason = block_reason_from_handoff(
                detail, row_block_reason=row.get("block_reason")
            ) or outcome
            fingerprint = block_fingerprint(phase, str(block_reason))  # type: ignore[arg-type]
            state, should_abort = record_block_fingerprint(state, fingerprint)
            if should_abort:
                state = transition(state, "block", reason=f"fingerprint_abort:{fingerprint}")
                await _run_io(
                    partial(
                        save_state,
                        state,
                        event="fingerprint_abort",
                        extra={
                            "row_phase": phase,
                            "fingerprint": fingerprint,
                            "reason": block_reason,
                            "block_reason": block_reason,
                        },
                    )
                )
                return state

        if outcome == "complete" and not can_complete_phase(state):
            extra: dict[str, Any] = {
                "row_phase": phase,
                "missing": sorted(missing_required_evidence(state)),
            }
            if state.phase == "verify" and not verify_validator_hash_matches(state):
                extra["validator_hash_mismatch"] = True
            await _run_io(
                partial(
                    save_state,
                    state,
                    event="gate_blocked",
                    extra=extra,
                )
            )
            return state

        try:
            state = transition(state, outcome)  # type: ignore[arg-type]
        except ValueError as exc:
            await _run_io(
                partial(
                    save_state,
                    state,
                    event="transition_rejected",
                    extra={"row_phase": phase, "reason": str(exc)},
                )
            )
            return state

        await _run_io(
            partial(
                save_state,
                state,
                event="transition",
                extra={"outcome": outcome, "from_phase": phase},
            )
        )
        if state.status in {"blocked", "done"}:
            break

    return state
