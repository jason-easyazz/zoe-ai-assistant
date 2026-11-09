"""0074 - the pendant NIGHT RE-PASS (``omi_repass.py``; flag ``ZOE_OMI_NIGHT_REPASS``, default off). Spec: ``docs/knowledge/omi-night-repass.md``.

  * ``omi_recordings.repass_state``    - '' (never in scope) | 'pending' (its WAV is HELD until the night pass has run) | 'done' (the transcript
                                         and segments were replaced) | 'shadow' (compared only, text untouched) | 'failed' (gave up after
                                         ``MAX_PASS_ATTEMPTS``; the WAV is released).
  * ``omi_recordings.repass_attempts`` - how many nights tried it (a carry-over is not a failure; a crash is).
  * ``omi_recordings.repass_model``    - PROVENANCE: the ASR model + the speaker model that wrote the stored text, e.g. ``large-v3+ecapa``.
  * ``omi_recordings.repass_at``       - PROVENANCE: when the pass finished (UTC text, like every other stamp here).
  * ``omi_segments.clean_text`` / ``clean_how`` - the CLEAN READ (``omi_repass_clean.py``): a lightly cleaned twin of each turn for DISPLAY only, beside the
                                         verbatim ``text`` (which memory, extraction and the day review keep reading). ``clean_how`` '' = not cleaned yet.
  * ``omi_channel_means``              - the per-channel MEAN of unit speaker embeddings (model_id + channel + scope = the device), the offset the speaker step removes from pendant turns / prints;
                                         a short session falls back to the stored per-device mean. Biometric-derived: erased with the member (``omi_voices.forget_user``).
  * ``omi_wearer_samples``             - the WEARER's pendant-channel voice reference, as the speaker units (panel ResNet34 embeddings) it is made of: ``confirmed`` ones
                                         (segments the owner labelled "me", or a heuristic proposal the owner confirmed) form the print;
                                         ``proposed`` ones (the loudest-nearest-voice heuristic) are NEVER used until the owner confirms them.
                                         Biometric: erased with the recording (``omi_voices.drop_recording``) and with the member
                                         (``omi_voices.forget_user``).

Additive only: a deploy that has not run this yet degrades to "the re-pass is off" (every reader is fail-soft). ``IF NOT EXISTS`` / a column
probe keep reruns safe on PostgreSQL and SQLite. The downgrade drops the table and the columns.

CHAIN: on 0073 (panel voice clusters).
"""

from __future__ import annotations

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None

COLUMNS = (
    ("repass_state", "TEXT NOT NULL DEFAULT ''"),
    ("repass_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("repass_model", "TEXT NOT NULL DEFAULT ''"),
    ("repass_at", "TEXT"),
)


SEG_COLUMNS = (
    ("clean_text", "TEXT NOT NULL DEFAULT ''"),
    ("clean_how", "TEXT NOT NULL DEFAULT ''"),
)


def _pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _have(table: str = "omi_recordings") -> set:
    return {r[1] for r in op.get_bind().exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}


def upgrade() -> None:
    blob = "BYTEA" if _pg() else "BLOB"
    op.execute(
        f"""CREATE TABLE IF NOT EXISTS omi_wearer_samples (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               recording_id TEXT NOT NULL,
               model_id TEXT NOT NULL,
               start_ms INTEGER NOT NULL,
               end_ms INTEGER NOT NULL,
               embedding {blob} NOT NULL,
               source TEXT NOT NULL CHECK (source IN ('owner_label', 'heuristic')),
               status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'confirmed')),
               proposal_id TEXT,
               created_at TEXT NOT NULL,
               UNIQUE (recording_id, start_ms, end_ms, model_id)
           )"""
    )
    op.execute(
        f"""CREATE TABLE IF NOT EXISTS omi_channel_means (
               user_id TEXT NOT NULL,
               model_id TEXT NOT NULL,
               channel TEXT NOT NULL CHECK (channel IN ('pendant', 'panel')),
               scope TEXT NOT NULL DEFAULT '',
               mean {blob} NOT NULL,
               n_units INTEGER NOT NULL DEFAULT 0,
               speech_s DOUBLE PRECISION NOT NULL DEFAULT 0,
               updated_at TEXT NOT NULL,
               PRIMARY KEY (user_id, model_id, channel, scope)
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_omi_wearer_samples_user ON omi_wearer_samples (user_id, model_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_omi_wearer_samples_rec ON omi_wearer_samples (recording_id)")
    if _pg():
        for name, ddl in COLUMNS:
            op.execute(f"ALTER TABLE omi_recordings ADD COLUMN IF NOT EXISTS {name} {ddl}")
    else:
        have = _have()
        for name, ddl in COLUMNS:
            if name not in have:
                op.execute(f"ALTER TABLE omi_recordings ADD COLUMN {name} {ddl}")
    op.execute("CREATE INDEX IF NOT EXISTS idx_omi_recordings_repass ON omi_recordings (repass_state, started_at)")
    if _pg():
        for name, ddl in SEG_COLUMNS:
            op.execute(f"ALTER TABLE omi_segments ADD COLUMN IF NOT EXISTS {name} {ddl}")
    else:
        have = _have("omi_segments")
        for name, ddl in SEG_COLUMNS:
            if name not in have:
                op.execute(f"ALTER TABLE omi_segments ADD COLUMN {name} {ddl}")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_omi_recordings_repass")
    op.execute("DROP TABLE IF EXISTS omi_wearer_samples")
    op.execute("DROP TABLE IF EXISTS omi_channel_means")
    if _pg():
        for name, _ in SEG_COLUMNS:
            op.execute(f"ALTER TABLE omi_segments DROP COLUMN IF EXISTS {name}")
    else:
        have = _have("omi_segments")
        for name, _ in SEG_COLUMNS:
            if name in have:
                op.execute(f"ALTER TABLE omi_segments DROP COLUMN {name}")
    if _pg():
        for name, _ in COLUMNS:
            op.execute(f"ALTER TABLE omi_recordings DROP COLUMN IF EXISTS {name}")
    else:
        have = _have()
        for name, _ in COLUMNS:
            if name in have:
                op.execute(f"ALTER TABLE omi_recordings DROP COLUMN {name}")
