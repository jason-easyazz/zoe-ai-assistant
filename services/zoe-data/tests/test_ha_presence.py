"""HA presence reader (flag ZOE_HA_PRESENCE): observed rows only, allow-listed, debounced, capped, flag-dark."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
from pathlib import Path

import aiosqlite
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_db  # noqa: E402
from panel_principal import require_member  # noqa: E402
from presence import ha_presence as hp  # noqa: E402

pytestmark = pytest.mark.ci_safe
MIG = ROOT / "alembic/versions/0064_presence_observations.py"


def _mig():
    spec = importlib.util.spec_from_file_location("mig_0064", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _migrate(path, fn="upgrade"):
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(_mig(), fn)()
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("ZOE_HA_PRESENCE", "shadow")
    hp.reset_state()
    yield
    hp.reset_state()


@pytest.fixture
def dbfile(tmp_path):
    p = str(tmp_path / "z.db")
    _migrate(p)
    return p


def _fake(payload):
    async def fetch(domain):
        return [e for e in payload if e["entity_id"].startswith(domain + ".")] if payload is not None else []
    return fetch


async def _run(path, payload, now):
    async with aiosqlite.connect(path) as db:
        n = await hp.poll_once(db, fetch=_fake(payload), now=now)
    return n


async def _rows(path):
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT entity_key, state, source, ttl_s, raw FROM presence_observations ORDER BY id") as c:
            return await c.fetchall()


def test_poll_writes_hashed_observed_rows(dbfile):
    payload = [
        {"entity_id": "person.alex", "state": "home", "attributes": {"latitude": 1.0}},
        {"entity_id": "device_tracker.phone", "state": "not_home"},
        {"entity_id": "device_tracker.tv", "state": "unavailable"},
    ]
    assert asyncio.run(_run(dbfile, payload, 1000.0)) == 3
    rows = asyncio.run(_rows(dbfile))
    got = {r[0]: r[1] for r in rows}
    assert got[hashlib.sha256(b"person.alex").hexdigest()] == "home"
    assert got[hashlib.sha256(b"device_tracker.phone").hexdigest()] == "away"
    assert got[hashlib.sha256(b"device_tracker.tv").hexdigest()] == "unknown"
    assert all(r[2] == "ha_bridge" and r[3] == hp.TTL_S and r[4] is None for r in rows)
    assert not any("alex" in str(c) for r in rows for c in r)
    assert asyncio.run(_run(dbfile, payload, 1060.0)) == 0  # unchanged: no new rows


def test_allow_list_drops_other_domains(dbfile):
    payload = [{"entity_id": "light.kitchen", "state": "on"}, {"entity_id": "lock.door", "state": "locked"}]

    async def fetch(domain):  # a misbehaving bridge ignoring the domain filter
        return payload

    async def go():
        async with aiosqlite.connect(dbfile) as db:
            return await hp.poll_once(db, fetch=fetch, now=1.0)

    assert asyncio.run(go()) == 0
    assert asyncio.run(_rows(dbfile)) == []
    assert hp.ALLOWED_DOMAINS == ("person", "device_tracker")


def test_debounce_defers_a_flap_then_writes(dbfile):
    ent = {"entity_id": "person.a", "state": "home"}
    asyncio.run(_run(dbfile, [ent], 1000.0))
    assert asyncio.run(_run(dbfile, [{**ent, "state": "not_home"}], 1030.0)) == 0  # inside the debounce window
    assert asyncio.run(_run(dbfile, [{**ent, "state": "not_home"}], 1000.0 + hp.DEBOUNCE_S + 1)) == 1
    assert [r[1] for r in asyncio.run(_rows(dbfile))] == ["home", "away"]


def test_cap_writes_one_storm_row(dbfile):
    payload = [{"entity_id": f"device_tracker.d{i}", "state": "home"} for i in range(hp.MAX_ROWS_PER_MIN + 15)]
    n = asyncio.run(_run(dbfile, payload, 5000.0))
    assert n == hp.MAX_ROWS_PER_MIN + 1
    assert asyncio.run(_run(dbfile, payload, 5010.0)) == 0  # same minute: capped, and the storm row is not repeated
    rows = asyncio.run(_rows(dbfile))
    assert sum(1 for r in rows if r[0] == hp.STORM_KEY) == 1


def test_off_registers_no_job_and_writes_nothing(dbfile, monkeypatch):
    monkeypatch.setenv("ZOE_HA_PRESENCE", "off")
    jobs = []

    class Sched:
        def add_job(self, *a, **k):
            jobs.append(k)

    assert hp.register(Sched()) is False and jobs == []
    assert asyncio.run(_run(dbfile, [{"entity_id": "person.a", "state": "home"}], 1.0)) == 0
    assert asyncio.run(_rows(dbfile)) == []
    monkeypatch.setenv("ZOE_HA_PRESENCE", "shadow")
    assert hp.register(Sched()) is True and jobs[0]["id"] == hp.JOB_ID and jobs[0]["seconds"] == 60


def test_bridge_failure_does_nothing(dbfile):
    async def boom(domain):
        raise RuntimeError("bridge down")

    async def go():
        async with aiosqlite.connect(dbfile) as db:
            return await hp.poll_once(db, fetch=boom, now=1.0)

    assert asyncio.run(go()) == 0 and asyncio.run(_rows(dbfile)) == []


def test_migration_round_trips(tmp_path):
    p = str(tmp_path / "m.db")
    _migrate(p)
    eng = sa.create_engine(f"sqlite:///{p}")
    assert "presence_observations" in sa.inspect(eng).get_table_names()
    eng.dispose()
    _migrate(p, "downgrade")
    eng = sa.create_engine(f"sqlite:///{p}")
    assert "presence_observations" not in sa.inspect(eng).get_table_names()
    eng.dispose()


def _client(dbfile, principal):
    from routers.presence import router

    app = FastAPI()
    app.include_router(router)

    async def _db():
        async with aiosqlite.connect(dbfile) as db:
            yield db

    from auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_route_member_only_and_flag_gated(dbfile, monkeypatch):
    asyncio.run(_run(dbfile, [{"entity_id": "person.a", "state": "home"}], 1.0))
    member = {"user_id": "m1", "role": "member"}
    monkeypatch.setenv("ZOE_HA_PRESENCE", "shadow")
    assert _client(dbfile, member).get("/api/presence/observed").status_code == 404
    monkeypatch.setenv("ZOE_HA_PRESENCE", "on")
    r = _client(dbfile, member).get("/api/presence/observed")
    assert r.status_code == 200
    assert r.json() == {"observed": [{"entity_key": hashlib.sha256(b"person.a").hexdigest(), "state": "home",
                                      "observed_at": r.json()["observed"][0]["observed_at"]}]}
    for bad in ({"user_id": "guest", "role": "guest"}, {"user_id": "p", "role": "member", "panel_id": "x"},
                {"user_id": "d", "role": "member", "device_token_role": "panel"}):
        assert _client(dbfile, bad).get("/api/presence/observed").status_code == 403
