"""A3 (2026-10-19): every intent the brain may dispatch has a direct handler, and nothing falls back to an mcporter subprocess.

The retired fallback (intent_router ``_build_command`` + ``_run_mcporter``) ran ``mcporter-safe call zoe-data.<tool>`` whenever a
direct executor returned None, and for four intents it was the ONLY implementation. Its tools/call carried no user, so
mcp_server wrote as family-admin, and user text was spliced into the command line. Now:

* the intents with no hand-written executor (journal prompts / streak, transactions, a content-less journal_create) run the
  same mcp_server tool IN PROCESS with the acting user passed explicitly, and their arguments are a dict, never a command;
* a direct executor that declines is the answer - nothing else is tried.
"""
import ast
import inspect
import json
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

import intent_router
from intent_router import Intent, execute_intent

SYSTEM_PY = Path(__file__).resolve().parents[1] / "routers" / "system.py"
USER = "a3-direct-handler-user"


def _dispatchable() -> set[str]:
    """``routers/system.py::_DISPATCHABLE_INTENTS``, read from source (importing the router drags the whole app in)."""
    for node in ast.walk(ast.parse(SYSTEM_PY.read_text())):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_DISPATCHABLE_INTENTS" for t in node.targets):
            return {elt.value for elt in node.value.args[0].elts}
    raise AssertionError("_DISPATCHABLE_INTENTS not found in routers/system.py")


def _unhandled(names) -> list[str]:
    """Intents execute_intent has no branch for AND that map to no in-process tool: they could only return None."""
    src = inspect.getsource(intent_router.execute_intent)
    return sorted(n for n in names if f'"{n}"' not in src and intent_router._in_process_tool_call(Intent(n, {})) is None)


def test_every_dispatchable_intent_has_a_handler():
    names = _dispatchable()
    assert len(names) > 20, names
    assert _unhandled(names) == []


def test_handler_check_flags_an_intent_with_no_handler():
    """Negative control: the check above is not vacuous."""
    assert _unhandled({"calendar_show", "a3_no_such_intent"}) == ["a3_no_such_intent"]


def test_the_subprocess_fallback_is_gone():
    assert not hasattr(intent_router, "_run_mcporter")
    assert not hasattr(intent_router, "_build_command")
    assert not hasattr(intent_router, "MCPORTER")
    src = Path(intent_router.__file__).read_text()
    assert "mcporter-safe" not in src
    assert "shlex" not in src


@pytest.fixture
def tool_calls(monkeypatch):
    """A stand-in ``mcp_server`` (so the slim CI lane never imports the real one) that records each in-process tool call."""
    class _Calls(list):
        replies: dict

    calls = _Calls()
    calls.replies = {}
    replies = calls.replies

    async def handle_tool(name, args, actor_context=None):
        calls.append((name, dict(args)))
        reply = replies.get(name, {})
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply)

    fake = types.ModuleType("mcp_server")
    fake.handle_tool = handle_tool
    monkeypatch.setitem(sys.modules, "mcp_server", fake)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,slots,tool,reply,expect",
    [
        ("journal_prompt", {}, "journal_get_prompts", {"prompts": ["What made you smile?"]}, "What made you smile?"),
        ("journal_create", {"content": ""}, "journal_get_prompts", {"prompts": ["What made you smile?"]},
         "journal prompts to get you started"),
        ("journal_streak", {}, "journal_get_streak", {"current_streak": 3, "longest_streak": 5, "total_entries": 9},
         "3-day streak"),
        ("transaction_summary", {"period": "month"}, "transaction_summary",
         {"period": "month", "total_expense": 12.5, "total_income": 0, "net": -12.5}, "Total spent: $12.50"),
        ("transaction_create", {"description": "coffee", "amount": 4.5}, "transaction_create",
         {"id": "t1", "description": "coffee", "amount": 4.5, "status": "created"}, "Recorded: $4.5 for coffee."),
    ],
)
async def test_tool_only_intents_run_in_process_as_the_acting_user(tool_calls, name, slots, tool, reply, expect):
    tool_calls.replies[tool] = reply
    result = await execute_intent(Intent(name, dict(slots)), USER)
    assert result and expect in result
    assert len(tool_calls) == 1
    called, args = tool_calls[0]
    assert called == tool
    assert args["_user_id"] == USER


@pytest.mark.asyncio
async def test_user_text_reaches_the_tool_verbatim_never_through_a_shell(tool_calls):
    nasty = 'lunch" amount=99999 ; rm -rf ~ #'
    tool_calls.replies["transaction_create"] = {"description": nasty, "amount": 7, "status": "created"}
    await execute_intent(Intent("transaction_create", {"description": nasty, "amount": 7}), USER)
    _, args = tool_calls[0]
    assert args["description"] == nasty
    assert args["amount"] == 7


@pytest.mark.asyncio
async def test_a_failed_in_process_write_says_so_and_claims_nothing(tool_calls):
    tool_calls.replies["transaction_create"] = {"error": "insert failed"}
    result = await execute_intent(Intent("transaction_create", {"description": "coffee", "amount": 4}), USER)
    assert result and "nothing has been recorded" in result
    assert "Recorded" not in result


@pytest.mark.asyncio
async def test_a_failed_in_process_read_falls_through(tool_calls):
    tool_calls.replies["journal_get_streak"] = RuntimeError("pool down")
    assert await execute_intent(Intent("journal_streak", {}), USER) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,direct,slots",
    [
        ("list_add", "_execute_list_add_direct", {"item": "milk", "list_type": "shopping"}),
        ("list_show", "_execute_list_show_direct", {"list_type": "shopping"}),
        ("list_remove", "_execute_list_remove_direct", {"item": "milk"}),
        ("calendar_create", "_execute_calendar_create_direct", {"title": "dentist", "date": "2099-06-15"}),
        ("calendar_show", "_execute_calendar_show_direct", {}),
        ("reminder_create", "_execute_reminder_create_direct", {"title": "call the plumber"}),
        ("reminder_list", "_execute_reminder_list_direct", {}),
        ("note_create", "_execute_note_create_direct", {"content": "gift ideas"}),
        ("journal_create", "_execute_journal_create_direct", {"content": "a good day"}),
        ("people_create", "_execute_people_create_direct", {"name": "Percival"}),
    ],
)
async def test_a_declining_direct_executor_is_final(monkeypatch, tool_calls, name, direct, slots):
    """The retired fallback ran mcporter here and wrote as family-admin. Now a decline returns None and no tool runs."""

    async def declines(_intent, _uid):
        return None

    monkeypatch.setattr(intent_router, direct, declines)
    assert await execute_intent(Intent(name, dict(slots)), USER) is None
    assert tool_calls == []
