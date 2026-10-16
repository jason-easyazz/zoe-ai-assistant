"""0054 - pendant segments + learned voices (``omi_diarize.py``, ``omi_voices.py``, ``routers/omi_voices.py``).

Design: ``docs/architecture/memories-space.md`` sections 2.4, 2.6, 3 and 7 (owner decision 2026-10-14: tagging a person in
your recordings teaches Zoe their voice, for SUGGESTIONS in your own recordings only, never auth). PURE STORAGE: every
behaviour that fills these tables is flag-dark (``ZOE_OMI_SPEAKER_TAG``, ``ZOE_OMI_LEARN_VOICES``).

  * ``omi_segments``                - one speaker turn of a recording: times, the text Moonshine produced for exactly that
                                      span, the model label (``speaker``) and score, the owner's correction (``owner_label``),
                                      a CONFIRMED tag (``tagged_person_id`` + ``tag_source``) and, separately, a SUGGESTION
                                      (``suggested_person_id``) that is never a tag. ``voice_cluster_id`` groups an unknown
                                      voice across recordings. ``not_person`` marks a line as Zoe's own reply, TV, radio or
                                      other media: never a person, never a review candidate. The day review reads these rows; nothing else derives from them.
  * ``omi_segment_embeddings``      - the centred ResNet34 mean of an ``other`` (or low-margin ``unsure``) segment, kept ONLY
                                      while learning is on so a later tag can teach Zoe the voice; deleted once used, else at
                                      ``expires_at`` (14 days). The wearer's own ``you`` segments are never stored.
  * ``omi_voice_clusters``          - "Voice 3, heard in 14 recordings over 3 days": an unknown voice in ONE member's layer.
                                      Its centroid is a biometric of an untagged bystander, so it expires like the embeddings.
                                      ``status='not_a_person'`` (+ kind): the member said "that's the TV"; later lines that match
                                      it are marked ``not_person`` and nothing of them is held.
  * ``learned_voiceprint_segments`` - each contribution to a ``learned_voiceprints`` row (0053), so the mean can be REBUILT
                                      when a recording is deleted or a tag corrected (un-learning).
  * ``omi_voice_settings``          - the member's "stop learning voices" switch.
  * ``learned_voiceprints.rejections`` - "No, that isn't Sam" count; it raises that voiceprint's suggestion margin.

Conventions as 0045 / 0053: TEXT ids and UTC timestamps, ``user_id`` with no FK to a users table, bytes as BYTEA / BLOB.
``people`` rows are SOFT-deleted, so the FK cascades are a backstop only: the purge is explicit (``omi_voices.purge_people``).
The nightly backup excludes the DATA of the four biometric tables (``postgres-nightly-backup.sh``). ``IF NOT EXISTS`` everywhere;
the downgrade drops the new tables and the column (lossy by design: learned voices are re-learned).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

SPEAKERS = ("you", "other", "unsure", "mixed", "unverified")
NOT_PERSON = ("zoe", "tv", "radio", "media")      # "not a person": Zoe's own replies, TV, radio, other media (gap audit P1-5)
TABLES = ("omi_voice_settings", "learned_voiceprint_segments", "omi_segment_embeddings", "omi_segments",
          "omi_voice_clusters")       # drop order (children first)


def _in(values) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def statements(dialect: str) -> list[str]:
    """The CREATE statements, in dependency order. Pure: the unit tests run them on SQLite too."""
    blob = "BYTEA" if dialect == "postgresql" else "BLOB"
    return [
        f"""CREATE TABLE IF NOT EXISTS omi_voice_clusters (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               model_id TEXT NOT NULL,
               seq INTEGER NOT NULL,
               centroid {blob} NOT NULL,
               n_segments INTEGER NOT NULL DEFAULT 0,
               status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'not_a_person')),
               not_person_kind TEXT CHECK (not_person_kind IN {_in(NOT_PERSON)}),
               first_heard_at TEXT NOT NULL,
               last_heard_at TEXT NOT NULL,
               created_at TEXT NOT NULL,
               expires_at TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_omi_voice_clusters_user ON omi_voice_clusters (user_id, status)",
        f"""CREATE TABLE IF NOT EXISTS omi_segments (
               id TEXT PRIMARY KEY,
               recording_id TEXT NOT NULL REFERENCES omi_recordings(id) ON DELETE CASCADE,
               user_id TEXT NOT NULL,
               seq INTEGER NOT NULL,
               start_ms INTEGER NOT NULL,
               end_ms INTEGER NOT NULL,
               text TEXT NOT NULL DEFAULT '',
               speaker TEXT NOT NULL CHECK (speaker IN {_in(SPEAKERS)}),
               score REAL,
               windows INTEGER NOT NULL DEFAULT 0,
               model_id TEXT,
               profile_channel TEXT CHECK (profile_channel IN ('pendant', 'panel')),
               owner_label TEXT CHECK (owner_label IN ('you', 'other')),
               owner_labelled_at TEXT,
               not_person TEXT CHECK (not_person IN {_in(NOT_PERSON)}),
               tagged_person_id TEXT REFERENCES people(id) ON DELETE SET NULL,
               tag_source TEXT CHECK (tag_source IN ('owner', 'suggested_confirmed')),
               tagged_at TEXT,
               suggested_person_id TEXT REFERENCES people(id) ON DELETE SET NULL,
               suggested_score REAL,
               voice_cluster_id TEXT REFERENCES omi_voice_clusters(id) ON DELETE SET NULL,
               created_at TEXT NOT NULL,
               UNIQUE (recording_id, seq)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_omi_segments_user ON omi_segments (user_id, recording_id)",
        "CREATE INDEX IF NOT EXISTS idx_omi_segments_person ON omi_segments (user_id, tagged_person_id)",
        "CREATE INDEX IF NOT EXISTS idx_omi_segments_cluster ON omi_segments (voice_cluster_id)",
        f"""CREATE TABLE IF NOT EXISTS omi_segment_embeddings (
               segment_id TEXT PRIMARY KEY REFERENCES omi_segments(id) ON DELETE CASCADE,
               user_id TEXT NOT NULL,
               model_id TEXT NOT NULL,
               embedding {blob} NOT NULL,
               expires_at TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_omi_segment_embeddings_user ON omi_segment_embeddings (user_id, expires_at)",
        f"""CREATE TABLE IF NOT EXISTS learned_voiceprint_segments (
               voiceprint_id TEXT NOT NULL REFERENCES learned_voiceprints(id) ON DELETE CASCADE,
               segment_id TEXT NOT NULL,
               recording_id TEXT NOT NULL,
               embedding {blob} NOT NULL,
               added_at TEXT NOT NULL,
               PRIMARY KEY (voiceprint_id, segment_id)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_lvp_segments_recording ON learned_voiceprint_segments (recording_id)",
        """CREATE TABLE IF NOT EXISTS omi_voice_settings (
               user_id TEXT PRIMARY KEY,
               learning_enabled INTEGER NOT NULL DEFAULT 1,
               updated_at TEXT NOT NULL
           )""",
    ]


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    return insp.has_table(table) and column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    for sql in statements(dialect):
        op.execute(sql)
    if dialect == "postgresql":      # no live-bind inspection, so ``alembic upgrade head --sql`` renders
        op.execute("ALTER TABLE learned_voiceprints ADD COLUMN IF NOT EXISTS rejections INTEGER NOT NULL DEFAULT 0")
    elif sa.inspect(bind).has_table("learned_voiceprints") and not _has_column(bind, "learned_voiceprints", "rejections"):
        op.execute("ALTER TABLE learned_voiceprints ADD COLUMN rejections INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE learned_voiceprints DROP COLUMN IF EXISTS rejections")
    elif _has_column(bind, "learned_voiceprints", "rejections"):
        op.execute("ALTER TABLE learned_voiceprints DROP COLUMN rejections")
