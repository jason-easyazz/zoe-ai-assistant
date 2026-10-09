"""0043 - people graph invariants (people_graph.py): evidence pointers, close reasons, one timestamptz form, the resolver's index.

Schema only and additive (the backfill is an operator block in docs/knowledge/relationship-memory-flag-enable.md); code written
for it runs unchanged before it (``people_graph.edge_columns`` reads the catalog).

``person_relationships`` gains ``close_reason`` (why an edge was closed - edges are closed, never deleted), ``turn_id`` /
``quote_span`` / ``speaker_rank`` (the evidence pointers) and ``valid_from_ts`` / ``valid_to_ts`` / ``recorded_ts``: the ONE
``timestamptz`` form of ``valid_from`` / ``valid_to`` / ``created_at``, whose text holds two formats (ISO ``...Z`` and
``NOW()::text``). On PostgreSQL a trigger parses either into them on every insert / update, so writers that still pass text keep
working; unparseable text leaves NULL and never fails the write. SQLite (tests) gets plain TEXT columns, unmaintained.

Indexes: ``people_user_live_idx`` (the resolver's roster read; ``people`` had no index on ``user_id``) and three edge indexes
(walk from either end, history by start, "which edges came from this turn").
"""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

_TEXT_COLS = ("close_reason TEXT", "turn_id TEXT", "quote_span TEXT")


def _add_column(dialect: str, column_sql: str) -> None:
    if dialect == "postgresql":
        op.execute(f"ALTER TABLE person_relationships ADD COLUMN IF NOT EXISTS {column_sql}")
    else:
        op.execute(f"ALTER TABLE person_relationships ADD COLUMN {column_sql}")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    pg = dialect == "postgresql"

    for col in _TEXT_COLS:
        _add_column(dialect, col)
    _add_column(dialect, "speaker_rank INTEGER")
    ts_type = "TIMESTAMPTZ" if pg else "TEXT"
    for name in ("valid_from_ts", "valid_to_ts", "recorded_ts"):
        _add_column(dialect, f"{name} {ts_type}")

    op.execute("CREATE INDEX IF NOT EXISTS people_user_live_idx ON people (user_id, lower(name)) WHERE deleted = 0")
    op.execute("CREATE INDEX IF NOT EXISTS person_relationships_user_b_current "
               "ON person_relationships (user_id, person_b_id) WHERE valid_to IS NULL")
    op.execute("CREATE INDEX IF NOT EXISTS person_relationships_user_a_hist "
               "ON person_relationships (user_id, person_a_id, valid_from)")
    op.execute("CREATE INDEX IF NOT EXISTS person_relationships_turn "
               "ON person_relationships (user_id, turn_id) WHERE turn_id IS NOT NULL")

    if pg:
        op.execute(
            """CREATE OR REPLACE FUNCTION zoe_try_timestamptz(t text) RETURNS timestamptz
               LANGUAGE plpgsql STABLE AS $$
               BEGIN
                   IF t IS NULL OR t = '' THEN
                       RETURN NULL;
                   END IF;
                   RETURN t::timestamptz;
               EXCEPTION WHEN others THEN
                   RETURN NULL;
               END
               $$"""
        )
        op.execute(
            """CREATE OR REPLACE FUNCTION person_relationships_sync_ts() RETURNS trigger
               LANGUAGE plpgsql AS $$
               BEGIN
                   NEW.valid_from_ts := zoe_try_timestamptz(COALESCE(NULLIF(NEW.valid_from, ''), NEW.created_at));
                   NEW.valid_to_ts := zoe_try_timestamptz(NEW.valid_to);
                   NEW.recorded_ts := zoe_try_timestamptz(NEW.created_at);
                   RETURN NEW;
               END
               $$"""
        )
        op.execute("DROP TRIGGER IF EXISTS person_relationships_sync_ts_trg ON person_relationships")
        op.execute(
            "CREATE TRIGGER person_relationships_sync_ts_trg BEFORE INSERT OR UPDATE ON person_relationships "
            "FOR EACH ROW EXECUTE FUNCTION person_relationships_sync_ts()"
        )


def downgrade() -> None:
    # Lossy by design (as 0015's): the evidence pointers, close reasons and the timestamptz copies are dropped with
    # their columns; the edges themselves and their text timestamps are untouched.
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS person_relationships_sync_ts_trg ON person_relationships")
        op.execute("DROP FUNCTION IF EXISTS person_relationships_sync_ts()")
        op.execute("DROP FUNCTION IF EXISTS zoe_try_timestamptz(text)")
    for idx in ("person_relationships_turn", "person_relationships_user_a_hist",
                "person_relationships_user_b_current", "people_user_live_idx"):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    for col in ("recorded_ts", "valid_to_ts", "valid_from_ts", "speaker_rank", "quote_span", "turn_id", "close_reason"):
        if dialect == "postgresql":
            op.execute(f"ALTER TABLE person_relationships DROP COLUMN IF EXISTS {col}")
        else:
            op.execute(f"ALTER TABLE person_relationships DROP COLUMN {col}")
