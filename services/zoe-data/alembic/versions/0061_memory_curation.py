"""0061 - caregiver curation (memories-space P7, ``memory_curation.py``; owner decisions D7 / D9 / D10, 2026-10-19).

Two tables. NO memory text lives in either: the curated rows themselves are ordinary rows of the member in the memory store,
stamped ``origin="curator"`` and the ``curated`` authority class (``memory_authority``).

  * ``memory_curation_optins`` - one row per (member, category) that is curatable beyond the defaults:
      - a CHILD's sensitive category (health / medication / money; D10: off by default) turned on by a curator;
      - an ADULT's category (any; D9: an adult's own word wins unless they opt a category in) turned on by THAT adult.
    ``revoked_at`` set = off again (kept, never deleted, so who turned it on stays on record). One row per pair.
  * ``memory_curation_audit`` - every curation action (add / edit / forget / opt-in / opt-out / held-note decision), written
    BEFORE the action (fail-closed: no audit row, no change) and again when it finished. Ids, a category and a short action word
    only - never memory text.

Conventions as 0045 / 0056: TEXT ids (account ids, no FK to a users table), UTC ISO timestamps, ``IF NOT EXISTS``. The SQLite unit
lane runs ``statements``. The next free number after the pending 0058 (#2141), 0059 (#2095) and 0060 (#2146): ``down_revision`` is
re-pointed at landing.
"""

from __future__ import annotations

from alembic import op

revision = "0061"
down_revision = "0058"
branch_labels = None
depends_on = None


def statements(dialect: str) -> list[str]:
    """The CREATE statements. Pure: the unit tests run them on SQLite too."""
    return [
        """CREATE TABLE IF NOT EXISTS memory_curation_optins (
               member_user_id TEXT NOT NULL,
               category TEXT NOT NULL,
               opted_in_by TEXT NOT NULL,
               opted_in_at TEXT NOT NULL,
               revoked_at TEXT,
               revoked_by TEXT,
               PRIMARY KEY (member_user_id, category)
           )""",
        """CREATE TABLE IF NOT EXISTS memory_curation_audit (
               id TEXT PRIMARY KEY,
               at TEXT NOT NULL,
               actor_user_id TEXT NOT NULL,
               member_user_id TEXT NOT NULL,
               action TEXT NOT NULL,
               category TEXT,
               row_ref TEXT,
               phase TEXT NOT NULL,
               ok INTEGER
           )""",
        "CREATE INDEX IF NOT EXISTS idx_memory_curation_audit_member ON memory_curation_audit (member_user_id, at)",
    ]


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    for sql in statements(dialect):
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_curation_audit")
    op.execute("DROP TABLE IF EXISTS memory_curation_optins")
