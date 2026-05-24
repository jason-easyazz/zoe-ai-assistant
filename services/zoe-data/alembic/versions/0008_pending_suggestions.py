"""0008 — Pending proactive save suggestions."""

revision = "0008"
down_revision = "0007"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS pending_suggestions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            session_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            description TEXT NOT NULL,
            list_type TEXT,
            when_hint TEXT,
            amount_hint TEXT,
            offer_phrase TEXT NOT NULL,
            pre_filled_slots TEXT,
            created_at TEXT NOT NULL,
            turns_elapsed INTEGER NOT NULL DEFAULT 0,
            expire_after_turns INTEGER NOT NULL DEFAULT 2,
            resolved INTEGER NOT NULL DEFAULT 0
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_pending_suggestions_user_session "
        "ON pending_suggestions (user_id, session_id, resolved)"
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS pending_suggestions")
