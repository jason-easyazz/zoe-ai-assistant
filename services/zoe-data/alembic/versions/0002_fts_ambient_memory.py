"""FTS for ambient_memory — tsvector + GIN index

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-16

Replaces SQLite FTS5 virtual table + triggers with a PostgreSQL
tsvector GENERATED ALWAYS AS column + GIN index. No triggers needed.
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE ambient_memory
        ADD COLUMN IF NOT EXISTS search_vector tsvector
            GENERATED ALWAYS AS (to_tsvector('english', COALESCE(transcript, ''))) STORED
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ambient_memory_search_idx
        ON ambient_memory USING GIN(search_vector)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ambient_memory_search_idx")
    op.execute("ALTER TABLE ambient_memory DROP COLUMN IF EXISTS search_vector")
