"""0082 - routines, stage 1: the store (``routines_store.py``; record ``docs/knowledge/routines.md``; flag ``ZOE_ROUTINES``, default off).

A routine is a named, ordered checklist for ONE person (the subject), tied to a moment of the day, that starts fresh each day.
Four tables, every column TEXT / INTEGER (timestamps are ISO text, as ``0001``):

  * ``routines``           - one row per routine. ``user_id`` = the SUBJECT (whose routine it is); ``created_by`` = who made it;
                             ``name`` as said or typed; ``anchor`` = waking | breakfast | lunch | dinner | leaving | bed | window;
                             ``window_start`` / ``window_end`` 'HH:MM' household time (NULL = the anchor's default);
                             ``days_rule`` = an RRULE in ``reminder_recurrence``'s subset (NULL = every day);
                             ``visibility`` personal (default) | family; ``prompt_mode`` card (default) | silent | phone.
  * ``routine_steps``      - the template lines. ``text`` is stored EXACTLY as entered (never cleaned, never generated);
                             ``kind`` = check | take_with | medicine.
  * ``routine_days``       - one routine's copy for one household date, made lazily on first need (a read, a tick, a one-off):
                             no night job. ``snapshot_at`` = when the template steps were copied in (NULL = a future day that so
                             far only holds one-off lines); ``closed_at`` / ``close_reason`` = settled (all_done | window_end).
  * ``routine_day_steps``  - the lines of one day copy: a snapshot of a template step (``origin='template'``) or a one-off line
                             (``origin='one_off'``, joins exactly one day). ``state`` = pending | done | skipped | snoozed;
                             ``known_by`` = said | tapped (``sensed`` is reserved for a later stage and never written);
                             ``actor_user_id`` = who ticked (NULL = a household tap at the panel); ``verified`` 1 = a verified voice
                             or a member session; ``prompts`` = how many times Zoe named the step.

FORGET. Every table is REACHED by both forget paths (``forget_reach.py``): the member erase (``routines_store.forget_user``, run by
``MemoryService.delete_user``) deletes the member's routines with their steps and days, and blanks them as the actor on anyone
else's; the entity forget (``routines_store.erase_entity``, a step of ``memory_forget_cascade``) deletes a routine, step or day line
whose words name the forgotten entity. Readings, carer links and the carer outbox are NOT here: they arrive with their own PRs.

Every statement is guarded (``IF NOT EXISTS``), so a rerun is a no-op. CHAIN: on 0079 (0080 / 0081 are reserved by other branches);
renumber at merge if the heads move. The downgrade drops the four tables (their rows are lost).
"""

from __future__ import annotations

from alembic import op

revision = "0082"
down_revision = "0079"
branch_labels = None
depends_on = None

TABLES = (
    """CREATE TABLE IF NOT EXISTS routines (
           id TEXT PRIMARY KEY,
           user_id TEXT NOT NULL,
           created_by TEXT NOT NULL,
           name TEXT NOT NULL,
           anchor TEXT NOT NULL,
           window_start TEXT,
           window_end TEXT,
           days_rule TEXT,
           visibility TEXT NOT NULL DEFAULT 'personal',
           prompt_mode TEXT NOT NULL DEFAULT 'card',
           enabled INTEGER NOT NULL DEFAULT 1,
           deleted INTEGER NOT NULL DEFAULT 0,
           created_at TEXT NOT NULL,
           updated_at TEXT NOT NULL
       )""",
    """CREATE TABLE IF NOT EXISTS routine_steps (
           id TEXT PRIMARY KEY,
           routine_id TEXT NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
           position INTEGER NOT NULL,
           text TEXT NOT NULL,
           kind TEXT NOT NULL DEFAULT 'check',
           deleted INTEGER NOT NULL DEFAULT 0,
           created_at TEXT NOT NULL,
           updated_at TEXT NOT NULL
       )""",
    """CREATE TABLE IF NOT EXISTS routine_days (
           id TEXT PRIMARY KEY,
           routine_id TEXT NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
           user_id TEXT NOT NULL,
           day TEXT NOT NULL,
           snapshot_at TEXT,
           started_at TEXT,
           start_signal TEXT,
           closed_at TEXT,
           close_reason TEXT,
           created_at TEXT NOT NULL,
           UNIQUE (routine_id, day)
       )""",
    """CREATE TABLE IF NOT EXISTS routine_day_steps (
           id TEXT PRIMARY KEY,
           day_id TEXT NOT NULL REFERENCES routine_days(id) ON DELETE CASCADE,
           step_id TEXT,
           origin TEXT NOT NULL,
           text TEXT NOT NULL,
           kind TEXT NOT NULL,
           position INTEGER NOT NULL,
           state TEXT NOT NULL DEFAULT 'pending',
           snooze_until TEXT,
           known_by TEXT,
           actor_user_id TEXT,
           verified INTEGER,
           prompts INTEGER NOT NULL DEFAULT 0,
           last_prompt_at TEXT,
           created_via TEXT,
           created_at TEXT NOT NULL,
           updated_at TEXT NOT NULL
       )""",
)

INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_routines_user ON routines (user_id, deleted)",
    "CREATE INDEX IF NOT EXISTS ix_routine_steps_routine ON routine_steps (routine_id, position)",
    "CREATE INDEX IF NOT EXISTS ix_routine_days_user_day ON routine_days (user_id, day)",
    "CREATE INDEX IF NOT EXISTS ix_routine_day_steps_day ON routine_day_steps (day_id, position)",
)


def upgrade() -> None:
    for ddl in TABLES + INDEXES:
        op.execute(ddl)


def downgrade() -> None:
    for name in ("routine_day_steps", "routine_days", "routine_steps", "routines"):
        op.execute(f"DROP TABLE IF EXISTS {name}")
