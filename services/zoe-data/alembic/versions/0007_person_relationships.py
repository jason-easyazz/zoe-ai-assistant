"""0007 — People relationships + Personal/Work context model.

Adds:
- people.context (personal | work) — replaces the work/personal split in circle
- Migrates people.circle from 6 values (inner/family/friends/work/acquaintance/public)
  to 3 tiers (inner/circle/public)
- person_relationships table for typed person-to-person edges
- people.is_partial, how_we_met, first_met_date, introduced_by_person_id
"""

revision = "0007"
down_revision = "0006"

from alembic import op


def upgrade():
    # ── 1. Add context column ──────────────────────────────────────────────
    op.execute("ALTER TABLE people ADD COLUMN context TEXT NOT NULL DEFAULT 'personal'")

    # ── 2. Migrate circle 6 → 3 tiers (order matters — work first) ────────
    op.execute("UPDATE people SET context='work', circle='circle' WHERE circle='work'")
    op.execute("UPDATE people SET context='personal', circle='inner' WHERE circle IN ('inner','family')")
    op.execute("UPDATE people SET context='personal', circle='circle' WHERE circle='friends'")
    op.execute("UPDATE people SET context='personal', circle='public' WHERE circle IN ('acquaintance','public')")
    # Any remaining unmapped rows → personal/circle
    op.execute(
        "UPDATE people SET context='personal', circle='circle' "
        "WHERE circle NOT IN ('inner','circle','public')"
    )

    # ── 3. person_relationships table ─────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS person_relationships (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            person_a_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            person_b_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            rel_type     TEXT NOT NULL,
            rel_a_to_b   TEXT NOT NULL,
            rel_b_to_a   TEXT NOT NULL,
            rel_group    TEXT NOT NULL,
            notes        TEXT,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS person_relationships_pair
            ON person_relationships(user_id, person_a_id, person_b_id)
    """)

    # ── 4. Partial contacts + how-we-met columns ──────────────────────────
    op.execute("ALTER TABLE people ADD COLUMN IF NOT EXISTS is_partial INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE people ADD COLUMN IF NOT EXISTS how_we_met TEXT")
    op.execute("ALTER TABLE people ADD COLUMN IF NOT EXISTS first_met_date TEXT")
    op.execute(
        "ALTER TABLE people ADD COLUMN IF NOT EXISTS introduced_by_person_id TEXT "
        "REFERENCES people(id) ON DELETE SET NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS person_relationships_pair")
    op.execute("DROP TABLE IF EXISTS person_relationships")

    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS introduced_by_person_id")
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS first_met_date")
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS how_we_met")
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS is_partial")
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS context")

    # Restore original circle values as best-effort
    op.execute("ALTER TABLE people ADD COLUMN IF NOT EXISTS circle TEXT NOT NULL DEFAULT 'acquaintance'")
