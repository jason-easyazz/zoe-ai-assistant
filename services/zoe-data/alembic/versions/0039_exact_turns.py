"""0039 - ``exact_turns``: the index of the owner's own verbatim turns (``exact_words.py``).

"What exactly did I say about the dentist?" / "when did I say it?" ask for the owner's WORDS and the DAY. Zoe stored
distilled facts (and the whole turn only beside the rows the extractor happened to extract), so for most ordinary sentences
there was nothing to quote (ZMB J1 / J2 measured 0 of 20 on 2026-10-07). One row per turn the owner said:

  * ``user_id`` + ``turn_id`` - the key; a re-delivered turn is one row (``ON CONFLICT DO NOTHING``);
  * ``said_at``  - epoch seconds, the instant the turn was said (the date that goes with the words);
  * ``text``     - the owner's OWN words only (``own_words``: no pasted mail, no quoted third person), PII-scrubbed, <= 500 chars;
  * ``tokens``   - `` tok1 tok2 `` stemmed content words, so ``LIKE '% tok %'`` is a whole-word lookup (no extension needed);
  * ``source``   - the lane (chat / backfill).

A forget deletes the rows that name the entity (``exact_words.erase_entity``); the audited user delete removes every row of
the user. Schema only, no backfill (``exact_words.backfill_recent`` catches up from ``chat_messages``; set
``ZOE_EXACT_WORDS_BACKFILL_HOURS`` once to reach further back). ``IF NOT EXISTS`` makes a rerun safe, and the statements run
unchanged on PostgreSQL and SQLite. The downgrade drops the table: the words are still in ``chat_messages``.
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS exact_turns (
               user_id TEXT NOT NULL,
               turn_id TEXT NOT NULL,
               said_at DOUBLE PRECISION NOT NULL,
               text TEXT NOT NULL,
               tokens TEXT NOT NULL,
               source TEXT NOT NULL DEFAULT 'chat',
               PRIMARY KEY (user_id, turn_id)
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_exact_turns_user_time ON exact_turns(user_id, said_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_exact_turns_user_time")
    op.execute("DROP TABLE IF EXISTS exact_turns")
