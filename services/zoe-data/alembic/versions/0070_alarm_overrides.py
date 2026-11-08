"""0070 - ``alarm_overrides``: one day's exception to a repeating alarm (``alarms.py``, docs/architecture/alarms.md).

"I have a flight next Friday, get me up an hour earlier": the weekday alarm keeps its rule, and Friday the 14th alone rings at 5:30.
One row per (alarm, local date): ``action`` is ``shift`` (ring at ``time_hhmm`` that day instead) or ``skip`` (do not ring that day).
``next_occurrence`` honours them, the ring engine prunes a row once its day has rung, and deleting an alarm or forgetting a member
removes them with it.

Numbered 0070 on top of 0069 (assigned by the coordinator: 0066-0069 are open PRs); ``IF NOT EXISTS`` makes a rerun safe on
PostgreSQL and SQLite.
"""

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS alarm_overrides (
               alarm_id TEXT NOT NULL,
               on_date TEXT NOT NULL,
               action TEXT NOT NULL,
               time_hhmm TEXT,
               created_at DOUBLE PRECISION NOT NULL DEFAULT 0,
               PRIMARY KEY (alarm_id, on_date)
           )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alarm_overrides")
