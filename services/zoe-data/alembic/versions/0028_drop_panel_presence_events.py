"""0028 — drop the unused panel presence events table."""

from alembic import context, op

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
    #
    # RETENTION: this backup is a rollback aid, not a store. Presence events are
    # documented as not persisted, so a surviving backup contradicts that policy
    # if it is left forever — the operator runbook carries an explicit "verify,
    # then DROP TABLE panel_presence_events_backup_0028" step. It is deliberately
    # not auto-dropped: a backup the migration deletes for you is not a backup
    # (Codex, #1583).
    # to_regclass is deliberately UNQUALIFIED everywhere in this file: the
    # DROP/CTAS/INSERT beside it resolve via search_path, so the existence
    # check must look in the same place. A 'public.'-qualified lookup misses
    # a table living in a non-public active schema and skips the backup while
    # the DROP still destroys it (Codex P1, #1583).
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        if context.is_offline_mode():
            # `alembic upgrade --sql`: the mock bind cannot return results, so
            # the same only-if-rows-exist conditional runs server-side. Nested
            # IFs on purpose — plpgsql plans an expression as a whole, so the
            # table reference must not be reachable when the table is absent.
            op.execute(f"""\
DO $$
BEGIN
    IF to_regclass('panel_presence_events') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM panel_presence_events) THEN
            CREATE TABLE IF NOT EXISTS {_BACKUP} AS
                SELECT * FROM panel_presence_events;
        END IF;
    END IF;
END;
$$""")
        else:
            exists = conn.exec_driver_sql(
                "SELECT to_regclass('panel_presence_events')"
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
    if conn.dialect.name != "postgresql":
        return
    # Only rows whose panel still exists: the CTAS backup carries no FK, so
    # a panel deleted between upgrade and downgrade would make this INSERT
    # violate the just-recreated FK and roll back the WHOLE downgrade. The
    # original column had ON DELETE CASCADE, so those rows were destined to
    # disappear with their panel anyway — dropping them here reproduces that
    # semantic instead of failing the migration (Codex, #1583).
    restore_sql = (
        f"INSERT INTO panel_presence_events SELECT * FROM {_BACKUP} b "
        "WHERE EXISTS (SELECT 1 FROM panels p WHERE p.panel_id = b.panel_id) "
        "ON CONFLICT (id) DO NOTHING"
    )
    if context.is_offline_mode():
        # `alembic downgrade --sql`: the mock bind cannot return results, so
        # the backup-exists check runs server-side instead.
        op.execute(f"""\
DO $$
BEGIN
    IF to_regclass('{_BACKUP}') IS NOT NULL THEN
        {restore_sql};
        DROP TABLE {_BACKUP};
    END IF;
END;
$$""")
    elif conn.exec_driver_sql(
        f"SELECT to_regclass('{_BACKUP}')"
    ).scalar():
        op.execute(restore_sql)
        op.execute(f"DROP TABLE IF EXISTS {_BACKUP}")
