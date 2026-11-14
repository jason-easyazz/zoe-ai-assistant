"""Routines, stage 1: the store (alembic 0082; record ``docs/knowledge/routines.md``).

A routine is a named, ordered checklist for ONE person (the subject), tied to a moment of the day, that starts fresh each day. This
module owns the four tables and every rule that keeps a tick honest. It speaks nothing and prompts nothing: the voice tier
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


def window_of(routine: dict) -> tuple[str, str]:
    d = DEFAULT_WINDOWS.get(routine.get("anchor") or "window", ("00:00", "23:59"))
    return (routine.get("window_start") or d[0], routine.get("window_end") or d[1])


def in_window(routine: dict, now: Optional[datetime] = None) -> bool:
    hm = household_now(now).strftime("%H:%M")
    a, b = window_of(routine)
    return a <= hm <= b


def runs_on(routine: dict, day: date) -> bool:
    """Does the routine run on ``day``? NULL rule = every day; an unparseable rule = never (fail closed: no wrong day copy)."""
    rule = routine.get("days_rule")
    if not rule:
        return True
    from reminder_recurrence import occurs_on, parse_rrule

    parsed = parse_rrule(rule)
    if parsed is None:
        return False
    try:
        anchor = date.fromisoformat(str(routine.get("created_at") or "")[:10])
    except ValueError:
        anchor = day
    return occurs_on(parsed, min(anchor, day), day)


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


@contextlib.asynccontextmanager
async def _write_gate(owner: str):
    """Hold the subject's erase gate through the write: a write that races ``forget_user`` lands before it (and is erased) or is
    dropped after it - never re-creates what was just forgotten."""
    import user_erase_gate as erase_gate

    async with erase_gate.writing(owner, erase_gate.generation(owner)) as live:
        if not live:
            raise StoreError("erased", 409)
        yield


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


def can_tick(routine: dict, kind: str, actor: Optional[str], *, verified: bool) -> Optional[str]:
    """None when ``actor`` may tick / skip / snooze a line of ``kind``; else the refusal code."""
    if kind == "medicine":
        if _guest(actor) or actor != routine.get("user_id"):
            return "not_the_subject"
        return None if verified else "unverified"
    if routine.get("visibility") == "family":
        return None                                  # the fridge rule: anyone at the panel, a household tap included
    return None if (not _guest(actor) and actor == routine.get("user_id")) else "not_the_subject"


# -- routines and steps ------------------------------------------------------------------------------------------------------------
async def create_routine(owner: str, name: str, anchor: str, *, created_by: Optional[str] = None, visibility: str = "personal",
                         window_start: Optional[str] = None, window_end: Optional[str] = None, days_rule: Optional[str] = None,
                         prompt_mode: str = "card", steps=()) -> dict:
    """A new routine for ``owner`` (the subject) with its template steps (``(text, kind)`` pairs or bare texts). Raises StoreError."""
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
    async with _write_gate(owner), _db() as db:
        await db.execute(
            "INSERT INTO routines (id, user_id, created_by, name, anchor, window_start, window_end, days_rule, visibility, prompt_mode, "
            "enabled, deleted, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)",
            (rid, owner, created_by or owner, name, anchor, window_start, window_end, days_rule, visibility, prompt_mode, now, now))
        for pos, (text, kind) in enumerate(pairs, 1):
            await db.execute("INSERT INTO routine_steps (id, routine_id, position, text, kind, deleted, created_at, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, 0, ?, ?)", (str(uuid.uuid4()), rid, pos, text, kind, now, now))
        await db.commit()
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
    _check_text(text)
    async with _db() as db:
        r = await _load(db, routine_id)
    if r is None or not can_see(r, actor):
        raise StoreError("not_found", 404)
    if not can_edit(r, actor):
        raise StoreError("not_allowed", 403)
    _check_kind(kind, r["visibility"])
    sid, now = str(uuid.uuid4()), _iso()
    async with _write_gate(r["user_id"]), _db() as db:
        top = await _rows(db, "SELECT COALESCE(MAX(position), 0), COUNT(*) FROM routine_steps WHERE routine_id = ? AND deleted = 0",
                          (routine_id,))
        if int(top[0][1]) >= MAX_STEPS:
            raise StoreError("too_many_steps")
        await db.execute("INSERT INTO routine_steps (id, routine_id, position, text, kind, deleted, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, 0, ?, ?)", (sid, routine_id, int(top[0][0]) + 1, text, kind, now, now))
        await db.commit()
    _log("step_added", kind=kind, member=r["user_id"], routine=routine_id)
    return {"id": sid, "text": text, "kind": kind}


# -- day copies --------------------------------------------------------------------------------------------------------------------
def _day_id(routine_id: str, day: date) -> str:
    return f"{routine_id}:{day.isoformat()}"


async def _ensure_day(db, r: dict, day: date, today: date) -> str:
    """The (routine, day) row, made if missing (deterministic id + ON CONFLICT: two racing callers make ONE). Template steps are
    copied in once the day has come; earlier days of the routine still open are settled (``missed``) on the way."""
    did, now = _day_id(r["id"], day), _iso()
    await db.execute("INSERT INTO routine_days (id, routine_id, user_id, day, created_at) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT DO NOTHING", (did, r["id"], r["user_id"], day.isoformat(), now))
    if day <= today:
        snap = await _rows(db, "SELECT snapshot_at FROM routine_days WHERE id = ?", (did,))
        if snap and snap[0][0] is None:
            for s in await _steps(db, r["id"]):
                await db.execute(
                    "INSERT INTO routine_day_steps (id, day_id, step_id, origin, text, kind, position, state, prompts, created_via, "
                    "created_at, updated_at) VALUES (?, ?, ?, 'template', ?, ?, ?, 'pending', 0, NULL, ?, ?) ON CONFLICT DO NOTHING",
                    (f"{did}:{s['id']}", did, s["id"], s["text"], s["kind"], s["position"], now, now))
            await db.execute("UPDATE routine_days SET snapshot_at = ? WHERE id = ? AND snapshot_at IS NULL", (now, did))
        await _settle_before(db, r, day)
    await db.commit()
    return did


async def _settle_before(db, r: dict, day: date) -> None:
    """Close the routine's earlier open days: every line still pending there is ``missed`` (logged), the day is closed."""
    for (did,) in await _rows(db, "SELECT id FROM routine_days WHERE routine_id = ? AND day < ? AND closed_at IS NULL "
                                  "AND snapshot_at IS NOT NULL", (r["id"], day.isoformat())):
        for kind, n in await _rows(db, "SELECT kind, COUNT(*) FROM routine_day_steps WHERE day_id = ? AND state IN ('pending', 'snoozed') "
                                       "GROUP BY kind", (did,)):
            _log("missed", kind=kind, member=r["user_id"], routine=r["id"], n=int(n))
        await db.execute("UPDATE routine_days SET closed_at = ?, close_reason = 'window_end' WHERE id = ? AND closed_at IS NULL",
                         (_iso(), did))


async def day_copy(viewer: Optional[str], routine_id: str, day: Optional[date] = None, *, now: Optional[datetime] = None) -> Optional[dict]:
    """``viewer``'s view of one routine's copy for ``day`` (default today), made on first need. None = not visible / not found / the
    routine does not run that day. ``{"routine", "day", "day_id", "steps": [...]}``, steps in order with their states."""
    today = household_today(now)
    day = day or today
    async with _db() as db:
        r = await _load(db, routine_id)
    if r is None or not can_see(r, viewer) or not r["enabled"] or not runs_on(r, day):
        return None
    async with _write_gate(r["user_id"]), _db() as db:
        did = await _ensure_day(db, r, day, today)
        rows = await _rows(db, f"SELECT {', '.join(_DS_COLS)} FROM routine_day_steps WHERE day_id = ? ORDER BY position", (did,))
    steps = [dict(zip(_DS_COLS, x)) for x in rows]
    for s in steps:
        s["today_only"] = s["origin"] == "one_off"
    return {"routine": r, "day": day.isoformat(), "day_id": did, "steps": steps}


async def add_one_off(actor: str, routine_id: str, day: date, text: str, *, kind: str = "take_with", created_via: str = "voice",
                      now: Optional[datetime] = None) -> dict:
    """One extra line on ONE day's copy ("take the drill", tomorrow's morning). Refused for a past day, a day the routine does not run,
    a medicine kind (a medicine line is a template step, never a one-off), or someone who may not edit the routine."""
    _check_text(text)
    if kind not in ONE_OFF_KINDS:
        raise StoreError("bad_kind")
    today = household_today(now)
    if day < today:
        raise StoreError("past_day")
    async with _db() as db:
        r = await _load(db, routine_id)
    if r is None or not can_see(r, actor):
        raise StoreError("not_found", 404)
    if not can_edit(r, actor):
        raise StoreError("not_allowed", 403)
    if not r["enabled"] or not runs_on(r, day):
        raise StoreError("not_that_day")
    stamp = _iso()
    async with _write_gate(r["user_id"]), _db() as db:
        did = await _ensure_day(db, r, day, today)
        n = (await _rows(db, "SELECT COUNT(*) FROM routine_day_steps WHERE day_id = ? AND origin = 'one_off'", (did,)))[0][0]
        if int(n) >= MAX_STEPS:
            raise StoreError("too_many_steps")
        dsid = f"{did}:o{uuid.uuid4().hex[:12]}"
        await db.execute(
            "INSERT INTO routine_day_steps (id, day_id, step_id, origin, text, kind, position, state, prompts, created_via, created_at, "
            "updated_at) VALUES (?, ?, NULL, 'one_off', ?, ?, ?, 'pending', 0, ?, ?, ?)",
            (dsid, did, text, kind, ONE_OFF_BASE + int(n) + 1, created_via, stamp, stamp))
        await db.commit()
    _log("one_off", kind=kind, member=r["user_id"], routine=routine_id, via=created_via)
    return {"id": dsid, "day_id": did, "day": day.isoformat(), "text": text, "kind": kind}


# -- the walk: what Zoe just named -------------------------------------------------------------------------------------------------
@dataclass
class Named:
    member: str
    day_step_id: str
    at: float


_NAMED: "OrderedDict[str, Named]" = OrderedDict()      # session -> the step Zoe named last
_LAST: "OrderedDict[str, tuple]" = OrderedDict()       # session -> (day_step_id, member, at, previous state) of the last said change
_MAX_SESSIONS = 1024


def _mono() -> float:
    return time.monotonic()


def _bounded(d: OrderedDict, key: str, value) -> None:
    d[key] = value
    d.move_to_end(key)
    while len(d) > _MAX_SESSIONS:
        d.popitem(last=False)


async def note_named(session_id: str, member: str, day_step_id: str, *, dry: bool = False) -> None:
    """Zoe's reply just named ``day_step_id`` to ``member`` in ``session_id``: the ONLY step a bare "done" can now tick, for
    ``WALK_S``. Counts the prompt (``prompts`` + 1, ``event=prompted``)."""
    if not session_id or not member:
        return
    _bounded(_NAMED, session_id, Named(member, day_step_id, _mono()))
    if dry:
        return
    async with _db() as db:
        rows = await _rows(db, "SELECT ds.kind, d.routine_id, d.user_id FROM routine_day_steps ds JOIN routine_days d ON d.id = ds.day_id "
                               "WHERE ds.id = ?", (day_step_id,))
        if not rows:
            return
        await db.execute("UPDATE routine_day_steps SET prompts = prompts + 1, last_prompt_at = ? WHERE id = ?", (_iso(), day_step_id))
        await db.commit()
    _log("prompted", kind=rows[0][0], member=rows[0][2], routine=rows[0][1])


def named_step(session_id: str, member: str) -> Optional[str]:
    """The step a bare reply in this session may act on: named within ``WALK_S``, to this same member. Else None."""
    n = _NAMED.get(session_id or "")
    if n is None or n.member != member or _mono() - n.at > WALK_S:
        return None
    return n.day_step_id


def walk_open(session_id: str, member: str) -> bool:
    return named_step(session_id, member) is not None


def close_walk(session_id: str) -> None:
    _NAMED.pop(session_id or "", None)


async def _apply(ds_id: str, action: str, actor: Optional[str], *, via: str, verified: bool) -> dict:
    """Change one day line: done | skip | snooze (from pending / snoozed) or undo (back to pending). Every visibility and medicine
    rule is checked HERE, so neither the voice tier nor a card route can skip them."""
    async with _db() as db:
        rows = await _rows(db, "SELECT ds.state, ds.kind, d.routine_id, d.closed_at FROM routine_day_steps ds "
                               "JOIN routine_days d ON d.id = ds.day_id WHERE ds.id = ?", (ds_id,))
        r = await _load(db, rows[0][2]) if rows else None
    if not rows or r is None or not can_see(r, actor):
        raise StoreError("not_found", 404)
    state, kind = rows[0][0], rows[0][1]
    refusal = can_tick(r, kind, actor, verified=verified)
    if refusal:
        _log("refused", kind=kind, via=via, verified=verified, member=r["user_id"], routine=r["id"], reason=refusal)
        raise StoreError(refusal, 403)
    if action == "undo":
        if state not in ("done", "skipped", "snoozed"):
            raise StoreError("nothing_to_undo", 409)
        new = "pending"
    else:
        if state not in ("pending", "snoozed"):
            raise StoreError("already_" + state, 409)
        new = {"done": "done", "skip": "skipped", "snooze": "snoozed"}.get(action)
        if new is None:
            raise StoreError("bad_action")
    until = _iso(_now_utc() + timedelta(minutes=SNOOZE_MIN)) if new == "snoozed" else None
    known = None if new == "pending" else via
    async with _write_gate(r["user_id"]), _db() as db:
        cur = await db.execute(
            "UPDATE routine_day_steps SET state = ?, snooze_until = ?, known_by = ?, actor_user_id = ?, verified = ?, updated_at = ? "
            "WHERE id = ? AND state = ?",
            (new, until, known, None if _guest(actor) else actor, int(bool(verified)), _iso(), ds_id, state))
        if int(getattr(cur, "rowcount", 1) or 0) == 0:
            raise StoreError("changed_meanwhile", 409)       # a tap and a voice raced: the second never double-acts
        await db.commit()
    event = {"done": "ticked", "skipped": "skipped", "snoozed": "snoozed", "pending": "undo"}[new]
    _log(event, kind=kind, via=via, verified=verified, member=r["user_id"], routine=r["id"],
         **({"was": state} if new == "pending" else {}))
    return {"id": ds_id, "state": new, "previous": state, "kind": kind, "routine_id": r["id"]}


async def act_said(session_id: str, member: str, action: str, *, verified: bool) -> dict:
    """A bare spoken reply ("done" / "skip" / "later") inside a walk: acts on the step Zoe JUST named, nothing else. No step was
    named (or it was > ``WALK_S`` ago, another person, another session) = ``StoreError("nothing_named")``: nothing is ticked."""
    ds_id = named_step(session_id, member)
    if ds_id is None:
        raise StoreError("nothing_named", 409)
    out = await _apply(ds_id, action, member, via="said", verified=verified)
    _bounded(_LAST, session_id, (ds_id, member, _mono(), out["previous"]))
    close_walk(session_id)
    return out


async def undo_said(session_id: str, member: str, *, verified: bool) -> dict:
    """"No, I haven't" / "untick that" right after a said change: put that one line back. ``quick=1`` = within ``QUICK_UNDO_S``."""
    last = _LAST.get(session_id or "")
    if last is None or last[1] != member or _mono() - last[2] > WALK_S:
        raise StoreError("nothing_to_undo", 409)
    out = await _apply(last[0], "undo", member, via="said", verified=verified)
    _LAST.pop(session_id, None)
    _bounded(_NAMED, session_id, Named(member, last[0], _mono()))     # the line is named again: "done" can redo it
    logger.info("ROUTINE event=undo_window quick=%d", int(_mono() - last[2] <= QUICK_UNDO_S))
    return out


async def act_tapped(actor: Optional[str], day_step_id: str, action: str, *, verified: bool) -> dict:
    """A tap on a card row. ``actor`` None = a household tap at the panel (nobody identified). ``verified`` = the subject's own member
    session or voice session holds the card."""
    return await _apply(day_step_id, action, actor, via="tapped", verified=verified)


def reset_walks() -> None:
    _NAMED.clear()
    _LAST.clear()


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
    import user_erase_gate as erase_gate

    try:
        async with erase_gate.erasing(uid), _db() as db:
            ids = [r[0] for r in await _rows(db, "SELECT id FROM routines WHERE user_id = ?", (uid,))]
            n = await _delete_routines(db, ids)
            await db.execute("DELETE FROM routine_day_steps WHERE day_id IN (SELECT id FROM routine_days WHERE user_id = ?)", (uid,))
            await db.execute("DELETE FROM routine_days WHERE user_id = ?", (uid,))
            await db.execute("UPDATE routine_day_steps SET actor_user_id = NULL WHERE actor_user_id = ?", (uid,))
            await db.execute("UPDATE routines SET created_by = user_id WHERE created_by = ?", (uid,))
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        if _no_store(exc):
            return 0
        logger.error("ROUTINE event=forget_failed error=%s", type(exc).__name__)
        raise
    logger.info("ROUTINE event=forget_user routines=%d", n)
    return n


async def erase_entity(user_id: str, rx: "re.Pattern[str]", *, strict: bool = False) -> int:
    """"Forget Dana": in the member's own routines, a routine whose NAME names the entity goes whole; a template step or a day line
    whose words name it is deleted. Counts only are logged. ``strict`` raises on a failure (the cascade's contract)."""
    try:
        n = 0
        async with _db() as db:
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
            await db.commit()
        logger.info("ROUTINE event=erase_entity removed=%d", n)
        return n
    except Exception as exc:  # noqa: BLE001
        if _no_store(exc):
            return 0
        logger.error("ROUTINE event=erase_entity_failed error=%s", type(exc).__name__)
        if strict:
            raise
        return 0
