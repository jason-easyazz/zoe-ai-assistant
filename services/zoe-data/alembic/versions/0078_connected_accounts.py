"""0078 - the feed spine, Stage 0 (``connected_accounts.py``, ``member_identities.py``; record ``docs/knowledge/connected-accounts.md``).

ADDITIVE. Three new tables, one index; nothing connects to any provider yet:

  * ``connected_accounts`` - one row per (member, provider, external account). ``subject_hash`` is the sha256 of the provider's
                             account subject (dedupe without storing it twice); ``label`` is what the member sees ("Work calendar")
                             and is personal data. ``status`` pending | active | error | revoked. ``last_error_class`` is a CLASS from
                             a fixed list, never a provider message. ``account_generation`` is bumped on disconnect, so a write that
                             started under an older connection is fenced. NO credential is stored here or anywhere in the database:
                             tokens go through the ``account_secrets`` seam.
  * ``sync_runs``          - one row per sync attempt: counts, outcome, error class, an opaque cursor before/after, ``backoff_until``.
  * ``member_identities``  - a member's own email addresses and phone numbers (normalised), so an attendee or a mail recipient can be
                             recognised as a member. One value belongs to at most one member.

``calendars.source_account_id`` (0052) and ``contact_sources.source_account_id`` (0053) stay a documented SOFT reference to
``connected_accounts.id``: the contacts column is ``NOT NULL DEFAULT ''`` (an FK would reject every local source), SQLite cannot add an FK
to an existing column without a table rebuild, and removal is the disconnect / forget routine's job, not a cascade.

Times are TEXT ISO-8601 UTC (``...Z``), the convention of 0052 / 0053 and 0071-0077. ``IF NOT EXISTS`` everywhere and no live-bind
inspection, so a rerun is a no-op and ``alembic upgrade head --sql`` renders. The downgrade drops the three tables (LOSSY: the rows go).
"""

from __future__ import annotations

from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None

KINDS = ("calendar", "contacts", "mail", "music", "other")
STATUSES = ("pending", "active", "error", "revoked")
ERROR_CLASSES = ("auth", "network", "rate_limited", "quota", "provider", "config", "internal")
OUTCOMES = ("running", "ok", "partial", "error", "fenced")
IDENTITY_KINDS = ("email", "phone")
TABLES = ("member_identities", "sync_runs", "connected_accounts")       # drop order (children first)


def _in(values) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def statements() -> list[str]:
    """The CREATE statements in dependency order. Pure: the tests run them on SQLite too."""
    return [
        f"""CREATE TABLE IF NOT EXISTS connected_accounts (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               provider TEXT NOT NULL,
               kind TEXT NOT NULL CHECK (kind IN {_in(KINDS)}),
               subject_hash TEXT NOT NULL,
               label TEXT NOT NULL DEFAULT '',
               scopes TEXT NOT NULL DEFAULT '[]',
               status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN {_in(STATUSES)}),
               account_generation INTEGER NOT NULL DEFAULT 1,
               last_sync_at TEXT,
               last_error_class TEXT CHECK (last_error_class IS NULL OR last_error_class IN {_in(ERROR_CLASSES)}),
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               UNIQUE (user_id, provider, subject_hash)
           )""",
        f"""CREATE TABLE IF NOT EXISTS sync_runs (
               id TEXT PRIMARY KEY,
               account_id TEXT NOT NULL REFERENCES connected_accounts(id) ON DELETE CASCADE,
               user_id TEXT NOT NULL,
               scope TEXT NOT NULL DEFAULT '',
               account_generation INTEGER NOT NULL,
               started_at TEXT NOT NULL,
               finished_at TEXT,
               outcome TEXT NOT NULL DEFAULT 'running' CHECK (outcome IN {_in(OUTCOMES)}),
               error_class TEXT CHECK (error_class IS NULL OR error_class IN {_in(ERROR_CLASSES)}),
               n_seen INTEGER NOT NULL DEFAULT 0,
               n_created INTEGER NOT NULL DEFAULT 0,
               n_updated INTEGER NOT NULL DEFAULT 0,
               n_deleted INTEGER NOT NULL DEFAULT 0,
               cursor_before TEXT,
               cursor_after TEXT,
               backoff_until TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS idx_sync_runs_account ON sync_runs (account_id, started_at)",
        f"""CREATE TABLE IF NOT EXISTS member_identities (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               kind TEXT NOT NULL CHECK (kind IN {_in(IDENTITY_KINDS)}),
               value_norm TEXT NOT NULL,
               verified INTEGER NOT NULL DEFAULT 0,
               source TEXT NOT NULL CHECK (source IN ('member', 'admin', 'account')),
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               UNIQUE (kind, value_norm)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_member_identities_user ON member_identities (user_id)",
        # the soft reference's lookup (disconnect / forget find a member's synced calendars by account)
        "CREATE INDEX IF NOT EXISTS idx_calendars_account ON calendars (source_account_id) WHERE source_account_id IS NOT NULL",
    ]


def upgrade() -> None:
    for sql in statements():
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_calendars_account")
    op.execute("DROP INDEX IF EXISTS idx_member_identities_user")
    op.execute("DROP INDEX IF EXISTS idx_sync_runs_account")
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
