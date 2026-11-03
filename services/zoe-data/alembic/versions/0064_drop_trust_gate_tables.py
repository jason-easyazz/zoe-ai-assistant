"""0064 - drop ``trust_allowlist`` and ``trust_audit`` (the never-built trust gate).

Both were created by 0001 for a tool-call trust gate that was never implemented: no code in
services/, scripts/, tools/ or labs/ has ever read or written either table (verified 2026-11-03;
both empty on the live box, alembic head 0063). The touch Settings "Trust Gate" panel that
called the non-existent ``/api/trust-gate/*`` routes is removed in the same change.

``DROP TABLE IF EXISTS`` makes a rerun safe on PostgreSQL and SQLite. The downgrade recreates
the 0001 shape (no data to restore: the tables were always empty).
"""

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS trust_allowlist")
    op.execute("DROP TABLE IF EXISTS trust_audit")


def downgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS trust_allowlist (
               id TEXT PRIMARY KEY,
               tool_pattern TEXT NOT NULL,
               description TEXT,
               added_by TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT NOW()::TEXT
           )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS trust_audit (
               id SERIAL PRIMARY KEY,
               tool_name TEXT NOT NULL,
               action TEXT NOT NULL,
               user_id TEXT,
               allowed INTEGER NOT NULL,
               reason TEXT,
               created_at TEXT NOT NULL DEFAULT NOW()::TEXT
           )"""
    )
