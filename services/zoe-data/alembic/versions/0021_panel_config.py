"""0021 — panel config: per-panel default speaker + pinned dock controls.

Answers "where is this panel, what speaker does it use, and which controls are
pinned to its dock" for a single panel. ``panels.location`` ALREADY exists and
already carries real data (``zoe-touch-pi`` → ``kitchen``), so this migration
only adds the two missing facts and does NOT introduce a second location field.

Why ``panels`` and not ``display_preferences``: ``display_preferences`` is a
column-per-preference store for *display* mechanics (brightness/idle/off) keyed
by ``device_id``; ``panels`` is the panel record and already owns ``location``.
``panel_id`` and ``device_id`` are the same key space (the panel sends
``localStorage.zoe_panel_id``, e.g. ``zoe-touch-pi``, which is the ``panels``
PK). Keeping location + speaker + pins in one row means one read, no join, and
no split-brain between two tables.

Schema notes:
  - ``default_player`` is the Music Assistant ``player_id`` this panel targets.
    NULL/'' → the caller falls back to the household-global preferred player, so
    the existing global endpoint keeps working unchanged.
  - ``pinned`` is the JSON-serialised ORDERED list of ``{entity_id, name}``
    (TEXT, matching the repo's text-column convention for JSON payloads — see
    0016). NULL is load-bearing and distinct from ``'[]'``:
      * NULL → never configured; the dock keeps its current fallback behaviour.
      * '[]' → the operator explicitly pinned nothing; the dock shows nothing.
    Conflating those two is exactly the bug this column shape prevents.

``IF NOT EXISTS`` makes the migration rerun-safe. ``ADD COLUMN IF NOT EXISTS``
is supported by PostgreSQL (production) and by SQLite only from 3.35+; the
SQLite branch therefore probes ``PRAGMA table_info`` instead, so test DBs
forward-exercise the same shape.
"""

from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


_COLUMNS = ("default_player", "pinned")


def _sqlite_existing_columns(bind) -> set[str]:
    rows = bind.exec_driver_sql("PRAGMA table_info(panels)").fetchall()
    return {str(r[1]) for r in rows}


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE panels ADD COLUMN IF NOT EXISTS default_player TEXT")
        op.execute("ALTER TABLE panels ADD COLUMN IF NOT EXISTS pinned TEXT")
    else:
        # SQLite (test DBs): no portable ADD COLUMN IF NOT EXISTS before 3.35.
        existing = _sqlite_existing_columns(bind)
        for col in _COLUMNS:
            if col not in existing:
                op.add_column("panels", sa.Column(col, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE panels DROP COLUMN IF EXISTS default_player")
        op.execute("ALTER TABLE panels DROP COLUMN IF EXISTS pinned")
    else:
        existing = _sqlite_existing_columns(bind)
        for col in _COLUMNS:
            if col in existing:
                op.drop_column("panels", col)
