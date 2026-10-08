"""0040 — proactive_ledger_lines: the delivery ledger's per-item lines (BH2) and the welcome taps.

``proactive_deliveries`` (0036) answers one question: what happened AFTER a block went out with a
reply (voiced? taken up?). It cannot say what the selector HELD BACK and why, and it carries no
signal from the person about whether a raise was welcome. This table is that evidence
(docs/research/best-ideas-register-2026-10-09.md, BH1 + BH2; the pull-not-push record, §3.3-§3.5):

  * ``line`` — ``raised`` (a ``[RAISE]`` settled with a reply), ``withheld`` (the selector had a
    candidate for this turn and a gate held it: ``reason`` = brief / held / gap / daily_cap /
    class_backoff / class_off), ``would_withhold`` (shadow: the class back-off WOULD have held it),
    ``pulled`` (delivered because the person asked: "what's up?"), ``welcome`` (a one-tap signal
    about an earlier raised / pulled line: ``signal`` = welcome | neutral | not_now).
  * ``kind`` — the item class the selector already has (open_loop | emotional | event); ``klass``
    is the coarse shape the panel may name (``question`` | ``notify``), never the content.
  * ``reason`` / ``score`` / ``shape`` / ``channel`` — why, the selector's salience for the item,
    the turn shape (greeting | cue | pull | brief), and the lane (chat | voice | ...).
  * ``sensitivity`` — the restraint tier's class for the item when that tier exists (empty
    otherwise); recorded, never interpreted here.
  * ``target_id`` — for a ``welcome`` line, the id of the raised / pulled line it is about.
  * ``idem_key`` UNIQUE — one line per item, reason and day (a withheld item is not re-logged on
    every turn); a welcome tap is one per target (the last tap wins).

No item text, no utterance and no reply text is stored: ``source_ref`` identifies the item the way
``proactive_candidates`` does. No FK to ``users`` (the bar's per-table ``user_id`` teardown sweeps
delete in any order). Timestamps are TEXT UTC (``%Y-%m-%dT%H:%M:%SZ``) like 0033 / 0036.
``IF NOT EXISTS`` makes a rerun safe and the statements run unchanged on PostgreSQL and SQLite.
The downgrade drops the table: a ledger of behaviour is not worth keeping past its schema.
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS proactive_ledger_lines (
               id TEXT PRIMARY KEY,
               idem_key TEXT NOT NULL UNIQUE,
               user_id TEXT NOT NULL,
               line TEXT NOT NULL,
               kind TEXT NOT NULL DEFAULT '',
               klass TEXT NOT NULL DEFAULT '',
               source_ref TEXT NOT NULL DEFAULT '',
               reason TEXT NOT NULL DEFAULT '',
               score REAL,
               shape TEXT NOT NULL DEFAULT '',
               channel TEXT NOT NULL DEFAULT '',
               session_id TEXT NOT NULL DEFAULT '',
               sensitivity TEXT NOT NULL DEFAULT '',
               signal TEXT NOT NULL DEFAULT '',
               target_id TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL
           )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_proactive_ledger_lines_user "
        "ON proactive_ledger_lines (user_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_proactive_ledger_lines_line "
        "ON proactive_ledger_lines (user_id, line, kind, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_proactive_ledger_lines_line")
    op.execute("DROP INDEX IF EXISTS idx_proactive_ledger_lines_user")
    op.execute("DROP TABLE IF EXISTS proactive_ledger_lines")
