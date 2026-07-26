"""JSONL persistence for engineering pipeline runs."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import threading
from functools import partial
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from pipeline_evidence import (
    EvidenceItem,
    PHASE_ORDER,
    PipelinePhase,
    PipelineState,
    TransitionRecord,
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

# Must mirror the skills each phase is actually dispatched with in
# kanban_adapter._CHAIN: the no-TOOLS_USED evidence fallback credits these as
# "pinned skills" tool evidence, so listing a skill a worker was never given
# would advance the pipeline on misleading evidence. implement deliberately
# pins only zoe-engineering (preload is kept minimal per _CHAIN).
_PHASE_SKILLS: dict[str, tuple[str, ...]] = {
    "scout": ("codebase-memory", "zoe-engineering"),
    "implement": ("zoe-engineering",),
    "verify": ("zoe-engineering",),
    "review": ("zoe-engineering",),
    "closeout": ("github-greptile-loop",),
    "retro": ("zoe-status-refresh",),
}

_LOCK = threading.Lock()
_TERMINAL = {"done", "archived", "blocked"}
_TERMINAL_SUCCESS = {"done", "archived"}


class PipelineStateConflict(RuntimeError):
    """Raised when a caller tries to persist a stale pipeline mutation."""


def _phase_after(candidate: PipelinePhase, current: PipelinePhase) -> bool:
    return PHASE_ORDER.index(candidate) > PHASE_ORDER.index(current)


def _has_later_successful_phase(phases: dict[str, dict], phase: PipelinePhase) -> bool:
    phase_idx = PHASE_ORDER.index(phase)
    for candidate in PHASE_ORDER[phase_idx + 1 :]:
        row = phases.get(candidate)
        if row and (row.get("status") or "").lower() in _TERMINAL_SUCCESS:
            return True
    return False


def _is_stale_duplicate_block(row: dict) -> bool:
    reason = str(row.get("block_reason") or row.get("reason") or "").upper()
    return "DUPLICATE_REDISPATCH" in reason


def _row_created_at(row: dict) -> float | None:
    raw = row.get("created_at")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _drop_stale_later_phase_rows(
    phases: dict[str, dict],
    current_phase: PipelinePhase,
) -> dict[str, dict]:
    """Ignore later-phase terminal rows from an older attempt.

    A retry can create a new implement row after an earlier verify row blocked. If
    sync consumes that old verify block immediately after the new implement row,
    the pipeline oscillates back to implement and redispatches forever.
    """
    current_row = phases.get(current_phase) or {}
    current_created = _row_created_at(current_row)
    if current_created is None:
        return phases
    current_status = (current_row.get("status") or "").lower()
    if current_status not in _TERMINAL:
        return phases

    filtered: dict[str, dict] = {}
    for phase, row in phases.items():
        if phase in PHASE_ORDER and _phase_after(phase, current_phase):
            created = _row_created_at(row)
            status = (row.get("status") or "").lower()
            if created is not None and created < current_created and status in _TERMINAL:
                continue
        filtered[phase] = row
    return filtered


def store_path() -> Path:
    override = os.environ.get("ZOE_PIPELINE_STORE_PATH", "").strip()
    if override:
        return Path(override)
    return Path(os.path.expanduser("~/.zoe/engineering_pipeline_runs.jsonl"))


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _latest_state_from_lines(lines: Iterable[str], task_ref: str) -> PipelineState | None:
    """Last-match-wins scan for ``task_ref``'s newest state.

    Takes any ITERABLE of lines — including an open file handle — so callers can
    stream the store instead of materialising it. This is an event-sourced store:
    every save_state re-appends a full state snapshot, so the file grows without
    bound (it reached 1.59 GB before the compactor existed). Reading it whole would
    pull all of that into RAM on a memory-tight Jetson. Only the latest matching
    payload is retained here, so memory stays O(one state) regardless of file size.
    """
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
                # Stream the handle: iterating it never materialises the file.
                # The scan now runs INSIDE the shared lock (it has to — you cannot
                # stream a handle after releasing it). That is the same lock TYPE
                # and ordering as before, held across the parse rather than only
                # the read. The trade is deliberate: the previous read() pulled the
                # entire store into RAM under this very lock — 1.59 GB before the
                # compactor existed — which is the hazard this fixes. LOCK_SH is
                # shared, so concurrent readers are unaffected; only a LOCK_EX
                # writer waits, and it already waited for the whole-file read.
                return _latest_state_from_lines(handle, task_ref)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
                # Stream rather than read() the whole store; already inside
                # LOCK_EX, so the hold is unchanged. The scan leaves the position
                # at EOF, and the append below re-seeks to SEEK_END explicitly, so
                # the write is unaffected.
                latest = _latest_state_from_lines(handle, state.task_ref)
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



def _already_covered_block(phase: str, block_reason: str | None) -> bool:
    return phase == "implement" and "ALREADY_COVERED" in str(block_reason or "").upper()


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


def complete_pipeline_after_external_merge(
    task_ref: str,
    *,
    pr_url: str | None = None,
    merge_sha: str | None = None,
    greptile_status: str | None = None,
    reason: str = "external PR merge recorded",
) -> PipelineState | None:
    """Journal a completed run when PR maintenance recovers a worker publish gap."""
    for attempt in range(2):
        state = load_latest_state(task_ref)
        if state is None:
            return None
        if state.status == "done":
            return state
        evidence = list(state.evidence)
        if pr_url and not any(item.kind == "pr" and item.artifact == pr_url for item in evidence):
            evidence.append(
                EvidenceItem(
                    kind="pr",
                    summary=pr_url[:500],
                    artifact=pr_url,
                    passed=True,
                    metadata={"source": "pr_maintenance", "phase": "implement"},
                )
            )
        if not any(
            item.kind == "greptile"
            and item.metadata.get("source") == "pr_maintenance"
            and item.metadata.get("phase") == "closeout"
            and item.metadata.get("merge_sha") == merge_sha
            for item in evidence
        ):
            evidence.append(
                EvidenceItem(
                    kind="greptile",
                    summary=(greptile_status or "MERGED")[:500],
                    artifact=pr_url,
                    passed=True,
                    metadata={
                        "source": "pr_maintenance",
                        "phase": "closeout",
                        "merge_sha": merge_sha,
                    },
                )
            )
        if not any(
            item.kind == "log"
            and item.metadata.get("source") == "pr_maintenance"
            and item.metadata.get("phase") == "retro"
            and item.metadata.get("merge_sha") == merge_sha
            for item in evidence
        ):
            evidence.append(
                EvidenceItem(
                    kind="log",
                    summary=reason[:500],
                    artifact=pr_url,
                    passed=True,
                    metadata={
                        "source": "pr_maintenance",
                        "phase": "retro",
                        "merge_sha": merge_sha,
                    },
                )
            )
        completed = state.model_copy(
            update={
                "phase": "retro",
                "status": "done",
                "evidence": evidence,
                "last_block_fingerprint": None,
                "repeated_block_count": 0,
                "block_classification": None,
                "split_packet": None,
                "history": [
                    *state.history,
                    TransitionRecord(
                        from_phase=state.phase,
                        to_phase="retro",
                        outcome="complete",
                        reason=reason,
                    ),
                ],
            }
        )
        try:
            return save_state(
                completed,
                event="external_merge_completed",
                extra={
                    "reason": reason,
                    "pr_url": pr_url,
                    "merge_sha": merge_sha,
                    "greptile_status": greptile_status,
                },
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


def _harness_verify_tests_enabled() -> bool:
    return (
        os.environ.get("ZOE_PIPELINE_HARNESS_VERIFY_TESTS", "true") or "true"
    ).strip().lower() not in {"0", "false", "no"}


def _pr_url_from_state(state: PipelineState) -> str:
    for item in reversed(state.evidence):
        if item.kind == "pr" and getattr(item, "artifact", None):
            return str(item.artifact).strip()
    return ""


async def _append_harness_focused_tests(state: PipelineState, phase: PipelinePhase) -> PipelineState:
    """Run the PR's focused tests harness-side and append `test` evidence.

    Makes verify deterministic: the harness runs the PR's changed test files
    itself instead of trusting the verify worker to do it. No-ops (returns state
    unchanged) when disabled, when passing test evidence already exists, when
    there is no PR to test, or when the runner cannot find/run focused tests
    (fail-open: the existing agent-driven flow then applies).
    """
    if not _harness_verify_tests_enabled():
        return state
    if any(
        item.kind == "test" and item.passed is True and item.metadata.get("phase") == phase
        for item in state.evidence
    ):
        return state
    pr_url = _pr_url_from_state(state)
    if not pr_url:
        return state
    from pipeline_focused_tests import focused_test_evidence_item, run_focused_pr_tests

    result = await _run_io(partial(run_focused_pr_tests, pr_url))
    if not result.ran:
        return state
    return with_evidence(state, focused_test_evidence_item(result, phase=phase))


def _harness_review_approve_enabled() -> bool:
    return (
        os.environ.get("ZOE_PIPELINE_HARNESS_REVIEW_APPROVE", "true") or "true"
    ).strip().lower() not in {"0", "false", "no"}


async def _append_harness_review_approval(state: PipelineState, phase: PipelinePhase) -> PipelineState:
    """Append objective `human` review-approval evidence when the PR passes review.

    Makes review deterministic: rather than trusting the zoe-reviewer agent (which
    can spuriously block "no code/evidence" or complete with an empty verdict), the
    harness approves review from objective signals — PR OPEN + all required CI
    checks green, on top of the verify phase having already run the PR's focused
    tests. Greptile threads/confidence are owned by the closeout greploop merge
    gate, NOT review (gating them here deadlocks, since a fresh PR has open Greptile
    threads at review time). No-ops (returns state unchanged) when disabled, when
    human evidence already exists, when there is no PR, or when the PR is not
    objectively review-ready (fail-open: the agent-driven flow then applies).
    """
    if not _harness_review_approve_enabled():
        return state
    if any(item.kind == "human" and item.passed is True for item in state.evidence):
        return state
    pr_url = _pr_url_from_state(state)
    if not pr_url:
        return state
    from pipeline_evidence import EvidenceItem
    from pipeline_review import assess_pr_review_ready

    readiness = await _run_io(partial(assess_pr_review_ready, pr_url))
    if not readiness.ready:
        return state
    return with_evidence(
        state,
        EvidenceItem(
            kind="human",
            summary=("harness review approval: " + readiness.reason)[:500],
            passed=True,
            metadata={
                "source": "harness",
                "phase": "review",
                "approver": "harness",
                "merge_readiness": "merge_ready",
            },
        ),
    )


def _harness_closeout_merge_enabled() -> bool:
    return (
        os.environ.get("ZOE_PIPELINE_HARNESS_CLOSEOUT_MERGE", "true") or "true"
    ).strip().lower() not in {"0", "false", "no"}


def _is_harness_closeout_merge_evidence(item: Any) -> bool:
    """True for the harness's OWN confirmed-merge closeout evidence.

    Single source of truth for both the _append_harness_closeout_merge idempotency
    guard and the sync_pipeline_from_chain closeout-completion check, so the two
    cannot drift if the evidence schema evolves.
    """
    md = getattr(item, "metadata", {}) or {}
    return (
        getattr(item, "kind", "") == "greptile"
        and getattr(item, "passed", None) is True
        and md.get("phase") == "closeout"
        and md.get("source") == "harness"
        and bool(md.get("merge_sha"))
    )


async def _append_harness_closeout_merge(state: PipelineState, phase: PipelinePhase) -> PipelineState:
    """Run the greploop merge guard harness-side and append `greptile` evidence on merge.

    Makes closeout deterministic: rather than trusting the closeout agent worker to
    invoke the greploop guard (it can bail "no valid PR URL"), the harness runs the
    proven guard CLI itself (which owns confidence/threads/squash-merge safety) and,
    when the PR ends up MERGED, records the closeout `greptile` evidence with the
    merge SHA. No-ops (returns state unchanged) when disabled, when closeout greptile
    evidence already exists, when there is no PR, or when the guard did not merge
    (fail-open: the agent-driven closeout flow then applies and can retry next cycle).
    """
    if not _harness_closeout_merge_enabled():
        return state
    # Idempotent only on the harness's OWN confirmed-merge evidence. We must NOT
    # skip just because the closeout agent recorded a greptile item (source!=
    # "harness"): that agent evidence can be written WITHOUT an actual merge, and
    # skipping here is exactly what let closeout false-complete (PR left open).
    if any(_is_harness_closeout_merge_evidence(item) for item in state.evidence):
        return state
    pr_url = _pr_url_from_state(state)
    if not pr_url:
        return state
    from pipeline_closeout import run_closeout_merge

    result = await _run_io(partial(run_closeout_merge, pr_url))
    if not result.merged:
        return state
    return with_evidence(
        state,
        EvidenceItem(
            kind="greptile",
            summary=("harness closeout merge: " + result.reason)[:500],
            artifact=pr_url,
            passed=True,
            metadata={
                "source": "harness",
                "phase": phase,
                "merge_sha": result.merge_sha,
            },
        ),
    )


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


def _run_is_already_covered(state: PipelineState) -> bool:
    """True when this run already proved no code change is needed.

    When ``implement`` finds the work already covered it records a
    ``skip_implementation`` transition carrying an ``ALREADY_COVERED`` reason and
    flips the run to the ``audit`` profile. History is append-only — unlike
    ``evidence``, which ``transition`` clears on ``request_changes`` /
    ``verification_failed`` — so it is the reliable marker across the loop-back
    transitions. Such a run has nothing left to implement/verify/review/merge, so
    its downstream no-op phases must converge to ``complete`` instead of bouncing
    review -> implement forever.
    """
    return any(
        record.outcome == "skip_implementation"
        and "ALREADY_COVERED" in str(getattr(record, "reason", "") or "").upper()
        for record in state.history
    )


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
    if any(
        item.kind == kind and item.metadata.get("phase") == phase and item.passed is True
        for item in state.evidence
    ):
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

    phases = _drop_stale_later_phase_rows(phases, state.phase)

    blocked_catch_up_allowed_from: PipelinePhase | None = None
    for phase in PHASE_ORDER:
        row = phases.get(phase)
        if not row:
            continue
        row_status = (row.get("status") or "").lower()
        skills = _PHASE_SKILLS.get(phase, ())

        if (
            row_status == "blocked"
            and _is_stale_duplicate_block(row)
            and _has_later_successful_phase(phases, phase)
        ):
            if state.status == "blocked" and state.phase == phase:
                blocked_catch_up_allowed_from = phase
            continue

        if phase == state.phase and row_status not in _TERMINAL and phase == "verify":
            state = await _append_harness_validators(state, "verify")

        if row_status not in _TERMINAL:
            continue

        task_id = row.get("id")
        detail: dict[str, Any] = {}
        if task_id:
            detail = await fetch_detail(task_id)

        while (
            phase != state.phase
            and _phase_after(phase, state.phase)
            and (state.status != "blocked" or state.phase == blocked_catch_up_allowed_from)
            and can_complete_phase(state)
        ):
            previous_phase = state.phase
            state = transition(
                state,
                "complete",
                reason=f"caught up to terminal {phase} row",
            )
            state = state.model_copy(
                update={
                    "last_block_fingerprint": None,
                    "repeated_block_count": 0,
                    "block_classification": None,
                    "split_packet": None,
                }
            )
            state = await _run_io(
                partial(
                    save_state,
                    state,
                    event="phase_catch_up",
                    extra={
                        "from_phase": previous_phase,
                        "to_phase": state.phase,
                        "row_phase": phase,
                    },
                )
            )

        if phase != state.phase:
            continue

        for item in evidence_from_handoff(phase, detail, skills=skills):  # type: ignore[arg-type]
            state = with_evidence(state, item)

        if phase == "implement" and audit_only_from_handoff(detail):
            state = state.model_copy(update={"evidence_profile": "audit"})

        if phase == "implement" and row_status in {"done", "archived"}:
            state = await _append_harness_validators(state, "implement")

        verify_harness_complete = False
        if phase == "verify" and row_status in _TERMINAL:
            # Deterministic verify: run the PR's validators + focused tests
            # harness-side so a verify worker that skipped the tests (completed
            # without `test` evidence) or spuriously blocked ("no PR to test")
            # cannot strand the chain. When the objective harness run satisfies
            # the evidence gate, complete verify regardless of the agent's
            # terminal signal; otherwise fall through to the agent-derived outcome.
            state = await _append_harness_validators(state, "verify")
            state = await _append_harness_focused_tests(state, "verify")
            if can_complete_phase(state):
                verify_harness_complete = True

        review_harness_complete = False
        if phase == "review" and row_status in _TERMINAL:
            # Deterministic review: the zoe-reviewer agent can spuriously block
            # ("cannot review without code/evidence") even with the PR_URL in its
            # handoff. Approve review from objective signals instead — PR open +
            # CI green + zero unresolved Greptile threads, on top of the verify
            # phase having already run the PR's focused tests. When that satisfies
            # the evidence gate, complete review regardless of the agent's signal;
            # otherwise fall through to the agent-derived outcome.
            state = await _append_harness_review_approval(state, "review")
            if can_complete_phase(state):
                review_harness_complete = True

        closeout_harness_complete = False
        closeout_merge_pending = False
        # Only the real-PR closeout path requires a harness-confirmed merge. The
        # audit / no-code closeout (no PR evidence, audit profile) has nothing to
        # merge and completes on its recorded evidence as before.
        if (
            phase == "closeout"
            and row_status in _TERMINAL
            and _harness_closeout_merge_enabled()
            and getattr(state, "evidence_profile", "") != "audit"
            and _pr_url_from_state(state)
        ):
            # Deterministic, AUTHORITATIVE closeout: run the proven greploop guard
            # CLI harness-side (regardless of any agent greptile claim) and complete
            # closeout ONLY on a harness-confirmed merge (source="harness" greptile +
            # merge_sha). An agent greptile item can be recorded without an actual
            # merge, so it must NOT advance closeout->retro (that left PRs open). If
            # the harness hasn't merged yet, hold closeout (retry next cycle) rather
            # than completing on the agent's unverified evidence.
            state = await _append_harness_closeout_merge(state, "closeout")
            harness_merged = any(
                _is_harness_closeout_merge_evidence(e) for e in state.evidence
            )
            if harness_merged and can_complete_phase(state):
                closeout_harness_complete = True
            elif not harness_merged:
                # Only the NOT-merged case suppresses completion (so an agent's
                # unverified greptile claim can't advance closeout). If the harness
                # DID merge but the gate is somehow unsatisfied, fall through to the
                # normal outcome path so the appended merge evidence is persisted
                # (gate_block) rather than discarded by a bare continue.
                closeout_merge_pending = True

        outcome = (
            "complete"
            if (verify_harness_complete or review_harness_complete or closeout_harness_complete)
            else infer_outcome(phase, row_status, detail)  # type: ignore[arg-type]
        )
        if closeout_merge_pending and outcome == "complete":
            # The PR is not actually merged yet; do not advance closeout->retro on
            # agent-claimed greptile evidence. Leave closeout pending so the harness
            # merge retries on the next poll cycle.
            continue
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
            and (_protocol_only_block(detail) or _run_is_already_covered(state))
            and (
                outcome in {"block", "verification_failed", "request_changes", "merge_blocked"}
                or (outcome == "complete" and not can_complete_phase(state))
            )
        ):
            # An already-covered run has no diff to verify/review/merge, so a
            # no-op downstream phase is recovered to completion. This covers both a
            # no-op block (e.g. review request_changes with no PR) and a terminal
            # "done" row whose gate evidence can't be produced (e.g. verify, where
            # the harness validators have no diff to pass on). Without this the run
            # either bounces review -> implement forever or stalls at the verify
            # gate, never reaching a terminal handoff and holding the lane.
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
            if _already_covered_block(phase, block_reason):  # type: ignore[arg-type]
                state = state.model_copy(update={"evidence_profile": "audit"})
                state = with_evidence(
                    state,
                    EvidenceItem(
                        kind="tool",
                        summary="focused harness test passed before edit; implementation already covered",
                        passed=True,
                        metadata={"source": "already_covered", "phase": "implement"},
                    ),
                )
                state = transition(state, "skip_implementation", reason=str(block_reason))
                state = state.model_copy(
                    update={
                        "last_block_fingerprint": None,
                        "repeated_block_count": 0,
                        "block_classification": None,
                        "split_packet": None,
                    }
                )
                state = await _run_io(
                    partial(
                        save_state,
                        state,
                        event="already_covered_implementation_skipped",
                        extra={"row_phase": phase, "reason": block_reason},
                    )
                )
                continue
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
            validator_hash_mismatch = state.phase == "verify" and not verify_validator_hash_matches(state)
            if validator_hash_mismatch:
                extra["validator_hash_mismatch"] = True
            if extra["missing"]:
                block_reason = "GATE_BLOCKED: missing required evidence " + ",".join(extra["missing"])
            elif validator_hash_mismatch:
                block_reason = "GATE_BLOCKED: validator hash mismatch"
            else:
                raise AssertionError(
                    "can_complete_phase returned False with no diagnosable gate reason "
                    f"(phase={state.phase!r}, evidence={[item.kind for item in state.evidence]!r})"
                )
            # Bounded auto-retry for the verify quality gate: a verify worker that
            # produced validator (+pr) evidence but skipped the focused pytest
            # leaves `test` evidence missing. Rather than terminally stranding the
            # chain (which strands the whole ticket on a single under-compliant
            # run), re-arm verify to todo — keeping the evidence already gathered —
            # so it re-dispatches under the hardened "you MUST run the PR's focused
            # tests" prompt. Bounded by verify attempts so it cannot loop.
            try:
                _verify_evidence_retry_limit = int(
                    os.environ.get("ZOE_PIPELINE_VERIFY_EVIDENCE_RETRY_LIMIT", "1") or "1"
                )
            except ValueError:
                _verify_evidence_retry_limit = 1
            if (
                state.phase == "verify"
                and not validator_hash_mismatch
                and extra["missing"] == ["test"]
                and state.attempts.get("verify", 0) <= _verify_evidence_retry_limit
            ):
                retry_reason = (
                    "VERIFY_EVIDENCE_RETRY: verify completed without `test` evidence "
                    "(focused pytest not run); re-arming verify to require it"
                )
                state = transition(state, "retry_evidence", reason=retry_reason)
                state = await _run_io(
                    partial(
                        save_state,
                        state,
                        event="verify_evidence_retry",
                        extra=extra,
                        allow_stale_evidence_merge=True,
                    )
                )
                return state
            state, _should_abort = record_block_fingerprint(
                state,
                block_fingerprint(phase, block_reason),  # type: ignore[arg-type]
            )
            # A gate block is already terminal for this phase until operator action
            # changes the state, so repeated-fingerprint escalation would be noise.
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
