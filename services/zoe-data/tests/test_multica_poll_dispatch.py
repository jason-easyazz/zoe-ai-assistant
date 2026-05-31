"""Tests for Multica poll-loop dispatch helpers."""
from multica_poll_dispatch import chain_needs_dispatch


def test_chain_needs_dispatch_not_found():
    assert chain_needs_dispatch({"found": False, "status": "not_found"}) is True


def test_chain_needs_dispatch_partial():
    assert chain_needs_dispatch({"found": True, "status": "partial"}) is True


def test_chain_needs_dispatch_running_blocked_done():
    assert chain_needs_dispatch({"found": True, "status": "running"}) is False
    assert chain_needs_dispatch({"found": True, "status": "blocked"}) is False
    assert chain_needs_dispatch({"found": True, "status": "done"}) is False


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
