"""Tests for Multica poll-loop dispatch helpers."""
from multica_poll_dispatch import chain_is_active, chain_is_running, chain_needs_dispatch
import executors.kanban_adapter as ka


def test_chain_needs_dispatch_not_found():
    assert chain_needs_dispatch({"found": False, "status": "not_found"}) is True


def test_chain_needs_dispatch_partial():
    assert chain_needs_dispatch({"found": True, "status": "partial"}) is True


def test_chain_needs_dispatch_running_blocked_done():
    assert chain_needs_dispatch({"found": True, "status": "running"}) is False
    assert chain_needs_dispatch({"found": True, "status": "blocked"}) is False
    assert chain_needs_dispatch({"found": True, "status": "done"}) is False


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
