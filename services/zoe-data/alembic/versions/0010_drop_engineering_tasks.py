"""0010 — Retire engineering_tasks.

Execution moved to Hermes Kanban (via the Zoe executor seam); Multica is the
issue source of truth. The engineering_tasks phase machine became a third
overlapping state store, so it is dropped. ``background_tasks.multica_issue_id``
is retained — it is still used for evolution-proposal deploy sync.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_engineering_tasks_background")
    op.execute("DROP INDEX IF EXISTS idx_engineering_tasks_phase")
    op.execute("DROP INDEX IF EXISTS idx_engineering_tasks_multica")
    op.execute("DROP TABLE IF EXISTS engineering_tasks")


def downgrade() -> None:
    # Recreate the table shape from 0009 (without backfilling data).
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
