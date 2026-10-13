"""0048 - guarded speaker-profile adaptation state on ``speaker_profiles``.

Six NULLABLE columns, nothing else, so a deploy that has not run this migration yet is unaffected: with
``ZOE_SPEAKER_ADAPT`` off (the default) no code path reads or writes them.

The enrolled voiceprint stays exactly where it is - ``embedding_blob`` is the immutable ANCHOR (it only ever
changes through an explicit enrolment, which averages into it). The adapted profile lives BESIDE it:

  * ``adapted_blob``       - the slowly-adapted unit embedding (float32, 256-dim), or NULL = never adapted.
  * ``adapted_from_count`` - the anchor's ``sample_count`` when adaptation started. A re-enrolment bumps
                             ``sample_count``, which makes this stale, which makes the adapted profile
                             INVALID (ignored when serving, restarted from the new anchor on the next update)
                             without the enrol route having to know adaptation exists.
  * ``adapt_count``        - how many updates have been applied (also the optimistic-concurrency token).
  * ``adapted_at``         - UTC ISO timestamp of the last applied update.
  * ``adapt_day`` / ``adapt_day_count`` - the UTC day and the number of updates applied on it (the daily cap).

Deleting the row ("forget my voice") removes the anchor and the adapted profile together because they share it.
Conventions match 0023: dialect-aware, rerun-safe.
"""

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("adapted_blob", "BYTEA", "BLOB"),
    ("adapted_from_count", "INTEGER", "INTEGER"),
    ("adapt_count", "INTEGER DEFAULT 0", "INTEGER DEFAULT 0"),
    ("adapted_at", "TEXT", "TEXT"),
    ("adapt_day", "TEXT", "TEXT"),
    ("adapt_day_count", "INTEGER DEFAULT 0", "INTEGER DEFAULT 0"),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, pg_type, _ in _COLUMNS:
            op.execute(f"ALTER TABLE speaker_profiles ADD COLUMN IF NOT EXISTS {name} {pg_type}")
    else:
        # SQLite has no ADD COLUMN IF NOT EXISTS - probe PRAGMA first so a rerun is a no-op.
        have = {r[1] for r in bind.exec_driver_sql("PRAGMA table_info(speaker_profiles)").fetchall()}
        for name, _, lite_type in _COLUMNS:
            if name not in have:
                op.execute(f"ALTER TABLE speaker_profiles ADD COLUMN {name} {lite_type}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for name, _, _ in _COLUMNS:
            op.execute(f"ALTER TABLE speaker_profiles DROP COLUMN IF EXISTS {name}")
    # SQLite pre-3.35 cannot DROP COLUMN; test DBs are disposable, so leave them.
