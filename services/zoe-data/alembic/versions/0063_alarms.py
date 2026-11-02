"""0063 - ``alarms`` + ``alarm_events``: wake-up alarms for the household (``alarms.py``, docs/architecture/alarms.md).

* ``alarms`` - one row per alarm. ``user_id`` is the owning member, or ``__household__`` for a household alarm. ``time_hhmm`` is
  the LOCAL wall time (household timezone), ``repeat_rule`` is once | daily | weekdays | weekends | days:<ISO weekdays>, and
  ``once_date`` the local date of a one-off. ``next_fire_at`` (epoch seconds) is the single scheduling truth: the ring engine claims
  a due row with a compare-and-swap on it, which is what makes a ring exactly-once across restarts. ``state`` is idle | ringing |
  snoozed, with ``ring_started_at`` / ``snooze_until`` / ``snooze_count`` for the lifecycle.
* ``alarm_events`` - an append-only log of what each alarm did (rang, snoozed, dismissed, auto-stopped, missed, music fallback).

Numbered 0063 on top of main's head (0058); ``IF NOT EXISTS`` makes a rerun safe on PostgreSQL and SQLite.
"""

from alembic import op

revision = "0063"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS alarms (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               label TEXT NOT NULL DEFAULT '',
               time_hhmm TEXT NOT NULL,
               repeat_rule TEXT NOT NULL DEFAULT 'once',
               once_date TEXT,
               sound_kind TEXT NOT NULL DEFAULT 'tone',
               sound_id TEXT NOT NULL DEFAULT 'sunrise',
               sound_name TEXT NOT NULL DEFAULT '',
               snooze_minutes INTEGER NOT NULL DEFAULT 9,
               ramp_seconds INTEGER NOT NULL DEFAULT 30,
               enabled INTEGER NOT NULL DEFAULT 1,
               next_fire_at DOUBLE PRECISION,
               state TEXT NOT NULL DEFAULT 'idle',
               ring_started_at DOUBLE PRECISION,
               snooze_until DOUBLE PRECISION,
               snooze_count INTEGER NOT NULL DEFAULT 0,
               last_fired_at DOUBLE PRECISION,
               created_at DOUBLE PRECISION NOT NULL DEFAULT 0,
               updated_at DOUBLE PRECISION NOT NULL DEFAULT 0
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_alarms_user ON alarms(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_alarms_due ON alarms(enabled, state, next_fire_at)")
    op.execute(
        """CREATE TABLE IF NOT EXISTS alarm_events (
               id TEXT PRIMARY KEY,
               alarm_id TEXT NOT NULL,
               ts DOUBLE PRECISION NOT NULL,
               event TEXT NOT NULL,
               detail TEXT NOT NULL DEFAULT ''
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_alarm_events_alarm ON alarm_events(alarm_id, ts)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_alarm_events_alarm")
    op.execute("DROP TABLE IF EXISTS alarm_events")
    op.execute("DROP INDEX IF EXISTS idx_alarms_due")
    op.execute("DROP INDEX IF EXISTS idx_alarms_user")
    op.execute("DROP TABLE IF EXISTS alarms")
