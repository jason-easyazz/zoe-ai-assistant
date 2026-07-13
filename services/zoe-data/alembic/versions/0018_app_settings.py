"""0018 — app_settings: household-level key/value settings.

A tiny generic store for settings that belong to the HOUSEHOLD / device, not to
one user row (user-scoped prefs stay in ``user_preferences``). First consumer:
``tts_voice`` — the Kokoro voice Zoe speaks with (``voice_settings.py``),
user-selectable from the touch panel's "Zoe's voice" card.

Conventions match 0016/0017: TEXT columns, ISO-8601 TEXT timestamps with
dialect-aware defaults, ``IF NOT EXISTS`` everywhere so reruns are safe.
Production is PostgreSQL; the SQLite branch exists for test DBs.
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (now()::text)
            )
            """
        )
    else:
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_settings")
