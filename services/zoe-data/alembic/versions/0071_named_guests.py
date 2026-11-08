"""0071 - one person-voice registry for the panel and the pendant (named guests, ``named_guests.py``; flag-dark).

Design: ``docs/knowledge/named-guests.md``. A NAMED GUEST is a person in a member's OWN people layer (``people``, 0006/0053)
whose voice that member taught Zoe at the panel. The voice lives in the registry Omi already writes, ``learned_voiceprints``
(0053): one row per (member, person, model, CHANNEL). ``channel`` already existed ('pendant'); this migration makes it part of
the key, so the pendant and the panel's microphone never share a vector (microphone drift: memories-space.md section 3.4) and a
person can hold one of each.

  * ``learned_voiceprints``     - UNIQUE (owner_user_id, people_id, model_id) becomes UNIQUE (..., channel).

Postgres finds the old constraint by its COLUMNS (its generated name is not assumed). SQLite (labs / unit tests) rebuilds the table
(it cannot drop a table constraint). Idempotent. The downgrade restores the old key after deleting the panel rows (lossy by design: a
guest is re-enrolled).

CHAIN: on 0069 (#2286, landed). 0070 (#2323) is in flight: whichever of the two lands second re-points its ``down_revision`` to
the other before merge (the single-head test enforces it).
"""

from __future__ import annotations

from alembic import op

revision = "0071"
down_revision = "0069"
branch_labels = None
depends_on = None

_NEW_KEY = ("CREATE UNIQUE INDEX IF NOT EXISTS uq_learned_voiceprints_channel "
            "ON learned_voiceprints (owner_user_id, people_id, model_id, channel)")

_PG_DROP_OLD_KEY = """DO $$
DECLARE c text;
BEGIN
  SELECT con.conname INTO c FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid
   WHERE rel.relname = 'learned_voiceprints' AND con.contype = 'u'
     AND (SELECT array_agg(att.attname::text ORDER BY att.attname::text) FROM unnest(con.conkey) k
            JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k)
         = ARRAY['model_id', 'owner_user_id', 'people_id'];
  IF c IS NOT NULL THEN
    EXECUTE format('ALTER TABLE learned_voiceprints DROP CONSTRAINT %I', c);
  END IF;
END $$"""

_SQLITE_COLS = ("id, owner_user_id, people_id, model_id, channel, embedding, n_segments, n_recordings, status, learning_enabled, "
                "created_at, updated_at, last_confirmed_at, expires_at, rejections")


def sqlite_statements(*, has_rejections: bool = True) -> list[str]:
    """The SQLite rebuild of ``learned_voiceprints`` with the channel in its key. Pure (the tests run it)."""
    cols = _SQLITE_COLS if has_rejections else _SQLITE_COLS.replace(", rejections", "")
    return [
        """CREATE TABLE learned_voiceprints_0071 (
               id TEXT PRIMARY KEY,
               owner_user_id TEXT NOT NULL,
               people_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
               model_id TEXT NOT NULL,
               channel TEXT NOT NULL DEFAULT 'pendant',
               embedding BLOB NOT NULL,
               n_segments INTEGER NOT NULL DEFAULT 0,
               n_recordings INTEGER NOT NULL DEFAULT 0,
               status TEXT NOT NULL DEFAULT 'provisional' CHECK (status IN ('provisional', 'confirmed')),
               learning_enabled INTEGER NOT NULL DEFAULT 1,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               last_confirmed_at TEXT,
               expires_at TEXT NOT NULL,
               rejections INTEGER NOT NULL DEFAULT 0
           )""",
        f"INSERT INTO learned_voiceprints_0071 ({cols}) SELECT {cols} FROM learned_voiceprints",
        "DROP TABLE learned_voiceprints",
        "ALTER TABLE learned_voiceprints_0071 RENAME TO learned_voiceprints",
        "CREATE INDEX IF NOT EXISTS idx_learned_voiceprints_owner ON learned_voiceprints (owner_user_id, people_id)",
        _NEW_KEY,
    ]


def postgres_statements() -> list[str]:
    return [_PG_DROP_OLD_KEY, _NEW_KEY]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for sql in postgres_statements():
            op.execute(sql)
        return
    import sqlalchemy as sa

    insp = sa.inspect(bind)
    if not insp.has_table("learned_voiceprints"):
        return                      # a bare lab database without 0053 has nothing to re-key
    has_rej = "rejections" in {c["name"] for c in insp.get_columns("learned_voiceprints")}
    if any(ix.get("name") == "uq_learned_voiceprints_channel" for ix in insp.get_indexes("learned_voiceprints")):
        return
    for sql in sqlite_statements(has_rejections=has_rej):
        op.execute(sql)


def downgrade() -> None:
    op.execute("DELETE FROM learned_voiceprints WHERE channel <> 'pendant'")
    op.execute("DROP INDEX IF EXISTS uq_learned_voiceprints_channel")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE learned_voiceprints ADD CONSTRAINT learned_voiceprints_owner_user_id_people_id_model_id_key "
                   "UNIQUE (owner_user_id, people_id, model_id)")
