"""0012 — Track reminder fire attempts/errors on proactive_scheduled.

Adds retry/failure bookkeeping so a reminder job that misfires (>misfire_grace
downtime) or raises can be reconciled and re-fired on restart without spinning
forever on a poison reminder.
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE proactive_scheduled ADD COLUMN IF NOT EXISTS attempts INTEGER DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE proactive_scheduled ADD COLUMN IF NOT EXISTS last_error TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE proactive_scheduled DROP COLUMN IF EXISTS last_error")
    op.execute("ALTER TABLE proactive_scheduled DROP COLUMN IF EXISTS attempts")
