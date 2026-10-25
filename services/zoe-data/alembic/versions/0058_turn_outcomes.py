"""0058 - ``turn_outcomes``: the per-turn FAILURE CAPTURE of the struggle detector (``turn_outcomes.py``, flag ``ZOE_STRUGGLE_OUTCOMES``).

One row per finished voice / chat / Telegram turn the detector observed:

  * how it was ROUTED (``route_domain`` + ``route_conf`` + ``route_source``: the two-stage head, the similarity router, or a failed
    head; ``head_top`` = the head's own top pick even when it gated to chat) and what EXECUTED it (``handler`` = ``tier:<name>`` /
    ``intent:<name>`` / ``skybridge:<domain>.<action>``, ``tools`` = the brain's tool NAMES);
  * the ``outcome`` class (did | cant_do | fell_through | brain | error | interrupted), ``path_served`` and ``latency_ms``;
  * ``traffic`` = household | harness | probe | ci (``traffic_class``): a harness row is text-free and can never become a label or an example;
  * when the turn FAILED (``failed`` = 1, ``signals``): the failed turn's words (``summary``, member + household rows only, the
    struggle detector's keep-words rules: PII-scrubbed display form, <= 160 chars, never a guest's / off-the-record / a forgotten
    entity's), a proposed correct route (``proposed_label`` + ``label_source`` followup | router) and the OWNER's decision
    (``label_status`` proposed | confirmed | rejected, ``label``, ``decided_by``, ``decided_at``). Only a confirmed row may feed the
    router (``struggle_proposals.queued_router_examples``).

Words live only on failed rows. Retention, forget and user delete reach the table through ``struggle_detector`` (the same sweep,
``erase_entity`` and ``delete_user`` that reach ``struggle_events``). ``IF NOT EXISTS`` makes a rerun safe and the statements run
unchanged on PostgreSQL and SQLite.

REVISION CHAIN NOTE: ``down_revision`` is ``0059`` (``0059_panel_client_certs``, #2095), the head on main when #2141 landed after #2146 (``0060``) and #2095.
Whichever migration lands next re-chains its ``down_revision`` onto this one (the single-head tests go red until it does).
"""

from alembic import op

revision = "0058"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS turn_outcomes (
               id TEXT PRIMARY KEY,
               ts DOUBLE PRECISION NOT NULL,
               user_id TEXT NOT NULL,
               is_guest INTEGER NOT NULL DEFAULT 0,
               traffic TEXT NOT NULL DEFAULT 'household',
               channel TEXT NOT NULL DEFAULT '',
               panel_id TEXT NOT NULL DEFAULT '',
               session_id TEXT NOT NULL DEFAULT '',
               turn_id TEXT NOT NULL DEFAULT '',
               message_id TEXT NOT NULL DEFAULT '',
               route_domain TEXT NOT NULL DEFAULT '',
               route_conf DOUBLE PRECISION,
               route_source TEXT NOT NULL DEFAULT '',
               head_top TEXT NOT NULL DEFAULT '',
               handler TEXT NOT NULL DEFAULT '',
               tools TEXT NOT NULL DEFAULT '',
               outcome TEXT NOT NULL DEFAULT '',
               path_served TEXT NOT NULL DEFAULT '',
               latency_ms INTEGER,
               failed INTEGER NOT NULL DEFAULT 0,
               signals TEXT NOT NULL DEFAULT '',
               proposed_label TEXT NOT NULL DEFAULT '',
               label_source TEXT NOT NULL DEFAULT '',
               label_status TEXT NOT NULL DEFAULT '',
               label TEXT NOT NULL DEFAULT '',
               decided_by TEXT NOT NULL DEFAULT '',
               decided_at DOUBLE PRECISION,
               summary TEXT NOT NULL DEFAULT '',
               mode TEXT NOT NULL DEFAULT ''
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_turn_outcomes_user_ts ON turn_outcomes(user_id, ts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_turn_outcomes_ts ON turn_outcomes(ts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_turn_outcomes_turn ON turn_outcomes(turn_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_turn_outcomes_label ON turn_outcomes(label_status, failed)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_turn_outcomes_label")
    op.execute("DROP INDEX IF EXISTS idx_turn_outcomes_turn")
    op.execute("DROP INDEX IF EXISTS idx_turn_outcomes_ts")
    op.execute("DROP INDEX IF EXISTS idx_turn_outcomes_user_ts")
    op.execute("DROP TABLE IF EXISTS turn_outcomes")
