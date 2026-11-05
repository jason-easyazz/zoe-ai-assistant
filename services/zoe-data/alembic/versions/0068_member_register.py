"""0068 - ``member_register`` (how Zoe speaks with THIS person; flag-dark ``ZOE_MEMBER_REGISTER``).

One row per (member, slot). ``slot`` is the register key for a single-valued setting (``length``, ``formality``, ``humour``,
``pace``) or ``topic:<normalised>`` for a liked topic. ``provenance`` is ``stated`` (the member's own words, or an owner edit),
``confirmed`` (an observed signal the member said yes to) or ``inferred`` (a proposal; never rendered into a prompt).
``status`` is ``active`` or ``forgotten``: a forgotten row is a TOMBSTONE with the value blanked, so a later inference cannot
re-add what the member took away (only a new statement by the member can).

Values are short enum words or a sanitised topic (``member_register.validate``); no free text, nothing affective.
No FK (the per-table ``user_id`` sweeps delete in any order). Idempotent. The downgrade drops the table: it is a preference
cache, rebuilt by the member saying it again.
"""

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS member_register (
    user_id TEXT NOT NULL,
    slot TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL DEFAULT 'inferred',
    status TEXT NOT NULL DEFAULT 'active',
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, slot)
)
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS member_register")
