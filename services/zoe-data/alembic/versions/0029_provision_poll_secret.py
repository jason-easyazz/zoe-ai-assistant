"""0029 — bind the first-boot pairing poll to the device that started it.

``panel_provision_codes`` gains ``poll_secret_hash``: the sha256 of a random
per-attempt secret that ``POST /api/panels/provision/request`` returns to the
pairing device only. ``GET /api/panels/provision/{code}`` refuses a poll that
does not present it, so the raw kiosk device token is released to the device
that started the flow rather than to whoever polls the code first (auth audit
2026-09-27).

The table itself may be ABSENT: the live database never had it (0005's CREATE
did not land there), and the 2026-09-27 deploy failed on a bare ``ALTER TABLE``
→ ``relation "panel_provision_codes" does not exist``. Schema comes from
Alembic, never from request-time DDL, so this migration first (re)asserts
0005's table and indexes EXACTLY with ``IF NOT EXISTS`` — a no-op where 0005
ran, the missing table where it did not — and then adds the column. Idempotent
on fresh and existing databases alike.

Downgrade drops ONLY the column, never the table: 0005 owns the table, and a
database where 0029 created it must not lose its pairing rows on a one-step
rollback.

``ADD COLUMN IF NOT EXISTS`` is PostgreSQL (production) and SQLite 3.35+ only,
so the SQLite branch probes ``PRAGMA table_info`` instead — the split 0026 uses.
"""

from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_COLUMN = "poll_secret_hash"

# Verbatim from 0005_panel_provisioning.py — keep in lockstep.
_TABLE_DDL = """
        CREATE TABLE IF NOT EXISTS panel_provision_codes (
            code            TEXT PRIMARY KEY,
            device_id       TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            panel_id        TEXT,
            token           TEXT,
            created_at      TEXT NOT NULL DEFAULT (to_char(timezone('UTC', now()), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')),
            expires_at      TEXT NOT NULL,
            confirmed_by    TEXT
        )
    """
_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_provision_codes_device ON panel_provision_codes (device_id)",
    "CREATE INDEX IF NOT EXISTS idx_provision_codes_status ON panel_provision_codes (status)",
    "CREATE INDEX IF NOT EXISTS idx_provision_codes_expires ON panel_provision_codes (expires_at)",
)


def _sqlite_existing_columns(bind) -> set[str]:
    rows = bind.exec_driver_sql("PRAGMA table_info(panel_provision_codes)").fetchall()
    return {str(r[1]) for r in rows}


def upgrade() -> None:
    op.execute(_TABLE_DDL)
    for stmt in _INDEX_DDL:
        op.execute(stmt)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"ALTER TABLE IF EXISTS panel_provision_codes ADD COLUMN IF NOT EXISTS {_COLUMN} TEXT")
    elif _COLUMN not in _sqlite_existing_columns(bind):
        op.add_column("panel_provision_codes", sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    # The column only — the table belongs to 0005.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"ALTER TABLE IF EXISTS panel_provision_codes DROP COLUMN IF EXISTS {_COLUMN}")
    elif _COLUMN in _sqlite_existing_columns(bind):
        op.drop_column("panel_provision_codes", _COLUMN)
