"""A health event written from a conversation is private to its owner.

Measured 2026-10-06 (day-sim ask 8, docs/research/samantha-flags-ab-2026-10-06.md): the user
said "I've got the dentist on Friday for a cracked molar and honestly I'm really nervous", the
brain called add_calendar_event on its own (category "Health"), every writer stored it as
visibility='family', and a DIFFERENT household member asking "what time is my dentist
appointment on Friday?" was answered from show_calendar: "Dentist appointment for cracked molar
on Friday". The calendar read is `user_id = me OR visibility = 'family'`, so the fix is on the
write: a conversational health event is stored `personal`. These tests run the REAL writer and
the REAL reader against one in-memory sqlite `events` table, as two different members.
"""
import contextlib
import sqlite3

import pytest

pytestmark = pytest.mark.ci_safe

import calendar_service
import intent_router
from intent_router import Intent, _execute_calendar_create_direct, _execute_calendar_show_direct

OWNER = "demo_owner"
STRANGER = "demo_stranger"


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _Sqlite:
    """Just enough of the pooled-connection surface over an in-memory `events` table."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE events (id TEXT, user_id TEXT, title TEXT, start_date TEXT, start_time TEXT, "
            "end_date TEXT, end_time TEXT, duration INTEGER, category TEXT, location TEXT, "
            "all_day INTEGER, recurring TEXT, metadata TEXT, visibility TEXT, deleted INTEGER)"
        )

    async def execute(self, sql, params=()):
        return _Cur(self.conn.execute(sql, tuple(params)).fetchall())


@pytest.fixture
def db(monkeypatch):
    fake = _Sqlite()

    @contextlib.asynccontextmanager
    async def ctx():
        yield fake

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr("database.get_db_ctx", ctx)
    monkeypatch.setattr(intent_router, "_notify_ui_channel", _noop)
    monkeypatch.delenv("ZOE_CALENDAR_HEALTH_PRIVATE", raising=False)
    return fake


async def _create(user, title, category=None):
    slots = dict(title=title, date=intent_router.today_for_zoe_tz().isoformat())
    if category:
        slots["category"] = category
    out = await _execute_calendar_create_direct(Intent("calendar_create", slots), user)
    assert out and out.startswith("Added")


async def _show(user):
    return await _execute_calendar_show_direct(Intent("calendar_show", dict(qualifier="today")), user)


@pytest.mark.asyncio
async def test_a_health_event_is_not_shown_to_another_member(db):
    await _create(OWNER, "Dentist appointment for cracked molar", "Health")
    assert "molar" in await _show(OWNER)                     # the owner still sees it
    seen = await _show(STRANGER)
    assert "molar" not in seen and "entist" not in seen, seen  # the household does not


@pytest.mark.asyncio
async def test_an_ordinary_event_stays_on_the_family_calendar(db):
    await _create(OWNER, "pick up the Kestrel proofs")
    assert "Kestrel" in await _show(STRANGER)


@pytest.mark.asyncio
async def test_the_kill_switch_restores_the_family_default(db, monkeypatch):
    monkeypatch.setenv("ZOE_CALENDAR_HEALTH_PRIVATE", "0")
    await _create(OWNER, "Dentist appointment for cracked molar", "Health")
    assert "molar" in await _show(STRANGER)


@pytest.mark.asyncio
async def test_the_title_alone_marks_a_health_event(db):
    await _create(OWNER, "see the physio about my knee")     # no category given
    assert "physio" not in await _show(STRANGER)


HEALTH = ["Dentist appointment for cracked molar", "dentist", "GP appointment", "see the physio",
          "Dr Smith 3pm", "blood test", "therapy session", "Mum's hip check-up", "root canal"]
NOT_HEALTH = ["School assembly", "pick up the Kestrel proofs", "Team dinner", "Maya's birthday party",
              "Parent teacher interviews", "Rottnest half-marathon", "Plumber visit"]


@pytest.mark.parametrize("title", HEALTH)
def test_health_titles_are_private(title):
    assert calendar_service.conversational_visibility(title) == "personal"


@pytest.mark.parametrize("title", NOT_HEALTH)
def test_other_titles_stay_family(title):
    assert calendar_service.conversational_visibility(title) == "family"


def test_a_health_category_alone_is_private():
    assert calendar_service.conversational_visibility("Friday 3pm", "Health") == "personal"
    assert calendar_service.conversational_visibility("Friday 3pm", "general") == "family"


def test_both_conversational_writers_use_the_policy():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    for name in ("intent_router.py", "mcp_server.py"):
        assert "visibility=conversational_visibility(" in (root / name).read_text(), name
