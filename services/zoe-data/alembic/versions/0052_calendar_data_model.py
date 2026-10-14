"""0052 - the calendar data model: where synced (and local) calendar data lives.

Design, field mappings (RFC 5545 iCalendar / RFC 8984 JSCalendar / Google Calendar API / Microsoft Graph)
and the owner decisions: ``docs/architecture/calendar-data-model.md``.

ADDITIVE ONLY. Nothing existing is rewritten, renamed or dropped on upgrade:

  * ``calendars``        - NEW. One row per calendar a member owns (local, or a synced CalDAV / Google /
                           Microsoft / Home Assistant / .ics calendar): name, colour, source, default
                           visibility, writable, sync cursor + status, conflict policy. NO credential or
                           token is ever stored here (those belong in the connectors vault,
                           ``account_secrets``); ``sync_cursor`` is a provider's change cursor (CalDAV
                           sync-token, Google nextSyncToken, Graph deltaLink), not a credential.
  * ``events``           - NEW NULLABLE / DEFAULTED columns beside the 0001 ones (which keep their meaning,
                           so every existing reader is unchanged): description, timezone / end_timezone
                           (IANA), start_at / end_at (UTC instants, ISO-8601 ``...Z`` text), status,
                           transparency, url, conference_url, calendar_id, source, external_uid /
                           external_id / etag / sequence / last_synced_at, read_only, rrule / rdates /
                           exdates / recurrence_id / series_id, deleted_at (sync tombstones).
  * ``event_attendees``  - NEW child table (attendees + the organizer, ``is_organizer``), with an optional
                           ``person_id`` link into the people graph and its provenance
                           (``person_link_source``). The forget cascade unlinks and scrubs it.
  * ``event_alarms``     - NEW child table (VALARM / JSCalendar alerts / Google reminders / Graph reminder).
                           Stored only: nothing fires them yet.

Every statement is guarded (``IF NOT EXISTS`` / a column probe), so a rerun is a no-op on PostgreSQL and on
SQLite. The downgrade removes what this revision added (the new columns' values and the new tables' rows are
lost - the 0001 columns and their rows are untouched).
"""

from alembic import op

revision = "0052"
down_revision = "0050"
branch_labels = None
depends_on = None

# (name, SQL type + constraints). Every one is NULLable or carries a DEFAULT, so existing rows stay valid.
EVENT_COLUMNS = (
    ("description", "TEXT"),
    ("timezone", "TEXT"),
    ("end_timezone", "TEXT"),
    ("start_at", "TEXT"),
    ("end_at", "TEXT"),
    ("status", "TEXT NOT NULL DEFAULT 'confirmed'"),
    ("transparency", "TEXT NOT NULL DEFAULT 'opaque'"),
    ("url", "TEXT"),
    ("conference_url", "TEXT"),
    ("calendar_id", "TEXT REFERENCES calendars(id)"),
    ("source", "TEXT NOT NULL DEFAULT 'local'"),
    ("external_uid", "TEXT"),
    ("external_id", "TEXT"),
    ("etag", "TEXT"),
    ("sequence", "INTEGER NOT NULL DEFAULT 0"),
    ("last_synced_at", "TEXT"),
    ("read_only", "INTEGER NOT NULL DEFAULT 0"),
    ("rrule", "TEXT"),
    ("rdates", "TEXT"),
    ("exdates", "TEXT"),
    ("recurrence_id", "TEXT"),
    ("series_id", "TEXT REFERENCES events(id) ON DELETE CASCADE"),
    ("deleted_at", "TEXT"),
)

EVENT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_events_user_start_at ON events (user_id, start_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_calendar ON events (calendar_id) WHERE calendar_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_events_series ON events (series_id) WHERE series_id IS NOT NULL",
    # The sync identity: one row per (calendar, iCal UID, overridden instance). A master has no recurrence_id.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_events_external_instance ON events "
    "(calendar_id, external_uid, COALESCE(recurrence_id, '')) "
    "WHERE external_uid IS NOT NULL AND calendar_id IS NOT NULL",
)


def _sqlite_event_columns(bind) -> set:
    """SQLite has no ``ADD/DROP COLUMN IF [NOT] EXISTS``: probe. PostgreSQL never probes (the guard is server-side), so
    ``alembic upgrade head --sql`` still renders offline (``tests/test_migrations_offline_render.py``)."""
    return {r[1] for r in bind.exec_driver_sql("PRAGMA table_info(events)").fetchall()}


def upgrade() -> None:
    bind = op.get_bind()

    op.execute(
        """CREATE TABLE IF NOT EXISTS calendars (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL REFERENCES users(id),
               name TEXT NOT NULL,
               colour TEXT,
               description TEXT,
               source TEXT NOT NULL DEFAULT 'local',
               source_account_id TEXT,
               source_calendar_id TEXT,
               timezone TEXT,
               default_visibility TEXT NOT NULL DEFAULT 'family',
               writable INTEGER NOT NULL DEFAULT 1,
               is_default INTEGER NOT NULL DEFAULT 0,
               household_shared INTEGER NOT NULL DEFAULT 0,
               sync_enabled INTEGER NOT NULL DEFAULT 0,
               sync_cursor TEXT,
               conflict_policy TEXT NOT NULL DEFAULT 'remote_wins',
               last_synced_at TEXT,
               last_sync_status TEXT,
               deleted INTEGER NOT NULL DEFAULT 0,
               deleted_at TEXT,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_calendars_user ON calendars (user_id) WHERE deleted = 0")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_calendars_source ON calendars "
        "(user_id, source, COALESCE(source_account_id, ''), source_calendar_id) "
        "WHERE source_calendar_id IS NOT NULL"
    )

    pg = bind.dialect.name == "postgresql"
    have = set() if pg else _sqlite_event_columns(bind)
    for name, ddl in EVENT_COLUMNS:
        if pg:
            op.execute(f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {name} {ddl}")
        elif name not in have:
            op.execute(f"ALTER TABLE events ADD COLUMN {name} {ddl}")
    for sql in EVENT_INDEXES:
        op.execute(sql)

    op.execute(
        """CREATE TABLE IF NOT EXISTS event_attendees (
               id TEXT PRIMARY KEY,
               event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
               user_id TEXT NOT NULL,
               display_name TEXT,
               email TEXT,
               person_id TEXT REFERENCES people(id) ON DELETE SET NULL,
               person_link_source TEXT,
               role TEXT NOT NULL DEFAULT 'req-participant',
               partstat TEXT NOT NULL DEFAULT 'needs-action',
               kind TEXT NOT NULL DEFAULT 'individual',
               is_organizer INTEGER NOT NULL DEFAULT 0,
               rsvp INTEGER NOT NULL DEFAULT 0,
               position INTEGER NOT NULL DEFAULT 0,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_event_attendees_event ON event_attendees (event_id, position)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_event_attendees_user ON event_attendees (user_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_event_attendees_person ON event_attendees (person_id) "
        "WHERE person_id IS NOT NULL"
    )

    op.execute(
        """CREATE TABLE IF NOT EXISTS event_alarms (
               id TEXT PRIMARY KEY,
               event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
               user_id TEXT NOT NULL,
               action TEXT NOT NULL DEFAULT 'display',
               offset_minutes INTEGER,
               related TEXT NOT NULL DEFAULT 'start',
               trigger_at TEXT,
               description TEXT,
               created_at TEXT NOT NULL
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_event_alarms_event ON event_alarms (event_id)")


def downgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"

    op.execute("DROP INDEX IF EXISTS idx_event_alarms_event")
    op.execute("DROP TABLE IF EXISTS event_alarms")
    for idx in ("idx_event_attendees_person", "idx_event_attendees_user", "idx_event_attendees_event"):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    op.execute("DROP TABLE IF EXISTS event_attendees")

    for idx in ("uq_events_external_instance", "idx_events_series", "idx_events_calendar", "idx_events_user_start_at"):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    have = set() if pg else _sqlite_event_columns(bind)
    for name, _ddl in reversed(EVENT_COLUMNS):
        if pg:
            op.execute(f"ALTER TABLE events DROP COLUMN IF EXISTS {name}")
        elif name in have:
            op.execute(f"ALTER TABLE events DROP COLUMN {name}")

    op.execute("DROP INDEX IF EXISTS uq_calendars_source")
    op.execute("DROP INDEX IF EXISTS idx_calendars_user")
    op.execute("DROP TABLE IF EXISTS calendars")
