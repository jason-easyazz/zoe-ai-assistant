"""0027 — evolution proposal autonomy contract.

Persist the autonomy contract the execution gate reads, so an approved proposal
can (or cannot) be auto-implemented by Zoe. Three columns on evolution_proposals:

  * autonomy_class     — only 'execute'/'promote' are auto-executable; default
                         'prepare' (review-only) is FAIL-CLOSED.
  * approval_required   — JSON array of approval classes the gate demands.
  * risk                — low|medium|high (advisory).

Policy (see evolution_autonomy.py — the app-side source of truth; keep in sync):
a DELIBERATE OPERATOR OVERRIDE of the framework's review-only default grants
'execute' to the narrow, reversible intent-fix types (intent_pattern,
user_frustration — the class proven by PR #1555), keeps security types
review-only + security_review-gated, and leaves everything else review-only.

Dialect-agnostic (Postgres prod + SQLite tests): plain TEXT columns, guarded
ADD COLUMN, plain UPDATE backfills — no jsonb type, no trigger.
"""
from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_DEFAULT_APPROVAL = '["user_or_admin_for_privileged_execution"]'
_SECURITY_APPROVAL = '["security_review","user_or_admin_for_privileged_execution"]'
_EXECUTE_TYPES = ("intent_pattern", "user_frustration")
_SECURITY_TYPES = ("security_vulnerability", "security_improvement")


def _has_column(bind, table: str, col: str) -> bool:
    insp = sa.inspect(bind)
    try:
        return col in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "evolution_proposals", "autonomy_class"):
        op.add_column("evolution_proposals",
                      sa.Column("autonomy_class", sa.Text(), nullable=False, server_default="prepare"))
    if not _has_column(bind, "evolution_proposals", "approval_required"):
        op.add_column("evolution_proposals",
                      sa.Column("approval_required", sa.Text(), nullable=False, server_default=_DEFAULT_APPROVAL))
    if not _has_column(bind, "evolution_proposals", "risk"):
        op.add_column("evolution_proposals",
                      sa.Column("risk", sa.Text(), nullable=False, server_default="medium"))

    # Backfill existing rows to the policy (new rows are stamped in Python at
    # creation; ADD COLUMN DEFAULT already set every existing row to review-only).
    # Literal IN clauses over constant type names — dialect-agnostic, no bindparam
    # array typing, no injection surface (the type list is fixed above).
    _exec_in = ",".join(f"'{t}'" for t in _EXECUTE_TYPES)
    _sec_in = ",".join(f"'{t}'" for t in _SECURITY_TYPES)
    op.execute(
        "UPDATE evolution_proposals SET autonomy_class='execute', risk='low', "
        f"approval_required='{_DEFAULT_APPROVAL}' WHERE type IN ({_exec_in})"
    )
    op.execute(
        "UPDATE evolution_proposals SET risk='high', "
        f"approval_required='{_SECURITY_APPROVAL}' WHERE type IN ({_sec_in})"
    )

    # Stamp the contract by type in the DB, so EVERY creator (evolution_notice,
    # mcp_server, any future path) gets the right autonomy_class without touching
    # each INSERT — and so an INSERT never has to list the new columns (which
    # would fail in a deploy window before this migration runs). Mirrors
    # evolution_autonomy.contract_for_type; keep in sync. Postgres-only (the
    # migration chain is Postgres-targeted; tests exercise the policy via the
    # Python module directly).
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE OR REPLACE FUNCTION evolution_stamp_autonomy() RETURNS trigger AS $$\n"
            "BEGIN\n"
            f"  IF NEW.type IN ({_exec_in}) THEN\n"
            "    NEW.autonomy_class := 'execute'; NEW.risk := 'low';\n"
            f"    NEW.approval_required := '{_DEFAULT_APPROVAL}';\n"
            f"  ELSIF NEW.type IN ({_sec_in}) THEN\n"
            "    NEW.autonomy_class := 'prepare'; NEW.risk := 'high';\n"
            f"    NEW.approval_required := '{_SECURITY_APPROVAL}';\n"
            "  ELSE\n"
            "    IF NEW.autonomy_class IS NULL THEN NEW.autonomy_class := 'prepare'; END IF;\n"
            f"    IF NEW.approval_required IS NULL THEN NEW.approval_required := '{_DEFAULT_APPROVAL}'; END IF;\n"
            "    IF NEW.risk IS NULL THEN NEW.risk := 'medium'; END IF;\n"
            "  END IF;\n"
            "  RETURN NEW;\n"
            "END;\n"
            "$$ LANGUAGE plpgsql;"
        )
        op.execute("DROP TRIGGER IF EXISTS trg_evolution_stamp_autonomy ON evolution_proposals")
        op.execute(
            "CREATE TRIGGER trg_evolution_stamp_autonomy BEFORE INSERT ON evolution_proposals "
            "FOR EACH ROW EXECUTE FUNCTION evolution_stamp_autonomy()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_evolution_stamp_autonomy ON evolution_proposals")
        op.execute("DROP FUNCTION IF EXISTS evolution_stamp_autonomy()")
    for col in ("risk", "approval_required", "autonomy_class"):
        if _has_column(bind, "evolution_proposals", col):
            op.drop_column("evolution_proposals", col)
