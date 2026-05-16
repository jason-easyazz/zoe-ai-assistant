"""0004 — A2A federation tables and column additions.

Adds:
- background_tasks: claimed/blocked states + blocker_reason + checkout_run_id + request_depth
- agent_cost_events: per-agent cost/token tracking
- llm_call_log: LLM call observability
- evolution_proposals: self-evolution proposal ledger
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── background_tasks: enhanced lifecycle columns ──────────────────────────
    op.execute("""
        ALTER TABLE background_tasks
        ADD COLUMN IF NOT EXISTS blocker_reason TEXT,
        ADD COLUMN IF NOT EXISTS checkout_run_id TEXT,
        ADD COLUMN IF NOT EXISTS request_depth INTEGER DEFAULT 0
    """)
    # Add 'claimed' and 'blocked' to the valid status values (PostgreSQL CHECK constraint)
    # We don't enforce CHECK via Alembic to keep the migration simple; status validation
    # is done in Python. The existing status column remains TEXT.

    # ── agent_cost_events ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_cost_events (
            id TEXT PRIMARY KEY,
            agent_name TEXT NOT NULL,
            model TEXT NOT NULL,
            task_id TEXT,
            user_id TEXT NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            estimated_cost_usd REAL DEFAULT 0.0,
            ts REAL NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_cost_events_agent ON agent_cost_events (agent_name)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_cost_events_ts ON agent_cost_events (ts)")

    # ── llm_call_log ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS llm_call_log (
            id TEXT PRIMARY KEY,
            agent_tier TEXT NOT NULL,
            model TEXT NOT NULL,
            session_id TEXT,
            user_id TEXT NOT NULL,
            latency_ms INTEGER,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            estimated_cost_usd REAL DEFAULT 0.0,
            feedback INTEGER,
            ts REAL NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_llm_log_tier ON llm_call_log (agent_tier)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_llm_log_ts ON llm_call_log (ts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_llm_log_session ON llm_call_log (session_id)")

    # ── evolution_proposals ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS evolution_proposals (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            evidence TEXT,
            target_patterns TEXT,
            status TEXT DEFAULT 'pending',
            multica_issue_id TEXT,
            proposed_at REAL NOT NULL,
            reviewed_at REAL,
            deployed_at REAL,
            validation_result TEXT,
            next_review_at REAL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_evolve_status ON evolution_proposals (status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_evolve_type ON evolution_proposals (type)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS evolution_proposals")
    op.execute("DROP TABLE IF EXISTS llm_call_log")
    op.execute("DROP TABLE IF EXISTS agent_cost_events")
    op.execute("""
        ALTER TABLE background_tasks
        DROP COLUMN IF EXISTS blocker_reason,
        DROP COLUMN IF EXISTS checkout_run_id,
        DROP COLUMN IF EXISTS request_depth
    """)
