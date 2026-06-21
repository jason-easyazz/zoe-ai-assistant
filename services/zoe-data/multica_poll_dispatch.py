"""Helpers for the Multica poll-loop webhook dispatch bridge."""
from __future__ import annotations

import datetime as _dt


def chain_needs_dispatch(chain: dict) -> bool:
    """Return True when a Hermes-assigned Multica issue should receive a Kanban phase.

    The poll bridge dispatches when there is no active run yet (``not_found``) or
    when the journal has a next ready phase with no Kanban row yet (``partial``).
    Legacy interrupted chains also report ``partial``. Active ``running`` and
    completed ``done`` chains are left alone. ``blocked`` chains are also left
    alone unless the pipeline has been reset to ``todo`` by an operator, in
    which case the stale executor row is treated as resumable.

    Pipeline ``fingerprint_abort`` / ``terminal_block`` suppresses redispatch
    regardless of executor or pipeline status.
    """
    if not chain:
        return False
    pipeline = chain.get("pipeline") or {}
    if pipeline.get("terminal_block") or pipeline.get("fingerprint_abort"):
        return False
    status = chain.get("status")
    if status == "blocked" and pipeline.get("status") == "todo":
        return True
    if status in ("running", "blocked", "done"):
        return False
    if status == "partial":
        return True
    if not chain.get("found"):
        return status == "not_found"
    return False


def _pipeline_suppressed(chain: dict) -> bool:
    pipeline = chain.get("pipeline") or {}
    return bool(pipeline.get("terminal_block") or pipeline.get("fingerprint_abort"))


def chain_needs_reconcile(chain: dict) -> bool:
    """True when a chain has regressed to a non-terminal ``partial`` state.

    A ``partial`` chain has a ready next phase needing dispatch. If the Multica
    board still shows the issue as ``in_review`` (set earlier when a PR appeared)
    while the journal bounced back to an earlier phase, the issue is neither a
    dispatch candidate (in_review issues are not polled for backfill) nor moved
    on by the done/blocked/running reconcile branches — so it freezes the single
    lane. This predicate flags exactly that case so the poll loop can converge
    the board back to ``in_progress`` and let the next cycle re-dispatch it.
    Terminal pipeline flags suppress reconciliation (those belong in ``blocked``).
    """
    if not chain or _pipeline_suppressed(chain):
        return False
    return chain.get("status") == "partial"


def chain_is_active(chain: dict) -> bool:
    """Return True when a chain belongs to the one-ticket-at-a-time lane.

    ``running`` is actively executing. ``partial`` means the journal has an
    existing run with a ready next phase that still needs dispatch, so it must
    also stay in the active lane. Terminal pipeline flags suppress dispatch and
    therefore also suppress occupying a slot.
    """
    if not chain or _pipeline_suppressed(chain):
        return False
    if chain.get("status") in ("running", "partial"):
        return True
    pipeline = chain.get("pipeline") or {}
    return pipeline.get("status") in ("running", "todo")


def chain_is_running(chain: dict) -> bool:
    """Return True only for active chains that do not need another dispatch."""
    if not chain or _pipeline_suppressed(chain):
        return False
    if chain.get("status") == "running":
        return True
    pipeline = chain.get("pipeline") or {}
    return pipeline.get("status") == "running"


def chain_poll_failed(chain: dict) -> bool:
    """True when the chain poll could not determine state (timeout/error sentinel).

    ``_poll_chain_guarded`` returns ``{"found": False, "status": "poll_timeout"|
    "poll_error"}`` when ``poll_ref`` times out or raises. That sentinel makes
    ``chain_needs_dispatch`` return False — which previously caused the poll loop
    to silently SKIP an in-progress chain forever (it stranded past implement,
    because an existing multi-row chain's poll is expensive and can time out under
    event-loop load, while a fresh todo's poll is cheap). Callers should treat a
    failed poll on a known in-progress chain as "state unknown — let the
    idempotent dispatch re-derive it", not as "inactive, skip".
    """
    if not chain:
        return True
    return bool(chain.get("timed_out")) or chain.get("status") in ("poll_timeout", "poll_error")


def _issue_age_hours(issue: dict, *, now: _dt.datetime) -> float | None:
    """Hours since the issue's last metadata update (falls back to created_at)."""
    raw = (issue or {}).get("updated_at") or (issue or {}).get("created_at") or ""
    if not raw:
        return None
    try:
        ts = _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return (now - ts).total_seconds() / 3600.0


def is_stale_in_progress(
    issue: dict, chain: dict, *, now: _dt.datetime, max_age_hours: float
) -> bool:
    """Return True when an ``in_progress`` issue is a dead chain holding the lane.

    A live chain advances and records progress, bumping the issue's metadata, so
    an ``in_progress`` ticket whose chain is no longer active (or could not be
    polled — e.g. a timed-out/dead executor ref) and that has had no metadata
    update for at least ``max_age_hours`` is a zombie occupying the single
    one-ticket lane. Resumable chains (``partial`` / pipeline ``todo``) stay
    active, so they are never reclaimed here.
    """
    if chain_is_active(chain):
        return False
    age = _issue_age_hours(issue, now=now)
    return age is not None and age >= max_age_hours
