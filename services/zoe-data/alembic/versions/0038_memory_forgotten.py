"""0038 - ``memory_forgotten``: the durable forget ledger (``memory_forgotten.py``, audit P2.2).

"Forget Dana" used to be a 300 s in-process tombstone, so the nightly digest (which re-reads the day's
``chat_messages``) could resurrect the name hours later. This table is what makes a forget last.

It MUST NOT RETAIN WHAT IT WAS ASKED TO FORGET, so it has no text column at all:

  * ``key_hash``     - HMAC-SHA256(per-user salt, normalised entity key), hex. The salt is derived from the
                       ``ZOE_FORGET_LEDGER_SALT`` secret and is never stored here: a copy of this table
                       alone can answer nothing, and with the secret it can only confirm a guess, not list.
  * ``forgotten_at`` / ``shield_until`` - UTC text (``%Y-%m-%dT%H:%M:%SZ``) like the other proactive tables;
                       the entry shields until ``shield_until`` (default 365 days).
  * ``scope`` / ``actor`` - what kind of forget it was and who asked (an id, never a name).

``PRIMARY KEY (user_id, key_hash)`` makes a repeated forget an upsert. No FK to ``users``, so the per-table
``user_id`` sweeps delete it in any order. Schema only, no backfill. ``IF NOT EXISTS`` makes a rerun safe, and
the statements run unchanged on PostgreSQL and SQLite. The downgrade drops the table: dropping it loses the
shield (forgotten names become minable again), which is the only effect.
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS memory_forgotten (
               user_id TEXT NOT NULL,
               key_hash TEXT NOT NULL,
               scope TEXT NOT NULL DEFAULT 'entity',
               actor TEXT NOT NULL DEFAULT '',
               forgotten_at TEXT NOT NULL,
               shield_until TEXT NOT NULL,
               PRIMARY KEY (user_id, key_hash)
           )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_forgotten")
