"""Unit tests for the single canonical calendar writer.

``calendar_service.create_event_record`` is the one INSERT shared by all three
event writers (voice/direct executor, the calendar_create_event MCP tool, and
the /api/calendar/events router). These tests pin the exact column list, the
value mapping, the all_day 0/1 coercion, the defaults, and that a voice-caller
subset (no end_date/duration/recurring/metadata) inserts NULLs cleanly.
"""

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import re

import pytest

from calendar_service import create_event_record


class _FakeDB:
    """Captures each execute() call for assertion. Mirrors the fake used by
    test_calendar_note_direct_execution.py."""

    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return None


def _columns(sql: str):
    """Extract the ordered column list from the INSERT statement."""
    m = re.search(r"INSERT INTO events\s*\((.*?)\)\s*VALUES", sql, re.DOTALL)
    assert m, f"could not parse columns from: {sql!r}"
    return [c.strip() for c in m.group(1).split(",")]


EXPECTED_COLUMNS = [
    "id", "user_id", "title", "start_date", "start_time", "end_date",
    "end_time", "duration", "category", "location", "all_day", "recurring",
    "metadata", "visibility", "deleted",
]


@pytest.mark.asyncio
async def test_full_column_list_and_value_mapping():
    db = _FakeDB()
    record = await create_event_record(
        db,
        user_id="u1",
        title="Dentist",
        start_date="2026-07-10",
        start_time="14:30",
        end_date="2026-07-10",
        end_time="15:00",
        duration=30,
        category="health",
        location="Clinic",
        all_day=False,
        recurring="FREQ=WEEKLY",
        metadata='{"note": "bring form"}',
        visibility="personal",
    )

    assert len(db.calls) == 1
    sql, params = db.calls[0]

    # Exact column list, in order, incl. the literal deleted=0 column.
    assert _columns(sql) == EXPECTED_COLUMNS
    # 14 bound placeholders + literal 0 for deleted.
    assert sql.count("?") == 14

    # Value mapping: params align to the 14 bound columns (deleted is literal 0).
    assert params == (
        record["id"], "u1", "Dentist", "2026-07-10", "14:30", "2026-07-10",
        "15:00", 30, "health", "Clinic", 0, "FREQ=WEEKLY",
        '{"note": "bring form"}', "personal",
    )
    # id is a generated uuid4 string.
    assert isinstance(record["id"], str) and len(record["id"]) == 36
    # Returned record reflects written values.
    assert record["title"] == "Dentist"
    assert record["all_day"] == 0
    assert record["visibility"] == "personal"


@pytest.mark.asyncio
async def test_all_day_true_coerced_to_one():
    db = _FakeDB()
    record = await create_event_record(
        db, user_id="u1", title="Birthday", start_date="2026-07-11", all_day=True,
    )
    _sql, params = db.calls[0]
    # all_day is the 11th bound column.
    assert params[10] == 1
    assert record["all_day"] == 1


@pytest.mark.asyncio
async def test_all_day_false_coerced_to_zero():
    db = _FakeDB()
    record = await create_event_record(
        db, user_id="u1", title="Standup", start_date="2026-07-11",
        start_time="09:00", all_day=False,
    )
    _sql, params = db.calls[0]
    assert params[10] == 0
    assert record["all_day"] == 0


@pytest.mark.asyncio
async def test_defaults_category_visibility_and_deleted_literal():
    db = _FakeDB()
    await create_event_record(
        db, user_id="u1", title="X", start_date="2026-07-11",
    )
    sql, params = db.calls[0]
    # category default 'general' (9th bound col), visibility default 'family'
    # (14th bound col).
    assert params[8] == "general"
    assert params[13] == "family"
    # deleted is a literal 0 in the VALUES tail, not a bound param (14 params
    # for 15 columns).
    assert ", 0)" in sql.replace("\n", " ").replace("  ", " ") or ",0)" in sql
    assert len(params) == 14  # 15 columns, deleted is the literal
    # all_day defaults False → coerced to 0 (the 11th bound column).
    assert params[10] == 0


@pytest.mark.asyncio
async def test_voice_subset_inserts_nulls_without_error():
    """The voice caller only supplies title/start_date/start_time/category/
    all_day. Everything else must go in as NULL without error."""
    db = _FakeDB()
    record = await create_event_record(
        db,
        user_id="family-admin",
        title="Dentist",
        start_date="2026-07-10",
        start_time="14:30",
        category="general",
        all_day=False,
    )
    _sql, params = db.calls[0]
    # Bound column order:
    # 0 id, 1 user_id, 2 title, 3 start_date, 4 start_time, 5 end_date,
    # 6 end_time, 7 duration, 8 category, 9 location, 10 all_day, 11 recurring,
    # 12 metadata, 13 visibility
    assert params[5] is None   # end_date
    assert params[6] is None   # end_time
    assert params[7] is None   # duration
    assert params[9] is None   # location
    assert params[11] is None  # recurring
    assert params[12] is None  # metadata
    assert params[8] == "general"
    assert params[13] == "family"
    assert params[10] == 0
    assert record["end_date"] is None
    assert record["location"] is None
