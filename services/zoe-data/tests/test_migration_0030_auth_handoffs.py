"""Migration 0030 (``auth_handoffs``) is create-if-missing and idempotent.

The 0029 lesson: schema comes from Alembic, never request-time DDL, and a
migration must not assume the live database matches the chain. Negative
controls: drop ``IF NOT EXISTS`` from 0030 (the re-run test goes red), or add
DDL to the engine/router (the no-request-time-DDL test goes red).
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


def _mig():
    spec = importlib.util.spec_from_file_location("mig_0030m", SVC / "alembic/versions/0030_auth_handoffs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(engine, fn_name):
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(_mig(), fn_name)()


def test_postgres_render_is_create_if_missing(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", _DUMMY_URL)
    buf = io.StringIO()
    cfg = Config(output_buffer=buf, stdout=buf)
    cfg.set_main_option("script_location", str(SVC / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _DUMMY_URL)
    command.upgrade(cfg, "0029:0030", sql=True)
    sql = buf.getvalue()
    assert "CREATE TABLE IF NOT EXISTS auth_handoffs" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_auth_handoffs_ref ON auth_handoffs (kind, ref)" in sql


def test_sqlite_upgrade_twice_then_downgrade():
    engine = sa.create_engine("sqlite://")
    _run(engine, "upgrade")
    _run(engine, "upgrade")  # re-run / table already present: a no-op
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(auth_handoffs)")}
    assert {"id", "kind", "ref", "user_id", "panel_id", "status", "detail", "reason", "expires_at"} <= cols
    assert "token" not in cols and "phone_url" not in cols  # the secret is never at rest
    _run(engine, "downgrade")
    with engine.connect() as conn:
        assert not conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name='auth_handoffs'").fetchall()


def test_no_request_time_ddl():
    for rel in ("auth_handoff.py", "routers/handoff.py"):
        src = (SVC / rel).read_text()
        assert "CREATE TABLE" not in src and "CREATE INDEX" not in src, rel
