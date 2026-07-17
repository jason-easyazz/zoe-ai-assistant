"""0022 — display_preferences: give sleep_enabled / sleep_seconds somewhere to live.

`_DEFAULT_DISPLAY_PREFS` has advertised `sleep_enabled` + `sleep_seconds` since the
dimmed-sleep work, and `home.html` reads both at boot to size its idle-sleep window.
But the table never had the columns and the PUT's INSERT enumerates columns
explicitly, so every write silently dropped them: `_row_to_prefs` fell back to the
hardcoded defaults and the operator's sleep choice could not be saved. The bug hid
because a GET *looked* right — the defaults are what it returns.

`off_enabled`/`off_seconds` have columns and work; this brings sleep to parity.

Conventions match 0018/0020: dialect-aware, `IF NOT EXISTS` so reruns are safe.
Production is PostgreSQL; the SQLite branch exists for test DBs. Columns are
nullable with no server default on purpose — NULL means "never set", which
`_row_to_prefs` already treats as "use the default" (it skips None), so existing
rows keep behaving exactly as they do today.
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE display_preferences "
            "ADD COLUMN IF NOT EXISTS sleep_enabled INTEGER"
        )
        op.execute(
            "ALTER TABLE display_preferences "
            "ADD COLUMN IF NOT EXISTS sleep_seconds INTEGER"
        )
    else:
        # SQLite has no ADD COLUMN IF NOT EXISTS — probe PRAGMA first so a rerun
        # (or a test DB created from a newer schema) doesn't error.
        bind = op.get_bind()
        cols = {r[1] for r in bind.exec_driver_sql(
            "PRAGMA table_info(display_preferences)").fetchall()}
        if "sleep_enabled" not in cols:
            op.execute("ALTER TABLE display_preferences ADD COLUMN sleep_enabled INTEGER")
        if "sleep_seconds" not in cols:
            op.execute("ALTER TABLE display_preferences ADD COLUMN sleep_seconds INTEGER")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE display_preferences DROP COLUMN IF EXISTS sleep_enabled")
        op.execute("ALTER TABLE display_preferences DROP COLUMN IF EXISTS sleep_seconds")
    # SQLite pre-3.35 cannot DROP COLUMN; test DBs are disposable, so leave them.
