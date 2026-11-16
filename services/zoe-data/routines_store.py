"""Routines, stage 1: the store (alembic 0081; record ``docs/knowledge/routines.md``).

A routine is a named, ordered checklist for ONE person (the subject), tied to a moment of the day, that starts fresh each day. This
module owns the four tables and every rule that keeps a tick honest. (This first slice holds routines, steps and both erasers; the
day copies, one-off lines and ticks follow in the next commit of the stack.) It speaks nothing and prompts nothing: the voice tier
(``routines_tier``) and the panel card routes call it.

FLAG ``ZOE_ROUTINES`` = off (default) | on, read per call by the callers. The store itself does not check it: the erasers must run
whatever the flag says (forgotten means forever).

THE RULES (each a store-level check, not a caller convention):
  * Text is stored and returned EXACTLY as entered: no strip, no case change, no number touched. Only an empty or over-long line is
    refused. (Speech is another matter: ``tts_waterfall._clean_for_speech`` rewrites dashes, ``~ > < | * _`` and degree signs, so a
    line read ALOUD can differ from the stored text; the save-time "Zoe will say it like this" check is a later PR.)
  * A day copy is made LAZILY, on first need (a read, a tick, a one-off). Template steps are copied in only once the day has come,
    so an edit before then still applies; a one-off for a later day creates that day's row early with no snapshot.
  * A SAID tick can only tick the step Zoe just named (``note_named``), for the same person, in the same session, within
    ``WALK_S`` (120 s). The caller never passes a step id for a said tick, so it cannot pick the wrong one.
  * A ``medicine`` step is ticked only by its subject, with a verified voice or the subject's own member session; never by a
    household tap, never on a family routine (the store refuses a medicine step there).
  * Visibility matches lists and reminders: a member sees their own routines and every ``family`` one; a guest / the unidentified
    panel sees family routines only. Personal = subject only for every write; family = any member, and a household tap may tick,
    skip or snooze a non-medicine line (the fridge rule).

FORGET WINS EVERY RACE (the store-wide write rule, pinned by a structural test that enumerates this module's writers): every write
path names its SUBJECT member (whose routine), its ACTOR member(s) (who acts; a household tap has none) and the ENTITY text it
carries, and runs inside ``_guarded_write``, which holds the erase gate of each member involved (sorted id order) and, inside ONE
real transaction with the same per-member advisory locks the erasers take, re-checks each member's erase mark (a write that began
before a member erase is refused, ``erased``) and the entity shield (the in-flight tombstone and the durable ``memory_forgotten``
ledger, exact words, fail closed: a forgotten name is never written, ``names_forgotten``). Reads apply no forgotten-entity filter:
the shield on write plus the gated erasers leave nothing to hide, and a failed erase fails the cascade (it is retried).

LOGS: one marker per event, codes and counts only - ``ROUTINE event=<prompted|ticked|skipped|snoozed|undo|missed|refused|created|
step_added|one_off> kind=<k> via=<said|tapped|-> verified=<0|1> member=<hash8> routine=<hash8>``; never a step's text or a name.
``undo quick=1`` (an undo within ``QUICK_UNDO_S`` of the tick) is the wrong-tick measure, which must stay 0.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_ROUTINES"
ANCHORS = ("waking", "breakfast", "lunch", "dinner", "leaving", "bed", "window")
KINDS = ("check", "take_with", "medicine")          # 'reading' arrives with its own PR
ONE_OFF_KINDS = ("check", "take_with")
VISIBILITY = ("personal", "family")
PROMPT_MODES = ("card", "silent", "phone")
#: the household default window per anchor ('HH:MM', household time); a routine's own window overrides it
DEFAULT_WINDOWS = {"waking": ("05:00", "10:00"), "breakfast": ("06:00", "10:00"), "leaving": ("06:00", "10:00"),
                   "lunch": ("11:00", "14:00"), "dinner": ("16:00", "20:00"), "bed": ("19:00", "23:59"), "window": ("00:00", "23:59")}
MAX_TEXT = 300
MAX_NAME = 60
MAX_STEPS = 30
WALK_S = 120.0
QUICK_UNDO_S = 600.0
SNOOZE_MIN = 15
ONE_OFF_BASE = 1000                                  # one-off lines sort after the template steps


def enabled() -> bool:
    from typed_env import flag

    return str(flag(ENV) or "off").strip().lower() == "on"


class StoreError(Exception):
    """A refused write: ``code`` is a fixed word the caller maps to its reply / HTTP status (``status``)."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


# -- time --------------------------------------------------------------------------------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def household_now(now: Optional[datetime] = None) -> datetime:
    """The household clock (``ZOE_TIMEZONE``), the same clock reminders fire on."""
    import zoneinfo

    from typed_env import flag

    try:
        tz = zoneinfo.ZoneInfo(str(flag("ZOE_TIMEZONE") or "Australia/Perth"))
    except Exception:  # noqa: BLE001
        tz = timezone.utc
    return (now or _now_utc()).astimezone(tz)


def household_today(now: Optional[datetime] = None) -> date:
    return household_now(now).date()


def _iso(now: Optional[datetime] = None) -> str:
    return (now or _now_utc()).astimezone(timezone.utc).isoformat()


# -- the store plumbing ------------------------------------------------------------------------------------------------------------
def _db():
    from db_compat import get_compat_db

    return get_compat_db()


def _no_store(exc: BaseException) -> bool:
    """"There is nothing to erase": the tables are not migrated, or the process NEVER had a database pool (a lab / bare unit run). A
    pool that was lost is not that, and still raises."""
    s = str(exc)
    if "routine" in s and ("no such table" in s or "does not exist" in s):
        return True
    if isinstance(exc, RuntimeError) and re.match(r"(?:db[_ ])?pool not initialised", s):
        from db_pool import pool_ever_initialised

        return not pool_ever_initialised()
    return False


async def _rows(db, sql: str, params=()) -> list[tuple]:
    cur = await db.execute(sql, tuple(params))
    return [tuple(r) for r in (await cur.fetchall() or [])]


def h8(value: str) -> str:
    return hashlib.sha256(str(value or "").encode()).hexdigest()[:8]


def _log(event: str, *, kind: str = "-", via: str = "-", verified: Optional[bool] = None, member: str = "", routine: str = "",
         **extra) -> None:
    tail = "".join(f" {k}={v}" for k, v in extra.items())
    logger.info("ROUTINE event=%s kind=%s via=%s verified=%s member=%s routine=%s%s", event, kind, via,
                "-" if verified is None else int(bool(verified)), h8(member) if member else "-", h8(routine) if routine else "-", tail)


# -- the ONE write gate (every write path in this module goes through it; a structural test enumerates them) -----------------
def _start() -> dict:
    """Every member's erase generation NOW: taken at the START of a write, before its first read. A whole-member erase after this
    point makes the write stale for good (``_guarded_write`` refuses it), however long it then waits."""
    import user_erase_gate as erase_gate

    return erase_gate.snapshot()


class _Write:
    """What a guarded write holds: ``db`` (inside ONE real transaction on Postgres) and the shield over the members' forgotten
    entities, fetched under the gate before the connection was borrowed (``forgotten`` is pure, so it may be asked mid-transaction)."""

    def __init__(self, members: list, hashes: dict):
        self.members, self._hashes, self.db = members, hashes, None

    def forgotten(self, text) -> bool:
        if not text:
            return False
        import memory_forgotten
        import memory_tombstones

        for m in self.members:
            if memory_tombstones.matching_tombstone(m, str(text)):
                return True
            wanted = self._hashes.get(m)
            if wanted and memory_forgotten.spans_in(m, str(text), wanted, near=False)[0]:
                return True
        return False

    def refuse_forgotten(self, *texts) -> None:
        if any(self.forgotten(t) for t in texts):
            raise StoreError("names_forgotten", 409)


async def _forgotten_hashes(member: str) -> frozenset:
    import memory_forgotten

    if not memory_forgotten.configured():
        return frozenset()                       # unconfigured: nothing can have been forgotten durably (the tombstone still holds)
    try:
        return await memory_forgotten.active_hashes(member, strict=True)
    except memory_forgotten.LedgerUnavailable:
        raise StoreError("ledger_unavailable", 503) from None   # fail closed: an outage never lets a forgotten name be written


@contextlib.asynccontextmanager
async def _txn(db, members):
    """ONE real transaction holding a per-member advisory lock (``list_service.list_write_txn``'s pattern), so an eraser and a writer
    serialise across PROCESSES too (``db_pool.AsyncpgCompat.commit`` is a no-op: without this every statement autocommits). Locks are
    taken in sorted member order. A DB shim without ``transaction`` (the SQLite tests) runs unwrapped."""
    txn = getattr(db, "transaction", None)
    if not callable(txn):
        yield
        return
    async with txn():
        for m in sorted(members):
            await db.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (f"routines|{m}",))
        yield


def _erased(seen: dict, member: str) -> bool:
    import user_erase_gate as erase_gate

    return erase_gate.generation(member) != seen.get(member, 0) or erase_gate.in_progress(member)


@contextlib.asynccontextmanager
async def _guarded_write(seen: dict, *, subject: str, actors=(), texts=()):
    """THE write gate. ``seen`` = ``_start()`` taken before the write's first read; ``subject`` = whose routine; ``actors`` = who is
    acting (a member id; None / a guest = a household tap); ``texts`` = the entity text the write carries.

    1. the erase gate of EVERY member involved (subject and actors), in sorted id order (the one global order: a writer never waits
       on a lower id while holding a higher one; an eraser holds one member's gate only, so no cycle can form);
    2. each member's erase mark: moved since ``seen`` or a whole-member erase running = ``StoreError("erased")`` (a stale write);
    3. the entity shield (in-flight tombstone + the durable ``memory_forgotten`` ledger, exact words, fail closed) over ``texts``
       for every member: a forgotten name is never written (``StoreError("names_forgotten")``);
    4. the connection, ONE transaction, the per-member advisory locks (same order), then (2) again immediately before the write.
    The erasers (``forget_user`` / ``erase_entity``) take the same gate and the same advisory lock, so a write lands wholly before
    an erase (and is erased) or wholly after it (and is refused)."""
    import user_erase_gate as erase_gate

    members = sorted({m for m in (subject, *actors) if m and not _guest(m)})
    async with contextlib.AsyncExitStack() as stack:
        for m in members:
            live = await stack.enter_async_context(erase_gate.writing(m, seen.get(m, 0)))
            if not live or _erased(seen, m):
                raise StoreError("erased", 409)
        w = _Write(members, {m: await _forgotten_hashes(m) for m in members})   # before a connection is borrowed (the ledger may borrow one)
        w.refuse_forgotten(*texts)
        w.db = await stack.enter_async_context(_db())
        await stack.enter_async_context(_txn(w.db, members))
        if any(_erased(seen, m) for m in members):
            raise StoreError("erased", 409)
        yield w
        await w.db.commit()


@contextlib.asynccontextmanager
async def _guarded_erase(member: str, *, whole: bool):
    """The erasers' half of the gate: the member's erase gate (``whole`` bumps the generation: every write scheduled before it is
    stale), the connection, one transaction with the member's advisory lock."""
    import user_erase_gate as erase_gate

    async with erase_gate.erasing(member, bump=whole), _db() as db, _txn(db, [member]):
        yield db
        await db.commit()


def _guest(user_id: Optional[str]) -> bool:
    if not user_id:
        return True
    import panel_principal

    return panel_principal.is_guest_id(user_id)


def _check_text(text, cap: int = MAX_TEXT) -> str:
    if not isinstance(text, str) or not text.strip():
        raise StoreError("empty_text")
    if len(text) > cap:
        raise StoreError("text_too_long")
    return text                                   # verbatim: never stripped, re-cased or rewritten


_R_COLS = ("id", "user_id", "created_by", "name", "anchor", "window_start", "window_end", "days_rule", "visibility", "prompt_mode",
           "enabled", "created_at")
_DS_COLS = ("id", "day_id", "step_id", "origin", "text", "kind", "position", "state", "snooze_until", "known_by", "actor_user_id",
            "verified", "prompts", "updated_at")


def _routine(row: tuple) -> dict:
    return dict(zip(_R_COLS, row))


# -- visibility --------------------------------------------------------------------------------------------------------------------
def can_see(routine: dict, viewer: Optional[str]) -> bool:
    """Own routines and family ones; a guest / the unidentified panel (``viewer`` None or a guest id) sees family ones only."""
    if routine.get("visibility") == "family":
        return True
    return not _guest(viewer) and routine.get("user_id") == viewer


def can_edit(routine: dict, actor: Optional[str]) -> bool:
    if _guest(actor):
        return False
    if routine.get("visibility") == "family":
        return True                                  # any member, as family reminders
    return actor in (routine.get("user_id"), routine.get("created_by"))


# -- routines and steps ------------------------------------------------------------------------------------------------------------
async def create_routine(owner: str, name: str, anchor: str, *, created_by: Optional[str] = None, visibility: str = "personal",
                         window_start: Optional[str] = None, window_end: Optional[str] = None, days_rule: Optional[str] = None,
                         prompt_mode: str = "card", steps=()) -> dict:
    """A new routine for ``owner`` (the subject) with its template steps (``(text, kind)`` pairs or bare texts). Raises StoreError."""
    seen = _start()
    if _guest(owner):
        raise StoreError("guest_owns_nothing", 403)
    _check_text(name, MAX_NAME)
    if anchor not in ANCHORS or visibility not in VISIBILITY or prompt_mode not in PROMPT_MODES:
        raise StoreError("bad_field")
    for hm in (window_start, window_end):
        if hm is not None and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", hm):
            raise StoreError("bad_window")
    if days_rule:
        from reminder_recurrence import parse_rrule

        if parse_rrule(days_rule) is None:
            raise StoreError("bad_days_rule")
    pairs = [(s, "check") if not isinstance(s, (tuple, list)) else (s[0], s[1]) for s in steps]
    if len(pairs) > MAX_STEPS:
        raise StoreError("too_many_steps")
    for text, kind in pairs:
        _check_text(text)
        _check_kind(kind, visibility)
    rid, now = str(uuid.uuid4()), _iso()
    async with _guarded_write(seen, subject=owner, actors=(created_by or owner,), texts=(name, *(t for t, _ in pairs))) as w:
        db = w.db
        await db.execute(
            "INSERT INTO routines (id, user_id, created_by, name, anchor, window_start, window_end, days_rule, visibility, prompt_mode, "
            "enabled, deleted, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)",
            (rid, owner, created_by or owner, name, anchor, window_start, window_end, days_rule, visibility, prompt_mode, now, now))
        for pos, (text, kind) in enumerate(pairs, 1):
            await db.execute("INSERT INTO routine_steps (id, routine_id, position, text, kind, deleted, created_at, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, 0, ?, ?)", (str(uuid.uuid4()), rid, pos, text, kind, now, now))
    _log("created", member=owner, routine=rid, steps=len(pairs))
    return await get_routine(owner, rid)


def _check_kind(kind: str, visibility: str) -> None:
    if kind not in KINDS:
        raise StoreError("bad_kind")
    if kind == "medicine" and visibility == "family":
        raise StoreError("medicine_not_on_family")


async def _load(db, routine_id: str) -> Optional[dict]:
    rows = await _rows(db, f"SELECT {', '.join(_R_COLS)} FROM routines WHERE id = ? AND deleted = 0", (routine_id,))
    return _routine(rows[0]) if rows else None


async def _steps(db, routine_id: str) -> list[dict]:
    rows = await _rows(db, "SELECT id, position, text, kind FROM routine_steps WHERE routine_id = ? AND deleted = 0 ORDER BY position",
                       (routine_id,))
    return [{"id": r[0], "position": int(r[1]), "text": r[2], "kind": r[3]} for r in rows]


async def get_routine(viewer: Optional[str], routine_id: str) -> Optional[dict]:
    """The routine with its template steps, or None when it does not exist OR the viewer may not see it (never a 403 oracle)."""
    async with _db() as db:
        r = await _load(db, routine_id)
        if r is None or not can_see(r, viewer):
            return None
        r["steps"] = await _steps(db, routine_id)
    return r


async def list_routines(viewer: Optional[str]) -> list[dict]:
    """Every routine ``viewer`` may see, the same filter as lists: ``visibility = 'family' OR user_id = viewer``."""
    me = None if _guest(viewer) else viewer
    async with _db() as db:
        rows = await _rows(db, f"SELECT {', '.join(_R_COLS)} FROM routines WHERE deleted = 0 AND (visibility = 'family' OR user_id = ?) "
                               "ORDER BY created_at", (me,))
    return [_routine(r) for r in rows]


async def add_step(actor: str, routine_id: str, text: str, kind: str = "check") -> dict:
    seen = _start()
    _check_text(text)
    async with _db() as db:
        r = await _load(db, routine_id)
    if r is None or not can_see(r, actor):
        raise StoreError("not_found", 404)
    if not can_edit(r, actor):
        raise StoreError("not_allowed", 403)
    _check_kind(kind, r["visibility"])
    sid, now = str(uuid.uuid4()), _iso()
    async with _guarded_write(seen, subject=r["user_id"], actors=(actor,), texts=(text,)) as w:
        if await _load(w.db, routine_id) is None:              # erased or deleted since the read above
            raise StoreError("not_found", 404)
        top = await _rows(w.db, "SELECT COALESCE(MAX(position), 0), COUNT(*) FROM routine_steps WHERE routine_id = ? AND deleted = 0",
                          (routine_id,))
        if int(top[0][1]) >= MAX_STEPS:
            raise StoreError("too_many_steps")
        await w.db.execute("INSERT INTO routine_steps (id, routine_id, position, text, kind, deleted, created_at, updated_at) "
                           "VALUES (?, ?, ?, ?, ?, 0, ?, ?)", (sid, routine_id, int(top[0][0]) + 1, text, kind, now, now))
    _log("step_added", kind=kind, member=r["user_id"], routine=routine_id)
    return {"id": sid, "text": text, "kind": kind}


# -- forget ------------------------------------------------------------------------------------------------------------------------
async def _delete_routines(db, ids: list[str]) -> int:
    n = 0
    for rid in ids:
        await db.execute("DELETE FROM routine_day_steps WHERE day_id IN (SELECT id FROM routine_days WHERE routine_id = ?)", (rid,))
        await db.execute("DELETE FROM routine_days WHERE routine_id = ?", (rid,))
        await db.execute("DELETE FROM routine_steps WHERE routine_id = ?", (rid,))
        cur = await db.execute("DELETE FROM routines WHERE id = ?", (rid,))
        n += int(getattr(cur, "rowcount", 0) or 0)
    return n


async def forget_user(user_id: str) -> int:
    """The member erase (``MemoryService.delete_user``), whatever the flag says: every routine the member is the subject of, with its
    steps, days and lines, deleted; the member blanked as the actor / creator on anyone else's. RAISES on a failure (fail closed)."""
    uid = str(user_id or "").strip()
    if not uid:
        return 0
    try:
        async with _guarded_erase(uid, whole=True) as db:
            ids = [r[0] for r in await _rows(db, "SELECT id FROM routines WHERE user_id = ?", (uid,))]
            n = await _delete_routines(db, ids)
            await db.execute("DELETE FROM routine_day_steps WHERE day_id IN (SELECT id FROM routine_days WHERE user_id = ?)", (uid,))
            await db.execute("DELETE FROM routine_days WHERE user_id = ?", (uid,))
            await db.execute("UPDATE routine_day_steps SET actor_user_id = NULL WHERE actor_user_id = ?", (uid,))
            await db.execute("UPDATE routines SET created_by = user_id WHERE created_by = ?", (uid,))
    except Exception as exc:  # noqa: BLE001
        if _no_store(exc):
            return 0
        logger.error("ROUTINE event=forget_failed error=%s", type(exc).__name__)
        raise
    logger.info("ROUTINE event=forget_user routines=%d", n)
    return n


async def erase_entity(user_id: str, rx: "re.Pattern[str]", *, strict: bool = False) -> int:
    """"Forget Dana": in the member's own routines, a routine whose NAME names the entity goes whole; a template step or a day line
    whose words name it is deleted. Counts only are logged. ``strict`` raises on a failure (the cascade's contract).

    Under the member's erase gate and advisory lock (``_guarded_erase``, no generation bump): a write in flight lands first and is
    erased here; one queued behind this sees the forgotten ledger (written by the forget BEFORE the cascade runs) and is refused."""
    try:
        n = 0
        async with _guarded_erase(user_id, whole=False) as db:
            mine = await _rows(db, "SELECT id, name FROM routines WHERE user_id = ?", (user_id,))
            n += await _delete_routines(db, [rid for rid, name in mine if rx.search(name or "")])
            keep = [rid for rid, name in mine if not rx.search(name or "")]
            for rid in keep:
                for sid, text in await _rows(db, "SELECT id, text FROM routine_steps WHERE routine_id = ?", (rid,)):
                    if rx.search(text or ""):
                        await db.execute("DELETE FROM routine_steps WHERE id = ?", (sid,))
                        n += 1
                for dsid, text in await _rows(db, "SELECT ds.id, ds.text FROM routine_day_steps ds JOIN routine_days d ON d.id = ds.day_id "
                                                  "WHERE d.routine_id = ?", (rid,)):
                    if rx.search(text or ""):
                        await db.execute("DELETE FROM routine_day_steps WHERE id = ?", (dsid,))
                        n += 1
        logger.info("ROUTINE event=erase_entity removed=%d", n)
        return n
    except Exception as exc:  # noqa: BLE001
        if _no_store(exc):
            return 0
        logger.error("ROUTINE event=erase_entity_failed error=%s", type(exc).__name__)
        if strict:
            raise
        return 0
