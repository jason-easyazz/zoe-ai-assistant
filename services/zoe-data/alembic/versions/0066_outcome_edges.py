"""0066 - proactive_outcome_edges: the outcome edge of the proactive loop (flag-dark).

``proactive/outcome_edges.py`` (``ZOE_OUTCOME_EDGES`` = off | on, default off) writes ONE edge per
(member, item, local day, touch point): the item is the selector's ``source_ref`` (an opaque id such
as ``open_loops:12`` - never text), the touch point is how it reached the person (``raise`` | ``pull``)
or the first-presence re-score (``presence``). The ``outcome`` is what the person then did, derived
from events Zoe already records, with no model: ``acted`` | ``dismissed`` | ``ignored`` | ``unknown``;
``outcome_source`` says which event decided it (``tap`` | ``loop_resolved`` | ``ledger_sweep`` |
``pull_ask`` | ``expiry``) and a stronger source overwrites a weaker one, never the reverse.

``rescore_rank`` / ``score_before`` / ``score_after`` are set on ``presence`` edges only (the deterministic
re-score of today's due items at the member's first presence of the day); the ``day`` marker row
(``item_id = '*'``) makes the re-score once per member per local day. No FK to ``users`` (the
per-table ``user_id`` teardown deletes in any order); ``MemoryService.delete_user`` erases the rows
(``outcome_edges.erase_user``). Timestamps are TEXT UTC like ``proactive_candidates``;
``IF NOT EXISTS`` makes a rerun safe on PostgreSQL and SQLite. The downgrade drops the table.
"""

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS proactive_outcome_edges (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               item_id TEXT NOT NULL,
               kind TEXT NOT NULL DEFAULT '',
               local_day TEXT NOT NULL,
               touch_point TEXT NOT NULL,
               outcome TEXT,
               outcome_at TEXT,
               outcome_source TEXT,
               rescore_rank INTEGER,
               score_before REAL,
               score_after REAL,
               created_at TEXT NOT NULL,
               UNIQUE (user_id, item_id, local_day, touch_point)
           )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_proactive_outcome_edges_open "
        "ON proactive_outcome_edges (outcome, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_proactive_outcome_edges_open")
    op.execute("DROP TABLE IF EXISTS proactive_outcome_edges")
