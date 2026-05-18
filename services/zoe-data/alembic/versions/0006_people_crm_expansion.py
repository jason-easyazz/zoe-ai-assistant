"""0006 — People CRM expansion.

Adds:
- people: circle, health_score, notification_count, contact_count, last_contacted_at
- person_activities, person_important_dates, person_gift_ideas, person_bucket_list tables
- journal_entries.person_id nullable FK
- notes.person_id nullable FK
"""

revision = "0006"
down_revision = "0005"

from alembic import op


def upgrade():
    # ── people table: new CRM columns (all safe: NOT NULL with DEFAULT or nullable) ──
    op.execute("ALTER TABLE people ADD COLUMN circle TEXT NOT NULL DEFAULT 'acquaintance'")
    op.execute("ALTER TABLE people ADD COLUMN health_score REAL NOT NULL DEFAULT 0.5")
    op.execute("ALTER TABLE people ADD COLUMN notification_count INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE people ADD COLUMN contact_count INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE people ADD COLUMN last_contacted_at TEXT")

    # Auto-infer circle from existing relationship text
    op.execute("""
        UPDATE people SET circle = CASE
            WHEN lower(relationship) LIKE '%inner%'
              OR lower(relationship) LIKE '%partner%'
              OR lower(relationship) LIKE '%spouse%' THEN 'inner'
            WHEN lower(relationship) LIKE '%family%'
              OR lower(relationship) LIKE '%parent%'
              OR lower(relationship) LIKE '%sibling%'
              OR lower(relationship) LIKE '%child%' THEN 'family'
            WHEN lower(relationship) LIKE '%work%'
              OR lower(relationship) LIKE '%colleague%'
              OR lower(relationship) LIKE '%client%' THEN 'work'
            WHEN lower(relationship) LIKE '%friend%' THEN 'friends'
            ELSE 'acquaintance'
        END
    """)

    # ── Activity timeline ──────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS person_activities (
            id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            activity_type TEXT NOT NULL,
            description TEXT NOT NULL,
            source TEXT,
            venue TEXT,
            session_id TEXT,
            mem_id TEXT,
            created_at TEXT NOT NULL DEFAULT NOW()::TEXT
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_pact_person ON person_activities(person_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pact_user   ON person_activities(user_id,    created_at DESC)")

    # ── Important dates (birthdays, anniversaries, …) ─────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS person_important_dates (
            id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            label TEXT NOT NULL,
            date_type TEXT NOT NULL DEFAULT 'birthday',
            month INTEGER,
            day INTEGER,
            year INTEGER,
            reminder_days_before INTEGER NOT NULL DEFAULT 7,
            mem_id TEXT,
            created_at TEXT NOT NULL DEFAULT NOW()::TEXT
        )
    """)

    # ── Gift ideas ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS person_gift_ideas (
            id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'idea',
            price_hint TEXT,
            source TEXT,
            mem_id TEXT,
            created_at TEXT NOT NULL DEFAULT NOW()::TEXT
        )
    """)

    # ── Bucket list / shared experiences ──────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS person_bucket_list (
            id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            description TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            done_at TEXT,
            mem_id TEXT,
            created_at TEXT NOT NULL DEFAULT NOW()::TEXT
        )
    """)

    # ── Nullable FKs on existing tables ───────────────────────────────────────
    op.execute("ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS person_id TEXT REFERENCES people(id)")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS person_id TEXT REFERENCES people(id)")


def downgrade():
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS person_id")
    op.execute("ALTER TABLE journal_entries DROP COLUMN IF EXISTS person_id")
    op.execute("DROP TABLE IF EXISTS person_bucket_list")
    op.execute("DROP TABLE IF EXISTS person_gift_ideas")
    op.execute("DROP TABLE IF EXISTS person_important_dates")
    op.execute("DROP INDEX IF EXISTS idx_pact_user")
    op.execute("DROP INDEX IF EXISTS idx_pact_person")
    op.execute("DROP TABLE IF EXISTS person_activities")
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS last_contacted_at")
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS contact_count")
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS notification_count")
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS health_score")
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS circle")
