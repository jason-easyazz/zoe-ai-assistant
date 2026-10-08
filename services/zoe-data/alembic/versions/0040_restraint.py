"""0040 — restraint: ``restraint_mutes`` and ``restraint_classes`` (``restraint.py``, ``ZOE_RESTRAINT``).

Register item BP1 (restraint in code). Two NEW tables and no change to any existing one, so a
deploy that has not run this migration yet degrades to "no mutes, classes recomputed from the
text" (every read in ``restraint.py`` is fail-open) instead of breaking the selector, the brief or
the ledger.

  * ``restraint_mutes`` — a spoken "don't mention that again" (SAL6 / plan B2.5), one row per mute,
    WITH PROVENANCE and without the owner's words: ``topic_key`` (space-separated stems of the
    topic), ``thread_ref`` (the ``proactive_candidates.source_ref`` when the mute named a thread
    that had just been raised), ``session_id``, ``turn_key`` (a digest of the utterance, no text),
    ``phrase`` (the pattern id), ``created_at``. "You can mention it again" does not delete the row:
    ``status`` becomes ``released`` and ``released_at`` / ``released_turn_key`` say when and by which
    turn (invalidate, never delete). The audited user delete and the forget cascade remove rows.
  * ``restraint_classes`` — the sensitivity class of a THREAD (a proactive candidate, an open loop, an
    emotional moment), decided nightly in code and stored. ``version`` is the classifier version and
    ``text_hash`` a digest of the thread's words: when either changes the earlier row gets
    ``invalid_at`` and a new row is inserted (invalidate, never delete); a reader trusts only a row
    with ``invalid_at IS NULL`` whose version and hash still match. ``signals`` records what decided
    each class (``entity`` / ``type`` / ``affect`` / ``kind`` / ``lexicon`` / ``name``).

Timestamps are TEXT UTC (``%Y-%m-%dT%H:%M:%SZ``) like ``proactive_candidates``. ``IF NOT EXISTS`` runs
unchanged on PostgreSQL and SQLite, so reruns are safe (0025/0030/0033/0036 convention). The
downgrade drops both tables: a mute is a preference, but a preference the schema cannot hold is
better gone than half-applied.

Numbering: 0040 follows 0039 on main; if the night-mind migration (also numbered 0040) lands first,
renumber this one to 0041 / ``down_revision = "0040"`` (one line, no content change).
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS restraint_mutes (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               topic_key TEXT NOT NULL DEFAULT '',
               thread_ref TEXT,
               scope TEXT NOT NULL DEFAULT 'topic',
               source TEXT NOT NULL DEFAULT 'spoken',
               session_id TEXT,
               turn_key TEXT NOT NULL DEFAULT '',
               phrase TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'active',
               created_at TEXT NOT NULL,
               released_at TEXT,
               released_turn_key TEXT
           )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_restraint_mutes_user ON restraint_mutes (user_id, status)"
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS restraint_classes (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               subject_ref TEXT NOT NULL,
               classes TEXT NOT NULL DEFAULT '',
               signals TEXT NOT NULL DEFAULT '',
               version INTEGER NOT NULL,
               text_hash TEXT NOT NULL,
               derived_at TEXT NOT NULL,
               invalid_at TEXT,
               UNIQUE (user_id, subject_ref, version, text_hash)
           )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_restraint_classes_user ON restraint_classes (user_id, invalid_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_restraint_classes_user")
    op.execute("DROP TABLE IF EXISTS restraint_classes")
    op.execute("DROP INDEX IF EXISTS idx_restraint_mutes_user")
    op.execute("DROP TABLE IF EXISTS restraint_mutes")
