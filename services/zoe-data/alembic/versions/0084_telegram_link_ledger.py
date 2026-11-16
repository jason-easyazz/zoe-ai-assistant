"""0084 - the messaging-account link: durable single use, and one holder per messaging id (``telegram_link.py``).

Two guarantees the database now holds, not the process:

  * ``telegram_link_redemptions`` - one row per REDEEMED link token (``token_id``, a hash of the token's signature, is the primary
    key). ``telegram_link.link`` inserts it in the SAME transaction as the link write, so a token can be redeemed once, across
    restarts and workers, until it expires; a refused or failed redemption rolls the row back with the link, so the member can
    simply scan again. Rows past ``expires_at`` are deleted by the same write path (no scheduled job). Ids and epoch seconds only;
    erased by the member erase (``telegram_link.forget_user``).
  * ``uq_user_preferences_telegram_id`` - a unique partial expression index on the ``telegram_id`` key of ``user_preferences.prefs``
    (the link lives in that JSON dict, which every existing reader keeps reading), so two concurrent redemptions cannot leave two
    members holding one messaging id: the second write fails on the index and is refused.

EXISTING LINKS (owner decision 2026-11-16). Every link made before this change is removed: until now a link did not need proof
of control of both sides, so none of them is known to be its member's. Each member reconnects once from Settings (the QR). This
also removes every id held by more than one member, which is what lets the unique index be built. A link that already carries the
current proof is kept, so a rerun is a no-op. An empty id is not a link and is not indexed.

``IF NOT EXISTS`` everywhere and no live-bind inspection, so a rerun is a no-op and ``alembic upgrade head --sql`` renders. PostgreSQL
SQL (the ``prefs::jsonb`` cast every reader of the link already uses). The downgrade drops the index and the table (LOSSY: spent-token
rows go, so a token redeemed in its last minutes could be redeemed again until it expires; the removed links stay removed).
"""

from __future__ import annotations

import logging

from alembic import context, op

revision = "0084"
down_revision = "0080"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

TABLE = "telegram_link_redemptions"
INDEX = "uq_user_preferences_telegram_id"
_TID = "(prefs::jsonb ->> 'telegram_id')"
#: Every link made before proof of control of both sides was required is removed (owner decision 2026-11-16): each member
#: reconnects once from Settings. That includes every id more than one member held, so the unique index below cannot fail.
#: A link carrying the current proof (``telegram_link.PROOF_VERSION``) is left alone, so a rerun changes nothing.
#: Logged with its row count when run against a live database.
RESET = f"""UPDATE user_preferences
               SET prefs = ((prefs::jsonb - 'telegram_id') - 'telegram_link_proof')::text, updated_at = NOW()
             WHERE {_TID} <> ''
               AND COALESCE(prefs::jsonb -> 'telegram_link_proof' ->> 'v', '') <> '2'"""


def statements() -> list[str]:
    """The upgrade statements, in order. Pure (no bind), so a test can read them. The table name is written out in the CREATE
    TABLE text (not interpolated): ``forget_reach`` finds per-member tables by reading these files."""
    return [
        """CREATE TABLE IF NOT EXISTS telegram_link_redemptions (
               token_id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               expires_at DOUBLE PRECISION NOT NULL,
               redeemed_at DOUBLE PRECISION NOT NULL
           )""",
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_user ON {TABLE} (user_id)",
        RESET,
        f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} ON user_preferences ({_TID}) WHERE {_TID} <> ''",
    ]


def upgrade() -> None:
    for sql in statements():
        if sql is RESET and not context.is_offline_mode():
            # a live bind: run it here so the number of rows changed can be logged (a count only, never an id)
            result = op.get_bind().exec_driver_sql(sql)
            logger.info("0084: removed %d messaging link(s) made before proof of both sides was required; "
                        "each member reconnects from Settings", max(int(result.rowcount or 0), 0))
        else:
            op.execute(sql)       # offline (``--sql``): rendered; there is no result to count


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
    op.execute(f"DROP INDEX IF EXISTS idx_{TABLE}_user")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
