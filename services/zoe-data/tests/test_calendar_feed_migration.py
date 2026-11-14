"""Migration 0079 (the calendar subscription feed, Stage 1) and the extended ``finish_run`` counts.

Synthetic data only, SQLite through the compat-DB shape of ``test_feed_plumbing_stage0`` (``ci_safe``). Negative controls are
noted per test.
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
import sqlite3

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import connected_accounts as ca
import db_compat
import user_erase_gate as erase_gate
from test_feed_plumbing_stage0 import SVC, _DUMMY_URL, _Db, _migrate

pytestmark = pytest.mark.ci_safe

MIG = "0079_calendar_feed.py"


def run(coro):
    return asyncio.run(coro)


def _module():
    spec = importlib.util.spec_from_file_location("mig_0079", SVC / "alembic/versions" / MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scratch(tmp_path, monkeypatch, *, with_0079=True):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
        c.exec_driver_sql("CREATE TABLE events (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, "
                          "visibility TEXT NOT NULL DEFAULT 'family')")
    _migrate(engine, "0052_calendar_data_model.py")
    _migrate(engine, "0078_connected_accounts.py")
    if with_0079:
        _migrate(engine, MIG)
    d = _Db(path)
    d.engine = engine

    import contextlib

    @contextlib.asynccontextmanager
    async def fake():
        yield d
    monkeypatch.setattr(db_compat, "get_compat_db", fake)
    erase_gate.reset()
    return d


@pytest.fixture
def db(tmp_path, monkeypatch):
    d = _scratch(tmp_path, monkeypatch)
    yield d
    erase_gate.reset()


def _cols(d, table):
    return {r[1] for r in d.rows(f"PRAGMA table_info({table})")}


def test_upgrade_adds_every_column_reruns_and_downgrades(db):
    mod = _module()
    _migrate(db.engine, MIG)                                                     # rerun: a no-op, no duplicate-column error
    for table, name, _ddl in mod.COLUMNS:
        assert name in _cols(db, table), (table, name)
    idx = {r[1] for r in db.rows("PRAGMA index_list(events)")}
    assert "idx_events_calendar_generation" in idx
    assert db.rows("SELECT dflt_value FROM pragma_table_info('calendars') WHERE name = 'consecutive_failures'") == [("0",)]
    _migrate(db.engine, MIG, down=True)
    for table, name, _ddl in mod.COLUMNS:
        assert name not in _cols(db, table), (table, name)
    assert "idx_events_calendar_generation" not in {r[1] for r in db.rows("PRAGMA index_list(events)")}
    _migrate(db.engine, MIG)                                                     # and back up again
    assert "content_hash" in _cols(db, "events")


def test_no_link_url_or_secret_column_is_added():
    """A subscription address is a credential: this revision must not create a place to keep one."""
    mod = _module()
    names = " ".join(name for _t, name, _d in mod.COLUMNS)
    assert not any(w in names for w in ("url", "link", "secret", "token", "password", "address", "href", "uri"))


def test_error_codes_are_a_fixed_list_never_a_message(db):
    db.conn.execute("INSERT INTO connected_accounts (id, user_id, provider, kind, subject_hash, created_at, updated_at) "
                    "VALUES ('a', 'u', 'subscription', 'calendar', 'h', 'x', 'x')")
    db.conn.execute("UPDATE connected_accounts SET last_error_code = 'too_large' WHERE id = 'a'")          # control: a code passes
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("UPDATE connected_accounts SET last_error_code = 'HTTP 404 for https://calendar.example/abc' WHERE id = 'a'")
    db.conn.execute("INSERT INTO sync_runs (id, account_id, user_id, account_generation, started_at) VALUES ('r', 'a', 'u', 1, 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("UPDATE sync_runs SET error_code = 'refused by example.org' WHERE id = 'r'")
    db.conn.rollback()


def test_the_code_list_is_one_list():
    assert _module().ERROR_CODES == ca.ERROR_CODES


def test_postgres_render_and_single_head(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", _DUMMY_URL)
    buf = io.StringIO()
    cfg = Config(output_buffer=buf, stdout=buf)
    cfg.set_main_option("script_location", str(SVC / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _DUMMY_URL)
    command.upgrade(cfg, "0078:0079", sql=True)
    command.downgrade(cfg, "0079:0078", sql=True)
    sql = buf.getvalue()
    assert "ALTER TABLE events ADD COLUMN IF NOT EXISTS content_hash TEXT" in sql
    assert "ALTER TABLE sync_runs DROP COLUMN IF EXISTS n_suppressed" in sql
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, heads


# -- finish_run: the 0079 counts ------------------------------------------------------------------------------------------------

def test_finish_run_records_the_feed_counts_and_the_code(db):
    a = run(ca.create("ann", "subscription", "calendar", "feed-subject-1"))
    r = run(ca.start_run(a, "ann", scope="calendar_feed"))
    counts = {"seen": 9, "created": 2, "updated": 1, "deleted": 1, "unchanged": 4, "skipped": 1, "pruned": 1, "suppressed": 1,
              "rrule_unsupported": 2}
    assert run(ca.finish_run(r, outcome="partial", counts=counts, error_class="provider", error_code="suspect_empty",
                             n_bytes=2048, duration_ms=37))
    assert db.rows("SELECT n_seen, n_created, n_updated, n_deleted, n_unchanged, n_skipped, n_pruned, n_suppressed, "
                   "n_rrule_unsupported, bytes, duration_ms, error_code FROM sync_runs") == [
        (9, 2, 1, 1, 4, 1, 1, 1, 2, 2048, 37, "suspect_empty")]
    assert db.rows("SELECT status, last_error_class, last_error_code FROM connected_accounts") == [("active", "provider", "suspect_empty")]
    r2 = run(ca.start_run(a, "ann"))
    assert run(ca.finish_run(r2, outcome="ok", counts={"unchanged": 9}, error_code=None))       # a clean run clears the code
    assert db.rows("SELECT last_error_code FROM connected_accounts") == [(None,)]
    r3 = run(ca.start_run(a, "ann"))
    with pytest.raises(ValueError):
        run(ca.finish_run(r3, outcome="error", error_class="auth", error_code="Unauthorized: token expired"))


def test_finish_run_without_the_extras_still_works_on_a_0078_database(tmp_path, monkeypatch):
    """Stage 0 callers pass no 0079 field, and must keep working before the migration runs. Negative control: the same run WITH a
    0079 field on the 0078-only database fails (the column is missing), proving the extras are written only when given."""
    d = _scratch(tmp_path, monkeypatch, with_0079=False)
    a = run(ca.create("ann", "google", "calendar", "s1"))
    assert run(ca.finish_run(run(ca.start_run(a, "ann")), outcome="ok", counts={"seen": 1}))
    assert d.rows("SELECT outcome, n_seen FROM sync_runs") == [("ok", 1)]
    with pytest.raises(sqlite3.OperationalError):
        run(ca.finish_run(run(ca.start_run(a, "ann")), outcome="ok", counts={"unchanged": 1}))
    erase_gate.reset()
