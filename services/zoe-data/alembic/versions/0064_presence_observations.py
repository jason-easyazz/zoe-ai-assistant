"""0064 - ``presence_observations``: OBSERVED presence from Home Assistant (``presence/ha_presence.py``, flag ``ZOE_HA_PRESENCE``).

One row per debounced state change of a ``person`` / ``device_tracker`` entity. ``entity_key`` is sha256(entity_id), never the raw
id; ``state`` is home | away | unknown; ``raw`` stays NULL unless a later phase decides otherwise. Observed rows only - inferred
presence will live in its own table and never merge with these. No member column: the rows carry no words and no member id.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "presence_observations",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("entity_key", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("ttl_s", sa.Integer(), nullable=False),
        sa.Column("raw", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
        sa.CheckConstraint("state IN ('home','away','unknown')", name="ck_presence_obs_state"),
    )
    op.create_index("idx_presence_obs_key_time", "presence_observations", ["entity_key", "observed_at"])


def downgrade() -> None:
    op.drop_index("idx_presence_obs_key_time", table_name="presence_observations")
    op.drop_table("presence_observations")
