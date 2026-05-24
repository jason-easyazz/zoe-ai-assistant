"""0005 — Touch panel SSH fields and provisioning codes table.

Adds:
- panels: ssh_user, ssh_key_path, ssh_port columns
- panel_provision_codes: first-boot pairing codes table
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── panels: SSH access fields ─────────────────────────────────────────────
    op.execute("""
        ALTER TABLE panels
        ADD COLUMN IF NOT EXISTS ssh_user TEXT DEFAULT 'pi'
    """)
    op.execute("""
        ALTER TABLE panels
        ADD COLUMN IF NOT EXISTS ssh_key_path TEXT
    """)
    op.execute("""
        ALTER TABLE panels
        ADD COLUMN IF NOT EXISTS ssh_port INTEGER DEFAULT 22
    """)

    # ── panel_provision_codes ─────────────────────────────────────────────────
    op.execute("""
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
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_provision_codes_device ON panel_provision_codes (device_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_provision_codes_status ON panel_provision_codes (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_provision_codes_expires ON panel_provision_codes (expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS panel_provision_codes")
    # SQLite does not support DROP COLUMN — skip SSH field removal for SQLite compat.
    # On PostgreSQL: ALTER TABLE panels DROP COLUMN ssh_user, DROP COLUMN ssh_key_path, DROP COLUMN ssh_port
