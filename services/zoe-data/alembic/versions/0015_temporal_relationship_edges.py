"""0015 — Temporal relationship edges (valid_from / valid_to / superseded_by).

Roadmap item 2 of ADR-relationship-memory: let a relationship *change over time
without losing history* ("was married → now divorced"), done relationally on the
existing ``person_relationships`` edge table — no graph DB (Samantha build-plan
guardrail: no heavy graph backend).

Adds three nullable temporal columns to ``person_relationships``:
  - valid_from    — when this edge started being true (backfilled = created_at).
  - valid_to      — when this edge stopped being true; NULL = **currently valid**.
  - superseded_by — id of the edge that replaced this one (NULL if none).

The old ``person_relationships_pair`` UNIQUE index over
(user_id, person_a_id, person_b_id) allowed exactly one row per pair — which is
incompatible with keeping history. It is replaced by a **partial** unique index
``person_relationships_pair_active`` scoped ``WHERE valid_to IS NULL`` so any
number of *historical* (superseded, valid_to set) rows may exist for a pair, but
only **one current** edge. Partial unique indexes are supported by both
PostgreSQL (production) and SQLite >= 3.8 (tests).

``IF NOT EXISTS`` on every DDL makes the migration rerun-safe. Production is
PostgreSQL; the same statements run on SQLite in tests (``ADD COLUMN IF NOT
EXISTS`` is honoured by modern SQLite via Postgres-compatible parsing in Alembic
raw SQL — where a given SQLite build lacks it we branch on dialect, matching
0007's pattern).
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _add_column(dialect: str, column_sql: str) -> None:
    """ADD COLUMN, using IF NOT EXISTS on Postgres and a plain ADD on SQLite.

    SQLite has no ``ADD COLUMN IF NOT EXISTS``; a fresh test DB is clean so a
    plain ``ADD COLUMN`` is safe there. Postgres keeps IF NOT EXISTS so a
    partial-failure rerun does not die on a duplicate column. Mirrors 0007.
    """
    if dialect == "postgresql":
        op.execute(f"ALTER TABLE person_relationships ADD COLUMN IF NOT EXISTS {column_sql}")
    else:
        op.execute(f"ALTER TABLE person_relationships ADD COLUMN {column_sql}")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name

    # ── 1. Temporal columns (all nullable) ────────────────────────────────
    _add_column(dialect, "valid_from TEXT")
    _add_column(dialect, "valid_to TEXT")
    _add_column(dialect, "superseded_by TEXT")

    # ── 2. Backfill valid_from for existing rows ──────────────────────────
    # Every pre-existing edge is currently valid: its window opened when it was
    # created and has not closed (valid_to stays NULL).
    op.execute(
        "UPDATE person_relationships SET valid_from = created_at WHERE valid_from IS NULL"
    )

    # ── 3. Swap the full pair index for a partial (current-only) one ──────
    # Old: one row per (user, a, b) — cannot keep history.
    # New: one *current* row per pair; unlimited superseded (valid_to != NULL).
    op.execute("DROP INDEX IF EXISTS person_relationships_pair")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS person_relationships_pair_active "
        "ON person_relationships(user_id, person_a_id, person_b_id) "
        "WHERE valid_to IS NULL"
    )


def downgrade() -> None:
    # NOTE: downgrade can FAIL if historical/superseded rows exist — recreating
    # the full (non-partial) unique pair index will hit a duplicate-pair
    # violation for any pair that has both a current and a superseded row. That
    # is acceptable: temporal history is intentionally lossy to reverse.
    op.execute("DROP INDEX IF EXISTS person_relationships_pair_active")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS person_relationships_pair "
        "ON person_relationships(user_id, person_a_id, person_b_id)"
    )
    op.execute("ALTER TABLE person_relationships DROP COLUMN IF EXISTS superseded_by")
    op.execute("ALTER TABLE person_relationships DROP COLUMN IF EXISTS valid_to")
    op.execute("ALTER TABLE person_relationships DROP COLUMN IF EXISTS valid_from")
