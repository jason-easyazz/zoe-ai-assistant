"""0011 — Enforce one active list name per user."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the most recently updated active duplicate, move active items to it,
    # then soft-delete the duplicate list rows before adding the uniqueness guard.
    op.execute("""
        WITH ranked AS (
            SELECT
                id,
                FIRST_VALUE(id) OVER (
                    PARTITION BY user_id, lower(name)
                    ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
                ) AS keep_id,
                ROW_NUMBER() OVER (
                    PARTITION BY user_id, lower(name)
                    ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
                ) AS rn
            FROM lists
            WHERE deleted = 0
        )
        UPDATE list_items
        SET list_id = ranked.keep_id, updated_at = NOW()::TEXT
        FROM ranked
        WHERE ranked.rn > 1
          AND list_items.list_id = ranked.id
          AND list_items.deleted = 0
    """)
    op.execute("""
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY user_id, lower(name)
                    ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
                ) AS rn
            FROM lists
            WHERE deleted = 0
        )
        UPDATE lists
        SET deleted = 1, updated_at = NOW()::TEXT
        FROM ranked
        WHERE ranked.rn > 1
          AND lists.id = ranked.id
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_lists_active_user_lower_name
        ON lists (user_id, lower(name))
        WHERE deleted = 0
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_lists_active_user_lower_name")
