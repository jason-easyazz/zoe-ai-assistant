"""Add metadata column to chat_sessions

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE chat_sessions
        ADD COLUMN IF NOT EXISTS metadata TEXT
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS metadata")
