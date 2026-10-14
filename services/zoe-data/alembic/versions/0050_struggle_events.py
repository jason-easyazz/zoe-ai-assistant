"""0050 - ``struggle_events``: the turns where Zoe visibly struggled (``struggle_detector.py``).

One row per detected signal (RE_ASK, REPHRASE, CORRECTION, CANT_DO, FALLTHROUGH, VERBOSE, MISHEAR, ABANDON,
NEGATIVE_FEEDBACK), written OFF the turn path by the detector's bounded worker:

  * ``user_id``    - the member, or ``guest`` (``is_guest`` = 1). A guest row carries NO text: ``summary`` is ''.
  * ``ts``         - epoch seconds of the turn that revealed the struggle.
  * ``signal`` / ``confidence`` / ``fix_type`` - what was seen, how sure, and the suggested class of fix.
  * ``prev_turn_id`` / ``turn_id`` - the failing turn and the turn that revealed it (pair signals), or '' / the turn.
  * ``domain_predicted`` / ``domain_served`` / ``path_served`` - the router's guess vs what actually answered.
  * ``pattern``    - a TEXT-FREE shape key (domain + question shape) the digest clusters on, for every user.
  * ``summary``    - the member's own words in display form (lower case, wake word stripped; PII-scrubbed raw; <= 160 chars) - the same words ``chat_messages`` keeps.
  * ``detail``     - JSON reason codes and numbers (similarities, gap, chars), never words.

A forget deletes the rows whose summary names the entity (``struggle_detector.erase_entity``); the audited user delete
removes every row of the user (``struggle_detector.delete_user``); ``ZOE_STRUGGLE_RETENTION_DAYS`` (default 90) bounds the
rest. ``IF NOT EXISTS`` makes a rerun safe and the statements run unchanged on PostgreSQL and SQLite. The downgrade drops
the table: the turns themselves are still in ``chat_messages``.
"""

from alembic import op

revision = "0050"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS struggle_events (
               id TEXT PRIMARY KEY,
               ts DOUBLE PRECISION NOT NULL,
               user_id TEXT NOT NULL,
               is_guest INTEGER NOT NULL DEFAULT 0,
               channel TEXT NOT NULL DEFAULT '',
               panel_id TEXT NOT NULL DEFAULT '',
               session_id TEXT NOT NULL DEFAULT '',
               signal TEXT NOT NULL,
               confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
               fix_type TEXT NOT NULL DEFAULT '',
               prev_turn_id TEXT NOT NULL DEFAULT '',
               turn_id TEXT NOT NULL DEFAULT '',
               domain_predicted TEXT NOT NULL DEFAULT '',
               domain_served TEXT NOT NULL DEFAULT '',
               path_served TEXT NOT NULL DEFAULT '',
               latency_ms INTEGER,
               stt_ms INTEGER,
               pattern TEXT NOT NULL DEFAULT '',
               summary TEXT NOT NULL DEFAULT '',
               detail TEXT NOT NULL DEFAULT '{}',
               mode TEXT NOT NULL DEFAULT 'shadow'
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_struggle_events_user_ts ON struggle_events(user_id, ts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_struggle_events_signal_ts ON struggle_events(signal, ts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_struggle_events_ts ON struggle_events(ts)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_struggle_events_ts")
    op.execute("DROP INDEX IF EXISTS idx_struggle_events_signal_ts")
    op.execute("DROP INDEX IF EXISTS idx_struggle_events_user_ts")
    op.execute("DROP TABLE IF EXISTS struggle_events")
