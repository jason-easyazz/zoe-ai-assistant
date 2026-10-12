"""0046 - enrollment_offers: the per-member biometric SETUP state Zoe keeps about offering voice enrolment.

Whether a member's voice (or face) IS enrolled is not stored here - it is read from ``speaker_profiles`` / ``face_profiles``, the
tables that actually hold the enrolment, so the two can never disagree. This table holds the one thing those tables cannot say:
what Zoe has OFFERED and what the member answered.

One row per (member, modality), ``modality`` = ``voice`` | ``face``:

  * ``last_offered_at`` / ``offer_count`` - when Zoe last offered it and how often (the once-a-day rule and the audit trail);
  * ``pending_since`` - set when an offer is made, cleared by any ANSWER; an offer made while the previous one is still pending
    counts that one as ignored (``ignored_count``), and enough ignored offers put the member in the same quiet period as a "no";
  * ``accepted_at`` - the member said yes (or asked for it themselves);
  * ``declined_at`` / ``declined_until`` - the member said no (``declined_at`` is only ever an explicit no); ``declined_until`` is the
    end of the quiet period, set by an explicit no and by the ignored-offers rule alike, and the ONLY thing the offer policy reads.

TEXT UTC timestamps (``%Y-%m-%dT%H:%M:%SZ``) like the proactive tables. No FK to ``users`` (the demo-user teardown sweeps rows by
exact id); the row holds no biometric and no words from the member. ``IF NOT EXISTS`` runs unchanged on PostgreSQL and SQLite, so
reruns are safe. The downgrade drops the table. (Numbered 0046: 0045 is ``omi_recordings``, which merged first.)
"""

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS enrollment_offers (
               user_id TEXT NOT NULL,
               modality TEXT NOT NULL,
               last_offered_at TEXT,
               offer_count INTEGER NOT NULL DEFAULT 0,
               pending_since TEXT,
               ignored_count INTEGER NOT NULL DEFAULT 0,
               accepted_at TEXT,
               declined_at TEXT,
               declined_until TEXT,
               updated_at TEXT NOT NULL,
               PRIMARY KEY (user_id, modality)
           )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS enrollment_offers")
