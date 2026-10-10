"""0044 - reply_sources and commitments (``reply_ledger.py``, ``commitments.py``).

Two NEW tables and no change to any existing one, so a deploy that has not run this migration yet degrades to the
pre-0044 behaviour (every read and write in the two modules is fail-soft): "why did you say that?" answers from the
in-process ledger only, and no commitment is recorded.

  * ``reply_sources`` - the per-reply provenance ledger of ``memory_provenance`` (BM5), persisted so a zoe-data restart
    no longer empties it. One row per reply, keyed by (user, session, reply id): the ledger is per CONVERSATION, not per
    user, so a voice session can never explain a chat reply. IDS and labels only - the row ids / owner-turn ids the reply
    restated (``sources``: ``[[kind, id, said_at], ...]``), the tool NAMES the turn called, the tier/domain of a direct
    reply, the count of rows served. No reply text, no quote, no user words: the explanation re-reads each row at answer
    time, so a row forgotten since is not quoted. Short retention (``reply_ledger.RETENTION_S``, 3 days), purged on write.
  * ``commitments`` - a timed promise ZOE'S OWN reply made ("I'll remind you at 5", "I'll check back tomorrow about the
    dentist"): who, which conversation and reply, the kind (``remind`` | ``check_back``), the due instant (UTC), the
    subject (<= 80 chars, from Zoe's reply, never from the user's words) and the tool NAMES actually called that turn. A
    deterministic sweep at the due time sets ``status`` (``open`` -> ``kept`` | ``fulfilled`` | ``owned`` | ``surfaced`` |
    ``missed`` (shadow: a breach she would have acted on) | ``void``); nothing is ever deleted by the sweep (history is the point), only the retention purge removes a closed row
    after 14 days and the forget cascade removes one naming a forgotten entity.

Timestamps are TEXT UTC (``%Y-%m-%dT%H:%M:%SZ``) like ``proactive_candidates``; ``reply_sources.ts`` is the epoch the
in-process ledger uses. ``IF NOT EXISTS`` runs unchanged on PostgreSQL and SQLite, so reruns are safe (0025/0030/0033/0036
convention). No FK to ``users`` (the bar's per-table ``user_id`` teardown sweeps the rows by exact demo id). The downgrade
drops both tables.
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS reply_sources (
               user_id TEXT NOT NULL,
               session_id TEXT NOT NULL,
               reply_id TEXT NOT NULL,
               ts REAL NOT NULL,
               kind TEXT NOT NULL,
               tier TEXT NOT NULL DEFAULT '',
               domain TEXT NOT NULL DEFAULT '',
               served INTEGER NOT NULL DEFAULT 0,
               sources TEXT NOT NULL DEFAULT '[]',
               extra TEXT NOT NULL DEFAULT '[]',
               tools TEXT NOT NULL DEFAULT '[]',
               created_at TEXT NOT NULL,
               PRIMARY KEY (user_id, session_id, reply_id)
           )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_reply_sources_conv ON reply_sources (user_id, session_id, ts)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_reply_sources_created ON reply_sources (created_at)"
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS commitments (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               session_id TEXT NOT NULL DEFAULT '',
               reply_id TEXT NOT NULL DEFAULT '',
               kind TEXT NOT NULL,
               due_at TEXT NOT NULL,
               about TEXT NOT NULL DEFAULT '',
               lang TEXT NOT NULL DEFAULT 'en',
               tools TEXT NOT NULL DEFAULT '[]',
               status TEXT NOT NULL DEFAULT 'open',
               resolution TEXT NOT NULL DEFAULT '',
               reminder_id TEXT,
               candidate_id TEXT,
               created_at TEXT NOT NULL,
               checked_at TEXT,
               UNIQUE (user_id, session_id, reply_id, kind, due_at)
           )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_commitments_open ON commitments (due_at) WHERE status = 'open'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_commitments_user ON commitments (user_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS commitments")
    op.execute("DROP TABLE IF EXISTS reply_sources")
