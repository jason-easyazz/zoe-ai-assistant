"""0067 - ``now_cards``: the per-member "now card" (``now_card.py``, flag ``ZOE_MEMORY_NOW_CARD``).

One row per member, built by the night pass (dreaming, then the night-mind CLI) and read BY KEY on a household turn. ``card_json``
holds the lines with their source pointers (``memory:<id>``, ``night_observations:<id>``, ``person:<id>``) so a serve can drop a line
whose source has gone; ``card_text`` / ``version`` are the as-built text and its content hash; ``built_on`` is the Zoe-local day the
card is for. Derived data only: no FK to ``users`` (the per-table ``user_id`` sweeps delete it in any order), the forget paths reach
it (``now_card.forget_user`` / ``now_card.erase_entity``) and the next night rebuilds it. Idempotent; the downgrade drops the table.
"""

from alembic import op

revision = "0067"
down_revision = "0066"  # 0066 = memory_curation (#2158), which lands first
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS now_cards (
    user_id TEXT PRIMARY KEY,
    card_json TEXT NOT NULL,
    card_text TEXT NOT NULL,
    version TEXT NOT NULL,
    built_on TEXT NOT NULL,
    built_at TEXT NOT NULL
)
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS now_cards")
