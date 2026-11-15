"""0079 - the calendar subscription feed, Stage 1 (``calendar_feed.py``; record ``docs/knowledge/connected-accounts.md``).

ADDITIVE. New NULLable / DEFAULTed columns and one index; nothing is rewritten, renamed or dropped on upgrade:

  * ``events``            - ``content_hash`` (sha256 of the normalised parsed event + attendees + alarms, DTSTAMP excluded: the
                            version an idempotent re-import compares, since a feed gives no per-event ETag), ``provider_updated_at``
                            (the provider's LAST-MODIFIED), ``account_generation`` (the connection a row was written under).
  * ``calendars``         - ``consecutive_failures``, ``last_success_at`` (freshness: ``last_synced_at`` is any non-error finish).
  * ``connected_accounts`` - ``last_error_code``: a CODE from the fixed list ``ERROR_CODES`` (finer than the 0078 error CLASS, so the
                            member can be told in plain words what went wrong), never a provider message.
  * ``sync_runs``         - the rest of a run's counts (unchanged / skipped / pruned / suppressed / rrule_unsupported), ``bytes``,
                            ``duration_ms`` and the run's ``error_code`` (same fixed list).
  * ``event_attendees``   - ``member_user_id``: the attendee who is a household member (via ``member_identities``), so "declined by
                            me" can be told from "declined by someone else".

NO link, URL or secret column is added: a subscription address is a credential and Zoe stores none until the credential vault
lands (owner decision). The feed bytes reach the engine through an injected fetcher.

Every statement is guarded (``IF NOT EXISTS`` on PostgreSQL, a column probe on SQLite), so a rerun is a no-op and
``alembic upgrade head --sql`` still renders (no live-bind inspection on PostgreSQL). The downgrade drops what this revision added
(the new columns' values are lost).
"""

from __future__ import annotations

from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None

#: the plain-words error codes (``connected_accounts.ERROR_CODES`` mirrors this list; a test pins the two together)
ERROR_CODES = ("http_404", "too_large", "not_calendar", "ssrf_blocked", "parse_failed", "revoked_link", "suspect_empty",
               "ledger_unavailable")


def _in(values) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


_CODE_CHECK = "CHECK ({col} IS NULL OR {col} IN " + _in(ERROR_CODES) + ")"

#: (table, column, SQL type + constraints). Every one is NULLable or carries a DEFAULT, so existing rows stay valid.
COLUMNS = (
    ("events", "content_hash", "TEXT"),
    ("events", "provider_updated_at", "TEXT"),
    ("events", "account_generation", "INTEGER"),
    ("calendars", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0"),
    ("calendars", "last_success_at", "TEXT"),
    ("connected_accounts", "last_error_code", "TEXT " + _CODE_CHECK.format(col="last_error_code")),
    ("sync_runs", "n_unchanged", "INTEGER NOT NULL DEFAULT 0"),
    ("sync_runs", "n_skipped", "INTEGER NOT NULL DEFAULT 0"),
    ("sync_runs", "n_pruned", "INTEGER NOT NULL DEFAULT 0"),
    ("sync_runs", "n_suppressed", "INTEGER NOT NULL DEFAULT 0"),
    ("sync_runs", "n_rrule_unsupported", "INTEGER NOT NULL DEFAULT 0"),
    ("sync_runs", "bytes", "INTEGER"),
    ("sync_runs", "duration_ms", "INTEGER"),
    ("sync_runs", "error_code", "TEXT " + _CODE_CHECK.format(col="error_code")),
    ("event_attendees", "member_user_id", "TEXT"),
)

#: the disconnect / fence lookup: a calendar's rows by the connection generation they were written under
INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_events_calendar_generation ON events (calendar_id, account_generation) "
    "WHERE calendar_id IS NOT NULL",
)


def _sqlite_columns(bind, table: str) -> set:
    """SQLite has no ``ADD/DROP COLUMN IF [NOT] EXISTS``: probe. PostgreSQL never probes (the guard is server-side)."""
    return {r[1] for r in bind.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}


def upgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"
    have: dict = {}
    for table, name, ddl in COLUMNS:
        if pg:
            op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {ddl}")
            continue
        cols = have.setdefault(table, _sqlite_columns(bind, table))
        if name not in cols:
            op.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            cols.add(name)
    for sql in INDEXES:
        op.execute(sql)


def downgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"
    op.execute("DROP INDEX IF EXISTS idx_events_calendar_generation")
    have: dict = {}
    for table, name, _ddl in reversed(COLUMNS):
        if pg:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {name}")
            continue
        cols = have.setdefault(table, _sqlite_columns(bind, table))
        if name in cols:
            op.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
            cols.discard(name)
