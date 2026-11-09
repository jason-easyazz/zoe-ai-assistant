"""0075 - the MORNING TRANSCRIPT CHECK (``transcript_check.py``; flag ``ZOE_TRANSCRIPT_CHECK``, default off). Spec: ``docs/knowledge/transcript-check.md``.

  * ``omi_check_items``       - the night re-pass's check queue: one UNCERTAIN span (a word, a low-confidence chunk, or - kind 'who' - a turn whose speaker is unsure) of one recording, with the
                                sentence around it, each model's alternative (JSON ``alts``), the audio snippet bounds, the chunk template the
                                contender ledger scores an answer against, and its fate. The per-model ledger itself is #2358's ``ledger.jsonl``.
                                Words of the owner's pendant audio: erased with the recording (``omi_voices.drop_recording``) and with the
                                member (``omi_voices.forget_user``), redacted by a forget (``omi_voices.redact``).
  * ``omi_check_asks``        - one row per member per household day: the spoken ask was made (``asked``) or reserved. The PRIMARY KEY is the
                                once-a-day lock.
  * ``omi_segments.text_provenance`` - '' (the pass's own text) | 'owner_corrected' (the owner touched the word).

Additive; ``IF NOT EXISTS`` / a column probe keep reruns safe on PostgreSQL and SQLite. CHAIN: on 0073 (the night re-pass, #2358). 0074 is #2360 (conversation notes); renumber at merge if the heads move.
"""

from __future__ import annotations

from alembic import op

revision = "0075"
down_revision = "0073"
branch_labels = None
depends_on = None


def _pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _have(table: str) -> set:
    return {r[1] for r in op.get_bind().exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS omi_check_items (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               recording_id TEXT NOT NULL,
               day_local TEXT NOT NULL,
               kind TEXT NOT NULL CHECK (kind IN ('slot', 'chunk', 'who')),
               start_ms INTEGER NOT NULL,
               end_ms INTEGER NOT NULL,
               before_text TEXT NOT NULL DEFAULT '',
               span_text TEXT NOT NULL DEFAULT '',
               after_text TEXT NOT NULL DEFAULT '',
               alts TEXT NOT NULL DEFAULT '[]',
               pick TEXT NOT NULL DEFAULT '',
               importance TEXT NOT NULL DEFAULT 'chat',
               score REAL NOT NULL DEFAULT 0,
               status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'skipped', 'expired', 'shadow')),
               answer TEXT,
               answered_via TEXT,
               answered_at TEXT,
               applied INTEGER NOT NULL DEFAULT 0,
               created_at TEXT NOT NULL,
               expires_at TEXT,
               chunk_tpl TEXT NOT NULL DEFAULT '',
               chunk_t0_ms INTEGER NOT NULL DEFAULT 0,
               chunk_t1_ms INTEGER NOT NULL DEFAULT 0,
               UNIQUE (recording_id, kind, start_ms, end_ms)
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_omi_check_items_user ON omi_check_items (user_id, status, day_local)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_omi_check_items_rec ON omi_check_items (recording_id)")
    op.execute(
        """CREATE TABLE IF NOT EXISTS omi_check_asks (
               user_id TEXT NOT NULL,
               day_local TEXT NOT NULL,
               outcome TEXT NOT NULL DEFAULT 'reserved',
               created_at TEXT NOT NULL,
               PRIMARY KEY (user_id, day_local)
           )"""
    )
    if _pg():
        op.execute("ALTER TABLE omi_segments ADD COLUMN IF NOT EXISTS text_provenance TEXT NOT NULL DEFAULT ''")
    elif "text_provenance" not in _have("omi_segments"):
        op.execute("ALTER TABLE omi_segments ADD COLUMN text_provenance TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_omi_check_items_rec")
    op.execute("DROP INDEX IF EXISTS idx_omi_check_items_user")
    for t in ("omi_check_items", "omi_check_asks"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
    if _pg():
        op.execute("ALTER TABLE omi_segments DROP COLUMN IF EXISTS text_provenance")
    elif "text_provenance" in _have("omi_segments"):
        op.execute("ALTER TABLE omi_segments DROP COLUMN text_provenance")
