"""0035 — proactive_deliveries: the delivery ledger (pull-not-push inbox, PR 1).

``proactive/ledger.py`` (flag ``ZOE_PROACTIVE_LEDGER``, default OFF) writes ONE open row per
item a conversation actually carried to a member: a ``[RAISE …]`` that settled with reply
text, or a ``[Today]`` brief line the selector marked surfaced. Research record
docs/research/pull-not-push-inbox-2026-10-04.md §3.1 / §3.5 / §5 (PR 1).

``proactive_candidates`` says a block went out with a reply, not whether the reply voiced
it, and not what the person did next (record §2.5). This table is that evidence:

  * ``idem_key`` UNIQUE — (member, session, kind, source_ref, delivered_by). A retried or
    double settle of the same delivery inserts nothing; one item, one row.
  * ``voiced`` — set when the sweep closes the row, from the reply chat persisted for that
    delivery: 1 the reply carried one of the item's anchor words, 0 it did not (the
    injected-but-dropped case, outcome ``undelivered``), NULL unverifiable (no anchors, or
    the row closed ``unknown`` before any reply was found).
  * ``outcome`` NULL = surfaced, awaiting the sweep; else ``accepted`` | ``ignored`` |
    ``undelivered`` | ``unknown`` (closed). ``expires_at`` bounds the wait: a row the sweep
    could not judge by then closes ``unknown`` — it never strands.
  * ``cue_words`` are the item's concrete anchors (what ``proactive_candidates`` already
    keeps); no reply text and no utterance is stored.

There is no FK to ``users`` (the per-table ``user_id`` sweeps in the Samantha-bar teardown
delete it in any order) and no FK to ``proactive_candidates`` (a candidate row is deleted
once out of cooldown; a ledger row outlives it). Timestamps are TEXT UTC
(``%Y-%m-%dT%H:%M:%SZ``) like ``proactive_candidates``. ``IF NOT EXISTS`` works on
PostgreSQL and SQLite, so reruns are safe (0025/0030/0033 convention). The downgrade drops
the table: a ledger of behaviour is not worth keeping past the schema that defines it.
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS proactive_deliveries (
               id TEXT PRIMARY KEY,
               idem_key TEXT NOT NULL UNIQUE,
               user_id TEXT NOT NULL,
               candidate_id TEXT,
               kind TEXT NOT NULL,
               source_ref TEXT NOT NULL,
               shape TEXT NOT NULL,
               delivered_by TEXT NOT NULL DEFAULT 'turn',
               session_id TEXT NOT NULL,
               cue_words TEXT NOT NULL DEFAULT '',
               voiced INTEGER,
               surfaced_at TEXT NOT NULL,
               expires_at TEXT NOT NULL,
               outcome TEXT,
               outcome_at TEXT,
               created_at TEXT NOT NULL
           )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_proactive_deliveries_open "
        "ON proactive_deliveries (outcome, surfaced_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_proactive_deliveries_user "
        "ON proactive_deliveries (user_id, surfaced_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_proactive_deliveries_user")
    op.execute("DROP INDEX IF EXISTS idx_proactive_deliveries_open")
    op.execute("DROP TABLE IF EXISTS proactive_deliveries")
