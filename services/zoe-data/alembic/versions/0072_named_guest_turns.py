"""0072 - ``named_guest_turns``: a named guest's own conversation space (``named_guest_memory.py``; flag-dark).

What Zoe and ONE named guest said to each other at the panel, keyed by the member layer the guest lives in (``user_id`` = the member
who enrolled them, ``people_id`` = that member's person). Read ONLY for a turn the server matched to that guest by voice; erased by
"forget <name>" (``omi_voices.purge_people``), "forget <name>'s voice", the owner's erase and the member erase
(``contacts_store.forget_user``). Runbook ``docs/knowledge/named-guests.md``. Words, not biometrics. Idempotent; the downgrade drops
the table.

CHAIN: on 0071 (``0071_named_guests.py``, the same stacked PR line).
"""

from __future__ import annotations

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None

TURNS = """CREATE TABLE IF NOT EXISTS named_guest_turns (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               people_id TEXT NOT NULL,
               session_id TEXT NOT NULL DEFAULT '',
               user_text TEXT NOT NULL,
               reply TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL
           )"""
TURNS_IDX = "CREATE INDEX IF NOT EXISTS idx_named_guest_turns_guest ON named_guest_turns (user_id, people_id, created_at)"


def upgrade() -> None:
    op.execute(TURNS)
    op.execute(TURNS_IDX)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS named_guest_turns")
