"""Settings > People > Delete runs the FULL member erase (audit gap #1; owner rule 2026-11-15: deleting a member erases everything).

The button used to send zoe-auth's ``DELETE /api/admin/users/{id}`` with no ``permanent`` flag: the sign-in was deactivated and every
bit of the member's data stayed. Now the button calls ONE action, ``POST /api/members/{id}/remove`` (``routers/member_removal.py``),
which runs the durable job in ``member_removal.py``: (1) the barrier (durable erase mark + erase gate), (2) the sign-in deactivated,
(3) ``MemoryService.delete_user``, failing closed, (4) only then the sign-in deleted. Invented ids only.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
from pathlib import Path

import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe

import erase_watermark as ew
import member_removal as mr
import user_erase_gate

ROOT = Path(__file__).resolve().parents[3]
SETTINGS = ROOT / "services" / "zoe-ui" / "dist" / "settings.html"
A, ADMIN, SESSION = "member-a", "admin-1", "synthetic-session"

_DDL = """CREATE TABLE memory_forgotten (user_id TEXT NOT NULL, key_hash TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'entity',
          actor TEXT NOT NULL DEFAULT '', forgotten_at TEXT NOT NULL, shield_until TEXT NOT NULL, PRIMARY KEY (user_id, key_hash))"""


class World:
    """The job's real SQL store on a scratch SQLite file, the erase mark in memory, and the three outside calls recorded in order."""

    def __init__(self, path, monkeypatch):
        self.path, self.calls = str(path), []
        self.erase_fails = False
        self.mark = ew.MemoryBackend()
        ew.set_backend(self.mark)
        user_erase_gate.reset()
        mr._RUNNING.clear()
        mr._LAST_ERROR.clear()
        mr._LOCKS.clear()
        asyncio.run(self._ddl())
        self.restart()
        real_barrier = mr.put_barrier

        async def barrier(uid):
            self.calls.append(("barrier", uid))
            await real_barrier(uid)

        async def auth(uid, session_id, *, permanent):
            if not session_id:
                raise mr.NeedsAdmin("no administrator session")
            assert session_id == SESSION, "the administrator's own session, passed through"
            assert self.mark.rows.get(uid), "the durable erase mark is written before any sign-in step"
            self.calls.append(("sign_in_delete" if permanent else "deactivate", uid))

        class _Svc:
            async def delete_user(svc, uid, *, actor, reason=""):
                self.calls.append(("delete_user", uid))
                if self.erase_fails:
                    raise RuntimeError("store down")
                return 3

        import memory_service

        monkeypatch.setattr(mr, "put_barrier", barrier)
        monkeypatch.setattr(mr, "_auth_delete", auth)
        monkeypatch.setattr(memory_service, "get_memory_service", lambda: _Svc())

    async def _ddl(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(_DDL)
            await db.commit()

    def restart(self):
        """A fresh process: a new store over the SAME file, nothing in memory."""
        @contextlib.asynccontextmanager
        async def ctx():
            async with aiosqlite.connect(self.path) as db:
                yield db
        mr.set_store(mr.SqlStore(ctx=ctx))
        mr._RUNNING.clear()
        mr._LAST_ERROR.clear()
        mr._LOCKS.clear()

    def steps(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "CACHE_SETTLE_S", 0.0)          # the cache-settle wait is pinned by its own tests below
    w = World(tmp_path / "zoe.db", monkeypatch)
    yield w
    mr.set_store(None)
    ew.set_backend(None)
    import auth

    auth._BARRED.clear()                                    # the in-process bar never leaks into another test


def test_settings_member_delete_runs_full_erase(world):
    """The rock for audit gap #1: the one action runs barrier -> deactivate -> the full erase -> the sign-in delete, in that order,
    and only then says done."""
    assert asyncio.run(mr.advance(A, actor=ADMIN, session_id=SESSION)) == "done"
    assert world.steps() == ["barrier", "deactivate", "delete_user", "sign_in_delete"]
    assert asyncio.run(mr.status(A))["status"] == "done"
    assert world.mark.rows.get(A), "the barrier stays up afterwards"


def test_a_failing_erase_never_reports_done_and_never_deletes_the_sign_in(world):
    world.erase_fails = True
    assert asyncio.run(mr.advance(A, actor=ADMIN, session_id=SESSION)) == "deactivated"
    st = asyncio.run(mr.status(A))
    assert st["status"] == "retrying" and st["erased"] is False and st["reason"] == "RuntimeError"
    assert "sign_in_delete" not in world.steps(), "the sign-in survives until the erase confirmed"
    assert world.mark.rows.get(A), "the member stays behind the barrier"
    # retried with the erase still failing: still not done, still no sign-in delete
    asyncio.run(mr.advance(A, actor=ADMIN, session_id=SESSION))
    assert asyncio.run(mr.status(A))["status"] == "retrying" and "sign_in_delete" not in world.steps()


def test_a_restart_resumes_the_job_and_the_unattended_pass_finishes_the_erase(world):
    world.erase_fails = True
    asyncio.run(mr.advance(A, actor=ADMIN, session_id=SESSION))
    world.restart()                                             # the process restarts: the job's state is on disk
    world.erase_fails = False
    assert asyncio.run(mr.status(A))["status"] == "retrying"
    assert asyncio.run(mr.resume_unattended()) == 1             # the retry loop's pass: no administrator needed for the erase
    st = asyncio.run(mr.status(A))
    assert st["state"] == "erased" and st["status"] == "retrying", "erased, but not done: the sign-in step waits for an admin"
    assert world.steps().count("delete_user") == 2 and "sign_in_delete" not in world.steps()
    assert asyncio.run(mr.advance(A, actor=ADMIN, session_id=SESSION)) == "done"   # an administrator re-opens Settings
    assert world.steps()[-1] == "sign_in_delete" and world.steps().count("barrier") == 1 and world.steps().count("deactivate") == 1


def test_the_barrier_is_written_before_deactivation_and_a_failed_barrier_touches_nothing(world, monkeypatch):
    asyncio.run(mr.advance(A, actor=ADMIN, session_id=SESSION))
    assert world.steps().index("barrier") < world.steps().index("deactivate")

    async def mark_down(uid, *, actor=""):
        raise ew.Unavailable("store down")
    monkeypatch.setattr(ew, "record", mark_down)
    world.calls.clear()
    assert asyncio.run(mr.advance("member-b", actor=ADMIN, session_id=SESSION)) == "intent", "the intent is durable, the barrier is not: the job stays at intent"
    assert world.steps() == ["barrier"], "no mark, no sign-in step and no erase"
    st = asyncio.run(mr.status("member-b"))
    assert st["status"] == "retrying" and st["erased"] is False


def test_without_an_admin_session_the_sign_in_steps_wait_and_nothing_is_skipped(world):
    assert asyncio.run(mr.advance(A, actor=ADMIN, session_id="")) == "barrier"
    assert world.steps() == ["barrier"], "no deactivation without an administrator, and so no erase either (order)"
    assert asyncio.run(mr.status(A))["reason"] == "needs_admin"


def test_the_settings_button_calls_the_one_action_and_never_says_deleted_before_done():
    src = SETTINGS.read_text(encoding="utf-8")
    fn = src[src.index("async function deleteUser("):]
    fn = fn[:fn.index("\n}\n")]
    assert "POST', `/api/members/${encodeURIComponent(id)}/remove`" in fn
    assert "/api/admin/users" not in fn, "the button no longer only deactivates the sign-in"
    assert "deleted" not in fn.lower().replace("deleteuser", ""), "nothing says deleted when the action is only started"
    watch = src[src.index("async function watchRemoval("):]
    watch = watch[:watch.index("\n}\n")]
    done_at = watch.index("st.status === 'done'")
    assert "was deleted" in watch[done_at:done_at + 200] and watch.count("was deleted") == 1, "deleted is said for done only"
    assert re.search(r"isn't finished yet\. Zoe will keep retrying", watch)


def test_the_route_is_admin_only_refuses_self_delete_and_passes_the_session():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import auth
    from routers import member_removal as route

    seen = []

    async def advance(uid, *, actor, session_id):
        seen.append((uid, actor, session_id))
        return "done"
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[auth.require_admin] = lambda: {"user_id": ADMIN, "role": "admin"}
    async def status(uid):
        return {"user_id": uid, "state": "start", "status": "in_progress"}
    real = (mr.advance, mr.status)
    mr.advance, mr.status = advance, status
    try:
        c = TestClient(app)
        r = c.post(f"/api/members/{A}/remove", headers={"X-Session-ID": SESSION})
        assert r.status_code == 202 and r.json()["status"] == "in_progress"
        assert c.post(f"/api/members/{ADMIN}/remove", headers={"X-Session-ID": SESSION}).status_code == 400
    finally:
        mr.advance, mr.status = real
    assert seen == [(A, ADMIN, SESSION)]
    deps = {d.call for r in route.router.routes for d in r.dependant.dependencies}
    assert auth.require_admin in deps and all(r.path.startswith("/api/members") for r in route.router.routes)


def test_a_crash_between_the_intent_and_the_barrier_is_resumed_not_orphaned(world):
    """The job row is written BEFORE the barrier: a crash right after it is found by the retry pass, which writes the barrier."""
    assert asyncio.run(mr.get_store().put(A, "intent", None)) is True        # the process died right after the intent
    world.restart()
    assert asyncio.run(mr.status(A))["status"] == "retrying"
    assert asyncio.run(mr.resume_unattended()) == 1
    assert world.mark.rows.get(A) and asyncio.run(mr.status(A))["state"] == "barrier"
    assert world.steps() == ["barrier"], "and it stops there: deactivation needs an administrator"


def test_a_job_never_moves_backwards_or_over_a_step_it_did_not_see(world):
    store = mr.get_store()
    asyncio.run(mr.advance(A, actor=ADMIN, session_id=SESSION))
    assert asyncio.run(store.get(A)) == "done"
    assert asyncio.run(store.put(A, "erased", "deactivated")) is False       # a second runner that read "deactivated" earlier
    assert asyncio.run(store.put(A, "intent", None)) is False                # nor can a fresh start overwrite it
    assert asyncio.run(store.get(A)) == "done"


def test_the_sign_in_call_can_only_name_the_member_that_was_erased(monkeypatch):
    """An id that would change the shape of the zoe-auth URL is refused (the route answers 400; the step fails before any request),
    and a valid id travels as ONE percent-encoded path segment."""
    import httpx

    urls = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def delete(self, url, params=None, headers=None):
            urls.append((url, params))
            return httpx.Response(200)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    for bad in ("victim#x", "victim/../admin", "victim?permanent=true", "", "a b"):
        assert not mr.valid_member_id(bad)
        with pytest.raises(mr.StepFailed):
            asyncio.run(mr._auth_delete(bad, SESSION, permanent=True))
    assert urls == []
    asyncio.run(mr._auth_delete("member-a.1@home", SESSION, permanent=True))
    assert urls == [(mr._auth_url() + "/api/admin/users/member-a.1%40home", {"permanent": "true"})]


def test_settings_shows_an_unfinished_removal_whose_sign_in_is_already_off():
    src = SETTINGS.read_text(encoding="utf-8")
    fn = src[src.index("async function loadUsers("):]
    fn = fn[:fn.index("\n}\n")]
    assert "/api/admin/users?active_only=false" in fn and "removalsById" in fn


def test_a_step_another_runner_holds_is_not_run_twice_and_a_dead_runners_claim_is_taken_over(world):
    """Each step is CLAIMED durably before its side effect: a second runner (another process) that finds the claim does not run the
    step; a claim whose runner died (older than CLAIM_STALE_S) is released and the step runs once."""
    world.erase_fails = True
    asyncio.run(mr.advance(A, actor=ADMIN, session_id=SESSION))               # stops at deactivated
    world.erase_fails = False
    store = mr.get_store()
    assert asyncio.run(store.put(A, "deactivated" + mr.CLAIM, "deactivated"))    # another process claimed the erase just now
    before = world.steps().count("delete_user")
    assert asyncio.run(mr.advance(A, actor=ADMIN, session_id=SESSION)) == "deactivated+run"
    assert world.steps().count("delete_user") == before, "the held step is not run a second time"
    assert asyncio.run(mr.status(A))["status"] == "in_progress"

    async def age_claim():                                                     # that process died 20 minutes ago
        async with aiosqlite.connect(world.path) as db:
            await db.execute("UPDATE memory_forgotten SET forgotten_at = '2000-01-01T00:00:00.000000Z' WHERE user_id = ?", (A,))
            await db.commit()
    asyncio.run(age_claim())
    assert asyncio.run(mr.advance(A, actor=ADMIN, session_id=SESSION)) == "done"
    assert world.steps().count("delete_user") == before + 1 and world.steps()[-1] == "sign_in_delete"


def test_settings_follows_a_removal_that_is_already_running_when_reopened():
    src = SETTINGS.read_text(encoding="utf-8")
    fn = src[src.index("async function loadRemovals("):]
    fn = fn[:fn.index("\n}\n")]
    assert "r.status === 'in_progress'" in fn and "watchRemoval(" in fn


def test_a_runner_whose_stale_claim_was_taken_over_can_neither_complete_nor_release_it(world):
    world.erase_fails = True
    asyncio.run(mr.advance(A, actor=ADMIN, session_id=SESSION))               # at deactivated
    world.erase_fails = False
    store = mr.get_store()
    old = "deactivated" + mr.CLAIM + ":runner-a"
    assert asyncio.run(store.put(A, old, "deactivated"))                       # runner A claimed the erase, then stalled

    async def age_claim():
        async with aiosqlite.connect(world.path) as db:
            await db.execute("UPDATE memory_forgotten SET forgotten_at = '2000-01-01T00:00:00.000000Z' WHERE user_id = ?", (A,))
            await db.commit()
    asyncio.run(age_claim())
    world.calls.clear()

    async def b_runs_while_a_returns():
        # runner B takes the stale claim over; while B's erase runs, A comes back and tries to complete / release ITS claim
        real = mr.full_erase

        async def erase(uid, actor):
            assert await store.put(A, "erased", old) is False, "A cannot complete a claim it lost"
            assert await store.put(A, "deactivated", old) is False, "nor release it"
            await real(uid, actor)
        mr.full_erase = erase
        try:
            return await mr.advance(A, actor=ADMIN, session_id=SESSION)
        finally:
            mr.full_erase = real
    assert asyncio.run(b_runs_while_a_returns()) == "done"
    assert world.steps() == ["delete_user", "sign_in_delete"]


def test_a_cached_session_of_a_member_under_removal_cannot_write_a_note(world, monkeypatch):
    """The class fix for #2669's review: zoe-auth ends the member's sessions at step 2, but zoe-data caches a validated identity for 30
    s. The ONE dependency every member route uses (``auth.get_current_user``) refuses a member under removal - from the in-process
    bar (step 1 also drops their cached identities) and, on every cache miss, from the DURABLE removal row - so a cached session posts
    nothing. (``erase_watermark.store_write_guard`` covers only writes inside a bound TURN; a plain REST write is stopped here.)"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import auth
    from database import get_db
    from routers import notes as notes_router

    writes = []

    class _DB:
        async def execute(self, sql, *a, **k):
            writes.append(sql)

            class _C:
                rowcount = 1

                async def fetchone(self):
                    return None
            return _C()

        async def commit(self):
            pass

    async def no_gate(*a, **k):
        return None
    monkeypatch.setattr(notes_router, "require_feature_access", no_gate)
    app = FastAPI()
    app.include_router(notes_router.router)
    app.dependency_overrides[get_db] = lambda: _DB()
    auth._session_cache.clear()
    auth._BARRED.clear()
    auth._cache_set("sess-a", {"user_id": A, "role": "member"})
    auth._cache_set("sess-b", {"user_id": "member-b", "role": "member"})
    asyncio.run(mr.advance(A, actor=ADMIN, session_id=""))                    # step 1 only (no admin session: stops at the barrier)
    c = TestClient(app, raise_server_exceptions=False)   # the control only needs to reach the INSERT
    r = c.post("/api/notes/", json={"title": "t", "content": "synthetic words"}, headers={"X-Session-ID": "sess-a"})
    assert r.status_code == 401 and writes == [], "the cached member is refused and nothing is written"
    # a fresh process (nothing barred, nothing cached): zoe-auth still validates the session (step 2 not done), the durable row refuses
    auth._session_cache.clear()
    auth._BARRED.clear()

    async def still_valid(session_id):
        return {"user_id": A, "role": "member"}
    monkeypatch.setattr(auth, "_validate_with_auth_service", still_valid)
    r = c.post("/api/notes/", json={"title": "t", "content": "synthetic words"}, headers={"X-Session-ID": "sess-a"})
    assert r.status_code == 401 and writes == []
    # control: another member's cached session writes
    auth._cache_set("sess-b", {"user_id": "member-b", "role": "member"})
    r = c.post("/api/notes/", json={"title": "t", "content": "synthetic words"}, headers={"X-Session-ID": "sess-b"})
    assert any("INSERT INTO notes" in w for w in writes), "B passed the auth dependency and the note was written"
    auth._session_cache.clear()
    auth._BARRED.clear()


def test_settings_keeps_following_a_long_removal_instead_of_going_quiet():
    src = SETTINGS.read_text(encoding="utf-8")
    fn = src[src.index("async function watchRemoval("):]
    fn = fn[:fn.index("\n}\n")]
    assert "for (let i = 0; ; i++)" in fn and "20000" in fn and "Still deleting" in fn


def test_a_lost_pool_fails_closed_and_only_a_process_that_never_had_one_reads_no_store(monkeypatch):
    """#2669 review P1: a pool that existed and was lost raises the same "db_pool not initialised" text as a process that never had
    one. Only the latter may read as "no member is being removed"; the lost pool must raise so get_current_user fails closed (503).
    Negative control: the old text match returned False for both."""
    import auth
    import db_pool

    class Lost:
        async def get(self, _uid):
            raise RuntimeError("db_pool not initialised — call init_pool() first")
    monkeypatch.setattr(mr, "get_store", lambda: Lost())
    auth._BARRED.discard(A)
    monkeypatch.setattr(db_pool, "_ever_initialised", True)
    with pytest.raises(RuntimeError):
        asyncio.run(auth._under_removal(A))
    monkeypatch.setattr(db_pool, "_ever_initialised", False)
    assert asyncio.run(auth._under_removal(A)) is False


@pytest.mark.parametrize("barrier_age_s,waits", [(0.0, True), (120.0, False)], ids=["fresh-barrier", "resumed-long-after"])
def test_the_erase_waits_out_the_session_cache_after_the_barrier(monkeypatch, barrier_age_s, waits):
    """#2669 review P1: another process's cached session is checked only against that process's own bar, for up to the cache TTL.
    The erase therefore starts no sooner than CACHE_TTL + 1 s after the barrier, so every later request of the member is a cache miss
    that reads the durable row and is refused. A removal resumed long after its barrier does not wait again."""
    from datetime import datetime, timedelta, timezone

    import auth
    import erase_watermark

    monkeypatch.setattr(mr, "CACHE_SETTLE_S", None)          # the real setting: auth.CACHE_TTL_SECONDS + 1
    mark = datetime.now(timezone.utc) - timedelta(seconds=barrier_age_s)

    async def watermark(_uid):
        return mark
    monkeypatch.setattr(erase_watermark, "watermark", watermark)
    slept, erased = [], []

    async def fake_sleep(s):
        slept.append(s)
    monkeypatch.setattr(mr.asyncio, "sleep", fake_sleep)

    class Svc:
        async def delete_user(self, uid, **_kw):
            erased.append(uid)
    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: Svc())
    asyncio.run(mr.full_erase(A, "test"))
    assert erased == [A]
    if waits:
        assert slept and auth.CACHE_TTL_SECONDS < slept[0] <= auth.CACHE_TTL_SECONDS + 1.5, slept
    else:
        assert slept == []


def test_an_unreadable_barrier_under_fail_open_gives_a_degraded_guest_never_the_member(monkeypatch):
    """#2669 review P1: with ZOE_AUTH_FAIL_CLOSED off, a barrier that cannot be read used to fall through to the validated MEMBER.
    It must give a degraded guest (like the zoe-auth outage fallback), and never cache it. Negative control: fail-closed still 503s."""
    import auth
    from fastapi import HTTPException

    async def unreadable(_uid):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(auth, "_under_removal", unreadable)
    member = {"user_id": A, "role": "member", "username": "a"}
    monkeypatch.setattr(auth, "_AUTH_FAIL_CLOSED", False)
    got = asyncio.run(auth._removal_gate(member))
    assert got["user_id"] == auth.GUEST_USER_ID and got["role"] == "guest" and got.get("auth_degraded") is True
    monkeypatch.setattr(auth, "_AUTH_FAIL_CLOSED", True)
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth._removal_gate(member))
    assert e.value.status_code == 503


def test_a_relayed_request_for_a_member_under_removal_is_refused(monkeypatch):
    """#2669 review P1: the relay resolves its own sessions and used to return before either removal check. The gate now applies to
    it: a relayed request whose fresh panel sign-in names a barred member gets 401. Negative control: another member passes."""
    import auth
    from fastapi import HTTPException

    monkeypatch.setattr(auth.relay_ingress, "is_relay", lambda _req: True)
    who = {}

    async def resolve_user(_req):
        return {"user_id": who["id"], "role": "member", "username": "m"}
    monkeypatch.setattr(auth.relay_ingress, "resolve_user", resolve_user)

    async def under_removal(uid):
        return uid == A
    monkeypatch.setattr(auth, "_under_removal", under_removal)
    who["id"] = A
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth.get_current_user(object()))
    assert e.value.status_code == 401
    who["id"] = "member-b"
    assert asyncio.run(auth.get_current_user(object()))["user_id"] == "member-b"


def test_every_member_identity_get_current_user_returns_passes_the_removal_gate():
    """#2669 review P1 (device-token members), fixed as a CLASS: every ``return`` in ``auth.get_current_user`` is either
    ``await _removal_gate(...)``, or one of the names already gated or guest-only (``cached`` - its in-process bar plus the erase's
    cache-settle wait; ``degraded`` - a guest; ``gated`` / ``validated`` - returned after _removal_gate), or a literal guest dict.
    A new return path that hands back a member without the gate fails here. Negative control: the checker flags a bare member return."""
    import ast
    import inspect
    import textwrap

    import auth

    allowed_names = {"cached", "degraded", "gated", "validated"}

    def ungated(src: str) -> list:
        fn = ast.parse(textwrap.dedent(src)).body[0]
        bad = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            v = node.value
            if isinstance(v, ast.Await) and isinstance(v.value, ast.Call) and getattr(v.value.func, "id", "") == "_removal_gate":
                continue
            if isinstance(v, ast.Name) and v.id in allowed_names:
                continue
            if isinstance(v, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "user_id" and isinstance(val, ast.Constant)
                                               and val.value == "guest" for k, val in zip(v.keys, v.values)):
                continue
            bad.append(ast.unparse(v)[:80])
        return bad

    assert ungated(inspect.getsource(auth.get_current_user)) == []
    rogue = "async def get_current_user(request):\n    member = {'user_id': 'x'}\n    return member\n"
    assert ungated(rogue) == ["member"], "the checker catches a member returned without the gate"


def test_a_device_token_bound_to_a_member_under_removal_is_refused(monkeypatch):
    """With ZOE_DEVICE_TOKEN_BINDS_MEMBER=1 a device token resolves to its member; that member under removal gets 401, and the 401 is
    not swallowed by the device-token try. Negative control: another member's token passes."""
    import auth
    from fastapi import HTTPException
    from routers import panel_auth

    class Req:
        def __init__(self):
            self.headers = {"X-Device-Token": "tok"}
    who = {}

    async def resolve(_tok):
        return {"user_id": who["id"], "role": "member", "username": "m"}
    monkeypatch.setattr(panel_auth, "_resolve_device_token_user", resolve)
    monkeypatch.setattr(auth.relay_ingress, "is_relay", lambda _req: False)

    async def under_removal(uid):
        return uid == A
    monkeypatch.setattr(auth, "_under_removal", under_removal)
    who["id"] = A
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth.get_current_user(Req()))
    assert e.value.status_code == 401
    who["id"] = "member-b"
    assert asyncio.run(auth.get_current_user(Req()))["user_id"] == "member-b"


def _turn_app(monkeypatch, user):
    """A tiny app: RequestTurnMiddleware around one route that depends on the real get_current_user (zoe-auth faked)."""
    import auth
    import erase_watermark
    from fastapi import Depends, FastAPI

    async def validate(_sid):
        return dict(user)
    monkeypatch.setattr(auth, "_validate_with_auth_service", validate)

    async def not_removed(_uid):
        return False
    monkeypatch.setattr(auth, "_under_removal", not_removed)
    monkeypatch.setattr(auth.relay_ingress, "is_relay", lambda _req: False)
    auth._session_cache.clear()
    seen = {}
    app = FastAPI()

    @app.post("/write")
    async def write(u=Depends(auth.get_current_user)):
        seen["bound"] = erase_watermark._TURN_CTX.get()
        seen["inflight"] = erase_watermark.inflight_turns(user["user_id"])
        return {"ok": True}
    app.add_middleware(auth.RequestTurnMiddleware)
    return app, seen


def test_a_members_request_is_a_turn_while_it_runs_and_is_closed_after(monkeypatch):
    """#2669 review P1: a request already past auth when a removal began could write after the erase (no bound turn, so the store
    write guard never judged it, and the delete did not wait for it). A signed-in member's request is now a turn for its duration:
    bound (so every member-owned write is judged against its START) and counted in flight (so the delete waits for it), then closed.
    Negative control: a guest opens no turn."""
    from fastapi.testclient import TestClient
    import erase_watermark

    app, seen = _turn_app(monkeypatch, {"user_id": A, "role": "member", "username": "a"})
    r = TestClient(app).post("/write", headers={"X-Session-ID": "sess-a"})
    assert r.status_code == 200
    assert seen["bound"] is not None and seen["inflight"] == 1, "bound and in flight while the route runs"
    assert erase_watermark.inflight_turns(A) == 0, "closed once the response is sent"

    app, seen = _turn_app(monkeypatch, {"user_id": "guest", "role": "guest", "username": "guest"})
    TestClient(app).post("/write", headers={"X-Session-ID": "sess-g"})
    assert seen["bound"] is None and seen["inflight"] == 0


def test_without_the_middleware_no_request_turn_is_opened(monkeypatch):
    """A direct call (or an app without RequestTurnMiddleware) opens nothing, so no turn can leak and stall a later delete."""
    import auth
    import erase_watermark

    class Req:
        scope = {}
        headers = {"X-Session-ID": "sess-x"}

    async def validate(_sid):
        return {"user_id": A, "role": "member", "username": "a"}
    monkeypatch.setattr(auth, "_validate_with_auth_service", validate)

    async def not_removed(_uid):
        return False
    monkeypatch.setattr(auth, "_under_removal", not_removed)
    monkeypatch.setattr(auth.relay_ingress, "is_relay", lambda _req: False)
    auth._session_cache.clear()
    assert asyncio.run(auth.get_current_user(Req()))["user_id"] == A
    assert erase_watermark.inflight_turns(A) == 0


def test_an_unsigned_admin_override_cannot_start_a_removal(monkeypatch):
    """#2669 review P1: require_admin passes the header-less family-admin promotion (ZOE_UNAUTHENTICATED_ROLE), so an anonymous LAN
    caller could durably bar another member. The route now needs a signed-in administrator. Negative control: a real admin session
    passes the check."""
    from fastapi import HTTPException
    from routers import member_removal as route

    class Req:
        def __init__(self, sid):
            self.headers = {"X-Session-ID": sid} if sid else {}
    started = []

    async def advance(*a, **k):
        started.append(a)
    monkeypatch.setattr(route.member_removal, "advance", advance)
    override = {"user_id": "family-admin", "role": "admin", "unauthenticated_override": True}
    for admin, sid in ((override, ""), ({"user_id": ADMIN, "role": "admin", "auth_degraded": True}, "s1"),
                       ({"user_id": ADMIN, "role": "admin"}, "")):
        with pytest.raises(HTTPException) as e:
            asyncio.run(route.remove_member(A, Req(sid), admin))
        assert e.value.status_code == 403
    assert started == [], "no removal step ran"
    route._signed_in_admin({"user_id": ADMIN, "role": "admin"}, Req("s1"))      # a signed-in admin passes


def test_a_delete_run_inside_the_members_own_request_does_not_wait_for_itself(monkeypatch):
    """#2669 review P1: an administrator forgetting their OWN memories (POST /api/memories/users/{own_id}/forget) runs the delete
    inside a request that is itself a turn of that member; the delete waited for that turn, which only closes after the response:
    a deadlock until the wait timed out. The delete now leaves the caller's own turn out. Negative control: another turn of the same
    member is still counted."""
    import erase_watermark

    async def go():
        own = erase_watermark.turn_open(None, A)
        ctx = erase_watermark.turn_bind(own)
        try:
            assert erase_watermark.inflight_turns(A) == 1
            assert erase_watermark.inflight_turns(A, exclude_own=True) == 0, "the delete does not wait for its own request"
            other = erase_watermark.turn_open(None, A)
            assert erase_watermark.inflight_turns(A, exclude_own=True) == 1, "another turn of the member is still waited for"
            erase_watermark.turn_close(other)

            async def child():      # a task started inside the turn inherits its context but is NOT that turn: it still waits
                return erase_watermark.inflight_turns(A, exclude_own=True)
            assert await asyncio.ensure_future(child()) == 1
        finally:
            erase_watermark.turn_unbind(ctx)
            erase_watermark.turn_close(own)
    asyncio.run(go())
