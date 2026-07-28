"""0028 — drop the unused panel presence events table."""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


_BACKUP = "panel_presence_events_backup_0028"


def upgrade() -> None:
    # services/zoe-data/AGENTS.md: "never DROP/DELETE without WHERE and a
    # backup". The table is dead (no writer remains in the tree; zero rows on
    # the live database), but CD runs this against any deployment whose history
    # differs, so preserve what is actually there rather than trusting a
    # point-in-time audit (Codex P1, #1583).
    #
    # Conditional on purpose: the copy is made ONLY when there is data to lose,
    # so the normal zero-row path drops cleanly and leaves no permanent empty
    # table behind. A surviving backup table means that deployment had rows.
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        exists = conn.exec_driver_sql(
            "SELECT to_regclass('public.panel_presence_events')"
        ).scalar()
        if exists:
            rows = conn.exec_driver_sql(
                "SELECT count(*) FROM panel_presence_events"
            ).scalar()
            if rows:
                op.execute(
                    f"CREATE TABLE IF NOT EXISTS {_BACKUP} AS "
                    "SELECT * FROM panel_presence_events"
                )
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
    # Restore whatever upgrade() preserved, then retire the backup: a downgrade
    # that leaves the rows sitting in a side table is not a restore.
    conn = op.get_bind()
    if conn.dialect.name == "postgresql" and conn.exec_driver_sql(
        f"SELECT to_regclass('public.{_BACKUP}')"
    ).scalar():
        op.execute(
            f"INSERT INTO panel_presence_events SELECT * FROM {_BACKUP} "
            "ON CONFLICT (id) DO NOTHING"
        )
        op.execute(f"DROP TABLE IF EXISTS {_BACKUP}")
