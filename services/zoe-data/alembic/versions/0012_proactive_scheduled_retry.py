"""0012 — Reminder fire bookkeeping + atomic delivery claim on proactive_scheduled.

Adds:
  - attempts / last_error: retry/failure state so a reminder job that misfires
    (>misfire_grace downtime) or raises can be reconciled and re-fired on
    restart without spinning forever on a poison reminder.
  - claimed_at: the atomic single-row claim that makes a reminder fire EXACTLY
    once across the scheduled-job, missed-listener, and reconcile paths. A
    delivery path must win `UPDATE ... SET claimed_at WHERE id=$1 AND fired=0
    AND (claimed_at IS NULL OR claimed_at < stuck_cutoff)` before delivering;
    only rowcount==1 delivers. A claim older than the stuck timeout is
    reclaimable so a crash mid-delivery eventually recovers.
  - schedule_generation (on BOTH proactive_scheduled and reminders): a
    monotonically-increasing version of a reminder's due-time/snooze state.
    schedule_reminder stamps each scheduled row with the reminder's current
    generation; update/snooze bump reminders.schedule_generation. A job that
    won the claim re-checks the generation before delivering and self-voids if
    the reminder was rescheduled since — so an already-running old-time job
    can't deliver after a reschedule (cancelling APScheduler can't stop it).
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
    op.execute(
        "ALTER TABLE proactive_scheduled ADD COLUMN IF NOT EXISTS claimed_at TEXT"
    )
    op.execute(
        "ALTER TABLE proactive_scheduled ADD COLUMN IF NOT EXISTS schedule_generation INTEGER DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE reminders ADD COLUMN IF NOT EXISTS schedule_generation INTEGER DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE reminders DROP COLUMN IF EXISTS schedule_generation")
    op.execute("ALTER TABLE proactive_scheduled DROP COLUMN IF EXISTS schedule_generation")
    op.execute("ALTER TABLE proactive_scheduled DROP COLUMN IF EXISTS claimed_at")
    op.execute("ALTER TABLE proactive_scheduled DROP COLUMN IF EXISTS last_error")
    op.execute("ALTER TABLE proactive_scheduled DROP COLUMN IF EXISTS attempts")
