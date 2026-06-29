"""0014 — Bring memory_consolidation_state under Alembic.

Why: ``memory_idle_consolidation._ensure_state_table`` created this table with a
runtime ``CREATE TABLE IF NOT EXISTS`` on first use. That is schema drift outside
the migration framework (``database.init_db`` documents the schema as
Alembic-managed) and it breaks least-privilege Postgres roles that have
read/write but no ``CREATE`` privilege — the background consolidation loop would
fail the first time it ran on such a role.

This migration is the source of truth for the table. The runtime DDL stays as a
harmless ``CREATE TABLE IF NOT EXISTS`` (dev/test convenience); once this
migration has run, that runtime statement is a no-op.

Columns mirror the runtime DDL EXACTLY (production is PostgreSQL):
  - session_id           text PRIMARY KEY
  - user_id              text NOT NULL
  - last_consolidated_at timestamptz NOT NULL DEFAULT now()
  - turns_consolidated   int  NOT NULL DEFAULT 0
The only key is the session_id PRIMARY KEY; the runtime DDL creates no other
index, so neither does this migration.

Dialect-aware so the migration can be exercised forward on a SQLite sample DB in
tests (``timestamptz``/``now()`` are Postgres-only); production is PostgreSQL.
``IF NOT EXISTS`` on both branches makes it rerun-safe and lets it coexist with a
table the runtime path may already have created.
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        # Mirrors memory_idle_consolidation._ensure_state_table exactly.
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_consolidation_state (
                session_id text PRIMARY KEY,
                user_id text NOT NULL,
                last_consolidated_at timestamptz NOT NULL DEFAULT now(),
                turns_consolidated int NOT NULL DEFAULT 0
            )
            """
        )
    else:
        # SQLite (tests). Same shape with portable spellings of the Postgres
        # types/defaults: timestamptz -> TIMESTAMP, now() -> CURRENT_TIMESTAMP,
        # int -> INTEGER.
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_consolidation_state (
                session_id text PRIMARY KEY,
                user_id text NOT NULL,
                last_consolidated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                turns_consolidated INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_consolidation_state")
