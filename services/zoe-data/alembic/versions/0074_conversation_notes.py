"""0074 - ``conversation_notes``: short notes about past conversations (``conversation_notes.py``; flag-dark).

One row per conversation (an episode of one person's turns): who it was with, what they talked about, how they seemed, what is left
open, and the POINTERS (``turn_ids``: chat message ids for a member, ``named_guest_turns`` ids for a guest - ids only, never text) to the
turns it was made from, so a forget that names something only a source turn said still reaches the note. ``user_id`` is the member layer the note lives in; ``people_id`` is ``''`` for the member's own conversations and the guest's
person id for a named guest's (guest scope only: read solely on a turn matched to THAT guest by voice). ``status`` is ``active``
(served), ``shadow`` (written by the shadow mode, never served) or ``pending`` (a pendant conversation the owner has not approved:
it lives in ``omi_day_candidates`` until then, so this table never holds one). Erased by the member erase, "forget <name>", "forget
what I told you about <topic>", "forget that" and a guest's erase. Runbook ``docs/knowledge/conversation-notes.md``. Idempotent;
the downgrade drops the table.

CHAIN: on 0073 (``0073_panel_voice_clusters.py``, the same stacked PR line).
"""

from __future__ import annotations

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None

NOTES = """CREATE TABLE IF NOT EXISTS conversation_notes (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               people_id TEXT NOT NULL DEFAULT '',
               episode_key TEXT NOT NULL,
               source TEXT NOT NULL DEFAULT 'chat',
               status TEXT NOT NULL DEFAULT 'active',
               with_name TEXT NOT NULL DEFAULT '',
               started_at TEXT NOT NULL,
               ended_at TEXT NOT NULL,
               turn_count INTEGER NOT NULL DEFAULT 0,
               turn_ids TEXT NOT NULL DEFAULT '[]',
               summary TEXT NOT NULL,
               topics TEXT NOT NULL DEFAULT '[]',
               mood TEXT NOT NULL DEFAULT '',
               open_threads TEXT NOT NULL DEFAULT '[]',
               follow_up TEXT NOT NULL DEFAULT '',
               sensitive INTEGER NOT NULL DEFAULT 0,
               served_count INTEGER NOT NULL DEFAULT 0,
               last_served_at TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL,
               UNIQUE (user_id, people_id, episode_key)
           )"""
NOTES_IDX = "CREATE INDEX IF NOT EXISTS idx_conversation_notes_layer ON conversation_notes (user_id, people_id, ended_at)"


def upgrade() -> None:
    op.execute(NOTES)
    op.execute(NOTES_IDX)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conversation_notes")
