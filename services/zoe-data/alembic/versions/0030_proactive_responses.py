"""0030 — proactive_responses: one row per full morning brief spoken (B2.1 / B2.2).

``proactive/arrival.py`` (flag ``ZOE_PROACTIVE_BRIEF_ON_ARRIVAL``, default OFF)
speaks a missed 07:30 morning brief the first time its member is present on a
panel between 07:00 and 11:00. With the flag on, BOTH that path and the 07:30
path take ONE row here before speaking the full brief:

  * ``UNIQUE (user_id, claim_key, local_date)`` is the once-per-member-per-day
    claim shared by the two paths (``trigger_type`` records which one spoke).
    ``INSERT ... ON CONFLICT DO NOTHING RETURNING id`` lets exactly one of them
    (or of two panels / two workers) win, so in-process throttles are never what
    keeps the brief from being spoken twice.
  * ``outcome`` / ``responded`` / ``responded_at`` are the B2.2 reward signal:
    the engine slow loop later records ``accepted`` (a user turn within
    ``response_window_s`` of the daemon playing it), ``ignored``,
    ``undelivered`` (expired unplayed) or ``unknown`` (no announcement linked).

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
               claim_key TEXT NOT NULL,
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
               UNIQUE (user_id, claim_key, local_date)
           )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS proactive_responses")
