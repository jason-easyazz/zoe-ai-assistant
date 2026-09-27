"""0030 — proactive_responses: one row per brief-on-arrival delivery (B2.1 / B2.2).

``proactive/arrival.py`` (flag ``ZOE_PROACTIVE_BRIEF_ON_ARRIVAL``, default OFF)
speaks a missed 07:30 morning brief the first time its member is present on a
panel between 07:00 and 11:00. Each delivery takes ONE row here:

  * ``UNIQUE (user_id, trigger_type, local_date)`` is the once-per-member-per-day
    claim. ``INSERT ... ON CONFLICT DO NOTHING RETURNING id`` lets exactly one of
    two panels (or two workers) win, so the in-process throttles are never what
    keeps the brief from being spoken twice.
  * ``outcome`` / ``responded`` / ``responded_at`` are the B2.2 reward signal:
    the engine slow loop later records ``accepted`` (a user turn within
    ``response_window_s`` of the daemon playing it), ``ignored``, or
    ``undelivered`` (never played).

Timestamps are TEXT UTC (``%Y-%m-%dT%H:%M:%SZ``) like ``voice_announcements``;
``local_date`` is the household (``ZOE_TIMEZONE``) date. ``CREATE TABLE IF NOT
EXISTS`` works on PostgreSQL (production) and SQLite (test DBs), so reruns are
safe and no dialect branch is needed (0025 convention).
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS proactive_responses (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               trigger_type TEXT NOT NULL,
               local_date TEXT NOT NULL,
               panel_id TEXT,
               pending_id TEXT,
               announcement_id TEXT,
               missed TEXT,
               response_window_s INTEGER NOT NULL,
               created_at TEXT NOT NULL,
               spoken_at TEXT,
               outcome TEXT,
               responded INTEGER,
               responded_at TEXT,
               evaluated_at TEXT,
               UNIQUE (user_id, trigger_type, local_date)
           )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS proactive_responses")
