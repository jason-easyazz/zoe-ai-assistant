"""0083 - ``named_guest_disclosures``: when a named guest was TOLD what Zoe keeps about them (``named_guest_disclosure.py``; flag-dark).

One row per guest (``user_id`` = the member layer the guest lives in, ``people_id`` = that member's person) and the time Zoe's reply
told them: that she knows their voice, what she keeps, and how it is forgotten. No row = not told yet. Ids and a time only: no words,
no biometrics. Erased with the guest (``named_guest_memory.erase_people``, so a guest who is re-taught is told again) and with the
member (``contacts_store.forget_user``). Runbook ``docs/knowledge/named-guests.md``. Idempotent; the downgrade drops the table.

NUMBER: 0083 is the next number nobody holds (0080-0082 are claimed by open PRs). CHAIN: on 0079, the head of ``main`` when this was
written; re-point ``down_revision`` at whichever of 0080-0082 is the head when this lands.
"""

from __future__ import annotations

from alembic import op

revision = "0083"
down_revision = "0079"
branch_labels = None
depends_on = None

TOLD = """CREATE TABLE IF NOT EXISTS named_guest_disclosures (
              user_id TEXT NOT NULL,
              people_id TEXT NOT NULL,
              told_at TEXT NOT NULL,
              PRIMARY KEY (user_id, people_id)
          )"""


def upgrade() -> None:
    op.execute(TOLD)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS named_guest_disclosures")
