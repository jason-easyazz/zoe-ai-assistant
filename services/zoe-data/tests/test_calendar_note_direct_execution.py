"""Direct-execution tests for the calendar / note / journal / people write intents.

Mirrors test_list_direct_execution.py (#993) and test_reminder_direct_execution.py
(#960). These intents used to fall through to an mcporter subprocess (retired, A3
2026-10-19: it wrote as family-admin). The direct executors write straight
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

    result = await execute_intent(
        Intent("calendar_create",
               {"title": "School assembly", "date": "2026-07-10", "time": "14:30"}),
        "family-admin",
    )

    assert result is not None
    assert result.startswith("Added School assembly to your calendar")
    inserts = db.sql_matching("INSERT INTO events")
    assert len(inserts) == 1
    _sql, params = inserts[0]
    assert "School assembly" in params
    assert "2026-07-10" in params
    assert "14:30" in params
    # timed event → all_day 0, visibility family
    assert 0 in params and "family" in params


@pytest.mark.asyncio
async def test_calendar_create_all_day_when_no_time(monkeypatch):
    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))

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
    direct executor fills today_for_zoe_tz() and succeeds."""
    from time_utils import today_for_zoe_tz

    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))

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

    The direct executor *declines* this case (returns None), and there is no
    fallback writer behind it: a spy on the executor proves the decline actually
    happened rather than the direct path being skipped."""
    import intent_router

    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))

    real_direct = intent_router._execute_calendar_create_direct
    direct_calls = []

    async def spy_direct(intent, user_id):
        direct_calls.append(intent.name)
        return await real_direct(intent, user_id)

    monkeypatch.setattr(intent_router, "_execute_calendar_create_direct", spy_direct)

    result = await execute_intent(
        Intent("calendar_create", {"title": "Dentist", "date": "the twelfth of never"}),
        "family-admin",
    )

    assert result is None
    assert db.sql_matching("INSERT INTO events") == []
    # The direct executor really ran and declined (no vacuous pass where the
    # direct path was never reached at all).
    assert direct_calls == ["calendar_create"]


@pytest.mark.asyncio
async def test_calendar_create_genuine_failure_returns_none(monkeypatch):
    @contextlib.asynccontextmanager
    async def broken_ctx():
        raise RuntimeError("db unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr("database.get_db_ctx", broken_ctx)

    result = await execute_intent(
        Intent("calendar_create", {"title": "X", "date": "2026-07-10"}), "family-admin"
    )

    assert result is None


@pytest.mark.asyncio
async def test_a_calendar_door_that_cannot_be_imported_gives_mains_none_with_the_flag_off(monkeypatch):
    """With ``ZOE_OS_CALENDAR`` off and ``calendar_service`` failing to import, the spoken create answers ``None`` (main's
    generic handler) and raises nothing. It used to raise ``UnboundLocalError``: the refusal clause named ``EventRefused``,
    which the failed import (inside the same ``try``) never bound. Control run: the separate import guard removed from
    ``_execute_calendar_create_direct`` (the import back inside the write's ``try``) makes this raise ``UnboundLocalError``."""
    import sys

    from intent_router import _execute_calendar_create_direct

    monkeypatch.delenv("ZOE_OS_CALENDAR", raising=False)
    monkeypatch.setitem(sys.modules, "calendar_service", None)          # the import now raises ImportError
    intent = Intent("calendar_create", {"title": "womble day", "date": "2099-07-10"})
    assert await _execute_calendar_create_direct(intent, "member-ova") is None


# --- note_create ------------------------------------------------------------


@pytest.mark.asyncio
async def test_note_create_writes_note_and_confirms(monkeypatch):
    db = _FakeDB()
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))

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

    monkeypatch.setattr("database.get_db_ctx", broken_ctx)

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

    result = await execute_intent(
        Intent("people_create", {"name": "Alice", "relationship": "sister"}),
        "family-admin",
    )

    assert result == "Added Alice as your sister to your contacts."
    inserts = db.sql_matching("INSERT INTO people")
    assert len(inserts) == 1
    _sql, params = inserts[0]
    assert "Alice" in params and "sister" in params
    # Private by default (not family-shared) + context 'personal'; circle is the
    # valid 'circle' tier (people.circle is NOT NULL). See fix: private default.
    assert "personal" in params and "family" not in params
    assert "circle" in params  # the 'circle' tier value (NOT NULL column)


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


# --- wiring: the direct path is the only path -------------------------------


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
async def test_direct_path_is_used(
    monkeypatch, intent_name, target, slots, reply
):
    calls = []

    async def fake_direct(intent, user_id):
        calls.append((intent.name, dict(intent.slots), user_id))
        return reply

    monkeypatch.setattr(f"intent_router.{target}", fake_direct)

    result = await execute_intent(Intent(intent_name, slots), "family-admin")

    assert result == reply
    assert calls == [(intent_name, slots, "family-admin")]


# --- the notes / journal door cannot load, or its store cannot be reached ---------------------------------------------------


def _opened(db, events):
    """``get_db_ctx`` that records each time the store is opened (``"store"`` in ``events``) and hands out ``db``."""
    @contextlib.asynccontextmanager
    async def ctx():
        events.append("store")
        yield db

    return ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("module, run, slots", [
    ("note_service", _execute_note_create_direct, {"content": "feed the womble"}),
    ("journal_service", _execute_journal_create_direct, {"content": "the womble slept"}),
])
async def test_a_door_that_cannot_be_imported_gives_mains_none_before_the_store_is_opened(monkeypatch, module, run, slots):
    """With ``ZOE_OS_NOTES`` / ``ZOE_OS_JOURNAL`` off and the door module failing to import, the executor answers ``None``
    (main's answer), raises nothing, and never opens the store: this PR loads the door before the store is opened, with its
    own guard. Fails on the PR's base (``feature/zoe-os-notes-2-kernel-area`` 4240fa8a9, where the module is imported inside
    ``async with get_db_ctx()``): the store is opened first (``events == ["store"]``). Fails on this PR's head before the
    guard, bd13bdd54: ``UnboundLocalError`` (the ``except note_service.NoteRefused`` / ``journal_service.JournalRefused``
    clause names the module the failed import never bound). Both runs: the new test file in a scratch checkout of each commit,
    recorded in ``~/.zoe/agent-tools/stack-round4/calnotes/PROGRESS.md``. Control run: the separate import guard removed (the
    import back as the first statement of the write's ``try``) raises ``UnboundLocalError`` for each."""
    import sys

    for flag in ("ZOE_OS_NOTES", "ZOE_OS_JOURNAL"):
        monkeypatch.delenv(flag, raising=False)
    db, events = _FakeDB(), []
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _opened(db, events))
    monkeypatch.setitem(sys.modules, module, None)          # ``import <module>`` now raises ImportError

    assert await run(Intent("note_create" if module == "note_service" else "journal_create", slots), "member-ova") is None
    assert events == [] and db.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("flag, module, run, slots", [
    ("ZOE_OS_NOTES", "note_service", _execute_note_create_direct, {"content": "feed the womble"}),
    ("ZOE_OS_JOURNAL", "journal_service", _execute_journal_create_direct, {"content": "the womble slept"}),
])
async def test_with_the_door_on_the_stamps_are_taken_before_the_store_and_an_unreachable_store_still_gives_none(
        monkeypatch, flag, module, run, slots):
    """Door on (``ZOE_OS_NOTES`` / ``ZOE_OS_JOURNAL`` = ``kernel``), the store unreachable: the executor took its erase stamps
    (a real snapshot, the door being on) BEFORE it opened the store, and the unreachable store is still main's ``None``, not
    a refusal line and not an exception. Fails on the PR's base (4240fa8a9): the executor takes no stamps at all
    (``events == ["store"]``); recorded with the run above. Control run: ``stamps_at = ...stamps_now()`` moved inside
    ``async with get_db_ctx()`` makes this fail for each (``["store"]``, the stamp never reached)."""
    import importlib

    import os_shadow

    for other in ("ZOE_OS_NOTES", "ZOE_OS_JOURNAL"):
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setenv(flag, "kernel")
    door = importlib.import_module(module)
    events, taken = [], []
    real = getattr(door, "stamps_now", None)                # (the PR's base has none: its executors never stamp)

    def spy():
        got = real()
        taken.append(got)
        events.append("stamps")
        return got

    monkeypatch.setattr(door, "stamps_now", spy, raising=False)
    monkeypatch.setattr(os_shadow, "stamp_snapshot", lambda: {"member-ova": 3})      # memory only; no state directory read
    _silence_ui(monkeypatch)

    @contextlib.asynccontextmanager
    async def unreachable():
        events.append("store")
        raise ConnectionError("no store")
        yield  # pragma: no cover

    monkeypatch.setattr("database.get_db_ctx", unreachable)
    assert await run(Intent("note_create" if module == "note_service" else "journal_create", slots), "member-ova") is None
    assert events == ["stamps", "store"] and taken == [{"member-ova": 3}]
