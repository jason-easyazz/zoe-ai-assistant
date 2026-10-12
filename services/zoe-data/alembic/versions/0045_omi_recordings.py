"""0045 - omi_recordings (``omi_recordings.py``, ``routers/omi_recordings.py``; flag ``ZOE_OMI_SYNC_ENABLED``).

ONE NEW table and no change to any existing one, so a deploy that has not run this migration yet degrades to "the
feature is off" (every read and write in the module is fail-soft and the routes are not mounted while the flag is off).

``omi_recordings`` is the owner's recordings made on the Omi pendant and synced when it came in range of the panel Pi
("record on the pendant, sync when in range of Zoe"; ``docs/architecture/omi-integration-plan.md`` section 4). One row per
recording. It holds METADATA, the TRANSCRIPT, and a path to the audio file until the audio is deleted:

  * ``recording_key`` is the Pi's idempotency key (ring incarnation + record seq range). ``UNIQUE (user_id, device_id,
    recording_key)`` makes a retried upload a no-op; ``sha256`` is the digest of the stored WAV so a retry with different
    bytes under the same key is a conflict, never a silent overwrite.
  * ``started_at`` / ``ended_at`` are when it was RECORDED (pendant timestamps, corrected to the host clock),
    ``time_quality`` says how trustworthy that is (``device`` | ``corrected`` | ``estimated``).
  * ``status``: ``pending`` -> ``transcribing`` -> ``done`` | ``failed``; ``deleted`` is a TOMBSTONE (no audio, no transcript,
    no times) kept so that a Pi retry of a recording the owner already deleted is acknowledged, not resurrected.
  * ``speaker_state`` is ``unverified`` for every row today: no speaker gate runs on a pendant recording yet, so the
    transcript may contain other people's words. ``reviewed`` stays 0 until the owner reviews it; an unreviewed transcript is
    kept OUT of fact extraction, recall and the digest (nothing reads this table for those).
  * ``keep_audio`` (default 0): the WAV is deleted right after a successful transcription unless the owner keeps it;
    ``audio_expires_at`` bounds how long a kept or untranscribed WAV can live (``ZOE_OMI_AUDIO_RETENTION_DAYS``, default 7).

Timestamps are TEXT UTC (``%Y-%m-%dT%H:%M:%SZ``) like ``commitments``. No FK to ``users`` (the per-user erase path sweeps
the rows by exact id). ``IF NOT EXISTS`` runs unchanged on PostgreSQL and SQLite, so reruns are safe. The downgrade drops the
table.
"""

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS omi_recordings (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               source TEXT NOT NULL DEFAULT 'omi',
               provenance TEXT NOT NULL DEFAULT 'recording',
               device_id TEXT NOT NULL,
               recording_key TEXT NOT NULL,
               sha256 TEXT NOT NULL DEFAULT '',
               started_at TEXT NOT NULL DEFAULT '',
               ended_at TEXT NOT NULL DEFAULT '',
               duration_s DOUBLE PRECISION NOT NULL DEFAULT 0,
               time_quality TEXT NOT NULL DEFAULT 'device',
               gap_before INTEGER NOT NULL DEFAULT 0,
               audio_path TEXT NOT NULL DEFAULT '',
               audio_bytes INTEGER NOT NULL DEFAULT 0,
               keep_audio INTEGER NOT NULL DEFAULT 0,
               audio_expires_at TEXT,
               status TEXT NOT NULL DEFAULT 'pending',
               attempts INTEGER NOT NULL DEFAULT 0,
               claimed_at TEXT,
               transcript TEXT NOT NULL DEFAULT '',
               speaker_state TEXT NOT NULL DEFAULT 'unverified',
               reviewed INTEGER NOT NULL DEFAULT 0,
               created_at TEXT NOT NULL,
               transcribed_at TEXT,
               deleted_at TEXT,
               UNIQUE (user_id, device_id, recording_key)
           )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_omi_recordings_user ON omi_recordings (user_id, started_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_omi_recordings_status ON omi_recordings (status, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS omi_recordings")
