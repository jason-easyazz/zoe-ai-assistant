"""Migration 0029 and the pairing router survive a database with NO
``panel_provision_codes`` table.

The 2026-09-27 deploy of #1741 failed at "Apply database migrations":
``ALTER TABLE panel_provision_codes ADD COLUMN IF NOT EXISTS poll_secret_hash``
→ ``relation "panel_provision_codes" does not exist`` — the live database never
had the table. So 0029 is ``ALTER TABLE IF EXISTS`` in both directions (a no-op
when the table is missing), and the router creates the table on first use
WITH ``poll_secret_hash`` (``_ensure_table``).

Negative controls: drop ``IF EXISTS`` from 0029, or its SQLite table guard, or
the column from the runtime DDL, and a test here goes red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import importlib.util
import io
from pathlib import Path

import aiosqlite
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

import routers.panel_provision as pp

SVC = Path(__file__).resolve().parents[1]
_DUMMY_URL = "postgresql+psycopg2://u:p@localhost/db"


def _render(monkeypatch, fn, rev: str) -> str:
    monkeypatch.setenv("POSTGRES_URL", _DUMMY_URL)
    buf = io.StringIO()
    cfg = Config(output_buffer=buf, stdout=buf)
    cfg.set_main_option("script_location", str(SVC / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _DUMMY_URL)
    fn(cfg, rev, sql=True)
    return buf.getvalue()


def test_postgres_upgrade_is_alter_table_if_exists(monkeypatch):
    sql = _render(monkeypatch, command.upgrade, "0028:0029")
    assert "ALTER TABLE IF EXISTS panel_provision_codes ADD COLUMN IF NOT EXISTS poll_secret_hash" in sql
    assert "ALTER TABLE panel_provision_codes" not in sql  # no bare ALTER left


def test_postgres_downgrade_is_alter_table_if_exists(monkeypatch):
    sql = _render(monkeypatch, command.downgrade, "0029:0028")
    assert "ALTER TABLE IF EXISTS panel_provision_codes DROP COLUMN IF EXISTS poll_secret_hash" in sql


def _migration():
    path = SVC / "alembic/versions/0029_provision_poll_secret.py"
    spec = importlib.util.spec_from_file_location("mig_0029", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(engine, fn_name: str) -> None:
    mod = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(mod, fn_name)()


def _columns(engine) -> set[str]:
    with engine.connect() as conn:
        return {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(panel_provision_codes)")}


def test_sqlite_upgrade_and_downgrade_are_noops_without_the_table():
    engine = sa.create_engine("sqlite://")
    _run(engine, "upgrade")      # the deploy failure: must not raise
    _run(engine, "downgrade")
    assert _columns(engine) == set()  # and it did not invent the table


def test_sqlite_upgrade_adds_the_column_when_the_table_exists():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE panel_provision_codes (code TEXT PRIMARY KEY, device_id TEXT NOT NULL,"
            " status TEXT, panel_id TEXT, token TEXT, created_at TEXT, expires_at TEXT NOT NULL,"
            " confirmed_by TEXT)")
    _run(engine, "upgrade")
    assert "poll_secret_hash" in _columns(engine)
    _run(engine, "upgrade")      # rerun-safe
    _run(engine, "downgrade")
    assert "poll_secret_hash" not in _columns(engine)


def test_runtime_ddl_carries_the_poll_secret_column():
    create = pp._TABLE_DDL[0]
    assert create.startswith("CREATE TABLE IF NOT EXISTS panel_provision_codes")
    assert "poll_secret_hash" in create


async def test_router_creates_the_table_on_first_use(monkeypatch, tmp_path):
    """A fresh box with no table: /request works and the table has the column."""
    monkeypatch.setattr(pp, "_TABLE_READY", False)
    pp._rate_limit.clear()
    conn = await aiosqlite.connect(str(tmp_path / "fresh.db"))
    conn.row_factory = aiosqlite.Row
    try:
        started = await pp.provision_request({"device_id": "aa:bb:cc:dd:ee:01"}, request=None, db=conn)
        cols = {r[1] for r in await (await conn.execute("PRAGMA table_info(panel_provision_codes)")).fetchall()}
        assert "poll_secret_hash" in cols
        row = await (await conn.execute(
            "SELECT poll_secret_hash FROM panel_provision_codes WHERE code = ?", (started["code"],))).fetchone()
        assert row["poll_secret_hash"] == pp._hash_secret(started["poll_secret"])
    finally:
        await conn.close()
