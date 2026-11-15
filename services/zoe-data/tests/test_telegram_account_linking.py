"""Tests for Telegram account linking (feat/telegram-account-linking).

Covers three pieces:
  1. Linking by proof of control of both sides: a signed link token minted for the
     signed-in member and redeemed from the Telegram account (routers/user_profile.py,
     routers/system.py, telegram_link.py); explicit, recorded unlinking; the member erase.
  2. The internal resolver telegram_id → user_id (routers/system.py).
  3. The /api/chat TRUSTED forwarded-user override security boundary
     (auth.resolve_acting_user): internal caller may set X-Zoe-User-Id;
     a public caller may NOT (no impersonation).

The user_preferences table is modelled by a tiny in-memory fake that understands
exactly the SQL these routers issue (SELECT prefs by user_id, INSERT ... ON
CONFLICT, key removal, the `prefs::jsonb ->> 'telegram_id'` match, and the
account lookups on `users` / `auth_users`), plus what alembic 0084 adds: the
redemption rows, the one-holder unique index, the member link lock, the erase
watermark read, and transactions that roll back. THE FAKE ENFORCES THE UNIQUE
INDEX, THE PRIMARY KEY AND THE LOCK ITSELF: weaker evidence than a real
PostgreSQL (it refuses a conflicting write at once, where PostgreSQL waits for
the other transaction), so the race tests here prove the code's handling of a
refusal, and the database guarantee is the migration's.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import asyncio
import contextlib
import contextvars
import importlib
import json
import sys
import weakref
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth
from routers import system, user_profile


_MISSING = object()


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self._rows = rows or []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class FakeUniqueViolation(Exception):
    """What asyncpg raises for the one-holder index (sqlstate 23505)."""

    sqlstate = "23505"
    constraint_name = "uq_user_preferences_telegram_id"


#: the open fake transaction of the current task: {"undo": [...], "locks": [...]}, or None (autocommit)
_TXN: contextvars.ContextVar = contextvars.ContextVar("fake_txn", default=None)


class FakePrefsDB:
    """In-memory user_preferences store keyed by user_id → prefs dict."""

    def __init__(self, seed: dict[str, dict] | None = None, accounts: dict[str, int] | None = None):
        self.redemptions: dict[str, tuple[str, float]] = {}    # telegram_link_redemptions: token_id -> (user_id, expires_at)
        self.watermarks: dict[str, str] = {}                    # memory_forgotten erase watermark: user_id -> forgotten_at
        self._locks: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
        # user_id → (prefs_json_str, updated_at_counter)
        self.store: dict[str, tuple[str, int]] = {}
        # auth_users.is_active per registered account; every seeded member is an active account by default
        self.accounts: dict[str, int] = dict(accounts) if accounts is not None else {uid: 1 for uid in (seed or {})}
        self._clock = 0
        for uid, prefs in (seed or {}).items():
            self._clock += 1
            self.store[uid] = (json.dumps(prefs), self._clock)
        self.commits = 0

    # -- transactions (undo log, rolled back on an exception) and the member link lock --------------------------------
    def _write(self, table: dict, key, value) -> None:
        txn = _TXN.get()
        if txn is not None:
            txn["undo"].append((table, key, table.get(key, _MISSING)))
        if value is _MISSING:
            table.pop(key, None)
        else:
            table[key] = value

    @contextlib.asynccontextmanager
    async def transaction(self):
        txn = {"undo": [], "locks": []}
        token = _TXN.set(txn)
        try:
            yield
        except BaseException:
            for table, key, old in reversed(txn["undo"]):
                if old is _MISSING:
                    table.pop(key, None)
                else:
                    table[key] = old
            raise
        finally:
            _TXN.reset(token)
            for lock in txn["locks"]:
                lock.release()

    def _held_by_another(self, uid: str, tid) -> bool:
        if not tid:
            return False
        for other, (prefs_str, _ts) in self.store.items():
            if other != uid and json.loads(prefs_str).get("telegram_id") == tid:
                return True
        return False

    async def execute(self, sql: str, params=()):
        await asyncio.sleep(0)      # every statement is a suspension point, as on a real connection
        p = list(params or [])
        norm = " ".join(sql.split())

        if norm.startswith("SELECT pg_advisory_xact_lock("):
            txn = _TXN.get()
            assert txn is not None, "a transaction-scoped lock outside a transaction"
            table = self._locks.setdefault(asyncio.get_running_loop(), {})
            lock = table.setdefault(p[0], asyncio.Lock())
            await lock.acquire()
            txn["locks"].append(lock)
            return _Cursor([{"pg_advisory_xact_lock": None}])

        if norm.startswith("SELECT forgotten_at FROM memory_forgotten WHERE user_id ="):
            at = self.watermarks.get(p[0])
            return _Cursor([{"forgotten_at": at}] if at else [])

        if norm.startswith("INSERT INTO memory_forgotten"):     # erase_watermark.SqlBackend.put: (user_id, key, scope, actor, at, never)
            self._write(self.watermarks, p[0], max(p[4], self.watermarks.get(p[0], "")))
            return _Cursor()

        if norm.startswith("DELETE FROM telegram_link_redemptions WHERE expires_at <"):
            for tok in [t for t, (_u, exp) in self.redemptions.items() if exp < p[0]]:
                self._write(self.redemptions, tok, _MISSING)
            return _Cursor()

        if norm.startswith("DELETE FROM telegram_link_redemptions WHERE user_id ="):
            for tok in [t for t, (u, _e) in self.redemptions.items() if u == p[0]]:
                self._write(self.redemptions, tok, _MISSING)
            return _Cursor()

        if norm.startswith("INSERT INTO telegram_link_redemptions"):    # ON CONFLICT (token_id) DO NOTHING RETURNING token_id
            if p[0] in self.redemptions:
                return _Cursor([])
            self._write(self.redemptions, p[0], (p[1], p[2]))
            return _Cursor([{"token_id": p[0]}])

        if norm.startswith("SELECT prefs FROM user_preferences WHERE user_id ="):
            uid = p[0]
            entry = self.store.get(uid)
            return _Cursor([{"prefs": entry[0]}] if entry else [])

        if norm.startswith("SELECT user_id, prefs FROM user_preferences WHERE prefs::jsonb ->> 'telegram_id' ="):
            tid = p[0]
            exclude = p[1] if "user_id != ?" in norm else None
            matches = []
            for uid, (prefs_str, ts) in self.store.items():
                if exclude is not None and uid == exclude:
                    continue
                try:
                    prefs = json.loads(prefs_str)
                except (json.JSONDecodeError, TypeError):
                    continue
                if prefs.get("telegram_id") == tid:
                    matches.append((uid, prefs_str, ts))
            # ORDER BY updated_at ASC (deterministic); LIMIT 1 when no exclude.
            matches.sort(key=lambda m: m[2])
            rows = [{"user_id": u, "prefs": pr} for (u, pr, _ts) in matches]
            if "LIMIT 1" in norm:
                rows = rows[:1]
            return _Cursor(rows)

        if norm.startswith("INSERT INTO user_preferences"):
            uid, prefs_json = p[0], p[1]
            self._clock += 1
            if "::jsonb ||" in norm and uid in self.store:
                stored = json.loads(self.store[uid][0])
                if "RETURNING user_id" in norm and stored.get(p[2]) not in (None, "", p[3]):
                    return _Cursor([])          # user_prefs.set_prefs(unless_held=...): the conflict branch's WHERE refused
                # user_prefs.set_pref: atomic single-key merge (jsonb ||).
                stored.update(json.loads(prefs_json))
                prefs_json = json.dumps(stored)
            if self._held_by_another(uid, json.loads(prefs_json).get("telegram_id")):
                raise FakeUniqueViolation("duplicate key value violates unique constraint")   # alembic 0084's index
            self._write(self.store, uid, (prefs_json, self._clock))
            return _Cursor([{"user_id": uid}] if "RETURNING user_id" in norm else [])

        if norm.startswith("UPDATE user_preferences SET prefs = (COALESCE(prefs, '{}')::jsonb -"):
            # user_prefs.delete_prefs: atomic removal of n keys, optionally only while the first key still holds
            # the expected value (params: key1..keyn, uid[, key1, value]).
            n = norm.count("- ?::text")
            keys, uid, rest = p[:n], p[n], p[n + 1:]
            entry = self.store.get(uid)
            if entry is not None:
                prefs = json.loads(entry[0])
                if not rest or prefs.get(rest[0]) == rest[1]:
                    for key in keys:
                        prefs.pop(key, None)
                    self._clock += 1
                    self._write(self.store, uid, (json.dumps(prefs), self._clock))
            return _Cursor()

        if norm.startswith("SELECT is_active FROM auth_users WHERE user_id ="):
            uid = p[0]
            return _Cursor([{"is_active": self.accounts[uid]}] if uid in self.accounts else [])

        if norm.startswith("SELECT 1 FROM users WHERE id ="):
            return _Cursor([{"1": 1}] if p[0] in self.accounts or p[0] in self.store else [])

        raise AssertionError(f"unexpected SQL: {norm}")

    async def commit(self):
        self.commits += 1


def _profile_client(db: FakePrefsDB, *, role: str = "user", user_id: str = "member-a") -> TestClient:
    app = FastAPI()
    app.include_router(user_profile.router)

    async def fake_get_db():
        yield db

    app.dependency_overrides[user_profile.get_current_user] = lambda: {
        "user_id": user_id,
        "role": role,
    }
    app.dependency_overrides[user_profile.get_db] = fake_get_db
    return TestClient(app)


def _system_client(db: FakePrefsDB) -> TestClient:
    app = FastAPI()
    app.include_router(system.router)

    async def fake_get_db():
        yield db

    app.dependency_overrides[system.get_db] = fake_get_db
    return TestClient(app)


# ─── 1. Linking by proof of control of both sides ─────────────────────────────


def _prefs(db: FakePrefsDB, uid: str) -> dict:
    return json.loads(db.store[uid][0]) if uid in db.store else {}


def _redeem(db: FakePrefsDB, monkeypatch, token: str, tid: str, username: str | None = None):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    body = {"token": token, "telegram_id": tid}
    if username:
        body["telegram_username"] = username
    return _system_client(db).post("/api/system/telegram/consume-link-token", json=body, headers={"X-Internal-Token": "tok"})


def test_profile_get_when_unlinked_is_null():
    db = FakePrefsDB()
    r = _profile_client(db).get("/api/user/profile/telegram")
    assert r.status_code == 200
    assert r.json() == {"telegram_id": None, "proof": None, "last_change": None}


def test_a_value_typed_onto_a_profile_never_links_an_account():
    db = FakePrefsDB(seed={"member-b": {"telegram_id": "99999"}, "member-a": {"theme": "dark"}})
    for value in ("99999", "12345", 12345, "not-a-number"):
        r = _profile_client(db).put("/api/user/profile/telegram", json={"telegram_id": value})
        assert r.status_code == 400, (value, r.text)
    assert _prefs(db, "member-a") == {"theme": "dark"}
    assert _prefs(db, "member-b") == {"telegram_id": "99999"}


def test_a_redeemed_link_token_links_the_redeeming_account_with_its_proof(monkeypatch):
    import telegram_link

    db = FakePrefsDB(seed={"member-a": {"theme": "dark"}})
    token = telegram_link.make_link_token("member-a")
    r = _redeem(db, monkeypatch, token, "55501", "@synthetic_a")
    assert r.status_code == 200, r.text
    stored = _prefs(db, "member-a")
    assert stored["telegram_id"] == "55501" and stored["theme"] == "dark"
    assert stored["telegram_link_proof"]["via"] == "signed_link"
    assert stored["telegram_link_proof"]["username"] == "synthetic_a"
    got = _profile_client(db).get("/api/user/profile/telegram").json()
    assert got["telegram_id"] == "55501" and got["proof"]["username"] == "synthetic_a"
    # single use: the same token cannot be redeemed twice
    assert _redeem(db, monkeypatch, token, "55501").status_code == 400


def test_a_link_never_displaces_another_members_link(monkeypatch):
    import telegram_link

    # its own member: a token is a pure function of (member, expiry second), so reusing another test's member could
    # mint that test's already-spent token
    db = FakePrefsDB(seed={"member-b": {"telegram_id": "55502"}, "member-c": {}})
    token = telegram_link.make_link_token("member-c")
    r = _redeem(db, monkeypatch, token, "55502")
    assert r.status_code == 409, r.text
    assert _prefs(db, "member-b") == {"telegram_id": "55502"}
    assert "telegram_id" not in _prefs(db, "member-c")
    # the refused token was released, not burned: after member-b unlinks, member-c can redeem it
    _profile_client(db, user_id="member-b").delete("/api/user/profile/telegram")
    assert _redeem(db, monkeypatch, token, "55502").status_code == 200
    assert _prefs(db, "member-c")["telegram_id"] == "55502"


def test_a_member_unlinks_their_own_link_and_the_unlink_is_recorded():
    db = FakePrefsDB(seed={"member-a": {"telegram_id": "55503", "telegram_link_proof": {"via": "signed_link"}, "theme": "dark"}})
    r = _profile_client(db).put("/api/user/profile/telegram", json={"telegram_id": None})
    assert r.status_code == 200
    assert r.json() == {"telegram_id": None, "linked": False}
    stored = _prefs(db, "member-a")
    assert "telegram_id" not in stored and "telegram_link_proof" not in stored and stored["theme"] == "dark"
    assert stored["telegram_link_change"]["event"] == "unlinked" and stored["telegram_link_change"]["by"] == "self"


def test_only_an_admin_unlinks_another_members_link():
    db = FakePrefsDB(seed={"member-b": {"telegram_id": "55504"}})
    r = _profile_client(db, user_id="member-a").delete("/api/user/profile/telegram", params={"user_id": "member-b"})
    assert r.status_code == 403
    assert _prefs(db, "member-b")["telegram_id"] == "55504"
    r = _profile_client(db, role="admin", user_id="admin-a").delete("/api/user/profile/telegram", params={"user_id": "member-b"})
    assert r.status_code == 200 and r.json()["removed"] is True
    assert "telegram_id" not in _prefs(db, "member-b")
    assert _prefs(db, "member-b")["telegram_link_change"]["by"] == "admin"


def test_a_guest_can_neither_mint_a_link_token_nor_unlink():
    db = FakePrefsDB()
    client = _profile_client(db, role="guest", user_id="guest")
    assert client.post("/api/user/profile/telegram/link-token").status_code == 403
    assert client.delete("/api/user/profile/telegram").status_code == 403
    assert client.put("/api/user/profile/telegram", json={"telegram_id": None}).status_code == 403


def test_the_member_erase_removes_the_link_and_its_records(monkeypatch):
    import contextlib

    import db_pool
    import telegram_link

    db = FakePrefsDB(seed={"member-a": {"telegram_id": "55505", "telegram_link_proof": {"via": "signed_link"},
                                        "telegram_link_change": {"event": "unlinked"}, "theme": "dark"}})

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    import asyncio

    assert asyncio.run(telegram_link.forget_user("member-a")) == 1
    assert _prefs(db, "member-a") == {"theme": "dark"}


def test_the_member_erase_is_all_or_nothing(monkeypatch):
    """The link and the member's redemption rows are erased in ONE transaction: a failure on the second leaves neither half done
    (the caller fails closed and retries), never a link gone with the rows left behind."""
    import asyncio
    import db_pool
    import telegram_link

    db = FakePrefsDB(seed={"member-a": {"telegram_id": "55505", "telegram_link_proof": {"via": "signed_link"}}})
    db.redemptions["tok-a"] = ("member-a", 9e12)
    real = db.execute

    async def failing(sql, params=()):
        if " ".join(sql.split()).startswith("DELETE FROM telegram_link_redemptions WHERE user_id ="):
            raise RuntimeError("injected")
        return await real(sql, params)

    db.execute = failing

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    with pytest.raises(RuntimeError):
        asyncio.run(telegram_link.forget_user("member-a"))
    assert _prefs(db, "member-a").get("telegram_id") == "55505", "the link removal rolled back with the failed delete"
    assert "tok-a" in db.redemptions


# ─── 1b. Durable single use, one holder per account, no moved link, the member erase (alembic 0084) ─────────────


def _system_app(db: FakePrefsDB) -> FastAPI:
    app = FastAPI()
    app.include_router(system.router)

    async def fake_get_db():
        yield db

    app.dependency_overrides[system.get_db] = fake_get_db
    return app


async def _post_redeems(db: FakePrefsDB, bodies: list[dict]) -> list[int]:
    """Redeem concurrently (asyncio.gather) through the real route; returns the status codes in order."""
    import httpx

    transport = httpx.ASGITransport(app=_system_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async def one(body):
            r = await client.post("/api/system/telegram/consume-link-token", json=body, headers={"X-Internal-Token": "tok"})
            return r.status_code
        return list(await asyncio.gather(*(one(b) for b in bodies)))


def _holders_of(db: FakePrefsDB, tid: str) -> list[str]:
    return sorted(uid for uid in db.store if _prefs(db, uid).get("telegram_id") == tid)


@pytest.fixture
def fixed_secret(monkeypatch):
    """A persistent signing secret (the recommended config). The module is reloaded IN PLACE, as a restart re-imports it."""
    import telegram_link

    monkeypatch.setenv("ZOE_TELEGRAM_LINK_SECRET", "synthetic-test-secret")
    importlib.reload(telegram_link)
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    return telegram_link


def test_single_use_survives_a_restart(monkeypatch, fixed_secret):
    db = FakePrefsDB(seed={"member-a": {}})
    token = fixed_secret.make_link_token("member-a")
    assert _redeem(db, monkeypatch, token, "55511").status_code == 200
    importlib.reload(fixed_secret)          # a restart (or another worker): fresh module state, same secret, same database
    assert fixed_secret.verify_link_token(token) == "member-a"      # still authentic and unexpired ...
    for tid in ("55512", "55511"):
        assert _redeem(db, monkeypatch, token, tid).status_code == 400   # ... but spent, durably
    assert _prefs(db, "member-a")["telegram_id"] == "55511" and _holders_of(db, "55512") == []
    assert len(db.redemptions) == 1


def test_one_token_redeemed_twice_at_once_links_once(monkeypatch, fixed_secret):
    db = FakePrefsDB(seed={"member-a": {}})
    token = fixed_secret.make_link_token("member-a")
    codes = asyncio.run(_post_redeems(db, [{"token": token, "telegram_id": "55513"}, {"token": token, "telegram_id": "55514"}]))
    assert sorted(codes) == [200, 400], codes
    held = [t for t in ("55513", "55514") if _holders_of(db, t)]
    assert len(held) == 1 and _holders_of(db, held[0]) == ["member-a"]


def test_two_tokens_racing_for_one_account_leave_one_holder(monkeypatch, fixed_secret):
    db = FakePrefsDB(seed={"member-a": {}, "member-b": {}})
    bodies = [{"token": fixed_secret.make_link_token(m), "telegram_id": "55515"} for m in ("member-a", "member-b")]
    codes = asyncio.run(_post_redeems(db, bodies))
    assert sorted(codes) == [200, 409], codes
    assert len(_holders_of(db, "55515")) == 1
    assert len(db.redemptions) == 1         # the refused redemption was rolled back with its link write


def test_a_redemption_never_moves_a_members_link(monkeypatch, fixed_secret):
    db = FakePrefsDB(seed={"member-a": {"telegram_id": "55516", "telegram_link_proof": {"via": "signed_link"}}})
    token = fixed_secret.make_link_token("member-a")
    r = _redeem(db, monkeypatch, token, "55517")
    assert r.status_code == 409, r.text
    assert _prefs(db, "member-a")["telegram_id"] == "55516" and _holders_of(db, "55517") == []
    assert db.redemptions == {}             # refused: the token is not spent
    # the explicit path: the member unlinks first, then the same token links the new account
    _profile_client(db, user_id="member-a").delete("/api/user/profile/telegram")
    assert _redeem(db, monkeypatch, token, "55517").status_code == 200
    assert _prefs(db, "member-a")["telegram_id"] == "55517"


def test_a_token_that_expires_while_waiting_for_the_member_lock_is_refused(monkeypatch, fixed_secret):
    """Expiry is checked inside the transaction, after the lock: a redemption that passed the caller's check and then waited
    past the token's expiry links nothing and spends nothing."""
    import time as _time

    db = FakePrefsDB(seed={"member-a": {}})
    token = fixed_secret.make_link_token("member-a")
    real_lock, real_time = fixed_secret._member_lock, _time.time

    async def slow_lock(conn, user_id):
        await real_lock(conn, user_id)
        monkeypatch.setattr(fixed_secret.time, "time", lambda: real_time() + 3600)      # the wait outlived the token

    monkeypatch.setattr(fixed_secret, "_member_lock", slow_lock)
    r = _redeem(db, monkeypatch, token, "55530")
    assert r.status_code == 400, r.text
    assert _holders_of(db, "55530") == [] and db.redemptions == {}


def test_the_member_erase_removes_the_members_redemption_rows(monkeypatch, fixed_secret):
    import db_pool

    db = FakePrefsDB(seed={"member-a": {}, "member-b": {}})
    assert _redeem(db, monkeypatch, fixed_secret.make_link_token("member-a"), "55518").status_code == 200
    assert _redeem(db, monkeypatch, fixed_secret.make_link_token("member-b"), "55519").status_code == 200
    assert sorted(u for u, _e in db.redemptions.values()) == ["member-a", "member-b"]

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    assert asyncio.run(fixed_secret.forget_user("member-a")) == 1
    assert [u for u, _e in db.redemptions.values()] == ["member-b"]
    assert "telegram_id" not in _prefs(db, "member-a") and _prefs(db, "member-b")["telegram_id"] == "55519"


@pytest.mark.parametrize("erase_first", [False, True])
def test_a_redemption_racing_the_member_erase_leaves_no_link(monkeypatch, fixed_secret, erase_first):
    """The erase as ``MemoryService.delete_user`` orders it for this store: the durable erase watermark first, then
    ``telegram_link.forget_user``. Both orders of the race; then a token issued before the erase is refused after it."""
    import db_pool
    import erase_watermark

    db = FakePrefsDB(seed={"member-a": {}})

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    monkeypatch.setattr(erase_watermark, "_backend", erase_watermark.SqlBackend(ctx=ctx))
    before = [fixed_secret.make_link_token("member-a"), fixed_secret.make_link_token("member-a", ttl=599)]

    async def erase():
        await erase_watermark.record("member-a", actor="test")
        await fixed_secret.forget_user("member-a")
        return "erased"

    async def race():
        redeem = _post_redeems(db, [{"token": before[0], "telegram_id": "55520"}])
        return await asyncio.gather(*((erase(), redeem) if erase_first else (redeem, erase())))

    asyncio.run(race())
    assert "telegram_id" not in _prefs(db, "member-a")
    assert _holders_of(db, "55520") == []
    # a token issued before the erase is void after it, whichever account redeems it
    assert asyncio.run(_post_redeems(db, [{"token": before[1], "telegram_id": "55521"}])) == [400]
    assert "telegram_id" not in _prefs(db, "member-a")


def test_migration_0084_holds_both_guarantees_and_keeps_one_head():
    import importlib.util
    import re

    versions = ROOT / "alembic" / "versions"
    spec = importlib.util.spec_from_file_location("mig_0084", versions / "0084_telegram_link_ledger.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sql = " ".join(" ".join(mod.statements()).split())
    assert "CREATE TABLE IF NOT EXISTS telegram_link_redemptions ( token_id TEXT PRIMARY KEY" in sql
    assert ("CREATE UNIQUE INDEX IF NOT EXISTS uq_user_preferences_telegram_id ON user_preferences "
            "((prefs::jsonb ->> 'telegram_id')) WHERE (prefs::jsonb ->> 'telegram_id') <> ''") in sql
    # every link without the current proof (so every duplicate holder too) is removed BEFORE the index is built
    assert "'telegram_link_proof' ->> 'v', '') <> '2'" in sql
    assert sql.index("- 'telegram_id'") < sql.index("CREATE UNIQUE INDEX")
    revs, downs = set(), set()
    for f in versions.glob("*.py"):
        src = f.read_text()
        r = re.search(r'^revision = "(\w+)"', src, re.M)
        d = re.search(r'^down_revision = "(\w+)"', src, re.M)
        revs |= {r.group(1)} if r else set()
        downs |= {d.group(1)} if d else set()
    assert mod.down_revision in revs and len(revs - downs) == 1, sorted(revs - downs)


# ─── 2. Internal resolver telegram_id → user_id ──────────────────────────────


def _resolve(db: FakePrefsDB, monkeypatch, tid: str):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    r = _system_client(db).get(f"/api/system/resolve-telegram/{tid}", headers={"X-Internal-Token": "tok"})
    assert r.status_code == 200
    return r.json()["user_id"]


def test_resolver_registered_returns_user_id(monkeypatch):
    db = FakePrefsDB(seed={"member-a": {"telegram_id": "99999", "telegram_link_proof": {"via": "signed_link", "v": 2}}})
    assert _resolve(db, monkeypatch, "99999") == "member-a"


def test_resolver_acts_for_nobody_on_a_link_without_the_current_proof(monkeypatch):
    """A link made before both sides had to prove control (no proof, or an older proof) is never honoured."""
    db = FakePrefsDB(seed={"member-a": {"telegram_id": "55540"},
                           "member-b": {"telegram_id": "55541", "telegram_link_proof": {"via": "signed_link"}}})
    assert _resolve(db, monkeypatch, "55540") is None and _resolve(db, monkeypatch, "55541") is None


def test_resolver_unregistered_returns_null(monkeypatch):
    db = FakePrefsDB(seed={"member-a": {"telegram_id": "99999"}})
    assert _resolve(db, monkeypatch, "77777") is None


def test_resolver_acts_for_nobody_when_the_link_is_ambiguous(monkeypatch):
    db = FakePrefsDB(seed={"member-a": {"telegram_id": "55506"}, "member-b": {"telegram_id": "55506"}})
    assert _resolve(db, monkeypatch, "55506") is None


def test_resolver_acts_for_nobody_once_the_member_account_is_inactive_or_gone(monkeypatch):
    db = FakePrefsDB(seed={"member-a": {"telegram_id": "55507", "telegram_link_proof": {"via": "signed_link", "v": 2}},
                           "member-b": {"telegram_id": "55508", "telegram_link_proof": {"via": "signed_link", "v": 2}}},
                     accounts={"member-a": 0})
    assert _resolve(db, monkeypatch, "55507") is None   # deactivated
    assert _resolve(db, monkeypatch, "55508") is None   # no account at all (deleted)


def test_resolver_requires_internal_token_when_not_loopback(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    db = FakePrefsDB(seed={"jason": {"telegram_id": "99999"}})
    app = FastAPI()
    app.include_router(system.router)

    async def fake_get_db():
        yield db

    app.dependency_overrides[system.get_db] = fake_get_db
    # Non-loopback client host, no token → 403.
    client = TestClient(app)
    r = client.get(
        "/api/system/resolve-telegram/99999",
        headers={"X-Forwarded-For": "8.8.8.8"},
    )
    # TestClient reports client.host as "testclient" (not loopback), so with a
    # token configured and none supplied this must be rejected.
    assert r.status_code == 403


# ─── 3. /api/chat trusted forwarded-user override security ────────────────────
#
# We exercise auth.resolve_acting_user directly through a tiny app so the test
# is independent of the (heavy) chat router import graph.


from fastapi import Depends as _Depends
from fastapi import Request as _Request

_acting_app = FastAPI()


@_acting_app.post("/echo-identity")
async def _echo_identity(request: _Request, user: dict = _Depends(auth.resolve_acting_user)):  # noqa: ARG001
    return {"user_id": user["user_id"], "role": user.get("role")}


def _acting_user_app() -> FastAPI:
    return _acting_app


def test_internal_caller_may_set_acting_user(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    # Valid X-Internal-Token → override honoured (loopback alone is NOT enough).
    client = TestClient(_acting_user_app())
    r = client.post(
        "/echo-identity",
        headers={"X-Zoe-User-Id": "jason", "X-Internal-Token": "tok"},
    )
    assert r.status_code == 200
    assert r.json() == {"user_id": "jason", "role": "user"}


def test_loopback_without_token_cannot_impersonate(monkeypatch):
    """CRITICAL (2026-07-04 review residual): bare loopback + X-Zoe-User-Id must
    NOT grant impersonation — a loopback SSRF or compromised local process would
    otherwise inherit acting-as-any-user. Token required, full stop."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    # TestClient is loopback-equivalent; force the internal check True to model
    # the strongest version of the old trust.
    monkeypatch.setattr(auth, "_is_internal_request", lambda request: True)
    client = TestClient(_acting_user_app())
    r = client.post("/echo-identity", headers={"X-Zoe-User-Id": "jason"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "guest"


def test_override_disabled_when_token_unprovisioned(monkeypatch):
    """With ZOE_INTERNAL_TOKEN unset the override is disabled entirely — even a
    caller that SENDS some token header cannot impersonate (nothing to match)."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "")
    monkeypatch.setattr(auth, "_is_internal_request", lambda request: True)
    client = TestClient(_acting_user_app())
    r = client.post(
        "/echo-identity",
        headers={"X-Zoe-User-Id": "jason", "X-Internal-Token": "anything"},
    )
    assert r.status_code == 200
    assert r.json()["user_id"] == "guest"


def test_public_caller_cannot_impersonate(monkeypatch):
    """CRITICAL: a NON-internal caller that sets X-Zoe-User-Id must be ignored —
    the header must never grant impersonation. Falls through to guest."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    # Force _is_internal_request to be False regardless of loopback, simulating a
    # public request (wrong/absent token AND non-loopback host).
    monkeypatch.setattr(auth, "_is_internal_request", lambda request: False)
    client = TestClient(_acting_user_app())
    r = client.post(
        "/echo-identity",
        headers={"X-Zoe-User-Id": "jason"},  # attacker-supplied, no valid internal auth
    )
    assert r.status_code == 200
    # Ignored → normal auth ran → guest identity, NOT "jason".
    body = r.json()
    assert body["user_id"] == "guest"
    assert body["user_id"] != "jason"


def test_internal_caller_without_header_uses_session_auth(monkeypatch):
    """An internal caller that does NOT send X-Zoe-User-Id still goes through
    normal session/guest auth — the override is opt-in, not automatic."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(auth, "_is_internal_request", lambda request: True)
    client = TestClient(_acting_user_app())
    r = client.post("/echo-identity")  # no X-Zoe-User-Id
    assert r.status_code == 200
    assert r.json()["user_id"] == "guest"


def test_is_internal_request_matches_token_boundary(monkeypatch):
    """_is_internal_request must accept a valid token and reject a bad one for a
    non-loopback host (the exact boundary the override relies on)."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")

    class _Req:
        def __init__(self, host, token):
            self.client = type("C", (), {"host": host})()
            self.headers = {"X-Internal-Token": token} if token else {}

    assert auth._is_internal_request(_Req("127.0.0.1", "")) is True  # loopback
    assert auth._is_internal_request(_Req("8.8.8.8", "tok")) is True  # valid token
    assert auth._is_internal_request(_Req("8.8.8.8", "wrong")) is False
    assert auth._is_internal_request(_Req("8.8.8.8", "")) is False
