"""calendar_feed - a member's read-only calendar subscription (an ICS feed), Stage 1 core: fetch seam, parse, reconcile.

Design: the Stage 1 design note of the 2026-11-13 gap audit (record ``docs/knowledge/connected-accounts.md``). Migration 0079.
Flag ``ZOE_CALENDAR_FEED`` = off (default) | shadow | on, read per call; nothing schedules a sync yet (the tick is a follow-up).

NO LINK, NO SECRET. A subscription address is a bearer credential and Zoe stores none until the credential vault lands (owner
decision). The engine never sees one: it is handed an injected ``Fetcher`` (later a separate broker process) that is given a
``FetchRequest`` (the account id, the last validators, the size cap) and returns a ``FetchResult`` (a status class, the bytes, the
new validators). ``account_secrets`` still refuses everything; no URL column exists.

* Fetch: conditional (ETag / Last-Modified go back to the fetcher); a 304, or a body whose sha256 equals the last good one, means
  everything unchanged and the parse is skipped. The engine re-checks the size cap (``ZOE_CALENDAR_FEED_MAX_BYTES``) and that the
  body is a calendar at all. A failure maps to an error CLASS plus a plain-words CODE (``connected_accounts.ERROR_CODES``) and an
  exponential backoff (30 min doubling to 6 h, never sooner than a 429's Retry-After).
* Parse: ``calendar_ical.parse_ics`` off the event loop (``proactive.scheduler.run_blocking``). Masters and overrides stay STORED rows
  (never expanded). Kept: what overlaps [today - 7 d, today + 400 d], a master whose rule reaches into it, and a master whose rule
  ``calendar_read`` cannot read (counted ``rrule_unsupported``; it would show once).
* Reconcile (``on``): one transaction per batch, each inside ``erase_gate.writing`` and ``connected_accounts.still_current`` - a
  false stops the run with that batch unwritten (erased / fenced). Idempotent ``upsert_by_external_id`` keyed (calendar, UID,
  RECURRENCE-ID) with ``version_col="content_hash"``; attendees / alarms replaced only on created / updated; overrides linked to their
  master. No UID = skipped; a duplicate key keeps the highest SEQUENCE.
* VISIBILITY CLAMP: an event's visibility is the MORE PRIVATE of its calendar's policy and its own CLASS (``parse_ics`` maps
  CLASS:PUBLIC to "family"); a provider never widens a private calendar to the household. A calendar this engine creates is
  ``personal``.
* Remote deletes: a stored row whose key is absent from a complete fetch is hard-deleted with its attendees, alarms and anticipation
  offers (a row that left the window is "pruned"). MASS-DELETE GUARD: 0 events after some, or > 50 % and > 20 rows to delete =
  delete nothing, outcome partial / provider / ``suspect_empty``, and only a second consecutive identical body confirms it.
* FORGET SHIELD: every incoming key is checked against the member's ledger (``memory_forgotten.feed_item_hash``, strict) before it
  is written, inside the batch's erase-gate lock; a hit is skipped and counted ``suppressed``. FAIL CLOSED: an unconfigured or
  unreadable ledger refuses the sync (``ledger_unavailable``), and ``forget_feed_event`` refuses rather than delete unshielded.
* ``shadow``: fetch, parse, count against the stored rows, write ``sync_runs`` only.
* ``disconnect_feed``: the fence (``connected_accounts.disconnect``), then the account's calendars, events, attendees, alarms and
  offers. Forget-me reaches the same rows through ``calendar_forget.forget_user`` / ``connected_accounts.forget_user``.

Logs carry the account id, counts, outcome, class and code ONLY: never a title, place, attendee, description, URL, host or body.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional, Union

import calendar_forget
import calendar_read
import calendar_service as cal
import connected_accounts as ca
import memory_forgotten
import user_erase_gate as erase_gate

logger = logging.getLogger(__name__)

FLAG = "ZOE_CALENDAR_FEED"
PROVIDER = "subscription"          # connected_accounts.provider of a feed account
SOURCE = "subscription"            # events.source / calendars.source of its rows (calendar_read.PROVIDER_SOURCES: read-only)
SCOPE = "calendar_feed"            # sync_runs.scope
FEED_CALENDAR_ID = "feed"          # calendars.source_calendar_id: one calendar per feed account (never the address or its hash)
DEFAULT_CALENDAR_VISIBILITY = "personal"
PARSER_VERSION = 1                 # a bump discards the stored validators: the next run is a full resync
WINDOW_PAST_DAYS = 7
WINDOW_FUTURE_DAYS = calendar_read.MAX_WINDOW_DAYS
BATCH = 200
GUARD_MIN_ROWS = 20
GUARD_RATIO = 0.5
BACKOFF_BASE_S = 30 * 60
BACKOFF_MAX_S = 6 * 3600

#: a fetcher's status classes, and what each failure records: (error class, error code)
FETCH_STATUSES = ("ok", "not_modified", "unauthorized", "not_found", "rate_limited", "too_large", "network", "server_error",
                  "blocked")
_FAILURES = {
    "unauthorized": ("auth", "revoked_link"),
    "not_found": ("auth", "http_404"),
    "rate_limited": ("rate_limited", None),
    "too_large": ("quota", "too_large"),
    "network": ("network", None),
    "server_error": ("provider", None),
    "blocked": ("config", "ssrf_blocked"),
}
_PRIVACY = {"family": 0, "busy": 1, "personal": 2}
_BASE_COLS = ("title", "start_date", "start_time", "end_date", "end_time", "duration", "category", "location")
#: parsed 0052 columns the engine copies (identity, provenance and links are the engine's own)
_PARSED_EXTRA = ("description", "timezone", "end_timezone", "start_at", "end_at", "status", "transparency", "url",
                 "conference_url", "sequence", "rrule", "rdates", "exdates")


@dataclass(frozen=True)
class FetchRequest:
    account_id: str
    etag: Optional[str]
    last_modified: Optional[str]
    max_bytes: int


@dataclass(frozen=True)
class FetchResult:
    status: str                              # one of FETCH_STATUSES
    body: bytes = b""
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_type: Optional[str] = None
    retry_after_s: Optional[int] = None


#: given the request, returns the result (sync callables run in a worker thread; async ones are awaited)
Fetcher = Callable[[FetchRequest], Union[FetchResult, Awaitable[FetchResult]]]


@dataclass
class SyncReport:
    account_id: str
    mode: str
    outcome: str                             # skipped | ok | partial | error | fenced | erased
    error_class: Optional[str] = None
    error_code: Optional[str] = None
    counts: dict = field(default_factory=dict)
    skipped: dict = field(default_factory=dict)   # reason -> n (no_uid / out_of_window / duplicate); not persisted
    n_bytes: int = 0
    duration_ms: int = 0
    backoff_until: Optional[str] = None


class FeedForgetRefused(RuntimeError):
    """A feed event could not be forgotten for good (no durable shield): nothing was deleted."""


# -- flags and small helpers ------------------------------------------------------------------------------------------------------

def mode() -> str:
    from typed_env import flag

    return flag(FLAG)


def interval_minutes() -> int:
    from typed_env import flag

    return max(5, int(flag("ZOE_CALENDAR_FEED_INTERVAL_MIN")))


def max_bytes() -> int:
    from typed_env import flag

    return max(1, int(flag("ZOE_CALENDAR_FEED_MAX_BYTES")))


def is_due(last_finished_at: Optional[str], backoff_until: Optional[str], now: datetime) -> bool:
    """For the (follow-up) scheduler tick: past the interval since the last finish and past any backoff."""
    stamp = _iso(now)
    if backoff_until and backoff_until > stamp:
        return False
    return not last_finished_at or last_finished_at <= _iso(now - timedelta(minutes=interval_minutes()))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db():
    from db_compat import get_compat_db
    return get_compat_db()


def clamp_visibility(policy: Optional[str], event_visibility: Optional[str]) -> str:
    """The MORE PRIVATE of the calendar's policy and the event's own class; anything unknown counts as personal (fail closed)."""
    p = policy if policy in _PRIVACY else "personal"
    e = event_visibility if event_visibility in _PRIVACY else "personal"
    return p if _PRIVACY[p] >= _PRIVACY[e] else e


def backoff_seconds(failures: int, retry_after_s: Optional[int] = None) -> int:
    s = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** max(0, failures - 1)))
    return max(s, int(retry_after_s or 0))


def _looks_like_calendar(body: bytes, content_type: Optional[str]) -> bool:
    if "html" in str(content_type or "").lower():
        return False
    head = body[:64].lstrip(b"\xef\xbb\xbf \t\r\n").upper()
    return head.startswith(b"BEGIN:VCALENDAR")


def _window(now: datetime) -> "tuple[date, date]":
    from time_utils import zoe_timezone

    today = now.astimezone(zoe_timezone()).date()
    return today - timedelta(days=WINDOW_PAST_DAYS), today + timedelta(days=WINDOW_FUTURE_DAYS)


def _d(raw: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(raw or "").strip()[:10])
    except ValueError:
        return None


def rrule_unsupported(row: dict) -> bool:
    raw = str(row.get("rrule") or "").strip()
    return bool(raw) and calendar_read._rule_with_bounds(raw)[0] is None


def in_window(row: dict, w0: date, w1: date) -> bool:
    """Does this stored or parsed row belong in the window? A series by any occurrence; an override also by its original date; a
    rule this module cannot read keeps its master once it has begun before the window ends."""
    d0 = _d(row.get("start_date"))
    if d0 is None:
        return False
    end = _d(row.get("end_date")) or d0
    rid = _d(row.get("recurrence_id"))
    if rid is not None and w0 <= rid <= w1:
        return True
    if calendar_read.is_recurring(row):
        if rrule_unsupported(row):
            return d0 <= w1
        span = max(0, (end - d0).days)
        return bool(calendar_read.occurrence_dates(row, w0 - timedelta(days=span), w1))
    return d0 <= w1 and end >= w0


def mass_delete_suspect(n_parsed: int, n_stored_in_window: int, n_delete: int) -> bool:
    """The mass-delete guard: a fetch that parses to 0 events while rows exist, or would delete more than half and more than 20."""
    if n_delete <= 0:
        return False
    return (n_parsed == 0 and n_stored_in_window > 0) or (n_delete > GUARD_MIN_ROWS and n_delete > GUARD_RATIO * n_stored_in_window)


def content_hash(values: dict, attendees: list, alarms: list) -> str:
    """sha256 of the normalised event + attendees + alarms. DTSTAMP is never parsed, so a re-export alone changes nothing."""
    payload = {"v": PARSER_VERSION, "event": values, "attendees": attendees, "alarms": alarms}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _key(uid: Any, rid: Any) -> "tuple[str, str]":
    return str(uid), str(rid or "")


# -- the plan: parsed feed -> the rows to keep (pure) ----------------------------------------------------------------------------

@dataclass
class _Item:
    key: "tuple[str, str]"
    values: dict
    attendees: list
    alarms: list
    hash: str


def plan(parsed: list, *, policy: str, w0: date, w1: date, report: SyncReport) -> "dict[tuple[str, str], _Item]":
    """The kept items by key. Counts ``seen``, ``skipped`` (with reasons) and ``rrule_unsupported`` into ``report``."""
    c, why = report.counts, report.skipped
    c["seen"] = len(parsed)
    best: dict = {}
    for p in parsed:
        ev = p["event"]
        if not ev.get("external_uid"):
            why["no_uid"] = why.get("no_uid", 0) + 1
            continue
        if not in_window(ev, w0, w1):
            why["out_of_window"] = why.get("out_of_window", 0) + 1
            continue
        k = _key(ev["external_uid"], ev.get("recurrence_id"))
        if k in best:
            why["duplicate"] = why.get("duplicate", 0) + 1
            if int(ev.get("sequence") or 0) < int(best[k]["event"].get("sequence") or 0):
                continue
        best[k] = p
    out: dict = {}
    for k, p in best.items():
        ev = p["event"]
        values = {col: ev.get(col) for col in _BASE_COLS}
        values["all_day"] = 1 if ev.get("all_day") else 0
        for col in _PARSED_EXTRA:
            v = ev.get(col)
            values[col] = json.dumps(v) if col in ("rdates", "exdates") and v is not None else v
        values["visibility"] = clamp_visibility(policy, ev.get("visibility"))
        values["provider_updated_at"] = ev.get("provider_updated_at")
        values["category"] = values.get("category") or "general"
        if rrule_unsupported(ev):
            c["rrule_unsupported"] = c.get("rrule_unsupported", 0) + 1
        out[k] = _Item(k, values, p["attendees"], p["alarms"], content_hash(values, p["attendees"], p["alarms"]))
    c["skipped"] = sum(why.values())
    return out


# -- the engine -------------------------------------------------------------------------------------------------------------------

async def _account(account_id: str) -> Optional[dict]:
    async with _db() as db:
        cur = await db.execute("SELECT user_id, provider, kind, status FROM connected_accounts WHERE id = ?", (account_id,))
        row = await cur.fetchone()
    if row is None:
        return None
    return {"user_id": str(row[0]), "provider": str(row[1]), "kind": str(row[2]), "status": str(row[3])}


async def _calendar(user_id: str, account_id: str) -> Optional[dict]:
    async with _db() as db:
        cur = await db.execute("SELECT id, default_visibility, sync_cursor, consecutive_failures FROM calendars "
                               "WHERE user_id = ? AND source = ? AND source_account_id = ?", (user_id, SOURCE, account_id))
        row = await cur.fetchone()
    if row is None:
        return None
    try:
        cursor = json.loads(row[2] or "{}")
    except (TypeError, ValueError):
        cursor = {}
    if not isinstance(cursor, dict) or cursor.get("v") != PARSER_VERSION:
        cursor = {}
    return {"id": str(row[0]), "policy": str(row[1] or DEFAULT_CALENDAR_VISIBILITY), "cursor": cursor,
            "failures": int(row[3] or 0)}


async def _stored(calendar_id: str) -> dict:
    async with _db() as db:
        cur = await db.execute("SELECT id, external_uid, recurrence_id, content_hash, start_date, end_date, rrule, recurring, "
                               "rdates, exdates, all_day FROM events WHERE calendar_id = ? AND external_uid IS NOT NULL",
                               (calendar_id,))
        rows = await cur.fetchall()
    names = ("id", "external_uid", "recurrence_id", "content_hash", "start_date", "end_date", "rrule", "recurring", "rdates",
             "exdates", "all_day")
    out = {}
    for r in rows:
        d = dict(zip(names, tuple(r)))
        out[_key(d["external_uid"], d["recurrence_id"])] = d
    return out


class _Stop(Exception):
    def __init__(self, why: str):
        super().__init__(why)
        self.why = why                       # "erased" | "fenced" | "ledger"


async def _gated(run: ca.Run, step: Callable[[Any, frozenset], Awaitable[Any]], *, shield: bool = False) -> Any:
    """One write step: the member's erase gate, then ONE transaction that first checks the account generation. ``shield`` loads the
    member's forgotten set (strict) under the gate's lock, so a forget that landed before this step is honoured by it."""
    async with erase_gate.writing(run.user_id, run.scheduled_at) as live:
        if not live:
            raise _Stop("erased")
        wanted: frozenset = frozenset()
        if shield:
            try:
                wanted = await memory_forgotten.active_hashes(run.user_id, strict=True)
            except memory_forgotten.LedgerUnavailable:
                raise _Stop("ledger") from None
        async with _db() as db:
            async with db.transaction():
                if not await ca.still_current(db, run.account_id, run.generation):
                    raise _Stop("fenced")
                return await step(db, wanted)


def _shielded(user_id: str, wanted: frozenset, key: "tuple[str, str]") -> bool:
    if not wanted:
        return False
    if memory_forgotten.feed_item_hash(user_id, key[0], key[1]) in wanted:
        return True
    return bool(key[1]) and memory_forgotten.feed_item_hash(user_id, key[0], None) in wanted   # the whole series was forgotten


async def _delete_rows(db, user_id: str, ids: list) -> int:
    n = 0
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        ph = ", ".join("?" for _ in chunk)
        await calendar_forget.withdraw_event_offers(db, user_id, chunk)
        await db.execute(f"DELETE FROM event_alarms WHERE event_id IN ({ph})", chunk)
        await db.execute(f"DELETE FROM event_attendees WHERE event_id IN ({ph})", chunk)
        await db.execute(f"UPDATE events SET series_id = NULL WHERE series_id IN ({ph})", chunk)
        cur = await db.execute(f"DELETE FROM events WHERE id IN ({ph})", chunk)
        n += int(getattr(cur, "rowcount", 0) or 0)
    return n


async def _link_members(db, event_id: str, attendees: list) -> None:
    """``event_attendees.member_user_id`` for an attendee whose email is a household member's own (``member_identities``)."""
    import member_identities as mi

    emails = {mi.normalise("email", a.get("email")) for a in attendees if a.get("email")}
    emails.discard(None)
    if not emails:
        return
    ph = ", ".join("?" for _ in emails)
    cur = await db.execute(f"SELECT value_norm, user_id FROM member_identities WHERE kind = 'email' AND value_norm IN ({ph})",
                           tuple(emails))
    for value, member in await cur.fetchall():
        await db.execute("UPDATE event_attendees SET member_user_id = ? WHERE event_id = ? AND LOWER(email) = ?",
                         (member, event_id, value))


async def _fetch(fetcher: Fetcher, req: FetchRequest) -> FetchResult:
    if asyncio.iscoroutinefunction(fetcher):
        res = await fetcher(req)
    else:
        from proactive.scheduler import run_blocking

        res = await run_blocking(fetcher, req)
    if inspect.isawaitable(res):                 # a callable object with an async __call__
        res = await res
    if not isinstance(res, FetchResult) or res.status not in FETCH_STATUSES:
        raise TypeError("the fetcher returned something that is not a FetchResult")
    return res


async def sync_account(account_id: str, fetcher: Fetcher, *, run_mode: Optional[str] = None, now: Optional[datetime] = None,
                       scheduled_at: Optional[int] = None) -> SyncReport:
    """One sync of one feed account. Never raises for a provider or parse problem (it is recorded on the run); raises only on a
    programming error. ``run_mode`` overrides the flag (the lab); ``now`` fixes the clock (tests)."""
    m = run_mode or mode()
    rep = SyncReport(account_id, m, "skipped")
    if m not in ("shadow", "on"):
        return rep
    acct = await _account(account_id)
    if acct is None or acct["provider"] != PROVIDER or acct["kind"] != "calendar":
        raise ValueError("calendar_feed.sync_account: not a calendar subscription account")
    uid = acct["user_id"]
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    cal_row = await _calendar(uid, account_id)          # shadow reads it too (counts against the stored rows) and writes nothing
    writes = m == "on"
    cursor = dict(cal_row["cursor"]) if cal_row else {}
    run = await ca.start_run(account_id, uid, scope=SCOPE, cursor_before=cursor.get("sha"), scheduled_at=scheduled_at)
    if run is None:
        rep.outcome = "erased" if acct["status"] != "revoked" else "fenced"
        return rep

    async def finish(outcome: str, *, error_class=None, error_code=None, retry_after=None, failures: int = 0) -> SyncReport:
        rep.error_class, rep.error_code = error_class, error_code
        rep.duration_ms = int((time.monotonic() - started) * 1000)
        if outcome == "error":
            rep.backoff_until = _iso(now + timedelta(seconds=backoff_seconds(max(1, failures), retry_after)))
        done = await ca.finish_run(run, outcome=outcome, counts=rep.counts, cursor_after=cursor.get("sha"),
                                   error_class=error_class, error_code=error_code, backoff_until=rep.backoff_until,
                                   n_bytes=rep.n_bytes, duration_ms=rep.duration_ms)
        if done:
            rep.outcome = outcome
        elif erase_gate.generation(uid) != run.scheduled_at:
            rep.outcome = "erased"
        else:
            rep.outcome = "fenced"
        logger.info("CALENDAR_FEED run account=%s mode=%s outcome=%s class=%s code=%s seen=%d created=%d updated=%d deleted=%d "
                    "unchanged=%d skipped=%d pruned=%d suppressed=%d rrule_unsupported=%d bytes=%d ms=%d", account_id, m,
                    rep.outcome, error_class, error_code, *(int(rep.counts.get(k, 0)) for k in (
                        "seen", "created", "updated", "deleted", "unchanged", "skipped", "pruned", "suppressed",
                        "rrule_unsupported")), rep.n_bytes, rep.duration_ms)
        return rep

    async def failed(error_class: str, error_code: Optional[str], retry_after: Optional[int] = None) -> SyncReport:
        failures = (cal_row or {}).get("failures", 0) + 1
        if cal_row is not None and writes:
            async def bump(db, _w):
                await db.execute("UPDATE calendars SET consecutive_failures = consecutive_failures + 1, last_sync_status = ?, "
                                 "updated_at = ? WHERE id = ?", ("error", _iso(now), cal_row["id"]))
            try:
                await _gated(run, bump)
            except _Stop:
                pass                         # erased / fenced: finish_run records which
        return await finish("error", error_class=error_class, error_code=error_code, retry_after=retry_after, failures=failures)

    # forgotten means forever: without a working ledger nothing is fetched or written
    if not memory_forgotten.configured():
        logger.warning("CALENDAR_FEED refused account=%s: the forget ledger is not configured", account_id)
        return await failed("config", "ledger_unavailable")

    cap = max_bytes()
    req = FetchRequest(account_id, cursor.get("etag"), cursor.get("last_modified"), cap)
    try:
        res = await _fetch(fetcher, req)
    except TypeError:
        raise
    except Exception as exc:  # noqa: BLE001 - a fetcher failure is a network-class failure; its text is never logged
        logger.warning("CALENDAR_FEED fetch failed account=%s (%s)", account_id, type(exc).__name__)
        return await failed("network", None)
    rep.n_bytes = len(res.body or b"")
    if res.status in _FAILURES:
        cls, code = _FAILURES[res.status]
        return await failed(cls, code, res.retry_after_s)
    stored = await _stored(cal_row["id"]) if cal_row else {}

    async def unchanged() -> SyncReport:
        rep.counts["unchanged"] = len(stored)
        if cal_row is not None and writes:
            new_cursor = dict(cursor, etag=res.etag or cursor.get("etag"), last_modified=res.last_modified or cursor.get("last_modified"))

            async def mark(db, _w):
                await _mark_success(db, cal_row["id"], new_cursor, now, "ok")
            try:
                await _gated(run, mark)
            except _Stop:
                pass
        return await finish("ok", error_code=None)

    if res.status == "not_modified":
        return await unchanged()
    body = res.body or b""
    if len(body) > cap:
        return await failed("quota", "too_large")
    if not _looks_like_calendar(body, res.content_type):
        return await failed("provider", "not_calendar")
    sha = hashlib.sha256(body).hexdigest()
    if cursor.get("sha") == sha and not cursor.get("suspect"):
        return await unchanged()

    policy = cal_row["policy"] if cal_row else DEFAULT_CALENDAR_VISIBILITY
    try:
        import calendar_ical
        from proactive.scheduler import run_blocking

        parsed = await run_blocking(calendar_ical.parse_ics, body, default_visibility=policy)
    except Exception as exc:  # noqa: BLE001 - malformed provider data; its text is never logged
        logger.warning("CALENDAR_FEED parse failed account=%s (%s)", account_id, type(exc).__name__)
        return await failed("provider", "parse_failed")

    w0, w1 = _window(now)
    items = plan(parsed, policy=policy, w0=w0, w1=w1, report=rep)
    try:
        wanted = await memory_forgotten.active_hashes(uid, strict=True)
    except memory_forgotten.LedgerUnavailable:
        return await failed("internal", "ledger_unavailable")
    suppressed = {k for k in items if _shielded(uid, wanted, k)}
    gone = [s for k, s in stored.items() if k not in items or k in suppressed]
    to_delete = [s for s in gone if _key(s["external_uid"], s["recurrence_id"]) not in suppressed
                 and in_window(s, w0, w1)]
    in_win = sum(1 for s in stored.values() if in_window(s, w0, w1))
    guard = mass_delete_suspect(len(parsed), in_win, len(to_delete))
    confirmed = guard and cursor.get("suspect") == sha

    if m == "shadow":
        c = rep.counts
        c["suppressed"] = len(suppressed)
        for k, it in items.items():
            if k in suppressed:
                continue
            s = stored.get(k)
            name = "created" if s is None else "unchanged" if s.get("content_hash") == it.hash else "updated"
            c[name] = c.get(name, 0) + 1
        if guard and not confirmed:
            return await finish("partial", error_class="provider", error_code="suspect_empty")
        c["deleted"] = len(to_delete)
        c["pruned"] = len(gone) - len(to_delete) - sum(1 for s in gone if _key(s["external_uid"], s["recurrence_id"]) in suppressed)
        return await finish("ok", error_code=None)

    try:
        cal_id = cal_row["id"] if cal_row else await _gated(run, lambda db, _w: _ensure_calendar(db, uid, account_id, now))
        await _write_items(run, uid, cal_id, items, stored, now, rep)
        if guard and not confirmed:
            new_cursor = dict(cursor, suspect=sha, v=PARSER_VERSION)

            async def hold(db, _w):
                await _mark_success(db, cal_id, new_cursor, now, "partial")
            await _gated(run, hold)
            return await finish("partial", error_class="provider", error_code="suspect_empty")

        async def remove(db, w):
            # a forget may have landed since the plan: re-check under this step's lock, then delete what is gone
            sup_rows = [s for s in gone if _key(s["external_uid"], s["recurrence_id"]) in suppressed
                        or _shielded(uid, w, _key(s["external_uid"], s["recurrence_id"]))]
            sup_ids = {s["id"] for s in sup_rows}
            dele = [s["id"] for s in to_delete if s["id"] not in sup_ids]
            skip = sup_ids | set(dele)
            prune = [s["id"] for s in gone if s["id"] not in skip]
            rep.counts["deleted"] = await _delete_rows(db, uid, dele)
            rep.counts["pruned"] = await _delete_rows(db, uid, prune)
            await _delete_rows(db, uid, list(sup_ids))
            new_cursor = {"v": PARSER_VERSION, "etag": res.etag, "last_modified": res.last_modified, "sha": sha}
            await _mark_success(db, cal_id, new_cursor, now, "ok")
        await _gated(run, remove, shield=True)
        cursor["sha"] = sha
    except _Stop as stop:
        if stop.why == "ledger":
            return await finish("error", error_class="internal", error_code="ledger_unavailable")
        return await finish("partial")       # erased / fenced: finish_run writes nothing / records "fenced"
    return await finish("ok", error_code=None)


async def _ensure_calendar(db, user_id: str, account_id: str, now: datetime) -> str:
    cur = await db.execute("SELECT id FROM calendars WHERE user_id = ? AND source = ? AND source_account_id = ?",
                           (user_id, SOURCE, account_id))
    row = await cur.fetchone()
    if row is not None:
        return str(row[0])
    cid = f"cal_{uuid.uuid4().hex}"
    stamp = _iso(now)
    await db.execute("INSERT INTO calendars (id, user_id, name, source, source_account_id, source_calendar_id, default_visibility, "
                     "writable, household_shared, sync_enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 1, ?, ?)",
                     (cid, user_id, "Subscribed calendar", SOURCE, account_id, FEED_CALENDAR_ID, DEFAULT_CALENDAR_VISIBILITY,
                      stamp, stamp))
    return cid


async def _mark_success(db, calendar_id: str, cursor: dict, now: datetime, status: str) -> None:
    stamp = _iso(now)
    await db.execute("UPDATE calendars SET sync_cursor = ?, last_synced_at = ?, last_success_at = ?, consecutive_failures = 0, "
                     "last_sync_status = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(dict(cursor, v=PARSER_VERSION), sort_keys=True), stamp, stamp, status, stamp, calendar_id))


async def _write_items(run: ca.Run, user_id: str, cal_id: str, items: dict, stored: dict, now: datetime, rep: SyncReport) -> None:
    """Masters, then overrides, in batches; each batch one gated + fenced transaction that re-reads the forget shield."""
    c = rep.counts
    stamp = _iso(now)
    master_ids = {k[0]: s["id"] for k, s in stored.items() if not k[1]}
    ordered = sorted(items.values(), key=lambda it: (bool(it.key[1]), it.key))
    for i in range(0, len(ordered), BATCH):
        batch = ordered[i:i + BATCH]

        async def step(db, wanted, batch=batch):
            for it in batch:
                if _shielded(user_id, wanted, it.key):
                    c["suppressed"] = c.get("suppressed", 0) + 1
                    continue
                s = stored.get(it.key)
                eid = s["id"] if s else str(uuid.uuid4())
                values = dict(it.values, id=eid, user_id=user_id, source=SOURCE, read_only=1, deleted=0,
                              account_generation=run.generation, last_synced_at=stamp, content_hash=it.hash)
                got = await ca.upsert_by_external_id(
                    db, "events", match={"calendar_id": cal_id, "external_uid": it.key[0], "recurrence_id": it.key[1] or None},
                    values=values, version_col="content_hash")
                c[got] = c.get(got, 0) + 1
                if not it.key[1]:
                    master_ids[it.key[0]] = eid
                if got in ("created", "updated"):
                    await cal.replace_attendees(db, eid, user_id, it.attendees)
                    await cal.replace_alarms(db, eid, user_id, it.alarms)
                    await _link_members(db, eid, it.attendees)
                if it.key[1]:
                    await db.execute("UPDATE events SET series_id = ? WHERE id = ? AND COALESCE(series_id, '') <> COALESCE(?, '')",
                                     (master_ids.get(it.key[0]), eid, master_ids.get(it.key[0])))
        await _gated(run, step, shield=True)


# -- forget one feed event, and disconnect a feed ---------------------------------------------------------------------------------

async def forget_feed_event(user_id: str, event_id: str) -> bool:
    """'Forget that event' on a feed row: write the permanent shield FIRST, then hard-delete the row (a master takes its whole
    series), its attendees, alarms and offers. False when it is not the member's feed event. Raises ``FeedForgetRefused`` (nothing
    deleted) when the shield cannot be made durable: forgotten means forever, so an unshielded delete would come back next sync."""
    if not memory_forgotten.configured():
        logger.error("CALENDAR_FEED forget refused: the forget ledger is not configured")
        raise FeedForgetRefused("the forget ledger is not configured")
    async with _db() as db:
        cur = await db.execute("SELECT external_uid, recurrence_id, calendar_id FROM events WHERE id = ? AND user_id = ? "
                               "AND source = ?", (event_id, user_id, SOURCE))
        row = await cur.fetchone()
    if row is None or not row[0]:
        return False
    ext, rid, cal_id = str(row[0]), (str(row[1]) if row[1] else None), row[2]
    async with erase_gate.erasing(user_id, bump=False):          # an in-flight batch finishes first, or waits and sees the shield
        if not await memory_forgotten.add_feed_item(user_id, ext, rid, actor=user_id):
            logger.error("CALENDAR_FEED forget refused: the shield is not durable; nothing deleted")
            raise FeedForgetRefused("the forget could not be made permanent")
        async with _db() as db:
            async with db.transaction():
                if rid is None:
                    cur = await db.execute("SELECT id FROM events WHERE user_id = ? AND calendar_id = ? AND external_uid = ?",
                                           (user_id, cal_id, ext))
                    ids = [str(r[0]) for r in await cur.fetchall()]
                else:
                    ids = [event_id]
                n = await _delete_rows(db, user_id, ids)
    logger.info("CALENDAR_FEED forget rows=%d", n)
    return True


async def disconnect_feed(account_id: str) -> Optional[int]:
    """Disconnect a feed: the fence first (``connected_accounts.disconnect``: generation bumped, revoked, secrets removed), then the
    account's calendars and every row in them. Rows removed, or None when there is no such account."""
    acct = await _account(account_id)
    if acct is None:
        return None
    if await ca.disconnect(account_id) is None:
        return None
    uid = acct["user_id"]
    async with erase_gate.erasing(uid, bump=False):
        async with _db() as db:
            async with db.transaction():
                cur = await db.execute("SELECT id FROM calendars WHERE user_id = ? AND source_account_id = ?", (uid, account_id))
                cal_ids = [str(r[0]) for r in await cur.fetchall()]
                n = 0
                for cid in cal_ids:
                    cur = await db.execute("SELECT id FROM events WHERE calendar_id = ?", (cid,))
                    n += await _delete_rows(db, uid, [str(r[0]) for r in await cur.fetchall()])
                    cur = await db.execute("DELETE FROM calendars WHERE id = ?", (cid,))
                    n += int(getattr(cur, "rowcount", 0) or 0)
    logger.info("CALENDAR_FEED disconnect account=%s rows=%d", account_id, n)
    return n
