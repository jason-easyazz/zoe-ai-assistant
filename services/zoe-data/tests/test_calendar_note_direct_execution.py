"""Direct-execution tests for the write intents that route through mcporter.

Mirrors test_list_direct_execution.py (#993) and test_reminder_direct_execution.py
(#960). calendar_create / note_create / journal_create / people_create used to
fall through to the mcporter subprocess path in intent_router, which spawns a
second mcp_server that dies on DB-pool init and returns None → ok:false, so the
write silently failed on the Flue sidecar. The direct executors write straight
through get_db_ctx, mirroring mcp_server's corresponding tool storage semantics
(table names, columns, category/visibility defaults, comma-split journal tags).
"""
import contextlib
import json

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep write-path guards -> GitHub blocking lane (#960/#993 suites)

from intent_router import (
    Intent,
    _execute_calendar_create_direct,
    _execute_journal_create_direct,
    _execute_note_create_direct,
    _execute_people_create_direct,
    execute_intent,
)


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeDB:
    """Captures every SQL statement so tests can assert what was written.

    The four executors under test are pure INSERTs (no SELECT), so a trivial
    cursor is enough. ``transaction`` is intentionally absent — these single
    INSERTs don't need one.
    """

    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return _Cursor()

    def sql_matching(self, needle):
        return [(sql, params) for sql, params in self.calls if needle in sql]


def _fake_db_ctx(db):
    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    return ctx


def _fail_mcporter(monkeypatch, msg="direct path should avoid mcporter"):
    async def fail(_cmd):
        raise AssertionError(msg)

    monkeypatch.setattr("intent_router._run_mcporter", fail)


def _silence_ui(monkeypatch):
    async def noop(_channel, _event, _data):
        return None

    monkeypatch.setattr("intent_router._notify_ui_channel", noop)


# --- calendar_create --------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_create_writes_event_and_confirms(monkeypatch):
    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    result = await execute_intent(
        Intent("calendar_create",
               {"title": "Dentist", "date": "2026-07-10", "time": "14:30"}),
        "family-admin",
    )

    assert result is not None
    assert result.startswith("Added Dentist to your calendar")
    inserts = db.sql_matching("INSERT INTO events")
    assert len(inserts) == 1
    _sql, params = inserts[0]
    assert "Dentist" in params
    assert "2026-07-10" in params
    assert "14:30" in params
    # timed event → all_day 0, visibility family
    assert 0 in params and "family" in params


@pytest.mark.asyncio
async def test_calendar_create_all_day_when_no_time(monkeypatch):
    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    result = await _execute_calendar_create_direct(
        Intent("calendar_create", {"title": "Birthday", "date": "2026-07-11"}),
        "family-admin",
    )

    assert result is not None
    _sql, params = db.sql_matching("INSERT INTO events")[0]
    # all_day flag is 1 when no start_time resolved
    assert 1 in params
    assert None in params  # start_time is None


@pytest.mark.asyncio
async def test_calendar_create_no_date_defaults_to_today(monkeypatch):
    """#1038: a dateless quick-add ("add lunch with Jess") means TODAY — the
    direct executor fills today_for_zoe_tz() and succeeds; no mcporter fallback."""
    from time_utils import today_for_zoe_tz

    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))

    async def dead_mcporter(_cmd):
        return None

    monkeypatch.setattr("intent_router._run_mcporter", dead_mcporter)

    result = await execute_intent(
        Intent("calendar_create", {"title": "Someday"}), "family-admin"
    )

    assert result is not None and "today" in result.lower()
    _sql, params = db.sql_matching("INSERT INTO events")[0]
    assert today_for_zoe_tz().isoformat() in params


@pytest.mark.asyncio
async def test_calendar_create_unparseable_date_returns_none(monkeypatch):
    """#1038 (review fix): a date that WAS given but can't be parsed must FAIL
    (None → ok:false), not silently land the event on today — the wrong day with
    no signal. Distinct from the absent-date case above.

    The direct executor *declines* this case (returns None) and execute_intent
    falls through to mcporter by design — so a spy (not _fail_mcporter) proves
    the decline actually happened rather than the direct path being skipped,
    while a dead return still exercises the ok:false end state."""
    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))

    mcporter_calls = []

    async def spy_dead_mcporter(cmd):
        mcporter_calls.append(cmd)
        return None

    monkeypatch.setattr("intent_router._run_mcporter", spy_dead_mcporter)

    result = await execute_intent(
        Intent("calendar_create", {"title": "Dentist", "date": "the twelfth of never"}),
        "family-admin",
    )

    assert result is None
    assert db.sql_matching("INSERT INTO events") == []
    # The fall-through fired → the direct executor really declined (no vacuous
    # pass where the direct path was never reached at all).
    assert mcporter_calls, "expected fall-through to mcporter after the direct decline"


@pytest.mark.asyncio
async def test_calendar_create_genuine_failure_returns_none(monkeypatch):
    @contextlib.asynccontextmanager
    async def broken_ctx():
        raise RuntimeError("db unavailable")
        yield  # pragma: no cover

    async def dead_mcporter(_cmd):
        return None

    monkeypatch.setattr("database.get_db_ctx", broken_ctx)
    monkeypatch.setattr("intent_router._run_mcporter", dead_mcporter)

    result = await execute_intent(
        Intent("calendar_create", {"title": "X", "date": "2026-07-10"}), "family-admin"
    )

    assert result is None


# --- note_create ------------------------------------------------------------


@pytest.mark.asyncio
async def test_note_create_writes_note_and_confirms(monkeypatch):
    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    result = await execute_intent(
        Intent("note_create", {"title": "Shopping", "content": "milk and eggs"}),
        "family-admin",
    )

    assert result == "Saved your note."
    inserts = db.sql_matching("INSERT INTO notes")
    assert len(inserts) == 1
    _sql, params = inserts[0]
    assert "milk and eggs" in params
    assert "Shopping" in params
    # visibility personal, category default general
    assert "personal" in params and "general" in params


@pytest.mark.asyncio
async def test_note_create_empty_content_returns_none(monkeypatch):
    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))

    async def dead_mcporter(_cmd):
        return None

    monkeypatch.setattr("intent_router._run_mcporter", dead_mcporter)

    result = await execute_intent(
        Intent("note_create", {"title": "Empty"}), "family-admin"
    )

    assert result is None
    assert db.sql_matching("INSERT INTO notes") == []


@pytest.mark.asyncio
async def test_note_create_genuine_failure_returns_none(monkeypatch):
    @contextlib.asynccontextmanager
    async def broken_ctx():
        raise RuntimeError("db unavailable")
        yield  # pragma: no cover

    async def dead_mcporter(_cmd):
        return None

    monkeypatch.setattr("database.get_db_ctx", broken_ctx)
    monkeypatch.setattr("intent_router._run_mcporter", dead_mcporter)

    result = await execute_intent(
        Intent("note_create", {"content": "x"}), "family-admin"
    )

    assert result is None


@pytest.mark.asyncio
async def test_note_create_survives_memory_mirror_runtime_error(monkeypatch):
    """Best-effort semantics: if _store_note_memory imports fine but raises at
    runtime, the note write still lands and returns a success confirmation
    (ok:true), never degrading to None/ok:false."""
    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("mempalace down")

    monkeypatch.setattr("routers.notes._store_note_memory", boom)

    result = await execute_intent(
        Intent("note_create", {"content": "milk and eggs"}), "family-admin"
    )

    assert result == "Saved your note."
    assert len(db.sql_matching("INSERT INTO notes")) == 1


# --- journal_create ---------------------------------------------------------


@pytest.mark.asyncio
async def test_journal_create_writes_entry_with_tags(monkeypatch):
    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    result = await _execute_journal_create_direct(
        Intent("journal_create",
               {"content": "Good day", "tags": "walk, sun"}),
        "family-admin",
    )

    assert result == "Saved your journal entry."
    inserts = db.sql_matching("INSERT INTO journal_entries")
    assert len(inserts) == 1
    _sql, params = inserts[0]
    assert "Good day" in params
    # tags comma-split into JSON array, mirroring mcp_server
    assert json.dumps(["walk", "sun"]) in params


@pytest.mark.asyncio
async def test_journal_create_empty_content_returns_none(monkeypatch):
    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))

    result = await _execute_journal_create_direct(
        Intent("journal_create", {"content": "   "}), "family-admin"
    )

    assert result is None
    assert db.sql_matching("INSERT INTO journal_entries") == []


# --- people_create ----------------------------------------------------------


@pytest.mark.asyncio
async def test_people_create_writes_person_and_confirms(monkeypatch):
    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    result = await execute_intent(
        Intent("people_create", {"name": "Alice", "relationship": "sister"}),
        "family-admin",
    )

    assert result == "Added Alice as your sister to your contacts."
    inserts = db.sql_matching("INSERT INTO people")
    assert len(inserts) == 1
    _sql, params = inserts[0]
    assert "Alice" in params and "sister" in params
    # Private by default (not family-shared) + context 'personal'; circle NULL
    # (no bogus "circle" literal). See fix: people_create defaults to private.
    assert "personal" in params and "family" not in params
    assert "circle" not in params  # circle is NULL now, not the column-name literal


@pytest.mark.asyncio
async def test_people_create_empty_name_returns_none(monkeypatch):
    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))

    result = await _execute_people_create_direct(
        Intent("people_create", {"name": ""}), "family-admin"
    )

    assert result is None
    assert db.sql_matching("INSERT INTO people") == []


# --- wiring: direct path runs ahead of mcporter -----------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent_name, target, slots, reply",
    [
        ("calendar_create", "_execute_calendar_create_direct",
         {"title": "X", "date": "2026-07-10"}, "Added X to your calendar today."),
        ("note_create", "_execute_note_create_direct",
         {"content": "hi"}, "Saved your note."),
        ("journal_create", "_execute_journal_create_direct",
         {"content": "hi"}, "Saved your journal entry."),
        ("people_create", "_execute_people_create_direct",
         {"name": "Bob"}, "Added Bob to your contacts."),
    ],
)
async def test_direct_path_used_before_mcporter(
    monkeypatch, intent_name, target, slots, reply
):
    calls = []

    async def fake_direct(intent, user_id):
        calls.append((intent.name, dict(intent.slots), user_id))
        return reply

    async def fail_mcporter(_cmd):
        raise AssertionError(f"{intent_name} direct path should avoid mcporter")

    monkeypatch.setattr(f"intent_router.{target}", fake_direct)
    monkeypatch.setattr("intent_router._run_mcporter", fail_mcporter)

    result = await execute_intent(Intent(intent_name, slots), "family-admin")

    assert result == reply
    assert calls == [(intent_name, slots, "family-admin")]
