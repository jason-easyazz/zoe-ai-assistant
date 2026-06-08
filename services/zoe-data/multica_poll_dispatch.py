"""Helpers for the Multica poll-loop webhook dispatch bridge."""
from __future__ import annotations


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
