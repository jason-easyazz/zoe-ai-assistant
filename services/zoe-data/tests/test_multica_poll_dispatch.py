"""Tests for Multica poll-loop dispatch helpers."""

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import datetime as dt

from multica_poll_dispatch import (
    chain_is_active,
    chain_is_running,
    chain_needs_dispatch,
    chain_needs_reconcile,
    chain_poll_failed,
    is_stale_in_progress,
)
import executors.kanban_adapter as ka


_NOW = dt.datetime(2026, 6, 14, 12, 0, tzinfo=dt.timezone.utc)


def _aged(hours):
    return (_NOW - dt.timedelta(hours=hours)).isoformat()


def test_chain_poll_failed_detects_sentinels_but_not_real_status():
    # The timeout/error sentinels from _poll_chain_guarded — a failed poll, NOT a
    # real "inactive" state. Previously these silently stranded in-progress chains.
    assert chain_poll_failed({"found": False, "status": "poll_timeout", "timed_out": True}) is True
    assert chain_poll_failed({"found": False, "status": "poll_error", "error": "boom"}) is True
    assert chain_poll_failed({}) is True
    # Real statuses are NOT poll failures.
    assert chain_poll_failed({"status": "partial", "phases": {"implement": "done"}}) is False
    assert chain_poll_failed({"status": "running"}) is False
    assert chain_poll_failed({"found": False, "status": "not_found"}) is False


def test_resolve_chain_for_dispatch_repolls_only_on_sentinel():
    """The re-poll recovery (main._resolve_chain_for_dispatch) that stops a poll
    timeout/error sentinel from stranding an in-progress chain: a sentinel triggers
    ONE fresh re-poll with a longer (>=60s) timeout, then the backfill decides on
    the resolved chain; a real chain is returned unchanged with no extra poll."""
    import asyncio

    from main import _resolve_chain_for_dispatch

    calls = []

    async def _repoll(ref, *, issue=None, timeout=None):
        calls.append(timeout)
        return {"found": True, "status": "partial", "phases": {"implement": "done"}}

    sentinel = {"found": False, "status": "poll_timeout", "timed_out": True}
    out = asyncio.run(
        _resolve_chain_for_dispatch(
            sentinel, ref="multica:x", issue={"id": "x"}, poll_guarded=_repoll, poll_timeout=20.0
        )
    )
    assert out["status"] == "partial"
    assert len(calls) == 1 and calls[0] >= 60.0

    real = {"found": True, "status": "partial", "phases": {"implement": "done"}}
    out2 = asyncio.run(
        _resolve_chain_for_dispatch(
            real, ref="multica:y", issue={"id": "y"}, poll_guarded=_repoll, poll_timeout=20.0
        )
    )
    assert out2 is real
    assert len(calls) == 1  # no second poll for a real chain


def test_chain_needs_dispatch_not_found():
    assert chain_needs_dispatch({"found": False, "status": "not_found"}) is True


def test_chain_needs_dispatch_partial():
    assert chain_needs_dispatch({"found": True, "status": "partial"}) is True


def test_chain_needs_dispatch_running_blocked_done():
    assert chain_needs_dispatch({"found": True, "status": "running"}) is False
    assert chain_needs_dispatch({"found": True, "status": "blocked"}) is False
    assert chain_needs_dispatch({"found": True, "status": "done"}) is False


def test_chain_needs_reconcile_partial_non_terminal():
    # A partial (regressed, ready-to-dispatch) chain needs board reconciliation.
    assert chain_needs_reconcile({"found": True, "status": "partial"}) is True


def test_chain_needs_reconcile_suppressed_by_terminal_flags():
    assert (
        chain_needs_reconcile(
            {"found": True, "status": "partial", "pipeline": {"terminal_block": True}}
        )
        is False
    )
    assert (
        chain_needs_reconcile(
            {"found": True, "status": "partial", "pipeline": {"fingerprint_abort": True}}
        )
        is False
    )


def test_chain_needs_reconcile_non_partial_statuses():
    # running/blocked/done/not_found are handled by their own branches.
    assert chain_needs_reconcile({"found": True, "status": "running"}) is False
    assert chain_needs_reconcile({"found": True, "status": "blocked"}) is False
    assert chain_needs_reconcile({"found": True, "status": "done"}) is False
    assert chain_needs_reconcile({"found": False, "status": "not_found"}) is False
    assert chain_needs_reconcile({}) is False


def test_chain_needs_dispatch_for_operator_resumed_blocked_executor_row():
    chain = {
        "found": True,
        "status": "blocked",
        "blocker": "SCOUT_BUDGET: stale blocked row from before a plan adjustment",
        "pipeline": {"status": "todo", "terminal_block": False, "fingerprint_abort": False},
    }
    assert chain_needs_dispatch(chain) is True


def test_chain_needs_dispatch_does_not_resume_terminal_blocked_pipeline():
    chain = {
        "found": True,
        "status": "blocked",
        "pipeline": {"status": "todo", "terminal_block": True, "fingerprint_abort": False},
    }
    assert chain_needs_dispatch(chain) is False


def test_chain_needs_dispatch_does_not_resume_fingerprint_blocked_pipeline():
    chain = {
        "found": True,
        "status": "blocked",
        "pipeline": {"status": "todo", "terminal_block": False, "fingerprint_abort": True},
    }
    assert chain_needs_dispatch(chain) is False


def test_chain_needs_dispatch_suppressed_on_fingerprint_abort():
    chain = {
        "found": True,
        "status": "partial",
        "pipeline": {"terminal_block": True, "fingerprint_abort": True},
    }
    assert chain_needs_dispatch(chain) is False


def test_chain_needs_dispatch_empty_or_missing_status():
    assert chain_needs_dispatch({}) is False
    assert chain_needs_dispatch({"found": True}) is False


def test_implement_body_requires_kanban_terminal_tools():
    import executors.kanban_adapter as ka

    body = ka.KanbanAdapter()._build_body(
        "implement",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": ""},
        "ZOE-9",
    )
    assert "kanban_complete" in body
    assert "kanban_block" in body
    assert "kanban_show" in body
    assert "TERMINAL PROTOCOL" in body


def test_chain_needs_dispatch_false_when_blocked_protocol_violation():
    chain = {
        "found": True,
        "status": "blocked",
        "blocker": "BLOCKER=PROTOCOL_VIOLATION: worker exited without kanban_complete/kanban_block",
    }
    assert chain_needs_dispatch(chain) is False


def test_chain_is_active_counts_running_and_partial():
    assert chain_is_active({"found": True, "status": "running"}) is True
    assert chain_is_active({"found": True, "status": "partial"}) is True
    assert chain_is_active({"found": True, "status": "blocked"}) is False
    assert chain_is_active({"found": True, "status": "done"}) is False
    assert chain_is_active({"found": False, "status": "not_found"}) is False


def test_chain_is_active_counts_journal_ready_phase_without_terminal_block():
    assert chain_is_active({"pipeline": {"status": "todo"}}) is True
    assert chain_is_active({"pipeline": {"status": "running"}}) is True
    assert chain_is_active({"status": "partial", "pipeline": {"terminal_block": True}}) is False
    assert chain_is_active({"status": "partial", "pipeline": {"fingerprint_abort": True}}) is False
    assert chain_is_active({"pipeline": {"status": "todo", "terminal_block": True}}) is False
    assert chain_is_active({"pipeline": {"status": "todo", "fingerprint_abort": True}}) is False


def test_chain_is_running_excludes_partial_backfill_work():
    assert chain_is_running({"found": True, "status": "running"}) is True
    assert chain_is_running({"found": True, "status": "partial"}) is False
    assert chain_is_running({"pipeline": {"status": "running"}}) is True
    assert chain_is_running({"pipeline": {"status": "todo"}}) is False
    assert chain_is_running({"status": "running", "pipeline": {"terminal_block": True}}) is False


def test_is_stale_in_progress_reclaims_old_dead_chain():
    # Chain no longer active (e.g. not_found) and untouched past the window.
    issue = {"updated_at": _aged(8)}
    assert is_stale_in_progress(
        issue, {"found": False, "status": "not_found"}, now=_NOW, max_age_hours=6
    ) is True


def test_is_stale_in_progress_reclaims_timed_out_poll():
    # A dead executor ref returns the poll-timeout sentinel; treat as inactive.
    issue = {"updated_at": _aged(72)}
    sentinel = {"found": False, "status": "poll_timeout", "timed_out": True}
    assert is_stale_in_progress(issue, sentinel, now=_NOW, max_age_hours=6) is True


def test_is_stale_in_progress_spares_active_chain_even_when_old():
    issue = {"updated_at": _aged(48)}
    for chain in (
        {"found": True, "status": "running"},
        {"found": True, "status": "partial"},
        {"pipeline": {"status": "todo"}},
    ):
        assert is_stale_in_progress(issue, chain, now=_NOW, max_age_hours=6) is False


def test_is_stale_in_progress_spares_recently_updated_chain():
    issue = {"updated_at": _aged(1)}
    assert is_stale_in_progress(
        issue, {"found": False, "status": "not_found"}, now=_NOW, max_age_hours=6
    ) is False


def test_is_stale_in_progress_needs_a_timestamp_to_judge_age():
    # No updated_at/created_at -> cannot prove staleness, so never reclaim.
    assert is_stale_in_progress(
        {}, {"found": False, "status": "not_found"}, now=_NOW, max_age_hours=6
    ) is False
