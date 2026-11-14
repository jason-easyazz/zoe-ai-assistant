"""memory_value_registry - the superseded-value registry (alembic 0080): which value of which subject's attribute a member
corrected AWAY, and when. Build step 5 of the memory redesign (design note section E; record
``docs/knowledge/memory-superseded-registry.md``).

Why. ``value_correction`` remembers a correction in process (``_REMEMBERED``: 6 h, 16 per member, lost on restart) so that an
idle pass mining an EARLIER transcript does not re-file the corrected-away value. This is the durable, word-free version of that
memory: rows of ``(subject_ref, attribute, role, value_norm, said_at)`` where ``role`` is ``superseded`` (the value the member
corrected away) or ``asserted`` (the value they corrected TO).

What it stores - and what it never stores:
  * ``subject_ref`` is ``'self'`` or a ``people.id``. Never a name or a label: an unresolved subject is not recorded.
  * ``attribute`` is one of ``ATTRIBUTES`` (number-valued only; CHECKed by the migration too).
  * ``value_norm`` is canonical JSON built by ``normalise`` from numbers and enum codes only. A string anywhere in it that is not
    a currency code, a unit or a weekday code is REFUSED, so no free text can enter through this module.
  * ``by_claim`` / ``source_turn_id`` / ``row_ids`` are ids (``_ID_RE``). Log lines carry counts and fixed codes only - no user
    id, no subject, no value.

The read: ``is_superseded(user_id, subject_ref, attribute, value_norm, said_at)`` - only entries said AFTER ``said_at`` count. A
value corrected away and later corrected BACK is current again (A -> B -> A keeps A): see ``_stale``.

Forget reach (forgotten means forever). Every forget path that erases a member's or a subject's memories erases the matching
registry rows, and ``forget_reach.TABLES`` lists the table so the inventory test fails if a path stops calling its eraser:
  * MEMBER  ``forget_user``      - ``MemoryService.delete_user`` (every row of the member). Fail closed.
  * ENTITY  ``erase_entity``     - the forget cascade's ``value_registry`` step: rows whose subject is a person the forgotten name
                                   names (live or already soft-deleted).
  * TOPIC   ``erase_topic``      - ``forget_topic.forget``: rows tied to the erased memory rows, or about a person a topic word names.
  * TURN    ``erase_for_turn``   - "forget that" (``intent_router``): rows recorded from the retracted turn or tied to its rows.
  * ROWS    ``erase_for_rows``   - ``MemoryService.erase_rows`` (the physical erase every row forget ends in): rows tied to them.
A record racing a forget loses: ``record`` writes under the member's erase gate (a member erase in flight drops it) and re-checks
after its commit that the subject person still exists (an entity forget soft-deleted them -> it deletes what it wrote).

Mode: ``ZOE_MEMORY_SUPERSEDED_REGISTRY`` off | shadow | on (default off). ``off``: nothing is recorded or read. ``shadow``:
``value_correction`` records alongside its in-process registry, which KEEPS DECIDING, and the idle pass logs a counts-only
agreement line (``MEMORY_SUPERSEDED_SHADOW``). ``on`` is reserved: today it behaves as ``shadow`` (the old path still decides);
only the read path (``is_superseded``) exists for the step that will flip it. The erasers run in EVERY mode: rows recorded while
the flag was on must still be forgotten after it is turned off.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from collections import Counter
from typing import Any, Callable, Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

TABLE = "memory_value_registry"
FLAG = "ZOE_MEMORY_SUPERSEDED_REGISTRY"
SELF = "self"
ROLES = ("superseded", "asserted")
#: the closed vocabulary (the number-valued attributes of design note B.2); the migration CHECKs the same tuple
ATTRIBUTES = ("event_date", "origin_date", "birthday", "age", "amount", "measure", "count", "time_of_day", "schedule")
#: attributes a subject has exactly ONE value of: a newer asserted value makes every other value of it stale. An event date, a
#: count or a schedule of a person may be one of several (two appointments, dogs and cats), so only an explicit 'superseded'
#: entry for that very value makes it stale.
SINGLE_VALUED = frozenset({"age", "birthday"})
RETENTION_S = 7 * 86400.0
MAX_ROW_IDS = 16
MAX_VALUES = 4
UNITS = ("km", "kg", "minute", "hour", "day", "week", "month", "year", "metre", "cm", "litre", "mile", "percent")
FREQS = ("daily", "weekly", "monthly", "yearly")
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_CUR_RE = re.compile(r"^[A-Z]{3}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")      # person / claim / turn / memory-row ids (``zoe_<user>_<hex>``)

#: in-process counters (counts only): what was recorded / refused / compared
COUNTS: Counter = Counter()


def mode() -> str:
    try:
        import typed_env

        return str(typed_env.flag("ZOE_MEMORY_SUPERSEDED_REGISTRY"))
    except Exception:  # noqa: BLE001 - an unreadable flag is the default
        return "off"


def recording() -> bool:
    return mode() in ("shadow", "on")


# -- normalised values: numbers and enum codes only -----------------------------------------------------------------------------

def _int(v: Any, lo: int, hi: int) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and (not math.isfinite(v) or v != int(v)):
        return None
    v = int(v)
    return v if lo <= v <= hi else None


def _num(v: Any, lo: float, hi: float) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        return None
    v = float(v)
    return v if lo <= v <= hi else None


def _date(v: dict, need_md: bool) -> Optional[dict]:
    if not set(v) <= {"y", "m", "d"}:
        return None
    out = {}
    for k, lo, hi in (("y", 1900, 2200), ("m", 1, 12), ("d", 1, 31)):
        if k in v:
            n = _int(v[k], lo, hi)
            if n is None:
                return None
            out[k] = n
    if need_md and not {"m", "d"} <= set(out):
        return None
    return out if ("m" in out or "d" in out) else None


def _value(attribute: str, v: Any) -> Optional[dict]:
    if not isinstance(v, dict):
        return None
    if attribute in ("event_date", "origin_date", "birthday"):
        return _date(v, attribute == "birthday")
    if attribute == "age":
        x = _num(v.get("years"), 0.0, 120.0) if set(v) == {"years"} else None
        return {"years": x} if x is not None and (x * 2) == int(x * 2) else None
    if attribute == "amount":
        if set(v) != {"cents", "cur"}:
            return None
        c = _int(v.get("cents"), 0, 10 ** 13)
        cur = v.get("cur")
        return {"cents": c, "cur": cur} if c is not None and isinstance(cur, str) and _CUR_RE.match(cur) else None
    if attribute == "measure":
        if set(v) != {"u", "v"}:
            return None
        x = _num(v.get("v"), 0.0, 1e9)
        return {"u": v["u"], "v": x} if x is not None and v.get("u") in UNITS else None
    if attribute == "count":
        n = _int(v.get("n"), 0, 10 ** 6) if set(v) == {"n"} else None
        return {"n": n} if n is not None else None
    if attribute == "time_of_day":
        if set(v) != {"h", "mi"}:
            return None
        h, mi = _int(v.get("h"), 0, 23), _int(v.get("mi"), 0, 59)
        return {"h": h, "mi": mi} if h is not None and mi is not None else None
    if attribute == "schedule":
        if not set(v) <= {"freq", "byday", "h", "mi"} or v.get("freq") not in FREQS:
            return None
        out: dict = {"freq": v["freq"]}
        if "byday" in v:
            days = v["byday"]
            if not isinstance(days, list) or not days or any(d not in WEEKDAYS for d in days):
                return None
            out["byday"] = sorted(set(days), key=WEEKDAYS.index)
        if "h" in v or "mi" in v:
            h, mi = _int(v.get("h"), 0, 23), _int(v.get("mi", 0), 0, 59)
            if h is None or mi is None:
                return None
            out["h"], out["mi"] = h, mi
        return out
    return None


def normalise(attribute: str, value: Any) -> Optional[str]:
    """Canonical JSON of ``value`` for ``attribute``, or None when it is not a valid normalised value (an unknown attribute, a
    stray key, a number out of range, ANY string that is not a currency / unit / weekday / frequency code). Pure."""
    if attribute not in ATTRIBUTES:
        return None
    v = _value(attribute, value)
    return json.dumps(v, sort_keys=True, separators=(",", ":")) if v is not None else None


def _ok_id(v: Any) -> bool:
    return isinstance(v, str) and bool(_ID_RE.match(v))


def _ok_subject(v: Any) -> bool:
    return v == SELF or _ok_id(v)


def entry_id(user_id: str, by_claim: str, role: str, value_norm: str) -> str:
    return hashlib.sha256(f"{user_id}|{by_claim}|{role}|{value_norm}".encode("utf-8")).hexdigest()[:24]


# -- the store ------------------------------------------------------------------------------------------------------------------

def _db():
    from db_compat import get_compat_db

    return get_compat_db()


def _missing_table(exc: BaseException) -> bool:
    s = str(exc)
    return type(exc).__name__ == "UndefinedTableError" or (TABLE in s and ("no such table" in s or "does not exist" in s))


def _no_store(exc: BaseException) -> bool:
    """Nothing to reach: the table is not migrated, or this process NEVER had a database pool (a lab, a bare unit run). A pool
    that existed and was lost is a real failure (the rows may still be stored)."""
    if _missing_table(exc):
        return True
    if isinstance(exc, RuntimeError):
        try:
            from db_pool import pool_ever_initialised

            return not pool_ever_initialised()
        except Exception:  # noqa: BLE001 - cannot tell: a failure, the safe side
            return False
    return False


async def _fetch(db, sql: str, params: tuple) -> list:
    cur = await db.execute(sql, params)
    return list(await cur.fetchall())


async def _person_live(db, user_id: str, person_id: str) -> bool:
    rows = await _fetch(db, "SELECT id FROM people WHERE id = ? AND user_id = ? AND (deleted = 0 OR deleted IS NULL)",
                        (person_id, user_id))
    return bool(rows)


async def record(user_id: str, subject_ref: str, attribute: str, *, superseded: Sequence[Any] = (), asserted: Any = None,
                 said_at: float, by_claim: str, source_turn_id: Optional[str] = None, row_ids: Iterable[str] = (),
                 scheduled_at: Optional[int] = None, now: Optional[float] = None) -> int:
    """Record one correction: each value in ``superseded`` was corrected away, ``asserted`` (optional) is the value it was
    corrected to, said at ``said_at`` (epoch seconds). Every value is validated by ``normalise`` and every id by ``_ID_RE``; ANY
    invalid input refuses the whole record. Written under the member's erase gate (``scheduled_at`` = the erase generation when the
    write was scheduled; a member erase since then drops it). Returns the entries written (0 = nothing). Never raises."""
    import user_erase_gate as erase_gate

    t = time.time() if now is None else float(now)
    uid = (user_id or "").strip().lower()
    olds = [normalise(attribute, v) for v in list(superseded or ())[:MAX_VALUES + 1]]
    new = normalise(attribute, asserted) if asserted is not None else None
    ids = [str(i) for i in dict.fromkeys(row_ids or ())]
    if (not uid or not _ok_subject(subject_ref) or attribute not in ATTRIBUTES or not _ok_id(by_claim)
            or (source_turn_id is not None and not _ok_id(source_turn_id)) or len(olds) > MAX_VALUES
            or any(o is None for o in olds) or (asserted is not None and new is None) or not (olds or new)
            or len(ids) > MAX_ROW_IDS or not all(_ok_id(i) for i in ids)
            or not isinstance(said_at, (int, float)) or isinstance(said_at, bool) or not math.isfinite(float(said_at))):
        COUNTS["refused"] += 1
        logger.info("MEMORY_SUPERSEDED_REFUSED attribute=%s", attribute if attribute in ATTRIBUTES else "-")
        return 0
    entries = [("superseded", o) for o in dict.fromkeys(olds) if o != new] + ([("asserted", new)] if new else [])
    gen = erase_gate.generation(uid) if scheduled_at is None else scheduled_at
    written: list = []
    n_new = 0
    try:
        async with erase_gate.writing(uid, gen) as live, _db() as db:
            if not live:
                COUNTS["dropped_erased"] += 1
                return 0             # forgotten means forever: a correction from before a member erase is never written
            if subject_ref != SELF and not await _person_live(db, uid, subject_ref):
                COUNTS["no_subject"] += 1
                return 0
            await db.execute(f"DELETE FROM {TABLE} WHERE user_id = ? AND expires_at <= ?", (uid, t))
            for role, value in entries:
                eid = entry_id(uid, by_claim, role, value)
                cur = await db.execute(
                    f"INSERT INTO {TABLE} (id, user_id, subject_ref, attribute, role, value_norm, said_at, by_claim, source_turn_id, "
                    f"row_ids, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
                    (eid, uid, subject_ref, attribute, role, value, float(said_at), by_claim, source_turn_id, json.dumps(ids),
                     float(said_at) + RETENTION_S, t))
                written.append(eid)
                n_new += int(getattr(cur, "rowcount", 0) or 0)
            await db.commit()
            if subject_ref != SELF and not await _person_live(db, uid, subject_ref):
                # an entity forget soft-deleted the subject while this was writing: its sweep may have run before the insert
                for eid in written:
                    await db.execute(f"DELETE FROM {TABLE} WHERE id = ? AND user_id = ?", (eid, uid))
                await db.commit()
                COUNTS["no_subject"] += 1
                return 0
    except Exception as exc:  # noqa: BLE001 - shadow bookkeeping never breaks the turn
        COUNTS["error"] += 1
        logger.warning("MEMORY_SUPERSEDED_NOT_RECORDED (%s)", type(exc).__name__)
        return 0
    COUNTS["recorded"] += n_new
    logger.info("MEMORY_SUPERSEDED_RECORDED attribute=%s superseded=%d asserted=%d new=%d", attribute,
                sum(1 for r, _ in entries if r == "superseded"), 1 if new else 0, n_new)
    return n_new


def _stale(events: Sequence[tuple], attribute: str, value_norm: str, said_at: Optional[float]) -> bool:
    """The staleness rule over ``(role, value_norm, said_at)`` events of ONE (subject, attribute). Only events said AFTER
    ``said_at`` count (None: every event). The newest assertion of THIS value cancels any older 'superseded' of it (A -> B -> A
    keeps A). For a single-valued attribute a newer assertion of ANOTHER value also makes this one stale. Pure."""
    after = sorted((e for e in events if said_at is None or e[2] > said_at), key=lambda e: e[2])
    if not after:
        return False
    if attribute in SINGLE_VALUED:
        asserted = [e for e in after if e[0] == "asserted"]
        if asserted:
            return asserted[-1][1] != value_norm
    mine = [e for e in after if e[1] == value_norm]
    return bool(mine) and mine[-1][0] == "superseded"


async def is_superseded(user_id: str, subject_ref: str, attribute: str, value: Any, said_at: Optional[float], *,
                        now: Optional[float] = None) -> bool:
    """True when ``value`` of ``subject_ref``'s ``attribute`` was corrected away AFTER ``said_at`` (epoch seconds of the words a
    fact was mined from) and not corrected back since. False on anything it cannot read (the old path still decides). Never
    raises. ``value`` is a normalised dict or its canonical JSON."""
    if isinstance(value, str):
        try:
            v = normalise(attribute, json.loads(value))
        except Exception:  # noqa: BLE001
            v = None
    else:
        v = normalise(attribute, value)
    uid = (user_id or "").strip().lower()
    if not v or not uid or not _ok_subject(subject_ref):
        return False
    t = time.time() if now is None else float(now)
    try:
        async with _db() as db:
            rows = await _fetch(db, f"SELECT role, value_norm, said_at FROM {TABLE} WHERE user_id = ? AND subject_ref = ? "
                                    f"AND attribute = ? AND expires_at > ?", (uid, subject_ref, attribute, t))
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory_value_registry: read failed (%s)", type(exc).__name__)
        return False
    return _stale([(str(r[0]), str(r[1]), float(r[2])) for r in rows], attribute, v, said_at)


# -- the shadow comparison (idle consolidation) ----------------------------------------------------------------------------------

def _render_age(value_norm: str) -> Optional[str]:
    try:
        x = float(json.loads(value_norm)["years"])
    except Exception:  # noqa: BLE001
        return None
    return str(int(x)) if x == int(x) else f"{int(x)} and a half"


async def shadow_check(user_id: str, fact: str, said_at: Optional[float], old_verdict: bool, *, now: Optional[float] = None) -> str:
    """Shadow only: would the registry call ``fact`` stale too? Compares with the in-process verdict and logs ONE counts-only line
    (``MEMORY_SUPERSEDED_SHADOW verdict=agree|old_only|registry_only``). It renders the registry's age entries back through the
    old path's own matcher (``value_correction.rewrite``) - the name is read from ``people`` at check time and never stored or
    logged - so a disagreement measures the STORE (restart loss, retention, said-at ordering, unrecorded shapes), not a new
    reader. Returns the verdict code ("" when off or unreadable). Never raises; the caller's decision is unchanged."""
    if not recording():
        return ""
    t = time.time() if now is None else float(now)
    uid = (user_id or "").strip().lower()
    try:
        import value_correction as vc

        async with _db() as db:
            rows = await _fetch(db, f"SELECT subject_ref, role, value_norm, said_at FROM {TABLE} WHERE user_id = ? AND attribute = 'age' "
                                    f"AND expires_at > ? AND subject_ref <> ?", (uid, t, SELF))
            subjects = sorted({str(r[0]) for r in rows})
            names: dict = {}
            for pid in subjects:
                got = await _fetch(db, "SELECT name FROM people WHERE id = ? AND user_id = ? AND (deleted = 0 OR deleted IS NULL)",
                                   (pid, uid))
                if got and got[0][0]:
                    names[pid] = str(got[0][0])
        registry = False
        for pid, name in names.items():
            events = [(str(r[1]), str(r[2]), float(r[3])) for r in rows if str(r[0]) == pid]
            for value_norm in {e[1] for e in events}:
                old = _render_age(value_norm)
                if not old or not _stale(events, "age", value_norm, said_at):
                    continue
                probe = "120" if old != "120" else "119"           # any other value: rewrite() only needs the old one to match
                if vc.rewrite(fact or "", vc.Correction(name, probe, old)) is not None:
                    registry = True
                    break
            if registry:
                break
    except Exception as exc:  # noqa: BLE001
        COUNTS["shadow_error"] += 1
        logger.debug("memory_value_registry: shadow check failed (%s)", type(exc).__name__)
        return ""
    verdict = "agree" if registry == bool(old_verdict) else ("old_only" if old_verdict else "registry_only")
    COUNTS[f"shadow_{verdict}"] += 1
    logger.info("MEMORY_SUPERSEDED_SHADOW lane=idle verdict=%s old=%d registry=%d entries=%d", verdict, int(bool(old_verdict)),
                int(registry), len(rows))
    return verdict


# -- forget reach ---------------------------------------------------------------------------------------------------------------

async def _drop_entries(db, uid: str, ids: Sequence[str]) -> int:
    n = 0
    for eid in ids:
        cur = await db.execute(f"DELETE FROM {TABLE} WHERE id = ? AND user_id = ?", (eid, uid))
        n += int(getattr(cur, "rowcount", 0) or 0)
    return n


async def forget_user(user_id: str) -> int:
    """The member erase (``MemoryService.delete_user``): every row of the member, under their erase gate. RAISES on a failure
    other than "no store" (the delete is retried)."""
    import user_erase_gate as erase_gate

    uid = (user_id or "").strip().lower()
    if not uid:
        return 0
    try:
        async with erase_gate.erasing(uid), _db() as db:
            cur = await db.execute(f"DELETE FROM {TABLE} WHERE user_id = ?", (uid,))
            await db.commit()
            n = int(getattr(cur, "rowcount", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        if _no_store(exc):
            return 0
        logger.error("memory_value_registry: rows NOT erased for a forgotten member (%s)", type(exc).__name__)
        raise
    logger.info("MEMORY_SUPERSEDED_ERASED path=member rows=%d", n)
    return n


async def erase_entity(user_id: str, rx: Any, *, db=None, person_ids: Iterable[str] = ()) -> int:
    """"Forget <name>" (the forget cascade's ``value_registry`` step, on the cascade's connection): every row whose subject is a
    person of this member whose name ``rx`` matches - live OR already soft-deleted (a retry after the people step ran), plus the
    ``person_ids`` the cascade resolved. ``rx`` is a compiled name pattern or the ledger's ``LedgerPattern`` (no name). RAISES on
    a failure: the cascade retries it and leaves a pending marker."""
    uid = (user_id or "").strip().lower()
    if not uid:
        return 0

    async def go(conn) -> int:
        doomed = {str(p) for p in person_ids or ()}
        for r in await _fetch(conn, "SELECT id, name FROM people WHERE user_id = ?", (uid,)):
            if r[1] and rx.search(str(r[1])):
                doomed.add(str(r[0]))
        if not doomed:
            return 0
        rows = await _fetch(conn, f"SELECT id, subject_ref FROM {TABLE} WHERE user_id = ?", (uid,))
        n = await _drop_entries(conn, uid, [str(r[0]) for r in rows if str(r[1]) in doomed])
        await conn.commit()
        return n

    if db is not None:
        n = await go(db)
    else:
        async with _db() as conn:
            n = await go(conn)
    logger.info("MEMORY_SUPERSEDED_ERASED path=entity rows=%d", n)
    return n


async def _erase_where(user_id: str, doomed: Callable[[tuple], bool], path: str) -> int:
    uid = (user_id or "").strip().lower()
    if not uid:
        return 0
    try:
        async with _db() as db:
            rows = await _fetch(db, f"SELECT id, subject_ref, source_turn_id, row_ids FROM {TABLE} WHERE user_id = ?", (uid,))
            names: dict = {}
            if any(str(r[1]) != SELF for r in rows):
                names = {str(r[0]): str(r[1] or "") for r in await _fetch(db, "SELECT id, name FROM people WHERE user_id = ?", (uid,))}
            ids = []
            for r in rows:
                try:
                    tied = set(json.loads(r[3] or "[]"))
                except (TypeError, ValueError):
                    tied = None                        # unreadable: over-forget, the safe error
                if tied is None or doomed((str(r[1]), names.get(str(r[1]), ""), r[2], tied)):
                    ids.append(str(r[0]))
            n = await _drop_entries(db, uid, ids)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        if _no_store(exc):
            return 0
        logger.error("memory_value_registry: rows NOT erased (path=%s, %s)", path, type(exc).__name__)
        raise
    logger.info("MEMORY_SUPERSEDED_ERASED path=%s rows=%d", path, n)
    return n


async def erase_topic(user_id: str, *, row_ids: Iterable[str] = (), name_matches: Optional[Callable[[str], bool]] = None) -> int:
    """"Forget what I told you about <topic>" (``forget_topic.forget``): rows tied to the topic's memory rows, and rows about a person
    whose name a topic word names (``name_matches(name)``; over-forgetting is the safe error). RAISES on a store failure."""
    gone = {str(i) for i in row_ids or ()}

    def doomed(r: tuple) -> bool:
        subject, name, _turn, tied = r
        return bool(tied & gone) or (subject != SELF and bool(name) and name_matches is not None and bool(name_matches(name)))

    return await _erase_where(user_id, doomed, "topic")


async def erase_for_turn(user_id: str, turn_text: str, *, row_ids: Iterable[str] = ()) -> int:
    """"Forget that" (``intent_router`` ``memory_forget_last``): rows recorded FROM the retracted turn (its content id, the formula
    ``record``'s callers use) and rows tied to the memory rows written from it. RAISES on a store failure."""
    from people_graph import content_turn_id

    tid = content_turn_id((user_id or "").strip().lower(), turn_text or "")
    gone = {str(i) for i in row_ids or ()}

    def doomed(r: tuple) -> bool:
        _subject, _name, turn, tied = r
        return (tid is not None and turn == tid) or bool(tied & gone)

    return await _erase_where(user_id, doomed, "turn")


async def erase_for_rows(user_id: str, row_ids: Iterable[str]) -> int:
    """The physical row erase (``MemoryService.erase_rows``, the end of every row forget): rows tied to any of ``row_ids``. RAISES on
    a store failure (the erase is then not done and is retried)."""
    gone = {str(i) for i in row_ids or ()}
    if not gone:
        return 0
    return await _erase_where(user_id, lambda r: bool(r[3] & gone), "rows")


def reset_state() -> None:
    COUNTS.clear()
