"""Migration 0033 (``user_model_cards``) is create-if-missing, idempotent, and the only DDL.

Negative controls: drop ``IF NOT EXISTS`` from 0033 (the re-run test goes red), or add DDL
to ``user_model_card.py`` (the no-request-time-DDL test goes red).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]


def _run(engine, fn_name):
    spec = importlib.util.spec_from_file_location(
        "mig_0033", SVC / "alembic/versions/0033_user_model_cards.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (mod.revision, mod.down_revision) == ("0033", "0032")
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(mod, fn_name)()


def test_sqlite_upgrade_twice_then_downgrade():
    engine = sa.create_engine("sqlite://")
    _run(engine, "upgrade")
    _run(engine, "upgrade")  # already present: a no-op
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(user_model_cards)")}
    assert cols == {"user_id", "card_json", "card_text", "version", "built_at", "source_count"}
    _run(engine, "downgrade")
    with engine.connect() as conn:
        assert not conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name='user_model_cards'").fetchall()


def test_no_request_time_ddl():
    for rel in ("user_model_card.py", "user_portrait.py"):
        src = (SVC / rel).read_text()
        assert "CREATE TABLE" not in src and "ALTER TABLE" not in src, rel
