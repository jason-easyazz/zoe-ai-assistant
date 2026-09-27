"""Migration 0029 works on a database with NO ``panel_provision_codes`` table.

The 2026-09-27 deploy of #1741 failed at "Apply database migrations":
``ALTER TABLE panel_provision_codes ADD COLUMN IF NOT EXISTS poll_secret_hash``
→ ``relation "panel_provision_codes" does not exist`` — the live database never
had the table (0005 did not land there). Schema comes from Alembic, never from
request-time DDL (.cursor/rules/db-safety.mdc), so 0029 re-asserts 0005's table
and indexes with ``IF NOT EXISTS`` and then adds the column; its downgrade drops
only the column.

Negative controls: drop the CREATE from 0029 (the empty-DB test goes red), or
make the downgrade drop the table (the downgrade tests go red), or put
request-time DDL back in the router (the router test goes red).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import importlib.util
import io
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

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


def test_postgres_upgrade_creates_the_table_then_adds_the_column(monkeypatch):
    sql = _render(monkeypatch, command.upgrade, "0028:0029")
    create = sql.index("CREATE TABLE IF NOT EXISTS panel_provision_codes")
    alter = sql.index("ALTER TABLE IF EXISTS panel_provision_codes ADD COLUMN IF NOT EXISTS poll_secret_hash")
    assert create < alter
    for idx in ("idx_provision_codes_device", "idx_provision_codes_status", "idx_provision_codes_expires"):
        assert f"CREATE INDEX IF NOT EXISTS {idx}" in sql
    # 0005's created_at default survives verbatim.
    assert "created_at      TEXT NOT NULL DEFAULT (to_char(timezone('UTC', now())" in sql


def test_postgres_downgrade_drops_only_the_column(monkeypatch):
    sql = _render(monkeypatch, command.downgrade, "0029:0028")
    assert "ALTER TABLE IF EXISTS panel_provision_codes DROP COLUMN IF EXISTS poll_secret_hash" in sql
    assert "DROP TABLE" not in sql


def test_0029_table_ddl_is_0005_verbatim():
    a = (SVC / "alembic/versions/0005_panel_provisioning.py").read_text()
    b = (SVC / "alembic/versions/0029_provision_poll_secret.py").read_text()
    i = a.index("CREATE TABLE IF NOT EXISTS panel_provision_codes")
    assert a[i:a.index("        )\n", i)] in b


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


def _indexes(engine) -> set[str]:
    with engine.connect() as conn:
        return {r[1] for r in conn.exec_driver_sql("PRAGMA index_list(panel_provision_codes)")}


def test_sqlite_upgrade_on_an_empty_db_creates_the_table_with_the_column():
    engine = sa.create_engine("sqlite://")
    _run(engine, "upgrade")      # the deploy failure: must create, not raise
    cols = _columns(engine)
    assert {"code", "device_id", "status", "token", "expires_at", "poll_secret_hash"} <= cols
    assert {"idx_provision_codes_device", "idx_provision_codes_status",
            "idx_provision_codes_expires"} <= _indexes(engine)
    _run(engine, "upgrade")      # re-running is a no-op
    assert _columns(engine) == cols


def test_sqlite_upgrade_on_a_0005_table_adds_only_the_column():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(_migration()._TABLE_DDL)
        conn.exec_driver_sql("INSERT INTO panel_provision_codes (code, device_id, created_at, expires_at)"
                             " VALUES ('ABC234', 'dev', 'x', 'y')")
    _run(engine, "upgrade")
    assert "poll_secret_hash" in _columns(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM panel_provision_codes").scalar() == 1


def test_sqlite_downgrade_drops_only_the_column_never_the_table():
    engine = sa.create_engine("sqlite://")
    _run(engine, "upgrade")
    _run(engine, "downgrade")
    cols = _columns(engine)
    assert cols and "poll_secret_hash" not in cols


def test_router_does_no_request_time_ddl():
    src = (SVC / "routers/panel_provision.py").read_text()
    assert "CREATE TABLE" not in src and "CREATE INDEX" not in src
    assert "_ensure_table" not in src
