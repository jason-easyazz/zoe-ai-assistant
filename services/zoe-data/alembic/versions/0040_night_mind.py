"""0040 - the night mind's tables (``night_mind.py`` / ``night_store.py``): runs, threads, observations.

The nightly reflection pass (flag ``ZOE_NIGHT_MIND``, default OFF) turns each household member's day into a few CITED observations.
Design record: docs/research/night-mind-2026-10-09.md section 5. An observation is a POINTER to the owner's words, never prose:

  * ``night_observations`` - one verified moment: the ``chat_messages`` id (``turn_id``), the owner's quote (an exact span of that turn,
    checked by substring at write; kept beside the pointer, as ``exact_turns`` keeps the words, so a forget can erase by text), the day
    it was said, enums (``kind`` / ``feeling`` / ``weight``), a validity interval and a ``state`` (current | history | held | retracted).
    ``authority_class`` is ``user_stated_derived`` at most (``held`` rows are the observation gate's pending candidates, never served).
  * ``night_threads``      - the structure that groups observations. No claim text of its own: ``title`` (<= 8 words) is audit/display
    only and never injected into a prompt. ``raise_policy`` / ``leave_reason`` are set by CODE every night; a ``leave`` thread is a
    deny-list consulted by the brief, the selector and the unprompted-recall floor, and never written into any prompt.
    ``source_ref`` (``night_threads:<id>``) is the key ``brief_first_turn.mentioned`` uses, so a thread the brief voiced is not re-raised.
  * ``night_runs``         - one row per member-night, COUNTS ONLY (no text), so a veto can always say what it saw.

All three are ``user_id``-scoped. A forget deletes the observations that name the entity (``night_mind.erase_entity``, beside
``exact_words.erase_entity``); the audited user delete removes every row (``night_mind.delete_user``). Schema only. ``IF NOT
EXISTS`` makes a rerun safe and the statements run unchanged on PostgreSQL and SQLite. The downgrade drops the tables: the words are
still in ``chat_messages``.
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS night_threads (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               title TEXT NOT NULL DEFAULT '',
               anchors TEXT NOT NULL DEFAULT '',
               topic TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'open',
               first_day TEXT NOT NULL DEFAULT '',
               last_day TEXT NOT NULL DEFAULT '',
               mentions_n INTEGER NOT NULL DEFAULT 0,
               weight_max INTEGER NOT NULL DEFAULT 1,
               last_feeling TEXT NOT NULL DEFAULT 'none',
               raise_policy TEXT NOT NULL DEFAULT 'wait',
               leave_reason TEXT NOT NULL DEFAULT '',
               next_raise_after TEXT NOT NULL DEFAULT '',
               last_raised_at TEXT NOT NULL DEFAULT '',
               ignored_raises INTEGER NOT NULL DEFAULT 0,
               source_ref TEXT NOT NULL DEFAULT '',
               opened_run TEXT NOT NULL DEFAULT '',
               closed_run TEXT NOT NULL DEFAULT ''
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_night_threads_user ON night_threads(user_id, last_day)")
    op.execute(
        """CREATE TABLE IF NOT EXISTS night_observations (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               thread_id TEXT NOT NULL DEFAULT '',
               turn_id TEXT NOT NULL DEFAULT '',
               quote TEXT NOT NULL,
               kind TEXT NOT NULL DEFAULT 'other',
               who TEXT NOT NULL DEFAULT '',
               feeling TEXT NOT NULL DEFAULT 'none',
               valence INTEGER NOT NULL DEFAULT 0,
               weight INTEGER NOT NULL DEFAULT 1,
               later TEXT NOT NULL DEFAULT 'na',
               day TEXT NOT NULL DEFAULT '',
               said_at DOUBLE PRECISION NOT NULL DEFAULT 0,
               valid_from DOUBLE PRECISION NOT NULL DEFAULT 0,
               valid_to DOUBLE PRECISION,
               state TEXT NOT NULL DEFAULT 'current',
               origin TEXT NOT NULL DEFAULT 'stated',
               authority_class TEXT NOT NULL DEFAULT 'user_stated_derived',
               basis TEXT NOT NULL DEFAULT '',
               run_id TEXT NOT NULL DEFAULT ''
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_night_obs_user_thread ON night_observations(user_id, thread_id, said_at)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_night_obs_turn_quote ON night_observations(user_id, turn_id, quote)")
    op.execute(
        """CREATE TABLE IF NOT EXISTS night_runs (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               night_date TEXT NOT NULL DEFAULT '',
               model_id TEXT NOT NULL DEFAULT '',
               prompt_sha TEXT NOT NULL DEFAULT '',
               schema_sha TEXT NOT NULL DEFAULT '',
               watermark_msg_id TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'ok',
               error_class TEXT NOT NULL DEFAULT '',
               wall_s DOUBLE PRECISION NOT NULL DEFAULT 0,
               counts TEXT NOT NULL DEFAULT '{}',
               created_at DOUBLE PRECISION NOT NULL DEFAULT 0
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_night_runs_user ON night_runs(user_id, night_date)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_night_runs_user")
    op.execute("DROP TABLE IF EXISTS night_runs")
    op.execute("DROP INDEX IF EXISTS uq_night_obs_turn_quote")
    op.execute("DROP INDEX IF EXISTS idx_night_obs_user_thread")
    op.execute("DROP TABLE IF EXISTS night_observations")
    op.execute("DROP INDEX IF EXISTS idx_night_threads_user")
    op.execute("DROP TABLE IF EXISTS night_threads")
