"""0020 ensures app_settings exists — the heal path for the SQLite→Postgres gap.

The bug: on DBs carried over from the legacy SQLite store the alembic_version was
stamped past 0018 while the app_settings table itself was never copied into Postgres,
so voice_settings.py's tts_voice preference silently never persisted. 0020 re-ensures
the table with an idempotent CREATE TABLE IF NOT EXISTS. These tests apply the REAL
migration against a SQLite DB in exactly that broken shape (table absent) and assert
it heals — and that a rerun is a no-op that keeps existing rows.
"""
import importlib.util
import sys
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

SVC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SVC))
VERSIONS = SVC / "alembic" / "versions"


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(SVC / "alembic"))
    cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg2://u:p@localhost/db")
    return cfg


def _load_migration(filename: str, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, VERSIONS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"), {"t": table}
    ).fetchone()
    return row is not None


def test_0020_is_single_head_after_0019():
    # The migration graph must never branch, and 0020 must extend the chain.
    script = ScriptDirectory.from_config(_alembic_config())
    heads = list(script.get_heads())
    assert len(heads) == 1, f"migration graph branched — heads: {heads}"
    assert script.get_revision("0020").down_revision == "0019"


def test_0020_heals_a_db_missing_app_settings():
    migration = _load_migration("0020_ensure_app_settings.py", "mig_0020")
    eng = create_engine("sqlite://")
    with eng.connect() as conn:
        # The exact broken shape: stamped past 0018, but the table is absent.
        assert not _table_exists(conn, "app_settings"), "precondition: table must be absent"

        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()

        assert _table_exists(conn, "app_settings"), "0020 must create the missing table"
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(app_settings)")).fetchall()}
        assert cols == {"key", "value", "updated_at"}
        # The consumer's write path (key/value) works against the healed table.
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('tts_voice', 'af_sky')"))
        got = conn.execute(text("SELECT value FROM app_settings WHERE key='tts_voice'")).fetchone()
        assert got[0] == "af_sky"


def test_0020_is_rerun_safe_and_keeps_existing_rows():
    migration = _load_migration("0020_ensure_app_settings.py", "mig_0020_rerun")
    eng = create_engine("sqlite://")
    with eng.connect() as conn:
        # A DB that already has the table + a saved preference (fresh install / healed).
        conn.execute(text(
            "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('tts_voice', 'af_heart')"))

        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()  # must be a no-op, not an error or a wipe

        got = conn.execute(text("SELECT value FROM app_settings WHERE key='tts_voice'")).fetchone()
        assert got[0] == "af_heart", "rerun must not drop or reset existing settings"
