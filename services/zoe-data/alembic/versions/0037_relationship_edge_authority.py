"""0037 - person_relationships edge provenance (authority / origin).

Memory-authority (docs/knowledge/memory-authority.md): every memory row carries WHO said it
(``user_stated`` | ``user_confirmed`` | ``inferred``) so a model inference can never close or
contradict something the user said. The relationship graph is the one people-graph table where
a writer can CLOSE an existing fact (``person_extractor._write_relationship`` supersedes an
edge when ``ZOE_TEMPORAL_RELATIONSHIPS_ENABLED`` is on), so its edges get the same stamp.

Two nullable TEXT columns, no backfill and no index: NULL means "written before provenance
existed" and the writer treats an unstamped edge as the user's (it can only be closed by a
user-class write). ``IF NOT EXISTS`` on Postgres, a plain ADD on SQLite (a fresh test DB is
clean), exactly like 0015. The code that reads/writes these columns is best-effort and works
unchanged on a database that has not run this revision.
"""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def _add_column(dialect: str, column_sql: str) -> None:
    if dialect == "postgresql":
        op.execute(f"ALTER TABLE person_relationships ADD COLUMN IF NOT EXISTS {column_sql}")
    else:
        op.execute(f"ALTER TABLE person_relationships ADD COLUMN {column_sql}")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    _add_column(dialect, "authority TEXT")
    _add_column(dialect, "origin TEXT")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE person_relationships DROP COLUMN IF EXISTS authority")
        op.execute("ALTER TABLE person_relationships DROP COLUMN IF EXISTS origin")
