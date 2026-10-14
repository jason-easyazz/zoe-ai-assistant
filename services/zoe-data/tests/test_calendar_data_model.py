"""The calendar data model (alembic 0052, docs/architecture/calendar-data-model.md), end to end on a real SQLite DB.

Pinned here (every name and address is invented; example.com / example.org per RFC 2606):
  * 0052 is the single head, rerun-safe, round-trips, and leaves every pre-existing row and column untouched;
  * a pre-0052 payload still creates / updates / reads exactly as before (new fields default, nothing required);
  * the new fields (description, timezone + derived UTC instants, status, url, conference_url, attendees) round-trip
    through the API, with the desktop calendar's legacy ``metadata.description`` bridged both ways;
  * an attendee may link only a contact the writer can see and the owner may share - never another member's private one;
  * ``busy`` visibility shows another member WHEN and "Busy", nothing else; ``personal`` shows them nothing;
  * "forget <person>" unlinks and scrubs the member's attendee rows (and the legacy metadata list), nobody else's;
  * a read-only (externally owned) event refuses API writes;
  * an invented .ics file imports, stores, exports and re-imports without losing a mapped field.

Negative controls run by hand when this was written: drop the ``calendar_attendees`` step from
``memory_forget_cascade._run`` -> the forget test goes red; return the full row instead of ``busy_projection`` in
``routers/calendar._present`` -> the busy test goes red; drop the ``unlinkable_person_ids`` check -> the private-link
test goes red; skip the DTEND -1 day on all-day import -> the .ics round-trip goes red.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import aiosqlite
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException

import calendar_service as cal
import memory_forget_cascade as cascade
from models import EventCreate, EventUpdate
from routers import calendar as calendar_router

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
MIG = SVC / "alembic/versions/0052_calendar_data_model.py"
FIXTURE = SVC / "tests/fixtures/calendar_sample.ics"
A, B = "member-a", "member-b"

# The 0001 shapes this revision builds on, in SQLite dialect (0001 itself is PostgreSQL-only: NOW()::TEXT).
BASE_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL)",
    """CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, relationship TEXT,
           email TEXT, visibility TEXT NOT NULL DEFAULT 'family', deleted INTEGER DEFAULT 0, updated_at TEXT)""",
    """CREATE TABLE events (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, start_date TEXT NOT NULL,
           start_time TEXT, end_date TEXT, end_time TEXT, duration INTEGER, category TEXT DEFAULT 'general',
           location TEXT, all_day INTEGER DEFAULT 0, recurring TEXT, metadata TEXT,
           visibility TEXT NOT NULL DEFAULT 'family', deleted INTEGER DEFAULT 0,
           created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
           FOREIGN KEY (user_id) REFERENCES users(id))""",
)
BASE_EVENT_COLUMNS = ["id", "user_id", "title", "start_date", "start_time", "end_date", "end_time", "duration",
                      "category", "location", "all_day", "recurring", "metadata", "visibility", "deleted",
                      "created_at", "updated_at"]


def _mig():
    spec = importlib.util.spec_from_file_location("mig_0052", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(engine, fn_name: str) -> None:
    mod = _mig()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(mod, fn_name)()


def _base_engine(path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        for ddl in BASE_DDL:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql("INSERT INTO users (id, name) VALUES ('member-a', 'Member A'), ('member-b', 'Member B')")
    return engine


# ── migration ─────────────────────────────────────────────────────────────────────────────────────────

def test_0052_chains_onto_an_existing_revision_and_the_graph_has_one_head():
    mod = _mig()
    assert mod.revision == "0052"
    revs, downs = set(), set()
    for f in (SVC / "alembic/versions").glob("*.py"):
        src = f.read_text()
        r = re.search(r'^revision = "(\w+)"', src, re.M)
        d = re.search(r'^down_revision = "(\w+)"', src, re.M)
        if r:
            revs.add(r.group(1))
        if d:
            downs.add(d.group(1))
    assert mod.down_revision in revs
    heads = revs - downs
    assert len(heads) == 1, f"more than one head: {sorted(heads)}"
    assert heads == {"0052"} or "0052" in downs, "0052 is the head, or a later revision chains onto it"


def test_upgrade_is_additive_rerun_safe_and_round_trips(tmp_path):
    engine = _base_engine(tmp_path / "m.db")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO events (id, user_id, title, start_date, start_time, category, location, metadata, visibility) "
            "VALUES ('old-1', 'member-a', 'Book club', '2026-10-01', '19:00', 'social', 'Library', '{\"k\": 1}', 'personal')")
        before = conn.exec_driver_sql("SELECT * FROM events").fetchall()

    _run(engine, "upgrade")
    _run(engine, "upgrade")  # already applied: a no-op
    with engine.connect() as conn:
        cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(events)")]
        assert cols[:len(BASE_EVENT_COLUMNS)] == BASE_EVENT_COLUMNS          # nothing renamed or reordered
        assert {"description", "timezone", "start_at", "status", "external_uid", "rrule", "read_only"} <= set(cols)
        row = dict(conn.exec_driver_sql("SELECT * FROM events").mappings().one())
        assert tuple(row[c] for c in BASE_EVENT_COLUMNS) == tuple(before[0])  # the old row is untouched
        assert (row["status"], row["source"], row["read_only"], row["sequence"], row["transparency"]) == (
            "confirmed", "local", 0, 0, "opaque")
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"calendars", "event_attendees", "event_alarms"} <= tables

    _run(engine, "downgrade")
    _run(engine, "downgrade")  # already gone: a no-op
    with engine.connect() as conn:
        assert [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(events)")] == BASE_EVENT_COLUMNS
        assert conn.exec_driver_sql("SELECT * FROM events").fetchall() == before
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"calendars", "event_attendees", "event_alarms"} & tables


def test_postgres_render_guards_every_column_server_side(monkeypatch):
    """No live-bind probe on PostgreSQL: ``alembic upgrade head --sql`` must render 0052 (and its downgrade)."""
    pytest.importorskip("psycopg2")
    from test_migrations_offline_render import _render
    from alembic import command

    up = _render(monkeypatch, command.upgrade, "0050:0052")
    for name, _ddl in _mig().EVENT_COLUMNS:
        assert f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {name} " in up
    assert "CREATE TABLE IF NOT EXISTS calendars" in up and "CREATE TABLE IF NOT EXISTS event_attendees" in up
    down = _render(monkeypatch, command.downgrade, "0052:0050")
    assert "ALTER TABLE events DROP COLUMN IF EXISTS description" in down


# ── API ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
async def db(tmp_path, monkeypatch):
    path = tmp_path / "cal.db"
    engine = _base_engine(path)
    _run(engine, "upgrade")
    engine.dispose()
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.create_function("NOW", 0, lambda: "2026-10-14T00:00:00")
    await conn.execute(
        "INSERT INTO people (id, user_id, name, email, visibility) VALUES "
        "('p-robin', 'member-a', 'Robin Vale', 'robin.vale@example.com', 'family'), "
        "('p-kim-private', 'member-b', 'Kim Ashby', 'kim.ashby@example.org', 'personal'), "
        "('p-sam-mine', 'member-a', 'Sam Okoro', NULL, 'personal')")
    await conn.commit()

    async def allow(db, user, feature, action):
        return None

    async def no_push(*args, **kwargs):
        return 0

    monkeypatch.setattr(calendar_router, "require_feature_access", allow)
    monkeypatch.setattr(calendar_router.broadcaster, "broadcast", no_push)
    monkeypatch.setattr(cascade, "RETRY_DELAY_S", 0)
    yield conn
    await conn.close()


def _u(user_id: str) -> dict:
    return {"user_id": user_id, "role": "user"}


async def _list(db, user_id, **kw):
    args = {"start_date": None, "end_date": None, "category": None, "include_cancelled": False}
    args.update(kw)
    return (await calendar_router.list_events(**args, user=_u(user_id), db=db))["events"]


async def test_a_pre_0052_payload_creates_updates_and_reads_as_before(db):
    legacy = {"title": "Dentist", "start_date": "2026-10-20", "start_time": "09:30", "duration": 30,
              "category": "health", "location": "Clinic", "metadata": {"get_ready_time": 10}, "visibility": "personal"}
    created = await calendar_router.create_event(EventCreate(**legacy), user=_u(A), db=db)
    for key, value in legacy.items():
        assert created[key] == value, key
    assert created["all_day"] is False and created["deleted"] is False
    assert (created["status"], created["source"], created["read_only"]) == ("confirmed", "local", False)
    assert created["description"] is None and created["timezone"] is None and created["start_at"] is None  # floating
    assert created["attendees"] == [] and created["alarms"] == []

    updated = await calendar_router.update_event(created["id"], EventUpdate(title="Dentist (moved)"), user=_u(A), db=db)
    assert updated["title"] == "Dentist (moved)" and updated["metadata"] == {"get_ready_time": 10}
    assert (await calendar_router.get_event(created["id"], user=_u(A), db=db))["title"] == "Dentist (moved)"


async def test_desktop_metadata_description_is_bridged_both_ways(db):
    ev = await calendar_router.create_event(
        EventCreate(title="Parent night", start_date="2026-10-22", metadata={"description": "Room 4", "travel_time": 5}),
        user=_u(A), db=db)
    assert ev["description"] == "Room 4"                                        # the column now holds it
    ev = await calendar_router.update_event(ev["id"], EventUpdate(description="Room 7, bring forms"), user=_u(A), db=db)
    assert ev["metadata"] == {"description": "Room 7, bring forms", "travel_time": 5}   # an old reader still sees it


async def test_legacy_metadata_attendees_surface_as_attendees_and_calendar_name_is_given(db):
    ev = await calendar_router.create_event(
        EventCreate(title="Book club", start_date="2026-10-22",
                    metadata={"attendees": [{"person_id": "p-robin", "name": "Robin Vale", "role": "participant"}]}),
        user=_u(A), db=db)
    assert [(a["display_name"], a["person_id"], a["role"], a["legacy"]) for a in ev["attendees"]] == [
        ("Robin Vale", "p-robin", "req-participant", True)]
    await db.execute("INSERT INTO calendars (id, user_id, name, source, created_at, updated_at) "
                     "VALUES ('cal-work', 'member-a', 'Work', 'google', 'x', 'x')")
    rec = await cal.create_event_record(db, user_id=A, title="Standup", start_date="2026-10-22",
                                        extra={"calendar_id": "cal-work", "source": "google", "read_only": 1})
    await db.commit()
    got = await calendar_router.get_event(rec["id"], user=_u(A), db=db)
    assert (got["calendar_name"], got["source"], got["read_only"]) == ("Work", "google", True)


async def test_new_fields_round_trip_through_the_api(db):
    payload = EventCreate(
        title="Planning lunch", start_date="2026-10-20", start_time="12:30", end_time="13:30",
        description="Budget sheet.\nVenue list.", timezone="Australia/Perth", status="Tentative",
        url="https://example.com/e/1", conference_url="https://meet.example.com/abc",
        attendees=[{"display_name": "Alex Rivers", "email": "mailto:alex.rivers@example.com", "is_organizer": True},
                   {"name": "Robin", "person_id": "p-robin", "role": "participant", "partstat": "accepted"},
                   {"display_name": "Sam", "person_id": "p-sam-mine", "role": "optional"}],
    )
    ev = await calendar_router.create_event(payload, user=_u(A), db=db)
    assert (ev["description"], ev["timezone"], ev["status"]) == ("Budget sheet.\nVenue list.", "Australia/Perth", "tentative")
    assert (ev["url"], ev["conference_url"]) == ("https://example.com/e/1", "https://meet.example.com/abc")
    assert (ev["start_at"], ev["end_at"]) == ("2026-10-20T04:30:00Z", "2026-10-20T05:30:00Z")   # AWST = UTC+8
    got = [(a["display_name"], a["email"], a["person_id"], a["person_link_source"], a["role"], a["partstat"],
            a["is_organizer"]) for a in ev["attendees"]]
    assert got == [("Alex Rivers", "alex.rivers@example.com", None, None, "chair", "needs-action", True),
                   ("Robin", None, "p-robin", "user", "req-participant", "accepted", False),
                   ("Sam", None, "p-sam-mine", "user", "opt-participant", "needs-action", False)]

    ev = await calendar_router.update_event(ev["id"], EventUpdate(timezone="Australia/Sydney", attendees=[]), user=_u(A), db=db)
    assert ev["start_at"] == "2026-10-20T01:30:00Z" and ev["attendees"] == []   # AEDT = UTC+11: instants re-derived
    ev = await calendar_router.update_event(ev["id"], EventUpdate(status="cancelled"), user=_u(A), db=db)
    assert ev["id"] not in [e["id"] for e in await _list(db, A)]               # cancelled: hidden by default
    assert ev["id"] in [e["id"] for e in await _list(db, A, include_cancelled=True)]


@pytest.mark.parametrize("bad", [
    {"timezone": "Mars/Olympus"},
    {"url": "javascript:alert(1)"},
    {"conference_url": "data:text/html,hi"},
    {"status": "maybe"},
    {"visibility": "everyone"},
    {"attendees": [{"role": "participant"}]},
])
async def test_out_of_vocabulary_values_are_a_422(db, bad):
    with pytest.raises(HTTPException) as exc:
        await calendar_router.create_event(EventCreate(title="x", start_date="2026-10-20", **bad), user=_u(A), db=db)
    assert exc.value.status_code == 422


async def test_an_attendee_cannot_link_another_members_private_contact(db):
    for pid in ("p-kim-private", "p-missing"):
        with pytest.raises(HTTPException) as exc:
            await calendar_router.create_event(
                EventCreate(title="x", start_date="2026-10-20", attendees=[{"name": "K", "person_id": pid}]),
                user=_u(A), db=db)
        assert exc.value.status_code == 422
    # B (not the owner) editing A's family event may not link A's private contact either
    ev = await calendar_router.create_event(EventCreate(title="Family dinner", start_date="2026-10-20"), user=_u(A), db=db)
    with pytest.raises(HTTPException):
        await calendar_router.update_event(ev["id"], EventUpdate(attendees=[{"name": "S", "person_id": "p-sam-mine"}]),
                                           user=_u(B), db=db)
    ev = await calendar_router.update_event(ev["id"], EventUpdate(attendees=[{"name": "Robin", "person_id": "p-robin"}]),
                                            user=_u(B), db=db)                # a household contact is fine
    assert [a["person_id"] for a in ev["attendees"]] == ["p-robin"]


async def test_busy_only_shows_another_member_the_time_and_nothing_else(db):
    busy = await calendar_router.create_event(
        EventCreate(title="Therapy", start_date="2026-10-21", start_time="15:00", end_time="16:00", category="health",
                    location="Suite 2", description="private notes", visibility="busy_only", timezone="Australia/Perth",
                    attendees=[{"name": "Dr Lane"}]), user=_u(A), db=db)
    private = await calendar_router.create_event(
        EventCreate(title="Gift shopping", start_date="2026-10-21", visibility="private"), user=_u(A), db=db)
    assert busy["visibility"] == "busy" and private["visibility"] == "personal"

    seen = {e["id"]: e for e in await _list(db, B)}
    assert private["id"] not in seen
    other = seen[busy["id"]]
    assert other["title"] == "Busy" and other["busy_only"] is True
    assert (other["start_date"], other["start_time"], other["end_time"], other["start_at"]) == (
        "2026-10-21", "15:00", "16:00", "2026-10-21T07:00:00Z")
    blob = json.dumps(other)
    for secret in ("Therapy", "Suite 2", "private notes", "Dr Lane", "health"):
        assert secret not in blob
    assert (await calendar_router.get_event(busy["id"], user=_u(B), db=db))["title"] == "Busy"
    assert busy["id"] not in [e["id"] for e in await _list(db, B, category="health")]
    with pytest.raises(HTTPException) as exc:
        await calendar_router.update_event(busy["id"], EventUpdate(title="mine now"), user=_u(B), db=db)
    assert exc.value.status_code == 404
    # the owner still sees everything
    own = await calendar_router.get_event(busy["id"], user=_u(A), db=db)
    assert own["title"] == "Therapy" and own["attendees"][0]["display_name"] == "Dr Lane"


async def test_forget_unlinks_and_scrubs_the_members_attendees_only(db):
    ev = await calendar_router.create_event(
        EventCreate(title="Planning lunch", start_date="2026-10-20",
                    metadata={"attendees": [{"person_id": "p-robin", "name": "Robin Vale"}, {"name": "Alex"}]},
                    attendees=[{"display_name": "Robin Vale", "person_id": "p-robin"},
                               {"display_name": "R. V.", "email": "robin.vale@example.com"},
                               {"display_name": "Alex Rivers", "email": "alex.rivers@example.com"}]),
        user=_u(A), db=db)
    b_ev = await calendar_router.create_event(
        EventCreate(title="Coffee", start_date="2026-10-20", attendees=[{"display_name": "Robin Vale", "person_id": "p-robin"}]),
        user=_u(B), db=db)

    res = await cascade.cascade_forget(A, "Robin Vale", db=db)
    assert "calendar_attendees" not in res.failed and res.attendees == 3        # two rows + one legacy metadata entry

    after = await calendar_router.get_event(ev["id"], user=_u(A), db=db)
    rows = [(a["display_name"], a["email"], a["person_id"], a["person_link_source"]) for a in after["attendees"]]
    assert rows == [(None, None, None, None), (None, None, None, None),
                    ("Alex Rivers", "alex.rivers@example.com", None, None)]
    assert after["metadata"]["attendees"] == [{"name": "Alex"}]
    assert "Robin" not in json.dumps(after)
    # another member's event is theirs: A's forget does not reach it
    other = await calendar_router.get_event(b_ev["id"], user=_u(B), db=db)
    assert other["attendees"][0]["display_name"] == "Robin Vale"


async def test_a_read_only_event_refuses_api_writes(db):
    rec = await cal.create_event_record(db, user_id=A, title="Team sync", start_date="2026-10-20",
                                        extra={"source": "google", "read_only": 1, "external_uid": "x@example.com"})
    await db.commit()
    for call in (calendar_router.update_event(rec["id"], EventUpdate(title="edited"), user=_u(A), db=db),
                 calendar_router.delete_event(rec["id"], user=_u(A), db=db)):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 409
    ok = await calendar_router.create_event(EventCreate(title="Mine", start_date="2026-10-20"), user=_u(A), db=db)
    assert (await calendar_router.delete_event(ok["id"], user=_u(A), db=db))["ok"] is True
    cur = await db.execute("SELECT deleted, deleted_at FROM events WHERE id = ?", (ok["id"],))
    deleted, deleted_at = await cur.fetchone()
    assert deleted == 1 and deleted_at                                              # the sync tombstone time


def test_extra_columns_are_allow_listed():
    with pytest.raises(ValueError):
        cal._extra_columns({"title; DROP TABLE events": 1})


# ── .ics round trip ───────────────────────────────────────────────────────────────────────────────────

def _comparable(parsed: list) -> list:
    out = []
    for item in parsed:
        out.append({
            "event": dict(item["event"]),
            "attendees": [{k: a[k] for k in ("display_name", "email", "role", "partstat", "kind", "is_organizer", "rsvp")}
                          for a in item["attendees"]],
            "alarms": [{k: a[k] for k in ("action", "related", "offset_minutes", "trigger_at", "description")}
                       for a in item["alarms"]],
        })
    return sorted(out, key=lambda x: (x["event"]["external_uid"], x["event"].get("recurrence_id") or ""))


async def test_ics_import_store_export_reimport_is_lossless(db):
    pytest.importorskip("icalendar")
    import calendar_ical

    first = calendar_ical.parse_ics(FIXTURE.read_bytes(), home_tz="Australia/Perth")
    assert len(first) == 6
    flight = next(p["event"] for p in first if p["event"]["external_uid"] == "flight-0004@example.com")
    assert (flight["timezone"], flight["start_time"], flight["start_at"]) == ("Australia/Perth", "09:30", "2026-11-10T01:30:00Z")
    camp = next(p["event"] for p in first if p["event"]["external_uid"] == "school-camp-0003@example.com")
    assert (camp["all_day"], camp["start_date"], camp["end_date"]) == (True, "2026-11-02", "2026-11-04")  # inclusive

    ids = await calendar_ical.store_parsed(db, first, user_id=A)
    await db.commit()
    stored = await _list(db, A, include_cancelled=True)
    assert {e["id"] for e in stored} == set(ids)
    override = next(e for e in stored if e.get("recurrence_id"))
    master = next(e for e in stored if e.get("rrule"))
    assert override["series_id"] == master["id"] and master["exdates"] == ["2026-10-13T08:00:00Z"]
    assert all(e["source"] == "ics" for e in stored)

    exported = calendar_ical.to_ics(stored)
    second = calendar_ical.parse_ics(exported, home_tz="Australia/Perth")
    assert _comparable(second) == _comparable(first)
    assert b"BEGIN:VTIMEZONE" in exported and b"CONFERENCE;VALUE=URI" in exported
