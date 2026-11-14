"""0080 - ``memory_value_registry``: the superseded-value registry (``memory_value_registry.py``; flag-dark, shadow only).

ADDITIVE. One new table and one index; no existing table or row is touched. Build step 5 of the memory redesign (design note,
section E): a value a member corrected AWAY ("Juno is 8, not 7": 7 is ``superseded``, 8 is ``asserted``) is recorded durably,
so a later writer that mines an older transcript can ask "was this value corrected away after these words were said?" without
re-reading text. It replaces, later, the in-process ``value_correction._REMEMBERED`` (6 h, lost on restart).

NO WORDS. Every column is an id, a closed-vocabulary label, a number or a time:

  * ``subject_ref``  - ``'self'`` (the member) or a ``people.id``. Never a name or a label: a subject that does not resolve to one
                       person is not recorded at all.
  * ``attribute``    - a CHECKed closed vocabulary, and only the attributes whose value is a NUMBER (dates, ages, amounts, measures,
                       counts, times, schedules). Word-valued attributes (a place, an employer, a diet) are not registry attributes.
  * ``value_norm``   - canonical JSON of numbers and enum codes only (``{"years": 8.0}``, ``{"cents": 1350000, "cur": "AUD"}``,
                       ``{"d": 19, "m": 12}``), validated by ``memory_value_registry.normalise`` before any write.
  * ``by_claim`` / ``source_turn_id`` / ``row_ids`` - ids only (a claim id, the turn's content id, memory row ids).

Low-entropy values are household content even without words (an age is an age): the table is erased by every forget path -
the member erase, the entity cascade, the topic forget, "forget that" and the physical row erase - and rows expire after 7 days
(``expires_at``). Times are DOUBLE PRECISION epoch seconds (the 0070 precedent), so the said-at ordering is exact arithmetic.

``IF NOT EXISTS`` everywhere and no live-bind inspection, so a rerun is a no-op and ``alembic upgrade head --sql`` renders. The
downgrade drops the table (LOSSY: the rows go - they are a 7-day cache of corrections, rebuilt by the next corrections).

CHAIN: on 0079 (``0079_calendar_feed.py``, PR #2474). This migration DEPENDS on that PR: until it is on main the chain has no 0079 and
this branch's migration checks are red by design (``test_memory_value_registry.py`` pins one head with 0079 present).
"""

from __future__ import annotations

from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None

TABLE = "memory_value_registry"
ROLES = ("superseded", "asserted")
#: the registry's closed attribute vocabulary: the number-valued attributes of the design note's B.2 table
ATTRIBUTES = ("event_date", "origin_date", "birthday", "age", "amount", "measure", "count", "time_of_day", "schedule")
INDEX = "idx_memory_value_registry_key"


def _in(values) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def statements() -> list[str]:
    """The CREATE statements. Pure: the tests run them on SQLite too."""
    return [
        f"""CREATE TABLE IF NOT EXISTS memory_value_registry (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               subject_ref TEXT NOT NULL,
               attribute TEXT NOT NULL CHECK (attribute IN {_in(ATTRIBUTES)}),
               role TEXT NOT NULL CHECK (role IN {_in(ROLES)}),
               value_norm TEXT NOT NULL,
               said_at DOUBLE PRECISION NOT NULL,
               by_claim TEXT NOT NULL,
               source_turn_id TEXT,
               row_ids TEXT NOT NULL DEFAULT '[]',
               expires_at DOUBLE PRECISION NOT NULL,
               created_at DOUBLE PRECISION NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_memory_value_registry_key ON memory_value_registry (user_id, subject_ref, attribute, said_at)",
    ]


def upgrade() -> None:
    for sql in statements():
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_memory_value_registry_key")
    op.execute("DROP TABLE IF EXISTS memory_value_registry")
