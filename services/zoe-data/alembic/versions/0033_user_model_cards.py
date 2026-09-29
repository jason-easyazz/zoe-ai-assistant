"""0033 — ``user_model_cards``: the stored user-model card (``user_model_card.py``).

This is the structured card of current facts that ``GET /api/memories/user-model`` serves
to the Flue sidecar, flag-dark behind ``ZOE_USER_MODEL_BLOCK``. One row per user:
``card_json`` holds the items with their source memory ids, so a serve can drop any fact
that has since been superseded. ``card_text`` and ``version`` are the as-built text and
its content hash. The row is rebuilt nightly, so the served block stays byte-identical
between builds.

It is a separate table, not columns on ``user_portraits``. A card exists without a
portrait (fewer than 5 memories, or before the first Sunday), and ``portrait_text`` is
``NOT NULL`` and is read by five consumers. There is no FK to ``users``, so the per-table
``user_id`` sweeps (``samantha_bar.db_teardown``) delete it in any order.

Idempotent (``IF NOT EXISTS``). The downgrade drops the table: it holds only derived data,
and the next dreaming pass rebuilds it.
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS user_model_cards (
    user_id TEXT PRIMARY KEY,
    card_json TEXT NOT NULL,
    card_text TEXT NOT NULL,
    version TEXT NOT NULL,
    built_at TEXT NOT NULL,
    source_count INTEGER NOT NULL DEFAULT 0
)
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_model_cards")
