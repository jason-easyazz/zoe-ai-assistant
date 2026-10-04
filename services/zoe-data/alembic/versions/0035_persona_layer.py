"""0035 — ``household_persona`` + ``member_modes`` (the persona layer, phase 0).

Two small tables behind ``ZOE_PERSONA_LAYER`` (flag-dark; ``persona_layer.py``):

* ``household_persona`` — at most ONE row (``scope = 'household'``): the validated persona
  record as JSON (3-5 traits, voice style, boundaries, optional backstory), its content
  ``version`` and who/when last wrote it. No row means "the default persona" (today's Zoe
  expressed as data), so a fresh install and a reset are the same state.
* ``member_modes`` — one row per member that has a relationship mode other than the
  default: ``mode`` (companion | mentor | helper | kid), ``minor`` (0/1; a minor can hold
  only kid/helper — validated in ``persona_layer.validate_member_mode``) and who/when.
  No row means ``companion``, non-minor.

Neither table holds anything affective: no mood, no score, no per-turn value. See
``docs/governance/emotional-safety-note.md``. There is no FK to ``users`` (the per-table
``user_id`` sweeps delete rows in any order); the reader ignores orphans.

Idempotent (``IF NOT EXISTS``). The downgrade drops ``household_persona`` ONLY (re-creatable
from the defaults) and KEEPS ``member_modes``: that table is the sole record that a member is a
minor, so dropping it on a rollback would turn flagged children into unflagged adults on the
next upgrade (``CREATE TABLE IF NOT EXISTS`` leaves the surviving rows untouched). It is a few
rows with no free text and nothing affective; an operator who truly wants it gone drops it by
hand (``DROP TABLE member_modes``) - that is deliberately not automatic.
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_HOUSEHOLD_DDL = """
CREATE TABLE IF NOT EXISTS household_persona (
    scope TEXT PRIMARY KEY,
    record_json TEXT NOT NULL,
    version TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
)
"""

_MODES_DDL = """
CREATE TABLE IF NOT EXISTS member_modes (
    user_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'companion',
    minor INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
)
"""


def upgrade() -> None:
    op.execute(_HOUSEHOLD_DDL)
    op.execute(_MODES_DDL)


def downgrade() -> None:
    # member_modes is KEPT on purpose (see the module docstring): it holds the minor flags.
    op.execute("DROP TABLE IF EXISTS household_persona")
