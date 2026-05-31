"""Helpers for the Multica poll-loop webhook dispatch bridge."""
from __future__ import annotations


def chain_needs_dispatch(chain: dict) -> bool:
    """Return True when a Hermes-assigned Multica issue should receive a Kanban chain.

    The poll bridge dispatches when there is no active chain yet (``not_found``) or
    when a prior dispatch was interrupted (``partial``). Active ``running`` /
    ``blocked`` chains and completed ``done`` chains are left alone.
    """
    if not chain:
        return False
    status = chain.get("status")
    if status in ("running", "blocked", "done"):
        return False
    if status == "partial":
        return True
    if not chain.get("found"):
        return status == "not_found"
    return False
