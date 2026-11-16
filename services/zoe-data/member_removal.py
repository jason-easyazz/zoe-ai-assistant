"""Remove a member (Settings > People > Delete): ONE durable, resumable job (owner rule 2026-11-15: deleting a member erases
everything).

The Settings button used to send zoe-auth's ``DELETE /api/admin/users/{id}`` with no ``permanent`` flag, which only DEACTIVATED the
sign-in and never touched the member's data. Now it calls ``POST /api/members/{id}/remove`` (``routers/member_removal.py``), and
this job runs four steps, in this order, each recorded before the next starts:

  1. ``barrier``      the durable erase mark (``erase_watermark.record``: every late writer, sender and turn of the member refuses from
                      here) and the erase gate's generation bump (``user_erase_gate.erasing``: background work scheduled for the member
                      drops). Both stay up afterwards - the mark is never removed.
  2. ``deactivated``  zoe-auth's existing non-permanent delete (the sign-in stops working, the member's sessions end).
  3. ``erased``       the full erase, ``MemoryService.delete_user`` (what ``POST /api/memories/users/{id}/forget`` runs). It FAILS CLOSED:
                      a failure or unreadable evidence leaves the member deactivated, the barrier up and the job pending, and the
                      administrator sees "not finished, retrying" - never "done".
  4. ``done``         only after step 3 confirmed: zoe-auth's permanent delete (the sign-in rows).

The job's state is ONE row in the forget ledger's own table (``memory_forgotten``, no migration: key ``member_delete|job``, scope
``member_delete``, ``actor`` = the step reached, ``shield_until`` the far-past sentinel so no ledger read sees it - the erase mark's
pattern): ids and a step name only, never words. It survives a restart. Steps 2 and 4 are zoe-auth calls that need an ADMIN session
(zoe-auth has no service credential), so they run when an administrator starts or re-opens the removal; the session is never stored.
Step 3 needs none: the retry loop (``run_retry_loop``, started by ``main.lifespan``) resumes every job that is deactivated but not yet
erased, at start and every ``RETRY_S``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

KEY = "member_delete|job"
SCOPE = "member_delete"
NEVER = "0000-01-01T00:00:00Z"
STEPS = ("barrier", "deactivated", "erased", "done")
RETRY_S = 300.0

#: in this process: the members whose job is running now, and the last failure code of each (a type name, never a message)
_RUNNING: set = set()
_LAST_ERROR: dict = {}
_LOCKS: dict = {}


class StepFailed(RuntimeError):
    """A step did not complete: the job stays where it was and is retried."""


class NeedsAdmin(StepFailed):
    """A zoe-auth step needs an administrator's session, and none is at hand (a restart, the retry loop)."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class SqlStore:
    """The job rows in ``memory_forgotten`` over ``db_pool`` (portable SQL). ``ctx``: an optional connection factory (tests, a script)."""

    def __init__(self, ctx: Optional[Callable[[], Any]] = None):
        self._ctx = ctx

    def _db(self):
        if self._ctx is not None:
            return self._ctx()
        from db_pool import get_db_ctx  # type: ignore[import]
        return get_db_ctx()

    async def get(self, user_id: str) -> Optional[str]:
        async with self._db() as db:
            cur = await db.execute("SELECT actor FROM memory_forgotten WHERE user_id = ? AND key_hash = ?", (user_id, KEY))
            row = await cur.fetchone()
        return str(row[0]) if row and row[0] else None

    async def put(self, user_id: str, step: str) -> None:
        async with self._db() as db:
            await db.execute(
                """INSERT INTO memory_forgotten (user_id, key_hash, scope, actor, forgotten_at, shield_until)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (user_id, key_hash) DO UPDATE
                   SET actor = excluded.actor, forgotten_at = excluded.forgotten_at, scope = excluded.scope,
                       shield_until = excluded.shield_until""",
                (user_id, KEY, SCOPE, step, _now(), NEVER))
            await db.commit()

    async def all(self) -> dict:
        async with self._db() as db:
            cur = await db.execute("SELECT user_id, actor FROM memory_forgotten WHERE key_hash = ?", (KEY,))
            return {str(r[0]): str(r[1]) for r in await cur.fetchall()}


_store: Any = None


def get_store():
    global _store
    if _store is None:
        _store = SqlStore()
    return _store


def set_store(store) -> None:
    """Swap the store (tests); ``None`` restores the SQL default."""
    global _store
    _store = store


# -- the four steps (module functions, so a test can watch their order) ---------------------------------------------------------

async def put_barrier(user_id: str) -> None:
    import erase_watermark
    import user_erase_gate

    await erase_watermark.record(user_id, actor="member_removal")      # RAISES when it cannot be made durable: nothing else runs
    async with user_erase_gate.erasing(user_id):
        pass


def _auth_url() -> str:
    return os.environ.get("ZOE_AUTH_URL", "http://localhost:8002")


async def _auth_delete(user_id: str, session_id: str, *, permanent: bool) -> None:
    """zoe-auth's ``DELETE /api/admin/users/{id}`` as the administrator (their own session, passed through, never stored). A 404 is
    "no such sign-in" (already gone): the step is complete. Anything else but a 2xx: ``StepFailed``."""
    if not session_id:
        raise NeedsAdmin("an administrator's session is needed for the sign-in step")
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.delete(f"{_auth_url()}/api/admin/users/{user_id}",
                                params={"permanent": "true" if permanent else "false"}, headers={"X-Session-ID": session_id})
    if r.status_code == 404 or 200 <= r.status_code < 300:
        return
    raise StepFailed(f"zoe-auth answered {r.status_code}")


async def deactivate_sign_in(user_id: str, session_id: str) -> None:
    await _auth_delete(user_id, session_id, permanent=False)


async def full_erase(user_id: str, actor: str) -> None:
    from memory_service import get_memory_service

    await get_memory_service().delete_user(user_id, actor=actor or "member_removal", reason="settings_remove_member")


async def delete_sign_in(user_id: str, session_id: str) -> None:
    await _auth_delete(user_id, session_id, permanent=True)


# -- the job -----------------------------------------------------------------------------------------------------------------

def _lock(user_id: str) -> asyncio.Lock:
    return _LOCKS.setdefault(user_id, asyncio.Lock())


async def advance(user_id: str, *, actor: str = "", session_id: str = "") -> str:
    """Run the job from where it stands, as far as it can go. Returns the step reached. A step that fails leaves the job at the step
    before it (recorded), records the failure's type name and returns; it never skips a step and never runs one out of order."""
    uid = str(user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required")
    store = get_store()
    async with _lock(uid):
        _RUNNING.add(uid)
        try:
            state = await store.get(uid)
            try:
                if state is None:
                    await put_barrier(uid)
                    state = "barrier"
                    await store.put(uid, state)
                if state == "barrier":
                    await deactivate_sign_in(uid, session_id)
                    state = "deactivated"
                    await store.put(uid, state)
                if state == "deactivated":
                    await full_erase(uid, actor)
                    state = "erased"
                    await store.put(uid, state)
                if state == "erased":
                    await delete_sign_in(uid, session_id)
                    state = "done"
                    await store.put(uid, state)
                _LAST_ERROR.pop(uid, None)
            except NeedsAdmin:
                _LAST_ERROR[uid] = "needs_admin"
            except Exception as exc:  # noqa: BLE001 - every failure leaves the job pending; the type name is all that is kept
                _LAST_ERROR[uid] = type(exc).__name__
                logger.warning("MEMBER_REMOVAL step_failed at=%s code=%s", state or "start", type(exc).__name__)
            return state or "start"
        finally:
            _RUNNING.discard(uid)


def status_of(user_id: str, state: Optional[str]) -> dict:
    """What the administrator is told, in plain states: ``in_progress`` (running now), ``retrying`` (not finished: a step failed or is
    waiting for an administrator; Zoe keeps trying), ``done`` (every step confirmed, the erase included), ``none`` (no removal)."""
    uid = str(user_id or "")
    if state is None:
        if uid in _RUNNING:                     # started, the barrier not written yet
            return {"user_id": uid, "state": "start", "status": "in_progress", "erased": False}
        if uid in _LAST_ERROR:                  # the barrier itself could not be written: nothing happened, nothing claimed
            return {"user_id": uid, "state": "start", "status": "retrying", "erased": False, "reason": _LAST_ERROR[uid]}
        return {"user_id": uid, "state": "none", "status": "none"}
    if state == "done":
        return {"user_id": uid, "state": state, "status": "done"}
    status = "in_progress" if uid in _RUNNING else "retrying"
    out = {"user_id": uid, "state": state, "status": status, "erased": state in ("erased", "done")}
    if status == "retrying" and uid in _LAST_ERROR:
        out["reason"] = _LAST_ERROR[uid]
    return out


async def status(user_id: str) -> dict:
    return status_of(user_id, await get_store().get(str(user_id or "")))


async def pending() -> list:
    rows = await get_store().all()
    return [status_of(u, s) for u, s in sorted(rows.items()) if s != "done"]


async def resume_unattended() -> int:
    """Resume every job that needs no administrator now: deactivated but not yet erased (step 3). Returns how many advanced."""
    n = 0
    for uid, state in sorted((await get_store().all()).items()):
        if state == "deactivated" and uid not in _RUNNING:
            if await advance(uid) != state:
                n += 1
    return n


async def run_retry_loop() -> None:
    """Started by ``main.lifespan``: at start and every ``RETRY_S``, the erase of every deactivated member is retried. Never raises."""
    while True:
        try:
            await resume_unattended()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("MEMBER_REMOVAL retry pass failed (%s)", type(exc).__name__)
        await asyncio.sleep(RETRY_S)
