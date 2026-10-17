"""0054 - the pendant day review, Phase 1 (``omi_day_review.py``, ``omi_review_apply.py``, ``routers/omi_day_review.py``; flag
``ZOE_OMI_DAY_REVIEW``, default off). Design: ``docs/architecture/memories-space.md`` sections 4-5.

Two NEW tables and one NEW nullable column, so a deploy that has not run this migration yet degrades to "the feature is off"
(every read and write in the modules is fail-soft and the routes are not mounted while the flag is off).

``omi_day_reviews``: one row per analysis of one member's household-local day (``seq`` 1, 2, ... when a later sync adds
recordings to a day that was already analysed). COUNTS ONLY: calls, chunks, kept/dropped by reason, the recording ids it read.
``status`` ``analysing`` -> ``ready`` | ``failed``; a stale ``analysing`` row is reclaimed. ``UNIQUE (user_id, day_local, seq)``
is the claim: two lanes cannot analyse the same batch.

``omi_day_candidates``: what the analysis PROPOSES. Nothing is written anywhere else until the owner approves a row:
  * ``kind`` - calendar_event | list_item | reminder | follow_up | person | owner_fact.
  * ``payload`` (JSON) - the kind's fields; ``evidence`` (JSON) - ``[{line, recording_id, quote}]``, every quote an exact span
    of the transcript line it cites; ``recording_ids`` (JSON) - the recordings it rests on.
  * ``speaker`` - who said the evidence, in the omi_segments vocabulary: ``you`` | ``someone_else`` | ``not_sure``. Today every
    pendant line is ``not_sure`` (no pendant enrolment yet). ``speaker_state`` is the recordings' own state at analysis time.
  * ``provenance`` is always ``pendant``. ``confidence`` is the code's score (capped while the speaker is not sure).
  * ``status`` - pending -> approving (a short claim while the write runs) -> approved | dismissed. A failed write goes back to
    ``pending`` with ``write_error``; ``written_ref`` names the row the approval wrote (``list_items:<id>``, ``memory:<id>`` ...),
    so the candidate is the provenance index for stores that carry none.
  * ``dedup_key`` - ``UNIQUE (user_id, day_local, dedup_key)``: the same thing heard in two recordings of a day is one row, and a
    rerun inserts nothing new.

``omi_recordings.day_review_id``: the review that read the recording (NULL = not analysed yet). It marks the row as analysed
WITHOUT touching ``reviewed``, which stays the owner's own "I have read this" flag.

TEXT timestamps / JSON as TEXT / no FK to users, like 0045, so the SQLite unit lane runs it. ``IF NOT EXISTS`` (and a column
check on SQLite) makes a rerun safe. The downgrade drops both tables and the column (SQLite >= 3.35 supports ``DROP COLUMN``).
"""

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS omi_day_reviews (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               day_local TEXT NOT NULL,
               seq INTEGER NOT NULL DEFAULT 1,
               status TEXT NOT NULL DEFAULT 'analysing',
               lane TEXT NOT NULL DEFAULT '',
               model TEXT NOT NULL DEFAULT '',
               calls INTEGER NOT NULL DEFAULT 0,
               chunks_total INTEGER NOT NULL DEFAULT 0,
               chunks_skipped INTEGER NOT NULL DEFAULT 0,
               counts TEXT NOT NULL DEFAULT '{}',
               dropped TEXT NOT NULL DEFAULT '{}',
               recording_ids TEXT NOT NULL DEFAULT '[]',
               prompt_sha TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL,
               finished_at TEXT,
               UNIQUE (user_id, day_local, seq)
           )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS omi_day_candidates (
               id TEXT PRIMARY KEY,
               review_id TEXT NOT NULL,
               user_id TEXT NOT NULL,
               day_local TEXT NOT NULL,
               kind TEXT NOT NULL,
               payload TEXT NOT NULL DEFAULT '{}',
               evidence TEXT NOT NULL DEFAULT '[]',
               recording_ids TEXT NOT NULL DEFAULT '[]',
               confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
               speaker TEXT NOT NULL DEFAULT 'not_sure',
               speaker_state TEXT NOT NULL DEFAULT 'unverified',
               provenance TEXT NOT NULL DEFAULT 'pendant',
               dedup_key TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'pending',
               written_ref TEXT,
               write_error TEXT,
               decided_via TEXT,
               decided_at TEXT,
               created_at TEXT NOT NULL,
               UNIQUE (user_id, day_local, dedup_key)
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_omi_day_candidates_user ON omi_day_candidates (user_id, status, day_local)")
    if _pg():
        op.execute("ALTER TABLE omi_recordings ADD COLUMN IF NOT EXISTS day_review_id TEXT")
    else:
        _sqlite_add_column()


def _pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _sqlite_add_column() -> None:
    cols = {r[1] for r in op.get_bind().exec_driver_sql("PRAGMA table_info(omi_recordings)").fetchall()}
    if "day_review_id" not in cols:
        op.execute("ALTER TABLE omi_recordings ADD COLUMN day_review_id TEXT")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_omi_day_candidates_user")
    op.execute("DROP TABLE IF EXISTS omi_day_candidates")
    op.execute("DROP TABLE IF EXISTS omi_day_reviews")
    if _pg():
        op.execute("ALTER TABLE omi_recordings DROP COLUMN IF EXISTS day_review_id")
    else:
        cols = {r[1] for r in op.get_bind().exec_driver_sql("PRAGMA table_info(omi_recordings)").fetchall()}
        if "day_review_id" in cols:
            op.execute("ALTER TABLE omi_recordings DROP COLUMN day_review_id")
