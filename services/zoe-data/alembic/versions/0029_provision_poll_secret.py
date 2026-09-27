"""0029 — bind the first-boot pairing poll to the device that started it.

``panel_provision_codes`` gains ``poll_secret_hash``: the sha256 of a random
per-attempt secret that ``POST /api/panels/provision/request`` returns to the
pairing device only. ``GET /api/panels/provision/{code}`` refuses a poll that
does not present it, so the raw kiosk device token is released to the device
that started the flow rather than to whoever polls the code first (auth audit
2026-09-27).

Additive and nullable. Rows written before this migration have no hash and are
refused by the poll; they expire within ``ZOE_PROVISION_CODE_TTL_S`` (5 min)
and the pairing device simply requests a fresh code.

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


def _sqlite_existing_columns(bind) -> set[str]:
    rows = bind.exec_driver_sql("PRAGMA table_info(panel_provision_codes)").fetchall()
    return {str(r[1]) for r in rows}


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"ALTER TABLE panel_provision_codes ADD COLUMN IF NOT EXISTS {_COLUMN} TEXT")
    elif _COLUMN not in _sqlite_existing_columns(bind):
        op.add_column("panel_provision_codes", sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"ALTER TABLE panel_provision_codes DROP COLUMN IF EXISTS {_COLUMN}")
    elif _COLUMN in _sqlite_existing_columns(bind):
        op.drop_column("panel_provision_codes", _COLUMN)
