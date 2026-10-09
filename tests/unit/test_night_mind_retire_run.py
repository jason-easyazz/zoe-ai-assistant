"""``scripts/maintenance/night_mind_retire_run.py``: retire one night of the night mind by its run id (the night date), counts only, dry run by default.

The real 0040 migration over SQLite, the real ``night_store`` row builders, and a stdlib shim for the ``await db.execute(sql, params)`` the pool's connection offers: no network,
no Postgres, nothing outside ``tmp_path``.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))
import night_store as ns  # noqa: E402


def _load_tool():
    spec = importlib.util.spec_from_file_location("night_mind_retire_run", REPO / "scripts" / "maintenance" / "night_mind_retire_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tool = _load_tool()


class _Cur:
    def __init__(self, cur):
        self._c = cur
        self.rowcount = cur.rowcount

    async def fetchone(self):
        return self._c.fetchone()


class Shim:
    """``await db.execute(sql, params)`` over sqlite3 (the pool's connection has the same shape)."""

    def __init__(self, con):
        self.con = con

    async def execute(self, sql, params=()):
        return _Cur(self.con.execute(sql, tuple(params)))

    async def commit(self):
        self.con.commit()


def _db(tmp_path) -> "tuple[Shim, sqlite3.Connection]":
    spec = importlib.util.spec_from_file_location("m0040", REPO / "services" / "zoe-data" / "alembic" / "versions" / "0040_night_mind.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    stmts: "list[str]" = []

    class Op:
        @staticmethod
        def execute(sql):
            stmts.append(sql)
    mod.op = Op
    mod.upgrade()
    con = sqlite3.connect(tmp_path / "n.db")
    for s in stmts:
        con.execute(s)
    return Shim(con), con


def _seed(con) -> None:
    def obs(i, user, run, state="current"):
        r = ns.obs_row(id=f"no-{i}", user_id=user, thread_id=f"t-{user}-{run}", turn_id=f"x{i}", quote=f"synthetic quote {i}", run_id=run, state=state)
        con.execute(ns._upsert_sql("night_observations", ns.OBS_COLS), tuple(r[c] for c in ns.OBS_COLS))

    def thread(tid, user, opened):
        r = ns.thread_row(id=tid, user_id=user, title="a thread", opened_run=opened, mentions_n=2)
        con.execute(ns._upsert_sql("night_threads", ns.THREAD_COLS), tuple(r[c] for c in ns.THREAD_COLS))

    def run(rid, user, night):
        r = ns.run_row(id=rid, user_id=user, night_date=night)
        con.execute(ns._upsert_sql("night_runs", ns.RUN_COLS), tuple(r[c] for c in ns.RUN_COLS))

    obs(1, "alice", "2026-10-10")
    obs(2, "alice", "2026-10-10", state="held")
    obs(3, "bob", "2026-10-10")
    obs(4, "alice", "2026-10-09")                      # an earlier night: must never be touched
    thread("t-alice-2026-10-10", "alice", "2026-10-10")
    thread("t-bob-2026-10-10", "bob", "2026-10-10")
    thread("t-alice-2026-10-09", "alice", "2026-10-09")
    run("nr-a", "alice", "2026-10-10")
    run("nr-b", "bob", "2026-10-10")
    run("nr-c", "alice", "2026-10-09")
    con.commit()


def _states(con):
    return (con.execute("select id, state from night_observations order by id").fetchall(),
            con.execute("select id, status from night_threads order by id").fetchall(),
            con.execute("select id, status from night_runs order by id").fetchall())


def test_a_dry_run_counts_and_changes_nothing(tmp_path):
    db, con = _db(tmp_path)
    _seed(con)
    before = _states(con)
    out = asyncio.run(tool.retire(db, "2026-10-10"))
    assert out == {"night_date": "2026-10-10", "applied": False, "observations": 3, "threads": 2, "runs": 2}
    assert _states(con) == before


def test_apply_retracts_exactly_that_night_and_keeps_the_rows(tmp_path):
    db, con = _db(tmp_path)
    _seed(con)
    out = asyncio.run(tool.retire(db, "2026-10-10", apply=True, now=1000.0))
    assert out["applied"] is True and (out["observations"], out["threads"], out["runs"]) == (3, 2, 2)
    obs, thr, runs = _states(con)
    assert obs == [("no-1", "retracted"), ("no-2", "retracted"), ("no-3", "retracted"), ("no-4", "current")]            # the earlier night is untouched; nothing is deleted
    assert thr == [("t-alice-2026-10-09", "open"), ("t-alice-2026-10-10", "retracted"), ("t-bob-2026-10-10", "retracted")]
    assert runs == [("nr-a", "retracted"), ("nr-b", "retracted"), ("nr-c", "ok")]
    assert con.execute("select valid_to from night_observations where id='no-1'").fetchone() == (1000.0,)
    assert con.execute("select raise_policy, leave_reason from night_threads where id='t-bob-2026-10-10'").fetchone() == ("leave", "retracted")      # never raised again
    assert asyncio.run(tool.retire(db, "2026-10-10", apply=True))["observations"] == 0                                    # idempotent


def test_one_member_only(tmp_path):
    db, con = _db(tmp_path)
    _seed(con)
    out = asyncio.run(tool.retire(db, "2026-10-10", user_id="bob", apply=True))
    assert (out["observations"], out["threads"], out["runs"]) == (1, 1, 1)
    assert dict(_states(con)[0])["no-1"] == "current" and dict(_states(con)[0])["no-3"] == "retracted"


def test_a_retracted_night_is_not_served_by_the_readers_snapshot(tmp_path):
    """The point of the tool: ``night_mind.snapshot`` (what every reader uses) serves only ``state = current`` observations, and a thread with none is not served."""
    import night_mind as nm
    db, con = _db(tmp_path)
    _seed(con)

    def mirror() -> "ns.MemoryBackend":
        be = ns.MemoryBackend()

        async def fill():
            for r in con.execute(f"select {', '.join(ns.OBS_COLS)} from night_observations").fetchall():
                await be.put_observation(dict(zip(ns.OBS_COLS, r)))
            for r in con.execute(f"select {', '.join(ns.THREAD_COLS)} from night_threads").fetchall():
                await be.put_thread(dict(zip(ns.THREAD_COLS, r)))
        asyncio.run(fill())
        return be

    async def served(be):
        ns.set_backend(be)
        nm.invalidate()
        try:
            threads, obs = await nm.snapshot("alice")
            return sorted(o["id"] for o in obs)
        finally:
            ns.set_backend(None)
            nm.invalidate()

    assert asyncio.run(served(mirror())) == ["no-1", "no-4"]            # before: the night is served (no-2 is held, never served)
    asyncio.run(tool.retire(db, "2026-10-10", apply=True))
    assert asyncio.run(served(mirror())) == ["no-4"]                     # after: only the earlier night is left


@pytest.mark.parametrize("bad", ["", "10-10-2026", "2026-13-40", "yesterday", "2026-10-10'; DROP TABLE night_runs; --"])
def test_a_bad_date_is_refused_before_any_sql(tmp_path, bad):
    db, con = _db(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(tool.retire(db, bad, apply=True))
    assert con.execute("select count(*) from night_runs").fetchone() == (0,)


def test_the_cli_refuses_a_bad_date_with_exit_2_and_prints_no_household_text():
    r = subprocess.run([sys.executable, str(REPO / "scripts/maintenance/night_mind_retire_run.py"), "--night-date", "nope"], capture_output=True, text=True, timeout=60)
    assert r.returncode in (1, 2) and r.stdout == ""                                                  # 2 = refused; 1 only if the pool cannot even be imported on this runner
