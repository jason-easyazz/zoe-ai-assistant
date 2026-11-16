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
    w = World(tmp_path / "zoe.db", monkeypatch)
    yield w
    mr.set_store(None)
    ew.set_backend(None)


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
    assert asyncio.run(mr.advance("member-b", actor=ADMIN, session_id=SESSION)) == "start"
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
