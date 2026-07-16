"""0020 — ensure app_settings exists (heal a SQLite→Postgres migration gap).

The ``app_settings`` table is created by migration 0018. But on databases that were
carried over from the legacy SQLite store, the ``alembic_version`` was stamped forward
(past 0018) while the table itself was **not** copied into Postgres — so ``app_settings``
was silently missing on those DBs, and ``voice_settings.py``'s persisted ``tts_voice``
preference never stuck (it fell open to the env default, so the panel voice picker
appeared to "not save").

Because those DBs are already stamped past 0018, ``alembic upgrade`` won't re-run 0018
to fix them. This forward, idempotent ``CREATE TABLE IF NOT EXISTS`` re-ensures the
table on any such DB. It's a no-op on fresh installs (0018 already created it) and on
DBs healed by hand. Same shape as 0018 (TEXT columns, ISO-8601 TEXT timestamp).
"""

from alembic import op

revision = "0020"
down_revision = "0019"
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
    # Intentionally a no-op: this migration only ENSURES the table exists — it does
    # not own it. Dropping app_settings here would break every 0018-era install on a
    # downgrade to 0019. The real DROP lives in 0018's downgrade.
    pass
