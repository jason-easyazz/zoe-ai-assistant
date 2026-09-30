"""``ZOE_USER_MODEL_BLOCK``: off / guest / synthetic / service ids give "" and read nothing
(the flag-off pin). With the flag on, the block is the stored user-model CARD:
* byte-identical while every source row is approved;
* re-rendered without a fact the store has since superseded;
* built once when missing;
* the stored card on a store error (fail open);
* never the old narrative portrait.
The nightly dreaming pass and portrait synthesis rebuild it. ``GET /api/memories/user-model``
is TOKEN-only. Invented data, fake DB, fake store.
"""
import asyncio
import datetime
import json
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import memory_digest
import memory_service
import user_model_card as umc
import user_portrait as up
from memory_service import MemoryRef
from routers.memories import router as memories_router

pytestmark = pytest.mark.ci_safe
EMPTY = {"version": "", "text": ""}
UTC = datetime.timezone.utc


def _row(i, text, typ="fact", status="approved"):
    added = (datetime.datetime.now(UTC) - datetime.timedelta(days=1, seconds=i)).isoformat()
    return MemoryRef(id=f"m{i}", text=text, metadata={
        "memory_type": typ, "status": status, "source": "turn_digest", "added_at": added})


@pytest.fixture
def env(monkeypatch):
    """Fake Postgres (users / user_portraits / user_model_cards) + fake memory store."""
    import db_pool

    st = NS(sql=[], name="sam", portrait="Sam is kind. He likes short, direct answers.",
            cards={}, rows=[_row(1, "User is vegetarian."),
                            _row(2, "User is training for a 10k in May.", "event"),
                            _row(3, "User's dog Rex is old.", "pet")],
            get_error=None, gets=[])

    async def execute(sql, params=()):
        st.sql.append(sql)
        one = None
        if "FROM users" in sql:
            one = (st.name,) if st.name is not None else None
        elif "FROM user_portraits" in sql:
            one = (st.portrait,) if st.portrait else None
        elif "FROM user_model_cards" in sql:
            one = st.cards.get(params[0])
        elif sql.lstrip().startswith("INSERT INTO user_model_cards"):
            st.cards[params[0]] = (params[1], params[2], params[3])

        async def fetchone():
            return one
        return NS(fetchone=fetchone)

    async def commit():
        return None

    class _Ctx:
        async def __aenter__(self):
            return NS(execute=execute, commit=commit)

        async def __aexit__(self, *a):
            return False

    class _Svc:
        async def list_by_status(self, *, user_id, status, limit):
            assert status == "approved"
            return [r for r in st.rows if r.metadata["status"] == "approved"]

        async def get(self, mem_id):
            st.gets.append(mem_id)
            if st.get_error:
                raise st.get_error
            return next((r for r in st.rows if r.id == mem_id), None)

    monkeypatch.setattr(db_pool, "get_db_ctx", _Ctx)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _Svc())
    monkeypatch.setenv("ZOE_USER_MODEL_BLOCK", "1")
    monkeypatch.delenv("ZOE_SYNTHETIC_USER_ALLOWLIST", raising=False)
    return st


def _block(uid="sam"):
    return asyncio.run(up.load_user_model_block(uid))


def _inserts(env):
    return sum(s.lstrip().startswith("INSERT INTO user_model_cards") for s in env.sql)


def _expected():
    d = datetime.datetime.now(UTC) - datetime.timedelta(days=1, seconds=2)
    return ("Name: Sam\nDiet: vegetarian\nPeople & pets: dog Rex is old\n"
            f"Current: training for a 10k in May (noted {d.day} {d:%b})\n"
            "How they talk (weekly portrait): He likes short, direct answers.")


@pytest.mark.parametrize("flag,uid", [("", "sam"), ("0", "sam"), ("off", "sam")] + [
    ("1", u) for u in ("", "guest", "Guest", "voice-daemon", "family-admin",
                       "demo_bar_0a1b2c3d", "test-probe", "bench-1")])
def test_off_guest_synthetic_service_are_empty_and_read_nothing(env, monkeypatch, flag, uid):
    monkeypatch.setenv("ZOE_USER_MODEL_BLOCK", flag)
    assert _block(uid) == EMPTY and env.sql == [] and env.gets == []
    assert asyncio.run(umc.rebuild_user_model_card(uid)) == {"status": "disabled"}
    assert env.sql == [] and env.cards == {}


def test_missing_card_is_built_once_then_served_byte_identically(env):
    out = _block()
    assert out["text"] == _expected() and out["version"] == umc.card_version(out["text"])
    assert "Sam is kind" not in out["text"], "never the narrative portrait"
    assert _inserts(env) == 1 and _block() == out
    assert _inserts(env) == 1, "served from the store, not rebuilt"


def test_a_superseded_fact_drops_at_serve_and_forget_empties(env):
    out = _block()
    env.rows[1] = _row(2, "User is training for a 10k in May.", "event", status="superseded")
    later = _block()
    assert "10k" not in later["text"] and "Diet: vegetarian" in later["text"]
    assert later["version"] == umc.card_version(later["text"]) != out["version"]
    env.rows = []  # forget: every source row gone
    assert _block() == EMPTY


def test_store_error_serves_the_stored_card(env):
    out = _block()
    env.get_error = RuntimeError("chroma down")
    assert _block() == out


def test_no_facts_no_card_and_no_name_no_name_line(env):
    env.rows = []
    assert _block() == EMPTY and _inserts(env) == 1
    env.cards.clear()
    env.name, env.rows = None, [_row(1, "User is vegetarian.")]
    assert _block()["text"] == ("Diet: vegetarian\n"
                                "How they talk (weekly portrait): He likes short, direct answers.")


def test_portrait_synthesis_rebuilds_the_card_whatever_its_status(env, monkeypatch):
    async def fake_synth(uid, db=None):
        return {"user_id": uid, "status": "too_few_memories"}
    monkeypatch.setattr(up, "_synthesize_portrait", fake_synth)
    res = asyncio.run(up.run_portrait_synthesis("sam"))
    assert res["status"] == "too_few_memories" and res["card"]["status"] == "ok"
    assert json.loads(env.cards["sam"][0])["name"] == "Sam"


@pytest.mark.parametrize("day,key", [(28, "user_model_card"), (27, "portrait")])  # Mon, Sun
def test_dreaming_rebuilds_the_card_every_night_exactly_once(env, monkeypatch, day, key):
    async def nop(*a, **k):
        return {}

    async def fake_synth(uid, db=None):
        return {"user_id": uid, "status": "ok"}
    for name in ("_rem_reinforce_pass", "_resolve_pending_person_links", "_extract_open_loops",
                 "_deep_sleep_pass", "_synthesis_pass"):
        monkeypatch.setattr(memory_digest, name, nop)
    monkeypatch.setattr(up, "_synthesize_portrait", fake_synth)
    monkeypatch.delenv("ZOE_MEMORY_LINT_IN_DREAMING", raising=False)

    class _Day(datetime.datetime):
        @classmethod
        def utcnow(cls):
            return datetime.datetime(2026, 9, day, 1, 0)
    monkeypatch.setattr(datetime, "datetime", _Day)
    res = asyncio.run(memory_digest.run_dreaming_cycle("sam", run_agent_sync_phase=False))
    card = res[key]["card"] if key == "portrait" else res[key]
    assert card["status"] == "ok" and "sam" in env.cards
    assert _inserts(env) == 1, "Sunday: the portrait's rebuild is not repeated"


def test_route_is_internal_token_only(env, monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok-um")
    app = FastAPI()
    app.include_router(memories_router)
    c = TestClient(app, client=("127.0.0.1", 50000))
    url, ok = "/api/memories/user-model?user_id=sam", {"X-Internal-Token": "tok-um"}
    assert c.get(url).status_code == 401, "loopback alone is not enough"
    assert c.get(url, headers={"X-Internal-Token": "nope"}).status_code == 403
    assert env.sql == []
    r = c.get(url, headers=ok)
    assert r.status_code == 200 and r.json() == _block()
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "")
    assert c.get(url, headers=ok).status_code == 403
