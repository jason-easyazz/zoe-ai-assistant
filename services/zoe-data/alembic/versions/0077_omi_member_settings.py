"""0077 - a member's OWN pendant settings (``omi_member_settings.py``; design ``docs/knowledge/omi-linking.md``).

Five columns on ``omi_voice_settings`` (migration 0055: one row per member, ``learning_enabled``), each defaulting to TODAY's behaviour so
an absent row, an existing row and a fresh row all mean "exactly what the operator flags say":

  * ``sync_paused``          0  - 1 = hold this member's uploads (the Pi keeps the audio, 409 ``device_not_registered``)
  * ``speaker_tag_enabled``  1  - 0 = do not split voices in this member's recordings even when ``ZOE_OMI_SPEAKER_TAG`` is on
  * ``keep_audio_days``      NULL = the operator's ``ZOE_OMI_AUDIO_RETENTION_DAYS``; a number only ever NARROWS it
  * ``day_review_enabled``   1  - 0 = no day review for this member even when ``ZOE_OMI_DAY_REVIEW`` is on
  * ``notice_ack_at``        NULL - when the member was told, at link time, that the pendant records the people around them

Erased with the row (``omi_voices.forget_user`` deletes ``omi_voice_settings`` for the member). Additive; ``ADD COLUMN IF NOT EXISTS`` on
PostgreSQL and a column probe on SQLite keep reruns safe. CHAIN: on 0076; renumber at merge if the heads move.
"""

from __future__ import annotations

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None

#: (column, DDL type + default). The defaults ARE today's behaviour; a test pins that.
COLUMNS = (
    ("sync_paused", "INTEGER NOT NULL DEFAULT 0"),
    ("speaker_tag_enabled", "INTEGER NOT NULL DEFAULT 1"),
    ("keep_audio_days", "INTEGER"),
    ("day_review_enabled", "INTEGER NOT NULL DEFAULT 1"),
    ("notice_ack_at", "TEXT"),
)


def _pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _have(table: str) -> set:
    return {r[1] for r in op.get_bind().exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS omi_voice_settings (
               user_id TEXT PRIMARY KEY,
               learning_enabled INTEGER NOT NULL DEFAULT 1,
               updated_at TEXT NOT NULL
           )"""
    )
    have = set() if _pg() else _have("omi_voice_settings")
    for name, ddl in COLUMNS:
        if _pg():
            op.execute(f"ALTER TABLE omi_voice_settings ADD COLUMN IF NOT EXISTS {name} {ddl}")
        elif name not in have:
            op.execute(f"ALTER TABLE omi_voice_settings ADD COLUMN {name} {ddl}")


def downgrade() -> None:
    have = set() if _pg() else _have("omi_voice_settings")
    for name, _ddl in reversed(COLUMNS):
        if _pg():
            op.execute(f"ALTER TABLE omi_voice_settings DROP COLUMN IF EXISTS {name}")
        elif name in have:
            op.execute(f"ALTER TABLE omi_voice_settings DROP COLUMN {name}")
