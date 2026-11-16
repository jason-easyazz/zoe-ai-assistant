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
erased, at start and every ``RETRY_S``. The job row is written as ``intent`` BEFORE the barrier (a crash between the two leaves a
resumable job, never an orphan mark), and every step moves it by compare-and-set (``SqlStore.put``), so a second runner can never
move it backwards or skip a step. The physical erase of on-disk palace residue inside ``delete_user`` stays that eraser's documented
best-effort step (reported in ``MemoryService.last_erase_report``); the logical erase is what step 3 confirms.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typed_env import env_str
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

KEY = "member_delete|job"
SCOPE = "member_delete"
NEVER = "0000-01-01T00:00:00Z"
STEPS = ("intent", "barrier", "deactivated", "erased", "done")
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


def _parse(at: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime(str(at), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


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

    async def put(self, user_id: str, step: str, expect: Optional[str]) -> bool:
        """Move the job to ``step`` only from ``expect`` (None = create it): a compare-and-set, so a second runner can never move a
        job backwards or over a step it did not see. True when this call moved it."""
        async with self._db() as db:
            if expect is None:
                cur = await db.execute(
                    """INSERT INTO memory_forgotten (user_id, key_hash, scope, actor, forgotten_at, shield_until)
                       VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (user_id, key_hash) DO NOTHING""",
                    (user_id, KEY, SCOPE, step, _now(), NEVER))
            else:
                cur = await db.execute(
                    "UPDATE memory_forgotten SET actor = ?, forgotten_at = ? WHERE user_id = ? AND key_hash = ? AND actor = ?",
                    (step, _now(), user_id, KEY, expect))
            await db.commit()
        return int(getattr(cur, "rowcount", 0) or 0) == 1

    async def claimed_at(self, user_id: str) -> Optional[str]:
        async with self._db() as db:
            cur = await db.execute("SELECT forgotten_at FROM memory_forgotten WHERE user_id = ? AND key_hash = ?", (user_id, KEY))
            row = await cur.fetchone()
        return str(row[0]) if row and row[0] else None

    async def reclaim(self, user_id: str, claim: str, at: str) -> bool:
        """Release a STALE claim (its runner died): back to the step it was taken from, only if nobody touched it since ``at``."""
        async with self._db() as db:
            cur = await db.execute(
                "UPDATE memory_forgotten SET actor = ?, forgotten_at = ? WHERE user_id = ? AND key_hash = ? AND actor = ? AND forgotten_at = ?",
                (base_step(claim), _now(), user_id, KEY, claim, at))
            await db.commit()
        return int(getattr(cur, "rowcount", 0) or 0) == 1

    async def all(self) -> dict:
        async with self._db() as db:
            cur = await db.execute("SELECT user_id, actor FROM memory_forgotten WHERE key_hash = ?", (KEY,))
            return {str(r[0]): str(r[1]) for r in await cur.fetchall()}


#: a step being run is CLAIMED durably first (``<step>+run:<owner token>``); a claim older than this belongs to a runner that died and
#: is released. Completing or releasing a claim is a compare-and-set on the WHOLE claim (token included): a runner whose claim was
#: taken over can neither complete nor release the new owner's. (Expiry cannot prove the old runner stopped; every step is idempotent -
#: the mark, a deactivation, the erase, a sign-in delete answered 404 - so a straggler repeats work, it never moves the job.)
CLAIM = "+run"
CLAIM_STALE_S = 900.0


def base_step(state: Optional[str]) -> Optional[str]:
    return state.split(CLAIM, 1)[0] if state and CLAIM in state else state


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
    import auth
    import erase_watermark
    import user_erase_gate

    # the member's REQUESTS stop first: their cached identities are dropped and every request of theirs is refused in this process
    # (another process refuses them from the durable removal row, written before this step: ``auth._under_removal``)
    auth.bar_member(user_id)
    await erase_watermark.record(user_id, actor="member_removal")      # RAISES when it cannot be made durable: nothing else runs
    async with user_erase_gate.erasing(user_id):
        pass


def _auth_url() -> str:
    return env_str("ZOE_AUTH_URL", "http://localhost:8002")


#: a member id the job accepts: what zoe-auth mints, nothing that could change the shape of the outbound URL
_ID_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")


def valid_member_id(user_id: str) -> bool:
    return bool(_ID_RX.match(str(user_id or "")))


async def _auth_delete(user_id: str, session_id: str, *, permanent: bool) -> None:
    """zoe-auth's ``DELETE /api/admin/users/{id}`` as the administrator (their own session, passed through, never stored). A 404 is
    "no such sign-in" (already gone): the step is complete. Anything else but a 2xx: ``StepFailed``. The id is validated and
    percent-encoded as ONE path segment, so the call can only ever name the member the job erased."""
    if not valid_member_id(user_id):
        raise StepFailed("not a member id")
    if not session_id:
        raise NeedsAdmin("an administrator's session is needed for the sign-in step")
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.delete(f"{_auth_url()}/api/admin/users/{quote(user_id, safe='')}",
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
    if not valid_member_id(uid):
        raise ValueError("not a member id")
    store = get_store()

    async def move(to: str, frm: Optional[str]) -> str:
        if not await store.put(uid, to, frm):         # another runner moved it: never act on a step this call did not see
            raise StepFailed("the job moved under this runner")
        return to

    async def step(frm: str, to: str, run) -> str:
        """CLAIM the step durably (``frm`` -> ``frm+run``, compare-and-set) BEFORE its side effect, so no second runner (another
        process) can run it too; then ``frm+run`` -> ``to``. A failed step releases its claim back to ``frm``."""
        claim = await move(f"{frm}{CLAIM}:{uuid.uuid4().hex[:12]}", frm)   # the token makes the claim THIS runner's alone
        try:
            await run()
        except BaseException:
            with contextlib.suppress(Exception):
                await store.put(uid, frm, claim)
            raise
        return await move(to, claim)

    async with _lock(uid):
        _RUNNING.add(uid)
        try:
            state = await store.get(uid)
            try:
                if state and CLAIM in state:   # a claim this process does not hold: wait, unless its runner is long gone
                    at = await store.claimed_at(uid)
                    stamp = _parse(at)
                    if stamp is not None and (datetime.now(timezone.utc) - stamp).total_seconds() < CLAIM_STALE_S:
                        raise StepFailed("another runner holds this step")
                    if not await store.reclaim(uid, state, at):
                        raise StepFailed("the job moved under this runner")
                    state = base_step(state)
                if state is None:                     # the INTENT is durable before the barrier: a crash between the two is resumed
                    state = await move("intent", None)
                if state == "intent":                 # put_barrier is idempotent: a resumed intent writes the mark again
                    state = await step("intent", "barrier", lambda: put_barrier(uid))
                if state == "barrier":
                    state = await step("barrier", "deactivated", lambda: deactivate_sign_in(uid, session_id))
                if state == "deactivated":
                    state = await step("deactivated", "erased", lambda: full_erase(uid, actor))
                if state == "erased":
                    state = await step("erased", "done", lambda: delete_sign_in(uid, session_id))
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
    claimed = CLAIM in state                 # a step is being run (here, or by a runner elsewhere)
    state = base_step(state)
    status = "in_progress" if uid in _RUNNING or claimed else "retrying"
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
        if (base_step(state) in ("intent", "deactivated") or CLAIM in state) and uid not in _RUNNING:
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
