"""JSONL persistence for engineering pipeline runs."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable

from pipeline_evidence import (
    PipelinePhase,
    PipelineState,
    TransitionOutcome,
    can_complete_phase,
    missing_required_evidence,
    transition,
    with_evidence,
)

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
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def load_latest_state(task_ref: str) -> PipelineState | None:
    path = store_path()
    if not path.exists():
        return None
    latest: PipelineState | None = None
    with _LOCK:
        for line in path.read_text(encoding="utf-8").splitlines():
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


def save_state(state: PipelineState, *, event: str, extra: dict[str, Any] | None = None) -> PipelineState:
    record = {
        "event": event,
        "task_ref": state.task_ref,
        "phase": state.phase,
        "status": state.status,
        "state": state.model_dump(),
    }
    if extra:
        record.update(extra)
    append_record(record)
    return state


def bootstrap_state(task_ref: str, *, start_phase: PipelinePhase = "implement") -> PipelineState:
    existing = load_latest_state(task_ref)
    if existing:
        return existing
    state = PipelineState(task_ref=task_ref, phase=start_phase)
    return save_state(state, event="bootstrap")


def pipeline_summary(state: PipelineState | None) -> dict[str, Any]:
    if not state:
        return {"tracked": False}
    missing = sorted(missing_required_evidence(state))
    return {
        "tracked": True,
        "phase": state.phase,
        "status": state.status,
        "evidence_ok": can_complete_phase(state) if state.status == "running" else True,
        "missing_evidence": missing,
        "attempts": dict(state.attempts),
    }


async def sync_pipeline_from_chain(
    task_ref: str,
    phases: dict[str, dict],
    fetch_detail: Callable[[str], Awaitable[dict[str, Any]]],
    *,
    start_phase: PipelinePhase = "implement",
) -> PipelineState:
    """Advance pipeline state from terminal Kanban phase rows and parsed handoffs."""
    from pipeline_handoff import evidence_from_handoff, infer_outcome

    state = bootstrap_state(task_ref, start_phase=start_phase)
    if state.status == "done":
        return state

    for phase in ("scout", "implement", "verify", "review", "closeout", "retro"):
        row = phases.get(phase)
        if not row:
            continue
        row_status = (row.get("status") or "").lower()
        if row_status not in _TERMINAL:
            continue

        task_id = row.get("id")
        detail: dict[str, Any] = {}
        if task_id:
            detail = await fetch_detail(task_id)

        if phase != state.phase:
            continue

        for item in evidence_from_handoff(phase, detail):  # type: ignore[arg-type]
            state = with_evidence(state, item)

        outcome = infer_outcome(phase, row_status, detail)  # type: ignore[arg-type]
        if not outcome:
            continue

        if outcome == "complete" and not can_complete_phase(state):
            save_state(
                state,
                event="gate_blocked",
                extra={
                    "phase": phase,
                    "missing": sorted(missing_required_evidence(state)),
                },
            )
            return state

        try:
            state = transition(state, outcome)  # type: ignore[arg-type]
        except ValueError as exc:
            save_state(state, event="transition_rejected", extra={"phase": phase, "reason": str(exc)})
            return state

        save_state(state, event="transition", extra={"outcome": outcome, "from_phase": phase})
        if state.status in {"blocked", "done"}:
            break

    return state
