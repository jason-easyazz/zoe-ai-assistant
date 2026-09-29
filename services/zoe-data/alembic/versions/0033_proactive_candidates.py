"""0033 — proactive_candidates: the nightly proactivity selector's output.

``proactive/selector.py`` (flag ``ZOE_PROACTIVE_SELECTOR``, default OFF) ranks, per
real member and at most ``CAP`` rows, the things worth raising unprompted: due
open loops, recent emotional moments, events in the next 48 h. At runtime a brain
turn may carry ONE of them (``[RAISE …]``), at most once per conversation.

  * ``UNIQUE (user_id, kind, source_ref)`` makes the nightly write an UPSERT, so
    ``cooldown_until`` / ``surfaced_count`` / ``last_surfaced_session`` survive the
    recompute — a raised loop is not re-created cooldown-free the next night.
  * Trigger = ``on_open`` (an open/greeting turn) or ``cue_words`` (any of them in
    the user's words), inside the window ending ``expires_at``.
  * ``last_surfaced_session`` is the durable once-per-conversation record.

Timestamps are TEXT UTC (``%Y-%m-%dT%H:%M:%SZ``) like ``proactive_responses``.
``CREATE TABLE IF NOT EXISTS`` works on PostgreSQL and SQLite, so reruns are safe
(0025/0030 convention).
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS proactive_candidates (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               kind TEXT NOT NULL,
               source_ref TEXT NOT NULL,
               text TEXT NOT NULL,
               hint TEXT NOT NULL DEFAULT '',
               salience REAL NOT NULL,
               on_open INTEGER NOT NULL DEFAULT 1,
               cue_words TEXT NOT NULL DEFAULT '',
               expires_at TEXT NOT NULL,
               cooldown_until TEXT,
               surfaced_count INTEGER NOT NULL DEFAULT 0,
               last_surfaced_session TEXT,
               last_surfaced_at TEXT,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               UNIQUE (user_id, kind, source_ref)
           )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS proactive_candidates")
