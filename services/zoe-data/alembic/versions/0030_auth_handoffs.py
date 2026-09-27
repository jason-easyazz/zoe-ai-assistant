"""0030 — ``auth_handoffs``: the app-connection handoff record (B7.5).

One row per "connect an app/account" attempt started on a panel
(``auth_handoff.start``): which member, which panel, which provider flow, and
its live status (pending → awaiting_phone → completing → done | error). ``ref``
is the provider phone token's NONCE — never the token — so a phone endpoint
that only holds the token can report progress. The phone URL itself (it carries
the token) is never stored.

Create-if-missing and idempotent (the 0029 lesson): ``CREATE TABLE IF NOT
EXISTS`` and ``CREATE INDEX IF NOT EXISTS``, so a re-run or a database where the
table already exists is a no-op. 0030 owns the table, so its downgrade drops it.
Schema comes from Alembic, never from request-time DDL.
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_TABLE_DDL = """
        CREATE TABLE IF NOT EXISTS auth_handoffs (
            id          TEXT PRIMARY KEY,
            kind        TEXT NOT NULL,
            provider    TEXT NOT NULL DEFAULT '',
            ref         TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            panel_id    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            detail      TEXT NOT NULL DEFAULT '',
            reason      TEXT NOT NULL DEFAULT '',
            created_at  DOUBLE PRECISION NOT NULL,
            updated_at  DOUBLE PRECISION NOT NULL,
            expires_at  DOUBLE PRECISION NOT NULL
        )
    """
_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_auth_handoffs_ref ON auth_handoffs (kind, ref)",
    "CREATE INDEX IF NOT EXISTS idx_auth_handoffs_expires ON auth_handoffs (expires_at)",
)


def upgrade() -> None:
    op.execute(_TABLE_DDL)
    for stmt in _INDEX_DDL:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth_handoffs")
