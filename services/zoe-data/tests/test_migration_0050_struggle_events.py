"""Migration 0050 (``struggle_events``): create-if-missing, rerun-safe, round-trips, the only DDL for the table, and a row
the detector's SQL backend writes reads back through the same backend.

Negative controls (run by hand when this was written): drop ``IF NOT EXISTS`` from 0050 -> the rerun test goes red; add a
``CREATE TABLE`` to ``struggle_detector.py`` -> the no-request-time-DDL test goes red.
"""
from __future__ import annotations

import importlib.util
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
MIG = SVC / "alembic/versions/0050_struggle_events.py"
COLUMNS = {"id", "ts", "user_id", "is_guest", "channel", "panel_id", "session_id", "signal", "confidence", "fix_type",
           "prev_turn_id", "turn_id", "domain_predicted", "domain_served", "path_served", "latency_ms", "stt_ms", "pattern",
           "summary", "detail", "mode"}


def _mod():
    spec = importlib.util.spec_from_file_location("mig_0050", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(engine, fn_name):
    mod = _mod()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(mod, fn_name)()


def test_revision_chain_is_linear_and_this_is_the_head():
    mod = _mod()
    assert mod.revision == "0050"
    revs, downs = set(), set()
    for f in (SVC / "alembic/versions").glob("*.py"):
        src = f.read_text()
        r = re.search(r'^revision = "(\w+)"', src, re.M)
        d = re.search(r'^down_revision = "(\w+)"', src, re.M)
        if r:
            revs.add(r.group(1))
        if d:
            downs.add(d.group(1))
    assert mod.down_revision in revs, "0050 must chain onto an existing revision"
    heads = revs - downs
    assert len(heads) == 1, f"more than one head: {sorted(heads)}"
    # 0050 was the head when it landed; #2024's 0051 (speaker model_id) now chains onto it. Either way it is on the one chain.
    assert "0050" in heads or "0050" in downs


def test_sqlite_upgrade_twice_then_downgrade():
    engine = sa.create_engine("sqlite://")
    _run(engine, "upgrade")
    _run(engine, "upgrade")  # already present: a no-op
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(struggle_events)")}
        idx = {r[1] for r in conn.exec_driver_sql("PRAGMA index_list(struggle_events)")}
    assert cols == COLUMNS
    assert {"idx_struggle_events_user_ts", "idx_struggle_events_signal_ts", "idx_struggle_events_ts"} <= idx
    _run(engine, "downgrade")
    _run(engine, "downgrade")  # already gone: a no-op
    with engine.connect() as conn:
        assert not conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='struggle_events'").fetchall()


def test_no_request_time_ddl():
    for rel in ("struggle_detector.py", "struggle_signals.py", "struggle_digest.py", "routers/struggles.py"):
        src = (SVC / rel).read_text()
        assert "CREATE TABLE" not in src and "ALTER TABLE" not in src and "DROP TABLE" not in src, rel


class _Cur:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount

    async def fetchall(self):
        return self._cur.fetchall()


class _Db:
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, sql, params=()):
        return _Cur(self.conn.execute(sql, params))

    async def commit(self):
        self.conn.commit()


async def test_the_sql_backend_round_trips_on_the_migrated_table(tmp_path, monkeypatch):
    import struggle_detector as sd

    path = tmp_path / "s.db"
    engine = sa.create_engine(f"sqlite:///{path}")
    _run(engine, "upgrade")
    conn = sqlite3.connect(path)

    @asynccontextmanager
    async def ctx():
        yield _Db(conn)

    be = sd.SqlBackend()
    monkeypatch.setattr(sd.SqlBackend, "_ctx", staticmethod(ctx))
    row = {c: "" for c in COLUMNS}
    row.update(id="r1", ts=100.0, user_id="member-a", is_guest=0, signal="RE_ASK", confidence=0.9, latency_ms=None,
               stt_ms=None, summary="what is the capital of chad -> what is the capital of chad", detail="{}")
    assert await be.add([row, {**row, "id": "r2", "user_id": "member-b", "ts": 50.0}]) == 2
    got = await be.rows(0.0, ["member-a"])
    assert [r["id"] for r in got] == ["r1"] and got[0]["summary"].startswith("what is")
    assert [r[0] for r in await be.matching("member-a", "CHAD")] == ["r1"]
    assert await be.purge_before(60.0) == 1
    assert await be.delete_ids("member-a", ["r1"]) == 1
    assert await be.delete_user("member-a") == 0
    conn.close()
