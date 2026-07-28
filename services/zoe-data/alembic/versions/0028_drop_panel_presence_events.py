"""0028 — drop the unused panel presence events table."""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS panel_presence_events")


def downgrade() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS panel_presence_events (
    id TEXT PRIMARY KEY,
    panel_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (panel_id) REFERENCES panels(panel_id) ON DELETE CASCADE
)
""")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_presence_panel_time "
        "ON panel_presence_events(panel_id, created_at)"
    )
