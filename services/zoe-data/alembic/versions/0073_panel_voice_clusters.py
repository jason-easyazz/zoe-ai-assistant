"""0073 - unknown panel voices for passive named guests (``named_guest_learning.py``; ``ZOE_GUEST_VOICE_LEARN``, flag-dark).

  * ``panel_voice_clusters`` - one unknown voice heard addressing Zoe at the panel: its centre, how many turns / wake-word turns,
                               the last conversations and days it was heard, its tightness, ``open`` | ``asked`` (Zoe asked its
                               name) and an ``expires_at`` (dropped when never confirmed).
  * ``panel_voice_samples``  - the speaker EMBEDDINGS behind it (at most 30 per voice, never audio).

Held for the household owner (``user_id`` = the first admin, who would own the guest an answered ask creates); the owner lists
and erases them. BIOMETRIC: the nightly backup carries no row
data of either table (``postgres-nightly-backup.sh`` BIOMETRIC_TABLES). Runbook ``docs/knowledge/named-guests.md``. Idempotent; the
downgrade drops both tables (lossy by design: unknown voices are re-heard).

CHAIN: on 0072 (the same stacked PR line).
"""

from __future__ import annotations

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def statements(dialect: str) -> list[str]:
    blob = "BYTEA" if dialect == "postgresql" else "BLOB"
    return [
        f"""CREATE TABLE IF NOT EXISTS panel_voice_clusters (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               model_id TEXT NOT NULL,
               centroid {blob} NOT NULL,
               n_turns INTEGER NOT NULL DEFAULT 0,
               n_wake INTEGER NOT NULL DEFAULT 0,
               sessions TEXT NOT NULL DEFAULT '[]',
               days TEXT NOT NULL DEFAULT '[]',
               tightness REAL NOT NULL DEFAULT 0,
               status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'asked')),
               asked_at TEXT,
               first_at TEXT NOT NULL,
               last_at TEXT NOT NULL,
               expires_at TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_panel_voice_clusters_expiry ON panel_voice_clusters (expires_at)",
        f"""CREATE TABLE IF NOT EXISTS panel_voice_samples (
               id TEXT PRIMARY KEY,
               cluster_id TEXT NOT NULL REFERENCES panel_voice_clusters(id) ON DELETE CASCADE,
               embedding {blob} NOT NULL,
               session_id TEXT NOT NULL DEFAULT '',
               day TEXT NOT NULL,
               created_at TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_panel_voice_samples_cluster ON panel_voice_samples (cluster_id, created_at)",
    ]


def upgrade() -> None:
    for sql in statements(op.get_bind().dialect.name):
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS panel_voice_samples")
    op.execute("DROP TABLE IF EXISTS panel_voice_clusters")
