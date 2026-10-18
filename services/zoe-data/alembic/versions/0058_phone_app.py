"""0058 - ``phone_devices`` + ``phone_push_tokens``: the box side of Zoe's own iPhone app, M0 (``phone_app.py``).

* ``phone_devices`` - one row per paired phone: the member, a display name, the platform, the SHA-256 of the device credential
  (``token_hash``, UNIQUE; the credential itself is shown to the phone once and never stored), the public half of the phone's
  Secure Enclave P-256 key (for the signed-challenge step-up of a later phase), timestamps and a revoked flag. Revoking keeps the
  row (audit) and deletes the device's push tokens.
* ``phone_push_tokens`` - the APNs tokens a phone registered, one per push type (alert | voip | pushtotalk | liveactivity), with
  the APNs environment and topic. Nothing sends to them in M0.

Pairing codes are NOT stored here: they live five minutes in the zoe-data process, hashed.

``IF NOT EXISTS`` makes a rerun safe and the statements run unchanged on PostgreSQL and SQLite. NOTE: other open PRs also add a
0058; whichever lands second re-chains its ``down_revision`` (and number) onto the new head.
"""

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS phone_devices (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               name TEXT NOT NULL DEFAULT '',
               platform TEXT NOT NULL DEFAULT 'ios',
               token_hash TEXT NOT NULL,
               public_key TEXT NOT NULL DEFAULT '',
               app_version TEXT NOT NULL DEFAULT '',
               created_at DOUBLE PRECISION NOT NULL DEFAULT 0,
               last_seen_at DOUBLE PRECISION NOT NULL DEFAULT 0,
               revoked INTEGER NOT NULL DEFAULT 0,
               revoked_at DOUBLE PRECISION
           )"""
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_phone_devices_token ON phone_devices(token_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_phone_devices_user ON phone_devices(user_id, revoked)")
    op.execute(
        """CREATE TABLE IF NOT EXISTS phone_push_tokens (
               device_id TEXT NOT NULL,
               push_type TEXT NOT NULL,
               token TEXT NOT NULL,
               environment TEXT NOT NULL,
               topic TEXT NOT NULL,
               updated_at DOUBLE PRECISION NOT NULL DEFAULT 0,
               PRIMARY KEY (device_id, push_type)
           )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS phone_push_tokens")
    op.execute("DROP INDEX IF EXISTS idx_phone_devices_user")
    op.execute("DROP INDEX IF EXISTS idx_phone_devices_token")
    op.execute("DROP TABLE IF EXISTS phone_devices")
