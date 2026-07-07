"""ambient_memory user scoping — nullable user_id + (user_id, timestamp) index

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-07

P-F4 (remediation-packets-2026-07): every ambient transcript row must carry
the owning user so the write path never stores ownerless audio and the read
path (`ambient_search`) can enforce mandatory user scoping.

Column is nullable for now — the table is empty in prod (verified 0 rows
2026-07-07) and capture is flag-OFF; NOT NULL enforcement lands with the W6
ambient packet. Downgrade drops the column only (no data-losing table ops).

Note: the packet names this migration "0016", but 0016 (`ui_layouts`) landed
after the packet was written — same content, number shifted to 0017.
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ambient_memory ADD COLUMN IF NOT EXISTS user_id TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ambient_user_time"
        " ON ambient_memory(user_id, timestamp)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_ambient_user_time")
    op.execute("ALTER TABLE ambient_memory DROP COLUMN IF EXISTS user_id")
