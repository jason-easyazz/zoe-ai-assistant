"""0013 — Exact-money: add transactions.amount_cents (integer cents).

Why: amounts were stored as REAL dollars and summarised with ``SUM(amount)``.
Float aggregation drifts (0.10 + 0.20 != 0.30 exactly), so weekly summaries
could be a cent or more off. Integer cents sum exactly.

What this does:
  - Adds a nullable ``amount_cents BIGINT`` column.
  - Backfills it losslessly from the existing REAL ``amount``:
    ``cents = round(dollars * 100)``. Casting REAL -> numeric first (Postgres)
    pins the float's value before rounding, so a clean two-decimal dollar value
    maps to exactly its cents with no cent lost or gained.

The legacy ``amount`` REAL column is intentionally KEPT, not dropped:
  - Other writers/readers (the MCP ``transaction_*`` tools in mcp_server.py)
    still use ``amount`` and are out of this change's scope. Dropping it would
    break the voice "add expense" path.
  - The transactions router now writes BOTH columns and reads/aggregates the
    exact ``amount_cents`` (falling back to ``round(amount*100)`` via COALESCE
    for rows written by paths that don't yet populate cents), so totals are
    drift-free while ``amount`` stays valid for legacy consumers.

Dialect-aware so the migration can be exercised forward on a SQLite sample DB in
tests; production is PostgreSQL.
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS amount_cents BIGINT")
        # REAL -> numeric pins the float value before rounding; ::bigint stores
        # the exact integer cents. ROUND is half-away-from-zero in Postgres.
        op.execute(
            "UPDATE transactions "
            "SET amount_cents = ROUND(amount::numeric * 100)::bigint "
            "WHERE amount_cents IS NULL AND amount IS NOT NULL"
        )
    else:
        # SQLite (tests). No ADD COLUMN IF NOT EXISTS; assume a clean table.
        op.execute("ALTER TABLE transactions ADD COLUMN amount_cents BIGINT")
        op.execute(
            "UPDATE transactions "
            "SET amount_cents = CAST(ROUND(amount * 100) AS INTEGER) "
            "WHERE amount_cents IS NULL AND amount IS NOT NULL"
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE transactions DROP COLUMN IF EXISTS amount_cents")
    else:
        op.execute("ALTER TABLE transactions DROP COLUMN amount_cents")
