"""``ZOE_USER_MODEL_BLOCK``: off / guest / synthetic / service ids give "" and read
nothing; name line + flattened, PII-scrubbed portrait cut to 1,400 chars on a sentence;
content-hash version; ``GET /api/memories/user-model`` is TOKEN-only. Invented data."""
import asyncio
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import user_portrait as up
from routers.memories import router as memories_router

pytestmark = pytest.mark.ci_safe
EMPTY = {"version": "", "text": ""}
LONG = " ".join(["Sam trains for a half marathon and runs by the river most days."] * 30)


def _block(uid="sam"):
    return asyncio.run(up.load_user_model_block(uid))


@pytest.fixture
def db(monkeypatch):
    """Fake db_pool.get_db_ctx: one users row + one portrait row, SQL logged."""
    import db_pool

    st = {"name": "sam", "portrait": "Sam is warm and direct.", "sql": []}

    async def execute(sql, params=()):
        st["sql"].append(sql)
        value = st["name"] if "FROM users" in sql else st["portrait"]

        async def fetchone():
            return (value,) if value is not None else None
        return NS(fetchone=fetchone)

    class _Ctx:
        async def __aenter__(self):
            return NS(execute=execute)

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(db_pool, "get_db_ctx", _Ctx)
    monkeypatch.setenv("ZOE_USER_MODEL_BLOCK", "1")
    monkeypatch.delenv("ZOE_SYNTHETIC_USER_ALLOWLIST", raising=False)
    return st


@pytest.mark.parametrize("flag,uid", [("", "sam"), ("0", "sam"), ("off", "sam")] + [
    ("1", u) for u in ("", "guest", "Guest", "voice-daemon", "family-admin",
                       "demo_bar_0a1b2c3d", "test-probe", "bench-1")])
def test_off_guest_synthetic_service_are_empty_and_read_nothing(db, monkeypatch, flag, uid):
    monkeypatch.setenv("ZOE_USER_MODEL_BLOCK", flag)
    assert _block(uid) == EMPTY and db["sql"] == []


def test_name_line_portrait_cap_and_version(db):
    out = _block()
    assert out["text"] == "You are speaking with Sam (the signed-in user).\nSam is warm and direct."
    assert len(out["version"]) == 16 and _block() == out
    db["portrait"] = LONG
    text = _block()["text"]
    assert up.USER_MODEL_MAX_CHARS // 2 < len(text) <= up.USER_MODEL_MAX_CHARS
    assert text.endswith("most days.") and text.count("\n") == 1  # sentence cut, flattened
    assert _block()["version"] != out["version"]
    words = up.compose_user_model_text("", "word " * 600)
    assert len(words) <= up.USER_MODEL_MAX_CHARS and words.endswith("word…")
    assert asyncio.run(up.load_portrait("sam", max_chars=0)) == LONG
    assert len(asyncio.run(up.load_portrait("sam"))) <= up.PORTRAIT_MAX_INJECT_CHARS + 1  # unchanged
    db["portrait"] = None
    assert _block()["text"] == "You are speaking with Sam (the signed-in user)."
    db["name"] = None
    assert _block() == EMPTY


def test_portrait_is_pii_scrubbed(db):
    db["portrait"] = "Sam keeps notes.\n\nHis wifi password is hunter2\tok."
    text = _block()["text"]
    assert "hunter2" not in text and "[REDACTED]" in text and text.count("\n") == 1
    db["portrait"] = "Sam's card is 4111 1111 1111 1111."
    assert _block()["text"] == "You are speaking with Sam (the signed-in user)."


def test_route_is_internal_token_only(db, monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok-um")
    app = FastAPI()
    app.include_router(memories_router)
    c = TestClient(app, client=("127.0.0.1", 50000))
    url, ok = "/api/memories/user-model?user_id=sam", {"X-Internal-Token": "tok-um"}
    assert c.get(url).status_code == 401, "loopback alone is not enough"
    assert c.get(url, headers={"X-Internal-Token": "nope"}).status_code == 403
    assert db["sql"] == []
    r = c.get(url, headers=ok)
    assert r.status_code == 200 and r.json() == _block()
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "")
    assert c.get(url, headers=ok).status_code == 403
