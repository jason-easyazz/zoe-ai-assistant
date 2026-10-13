"""A mcporter fallback records a write receipt ONLY when the result says the row was written (PR #2004 review).

``mcp_server.handle_tool`` catches a DB / validation exception and returns ``{"error": ...}`` with ``isError`` false, so the
``mcporter-safe`` subprocess exits 0 having written nothing. Exit 0 is therefore not proof; the receipt - the only thing the
write-claim guard lets a brain claim stand on - needs an explicit ``status`` of added / created and no error.
"""
import pytest

pytestmark = pytest.mark.ci_safe

import write_receipts as wr
from intent_router import Intent, execute_intent

USER = "receipt-verify-user"

CASES = [
    ("list_add", {"item": "milk", "list_type": "shopping"}, "list", "_execute_list_add_direct", "added"),
    ("calendar_create", {"title": "dentist", "date": "2099-06-15", "time": "09:00"}, "calendar",
     "_execute_calendar_create_direct", "created"),
    ("reminder_create", {"title": "call the plumber", "date": "2099-06-15"}, "reminder",
     "_execute_reminder_create_direct", "created"),
    ("note_create", {"title": "gift ideas", "content": "gift ideas"}, "note", "_execute_note_create_direct", "created"),
]


async def _run(monkeypatch, name, slots, direct, raw):
    async def no_direct(_intent, _uid):
        return None

    async def fake_mcporter(_cmd):
        return raw

    monkeypatch.setattr(f"intent_router.{direct}", no_direct)
    monkeypatch.setattr("intent_router._build_command", lambda _i, _u: "mcporter-safe call x")
    monkeypatch.setattr("intent_router._run_mcporter", fake_mcporter)
    wr.reset(USER)
    started = wr.begin()
    reply = await execute_intent(Intent(name, dict(slots)), USER)
    return reply, started


@pytest.mark.asyncio
@pytest.mark.parametrize("name,slots,kind,direct,status", CASES)
async def test_a_success_result_records_a_receipt(monkeypatch, name, slots, kind, direct, status):
    reply, started = await _run(monkeypatch, name, slots, direct, f'{{"id": "r1", "status": "{status}"}}')
    assert wr.has(USER, started, kind), reply


@pytest.mark.asyncio
@pytest.mark.parametrize("name,slots,kind,direct,status", CASES)
@pytest.mark.parametrize(
    "raw",
    [
        '{"error": "connection closed"}',   # handle_tool caught an exception: exit 0, nothing written
        '{"error": ""}',
        "{}",                               # empty result
        "",                                 # nothing on stdout
        "not json at all",
        "[]",
        '{"id": "r1"}',                     # no status
        '{"status": "failed"}',
        '{"status": "created", "error": "partial write rolled back"}',
        "null",
    ],
)
async def test_a_failed_empty_or_error_result_records_nothing(monkeypatch, name, slots, kind, direct, status, raw):
    reply, started = await _run(monkeypatch, name, slots, direct, raw)
    assert not wr.has(USER, started), (raw, reply)


@pytest.mark.asyncio
async def test_an_error_result_is_reported_honestly_not_as_a_save(monkeypatch):
    reply, _ = await _run(monkeypatch, "list_add", {"item": "milk", "list_type": "shopping"},
                          "_execute_list_add_direct", '{"error": "db down"}')
    assert reply.startswith("Sorry, that didn't work")


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["{}", "", "plain text", '{"id": "x"}'])
async def test_an_unconfirmed_result_never_templates_a_saved_line(monkeypatch, raw):
    reply, _ = await _run(monkeypatch, "list_add", {"item": "milk", "list_type": "shopping"},
                          "_execute_list_add_direct", raw)
    assert reply and "Added milk" not in reply
    assert "couldn't confirm" in reply
