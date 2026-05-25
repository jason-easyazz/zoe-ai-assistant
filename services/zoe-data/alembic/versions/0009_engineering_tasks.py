"""0009 — Durable engineering workflow state."""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE background_tasks
        ADD COLUMN IF NOT EXISTS multica_issue_id TEXT
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS engineering_tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            task TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'api',
            source_id TEXT,
            multica_issue_id TEXT,
            background_task_id INTEGER,
            idempotency_key TEXT UNIQUE,
            phase TEXT NOT NULL DEFAULT 'queued',
            status TEXT NOT NULL DEFAULT 'active',
            branch TEXT,
            pr_number INTEGER,
            pr_url TEXT,
            greptile_status TEXT,
            greptile_confidence INTEGER,
            greptile_unaddressed_count INTEGER,
            round_count INTEGER NOT NULL DEFAULT 0,
            max_rounds INTEGER NOT NULL DEFAULT 5,
            target_confidence INTEGER NOT NULL DEFAULT 5,
            blocker_reason TEXT,
            last_error TEXT,
            cost_estimated_usd REAL NOT NULL DEFAULT 0.0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_engineering_tasks_multica "
        "ON engineering_tasks (multica_issue_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_engineering_tasks_phase "
        "ON engineering_tasks (phase, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_engineering_tasks_background "
        "ON engineering_tasks (background_task_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_background_tasks_multica "
        "ON background_tasks (multica_issue_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_background_tasks_multica")
    op.execute("DROP INDEX IF EXISTS idx_engineering_tasks_background")
    op.execute("DROP INDEX IF EXISTS idx_engineering_tasks_phase")
    op.execute("DROP INDEX IF EXISTS idx_engineering_tasks_multica")
    op.execute("DROP TABLE IF EXISTS engineering_tasks")
    op.execute("""
        ALTER TABLE background_tasks
        DROP COLUMN IF EXISTS multica_issue_id
    """)
