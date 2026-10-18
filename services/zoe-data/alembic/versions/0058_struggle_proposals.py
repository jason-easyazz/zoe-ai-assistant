"""0058 - ``struggle_proposals`` + ``struggle_proposal_events``: Phase 2 of the struggle detector (``struggle_proposals.py``).

The night pass clusters the last days of ``struggle_events`` (0050) into PROPOSALS - a recurring problem with a candidate fix class:

  * ``struggle_proposals`` - one row per recurring problem. ``fingerprint`` (UNIQUE) is a hash of ``kind`` + a TEXT-FREE key
    (router domain + question shape; a router example also its label and ``label_source`` = followup | router: whether a successful
    re-ask / correction proved the label, or only the router's own belief did), so a rerun updates the same row instead of adding a duplicate. ``kind`` is
    router_example | intent_gap | answer_quality | stt_term; ``status`` is proposed | accepted | rejected | applied. The row holds
    COUNTS and the text-free ``pattern`` only - never a household utterance. ``summary`` is one sentence written from those
    counts (a template, or the night model's rewording of the same counts; ``summary_source`` says which). ``followup`` is the
    JSON an ACCEPT produced (a router self-train queue marker, or a task stub) - nothing is applied automatically.
  * ``struggle_proposal_events`` - the EVIDENCE: which ``struggle_events`` ids back a proposal. No foreign key on purpose:
    ``struggle_events`` rows disappear by retention sweep, forget and user delete, and ``struggle_proposals.forget_events`` /
    ``forget_user`` / ``reconcile`` (and every read, which joins back to ``struggle_events``) take the evidence out with them.

``IF NOT EXISTS`` makes a rerun safe and the statements run unchanged on PostgreSQL and SQLite. The downgrade drops both tables:
the events themselves stay in ``struggle_events``.
"""

from alembic import op

revision = "0058"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS struggle_proposals (
               id TEXT PRIMARY KEY,
               fingerprint TEXT NOT NULL,
               kind TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'proposed',
               pattern TEXT NOT NULL DEFAULT '',
               domain TEXT NOT NULL DEFAULT '',
               label TEXT NOT NULL DEFAULT '',
               label_source TEXT NOT NULL DEFAULT '',
               signals TEXT NOT NULL DEFAULT '{}',
               episode_count INTEGER NOT NULL DEFAULT 0,
               event_count INTEGER NOT NULL DEFAULT 0,
               user_count INTEGER NOT NULL DEFAULT 0,
               day_count INTEGER NOT NULL DEFAULT 0,
               first_seen DOUBLE PRECISION NOT NULL DEFAULT 0,
               last_seen DOUBLE PRECISION NOT NULL DEFAULT 0,
               confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
               summary TEXT NOT NULL DEFAULT '',
               summary_source TEXT NOT NULL DEFAULT 'template',
               followup TEXT NOT NULL DEFAULT '{}',
               decision_note TEXT NOT NULL DEFAULT '',
               created_at DOUBLE PRECISION NOT NULL DEFAULT 0,
               updated_at DOUBLE PRECISION NOT NULL DEFAULT 0,
               decided_at DOUBLE PRECISION,
               decided_by TEXT NOT NULL DEFAULT '',
               mode TEXT NOT NULL DEFAULT 'shadow'
           )"""
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_struggle_proposals_fingerprint ON struggle_proposals(fingerprint)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_struggle_proposals_status ON struggle_proposals(status, kind)")
    op.execute(
        """CREATE TABLE IF NOT EXISTS struggle_proposal_events (
               proposal_id TEXT NOT NULL,
               event_id TEXT NOT NULL,
               user_id TEXT NOT NULL,
               ts DOUBLE PRECISION NOT NULL DEFAULT 0,
               signal TEXT NOT NULL DEFAULT '',
               PRIMARY KEY (proposal_id, event_id)
           )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_struggle_proposal_events_event ON struggle_proposal_events(event_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_struggle_proposal_events_user ON struggle_proposal_events(user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_struggle_proposal_events_user")
    op.execute("DROP INDEX IF EXISTS idx_struggle_proposal_events_event")
    op.execute("DROP TABLE IF EXISTS struggle_proposal_events")
    op.execute("DROP INDEX IF EXISTS idx_struggle_proposals_status")
    op.execute("DROP INDEX IF EXISTS idx_struggle_proposals_fingerprint")
    op.execute("DROP TABLE IF EXISTS struggle_proposals")
