"""0016 — ui_layouts: layout memory for the composed UI.

Stores, per (user, intent-family), the most recently *successfully composed*
component tree so the generated interface can EVOLVE instead of being amnesiac.
v1 semantics are layout-as-few-shot: stored trees are never rendered directly
(their text content goes stale immediately); ``ui_compose.compose_card`` injects
them into the compose prompt as a structural hint only. See
``services/zoe-data/ui_layouts.py``.

Schema notes:
  - ``tree`` is the JSON-serialised component tree (TEXT, matching the repo's
    text-column convention for JSON payloads).
  - Uniqueness on (user_id, intent_family) is enforced by a single UNIQUE
    index, ``ui_layouts_user_family`` — one object serves both the uniqueness
    contract and the lookup index (a second non-unique index over the same
    columns would be pure duplication). ``INSERT .. ON CONFLICT (user_id,
    intent_family)`` resolves against a unique index on both PostgreSQL and
    SQLite.
  - Timestamps are ISO-8601 TEXT (matching the SQLite-migrated convention used
    across the schema); ``created_at`` defaults dialect-aware
    (``now()::text`` on Postgres, ``CURRENT_TIMESTAMP`` on SQLite for tests).

``IF NOT EXISTS`` everywhere makes the migration rerun-safe. Production is
PostgreSQL; the SQLite branch exists for forward-exercising on test DBs
(matching 0014's pattern).
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS ui_layouts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                intent_family TEXT NOT NULL,
                tree TEXT NOT NULL,
                uses INTEGER NOT NULL DEFAULT 1,
                last_used_at TEXT,
                created_at TEXT DEFAULT (now()::text)
            )
            """
        )
    else:
        # SQLite (tests): portable spellings of the Postgres defaults.
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS ui_layouts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                intent_family TEXT NOT NULL,
                tree TEXT NOT NULL,
                uses INTEGER NOT NULL DEFAULT 1,
                last_used_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    # One current layout per (user, intent-family); also the lookup index.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ui_layouts_user_family "
        "ON ui_layouts(user_id, intent_family)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ui_layouts_user_family")
    op.execute("DROP TABLE IF EXISTS ui_layouts")
