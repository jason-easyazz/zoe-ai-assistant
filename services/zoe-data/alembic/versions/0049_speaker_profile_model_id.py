"""0049 - ``speaker_profiles.model_id``: which embedding space a voiceprint lives in.

Resemblyzer and WeSpeaker ResNet34 are both 256-d, so a shape check cannot keep their vectors apart; a profile of one
scored against an embedding of the other runs without error and means nothing. Every row now names its model
(``services/zoe-data/speaker_models.py``) and nothing ever compares two different ids.

One NULLABLE column and a lookup index, nothing else, so a deploy that has not run this migration yet is unaffected
(``ZOE_SPEAKER_MODEL`` defaults to ``resemblyzer`` and the legacy paths tolerate a missing column).

  * ``model_id`` - e.g. ``wespeaker_resnet34+i8qdq-u8s8+c1``. NULL / empty means ``resemblyzer`` (every row enrolled
    before this migration, and every row written by a caller that does not know about models).
  * Existing rows are BACKFILLED to ``resemblyzer`` so the column states what is true rather than relying on the
    NULL convention alone; the convention still holds for anything written later without the column.
  * A member may now hold ONE ROW PER model_id (a re-enrolment under ``dual`` writes both). ``user_id`` was never unique
    on this table, so no constraint changes; the new index is on (user_id, model_id).

Conventions match 0023 / 0048: dialect-aware, rerun-safe.
"""

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE speaker_profiles ADD COLUMN IF NOT EXISTS model_id TEXT")
    else:
        # SQLite has no ADD COLUMN IF NOT EXISTS - probe PRAGMA first so a rerun is a no-op.
        have = set(r[1] for r in bind.exec_driver_sql("PRAGMA table_info(speaker_profiles)").fetchall())
        if "model_id" not in have:
            op.execute("ALTER TABLE speaker_profiles ADD COLUMN model_id TEXT")
    op.execute("UPDATE speaker_profiles SET model_id = 'resemblyzer' WHERE model_id IS NULL OR model_id = ''")
    op.execute("CREATE INDEX IF NOT EXISTS idx_speaker_profiles_user_model ON speaker_profiles(user_id, model_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_speaker_profiles_user_model")
    if op.get_bind().dialect.name == "postgresql":
        # A downgrade must not leave a ResNet34 row behind masquerading as a Resemblyzer one once the column is gone.
        op.execute("DELETE FROM speaker_profiles WHERE model_id IS NOT NULL AND model_id != 'resemblyzer'")
        op.execute("ALTER TABLE speaker_profiles DROP COLUMN IF EXISTS model_id")
    # SQLite pre-3.35 cannot DROP COLUMN; test DBs are disposable, so leave them.
