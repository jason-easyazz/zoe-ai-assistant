"""0023 — speaker_profiles.consent_at: explicit biometric-enrollment consent.

Speaker identification is opt-in per person (W6 consent): a profile row may
only be matched against — or synced down to a panel — after its owner has
explicitly consented to voice identification. NULL means "no recorded
consent"; such rows are excluded from /api/voice/identify and
/api/voice/profiles/sync but still listed in /api/voice/profiles so the
settings UI can show (and complete) a half-enrolled profile.

Nullable with no default on purpose: pre-existing rows (enrolled before the
consent gate existed) stay inert until re-consented via the enroll flow.

Conventions match 0018/0020/0022: dialect-aware, rerun-safe.
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE speaker_profiles "
            "ADD COLUMN IF NOT EXISTS consent_at TIMESTAMP"
        )
    else:
        # SQLite has no ADD COLUMN IF NOT EXISTS — probe PRAGMA first so a rerun
        # (or a test DB created from a newer schema) doesn't error.
        bind = op.get_bind()
        cols = {r[1] for r in bind.exec_driver_sql(
            "PRAGMA table_info(speaker_profiles)").fetchall()}
        if "consent_at" not in cols:
            op.execute("ALTER TABLE speaker_profiles ADD COLUMN consent_at TIMESTAMP")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE speaker_profiles DROP COLUMN IF EXISTS consent_at")
    # SQLite pre-3.35 cannot DROP COLUMN; test DBs are disposable, so leave them.
