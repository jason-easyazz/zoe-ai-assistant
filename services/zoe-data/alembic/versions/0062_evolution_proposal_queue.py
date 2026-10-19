"""0062 - the single evolution proposal queue (self-evolution Phase 0; ``evolution_queue.py``, design
``docs/architecture/self-evolution-nightly-loop.md`` section 2).

``evolution_proposals`` (0004 + the autonomy columns of 0027) becomes THE queue every proposal writer feeds:

  * new columns ``change_class`` (harness | release | code), ``risk_tier`` (0-3), ``component``, ``evidence_json`` (the gate evidence,
    JSON text), ``lesson_id``, ``source`` (which writer), ``decision`` (empty | approved: a person verdict, apart from the status),
    ``decided_by``, ``decision_note``, ``updated_at``;
  * ``status`` takes the queue vocabulary (proposed, probed, applied, canary, kept, reverted, rejected). Existing rows are REMAPPED,
    not dropped: pending -> proposed, approved -> proposed + decision approved, deployed -> applied, validated -> kept,
    failed -> reverted, rejected stays; any other value becomes rejected with ``decision_note`` = ``legacy status: <value>``. Legacy
    rows get class ``code``, risk tier 3, a component from their type and ``source`` = ``legacy``.
  * ``evolution_proposal_history`` - one row per status move or decision (the audit the score page and "what changed" read).
  * the struggle detector proposals (0057) FOLD IN: every ``struggle_proposals`` row gets its queue row under the SAME id (source
    ``struggle``; router_example / answer_quality / stt_term are class harness, tier 1; intent_gap is class code, tier 3), and the
    status / decision columns leave ``struggle_proposals``, which keeps only the struggle-domain detail (counts, pattern, label,
    follow-up). ``status`` accepted -> proposed + decision approved.
  * PostgreSQL only: CHECK constraints on status, class, tier and decision, and the column default becomes ``proposed``. The legal
    MOVES are enforced in code (``evolution_queue.TRANSITIONS``), the only writer.

The table is created if missing with its 0004 + 0027 shape so this migration stands alone on SQLite (the tests). Rerun-safe: every
step is guarded. The downgrade reverses the remap and the fold (struggle rows get their status back and leave the queue).
"""
from alembic import context, op
import sqlalchemy as sa

revision = "0062"
down_revision = "0059"
branch_labels = None
depends_on = None

STATUSES = ("proposed", "probed", "applied", "canary", "kept", "reverted", "rejected")
CLASSES = ("harness", "release", "code")

NEW_COLUMNS = (
    ("change_class", "TEXT NOT NULL DEFAULT 'code'"),
    ("risk_tier", "INTEGER NOT NULL DEFAULT 3"),
    ("component", "TEXT NOT NULL DEFAULT ''"),
    ("evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("lesson_id", "TEXT"),
    ("source", "TEXT NOT NULL DEFAULT 'legacy'"),
    ("decision", "TEXT NOT NULL DEFAULT ''"),
    ("decided_by", "TEXT NOT NULL DEFAULT ''"),
    ("decision_note", "TEXT NOT NULL DEFAULT ''"),
    ("updated_at", "DOUBLE PRECISION"),
)
STRUGGLE_MOVED = (
    ("status", "TEXT NOT NULL DEFAULT 'proposed'"),
    ("decided_at", "DOUBLE PRECISION"),
    ("decided_by", "TEXT NOT NULL DEFAULT ''"),
    ("decision_note", "TEXT NOT NULL DEFAULT ''"),
)
CHECKS = (
    ("ck_evolution_status", "status IN (" + ", ".join("'" + s + "'" for s in STATUSES) + ")"),
    ("ck_evolution_class", "change_class IN (" + ", ".join("'" + c + "'" for c in CLASSES) + ")"),
    ("ck_evolution_risk_tier", "risk_tier BETWEEN 0 AND 3"),
    ("ck_evolution_decision", "decision IN ('', 'approved')"),
)
# legacy type -> component (anything else keeps its type as the component)
LEGACY_COMPONENT = (
    ("intent_pattern", "intent_router"),
    ("user_frustration", "chat"),
    ("user_issue_report", "chat"),
    ("agent_health", "brain"),
    ("security_vulnerability", "security"),
    ("security_improvement", "security"),
)
# the 0057 struggle status -> (queue status, decision); and back
STRUGGLE_STATUS_SQL = ("CASE WHEN q.status IN ('applied', 'canary', 'kept') THEN 'applied' "
                       "WHEN q.status IN ('rejected', 'reverted') THEN 'rejected' "
                       "WHEN q.decision = 'approved' THEN 'accepted' ELSE 'proposed' END")


def _offline() -> bool:
    """``alembic upgrade --sql``. Outside an alembic environment (the tests drive ``upgrade()`` through ``Operations``) it is online."""
    try:
        return bool(context.is_offline_mode())
    except (NameError, AttributeError):     # no proxy yet, or a torn-down one from an earlier alembic run
        return False


def _pg() -> bool:
    return _offline() or op.get_bind().dialect.name == "postgresql"


def _has_table(name: str) -> bool:
    if _offline():
        return True
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, col: str) -> bool:
    if _offline():
        return True
    try:
        return col in [c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)]
    except Exception:
        return False


def _add_column(table: str, col: str, ddl: str) -> None:
    if _offline():
        op.execute("ALTER TABLE " + table + " ADD COLUMN IF NOT EXISTS " + col + " " + ddl)
    elif not _has_column(table, col):
        op.execute("ALTER TABLE " + table + " ADD COLUMN " + col + " " + ddl)


def _drop_column(table: str, col: str) -> None:
    if _offline():
        op.execute("ALTER TABLE " + table + " DROP COLUMN IF EXISTS " + col)
    elif _has_column(table, col):
        op.execute("ALTER TABLE " + table + " DROP COLUMN " + col)


LEGACY_TO_QUEUE = ("CASE WHEN status IS NULL OR status IN ('pending', 'approved', 'proposed') THEN 'proposed' "
                   "WHEN status = 'deployed' THEN 'applied' WHEN status = 'validated' THEN 'kept' WHEN status = 'failed' THEN 'reverted' "
                   "WHEN status IN ('probed', 'applied', 'canary', 'kept', 'reverted', 'rejected') THEN status ELSE 'rejected' END")


def upgrade() -> None:
    pg = _pg()
    op.execute(
        """CREATE TABLE IF NOT EXISTS evolution_proposals (
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
               next_review_at REAL,
               autonomy_class TEXT NOT NULL DEFAULT 'prepare',
               approval_required TEXT NOT NULL DEFAULT '[]',
               risk TEXT NOT NULL DEFAULT 'medium'
           )"""
    )
    for col, ddl in NEW_COLUMNS:
        _add_column("evolution_proposals", col, ddl)
    op.execute(
        """CREATE TABLE IF NOT EXISTS evolution_proposal_history (
               id TEXT PRIMARY KEY,
               proposal_id TEXT NOT NULL,
               at DOUBLE PRECISION NOT NULL,
               from_status TEXT,
               to_status TEXT,
               decision TEXT NOT NULL DEFAULT '',
               actor TEXT NOT NULL DEFAULT '',
               note TEXT NOT NULL DEFAULT ''
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_evolution_history_proposal ON evolution_proposal_history (proposal_id, at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_evolution_history_at ON evolution_proposal_history (at)")

    # 1. the legacy rows: one history row each recording the status they had, then the remap (a rerun finds every row recorded)
    op.execute(
        "INSERT INTO evolution_proposal_history (id, proposal_id, at, from_status, to_status, decision, actor, note) "
        "SELECT 'm0062-' || id, id, COALESCE(reviewed_at, proposed_at, 0), NULL, " + LEGACY_TO_QUEUE + ", "
        "CASE WHEN status = 'approved' THEN 'approved' ELSE '' END, 'migration:0062', 'legacy status: ' || COALESCE(status, 'null') "
        "FROM evolution_proposals WHERE id NOT IN (SELECT proposal_id FROM evolution_proposal_history)"
    )
    op.execute("UPDATE evolution_proposals SET decision = 'approved' WHERE status = 'approved'")
    op.execute("UPDATE evolution_proposals SET decision_note = 'legacy status: ' || status WHERE status IS NOT NULL AND status NOT IN "
               "('pending', 'approved', 'deployed', 'validated', 'failed', " + ", ".join("'" + s + "'" for s in STATUSES) + ")")
    op.execute("UPDATE evolution_proposals SET status = " + LEGACY_TO_QUEUE)
    for legacy_type, component in LEGACY_COMPONENT:
        op.execute("UPDATE evolution_proposals SET component = '" + component + "' WHERE component = '' AND type = '" + legacy_type + "'")
    op.execute("UPDATE evolution_proposals SET component = SUBSTR(type, 1, 64) WHERE component = ''")
    op.execute("UPDATE evolution_proposals SET updated_at = COALESCE(reviewed_at, deployed_at, proposed_at) WHERE updated_at IS NULL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_evolution_queue_class_status ON evolution_proposals (change_class, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_evolution_queue_source ON evolution_proposals (source)")

    # 2. the struggle proposals fold in (same id), then their status / decision columns leave struggle_proposals
    if _has_table("struggle_proposals") and _has_column("struggle_proposals", "status"):
        op.execute(
            "INSERT INTO evolution_proposals (id, type, title, description, status, proposed_at, reviewed_at, deployed_at, change_class, "
            "risk_tier, component, evidence_json, source, decision, decided_by, decision_note, updated_at) "
            "SELECT sp.id, 'struggle_' || sp.kind, SUBSTR(sp.summary, 1, 300), sp.summary, "
            "CASE sp.status WHEN 'applied' THEN 'applied' WHEN 'rejected' THEN 'rejected' ELSE 'proposed' END, "
            "sp.created_at, sp.decided_at, CASE WHEN sp.status = 'applied' THEN sp.updated_at END, "
            "CASE sp.kind WHEN 'intent_gap' THEN 'code' ELSE 'harness' END, CASE sp.kind WHEN 'intent_gap' THEN 3 ELSE 1 END, "
            "CASE sp.kind WHEN 'router_example' THEN 'router' WHEN 'intent_gap' THEN 'intent_router' WHEN 'answer_quality' THEN 'brain' "
            "WHEN 'stt_term' THEN 'stt' ELSE SUBSTR(sp.kind, 1, 64) END, '{}', 'struggle', "
            "CASE WHEN sp.status IN ('accepted', 'applied') THEN 'approved' ELSE '' END, sp.decided_by, sp.decision_note, sp.updated_at "
            "FROM struggle_proposals sp WHERE sp.id NOT IN (SELECT id FROM evolution_proposals)"
        )
        op.execute(
            "INSERT INTO evolution_proposal_history (id, proposal_id, at, from_status, to_status, decision, actor, note) "
            "SELECT 'm0062-' || sp.id, sp.id, COALESCE(sp.decided_at, sp.created_at, 0), NULL, "
            "CASE sp.status WHEN 'applied' THEN 'applied' WHEN 'rejected' THEN 'rejected' ELSE 'proposed' END, "
            "CASE WHEN sp.status IN ('accepted', 'applied') THEN 'approved' ELSE '' END, 'migration:0062', "
            "'struggle_proposals status: ' || sp.status FROM struggle_proposals sp "
            "WHERE sp.id NOT IN (SELECT proposal_id FROM evolution_proposal_history)"
        )
        op.execute("DROP INDEX IF EXISTS idx_struggle_proposals_status")
        for col, _ddl in STRUGGLE_MOVED:
            _drop_column("struggle_proposals", col)
    if _has_table("struggle_proposals"):
        op.execute("CREATE INDEX IF NOT EXISTS idx_struggle_proposals_kind ON struggle_proposals (kind)")

    # 3. PostgreSQL: the value sets as CHECK constraints, and the new default
    if pg:
        op.execute("ALTER TABLE evolution_proposals ALTER COLUMN status SET DEFAULT 'proposed'")
        for name, expr in CHECKS:
            op.execute("ALTER TABLE evolution_proposals DROP CONSTRAINT IF EXISTS " + name)
            op.execute("ALTER TABLE evolution_proposals ADD CONSTRAINT " + name + " CHECK (" + expr + ")")


def downgrade() -> None:
    pg = _pg()
    if pg:
        for name, _expr in CHECKS:
            op.execute("ALTER TABLE evolution_proposals DROP CONSTRAINT IF EXISTS " + name)
        op.execute("ALTER TABLE evolution_proposals ALTER COLUMN status SET DEFAULT 'pending'")
    if _has_table("struggle_proposals") and not (not _offline() and _has_column("struggle_proposals", "status")):
        for col, ddl in STRUGGLE_MOVED:
            _add_column("struggle_proposals", col, ddl)
        op.execute(
            "UPDATE struggle_proposals SET "
            "status = COALESCE((SELECT " + STRUGGLE_STATUS_SQL + " FROM evolution_proposals q WHERE q.id = struggle_proposals.id), 'proposed'), "
            "decided_at = (SELECT q.reviewed_at FROM evolution_proposals q WHERE q.id = struggle_proposals.id), "
            "decided_by = COALESCE((SELECT q.decided_by FROM evolution_proposals q WHERE q.id = struggle_proposals.id), ''), "
            "decision_note = COALESCE((SELECT q.decision_note FROM evolution_proposals q WHERE q.id = struggle_proposals.id), '')"
        )
        op.execute("DROP INDEX IF EXISTS idx_struggle_proposals_kind")
        op.execute("CREATE INDEX IF NOT EXISTS idx_struggle_proposals_status ON struggle_proposals (status, kind)")
    if _has_column("evolution_proposals", "source"):
        op.execute("DELETE FROM evolution_proposals WHERE source = 'struggle'")
        op.execute("UPDATE evolution_proposals SET status = CASE "
                   "WHEN status IN ('proposed', 'probed') AND decision = 'approved' THEN 'approved' "
                   "WHEN status IN ('proposed', 'probed') THEN 'pending' WHEN status IN ('applied', 'canary') THEN 'deployed' "
                   "WHEN status = 'kept' THEN 'validated' WHEN status = 'reverted' THEN 'failed' ELSE status END")
    op.execute("DROP INDEX IF EXISTS idx_evolution_queue_class_status")
    op.execute("DROP INDEX IF EXISTS idx_evolution_queue_source")
    op.execute("DROP INDEX IF EXISTS idx_evolution_history_at")
    op.execute("DROP INDEX IF EXISTS idx_evolution_history_proposal")
    op.execute("DROP TABLE IF EXISTS evolution_proposal_history")
    for col, _ddl in reversed(NEW_COLUMNS):
        _drop_column("evolution_proposals", col)
