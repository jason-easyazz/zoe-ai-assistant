"""The calendar subscription engine (``calendar_feed.py``, Stage 1 core): fetch seam, parse, reconcile, guards, forget, disconnect.

Synthetic feeds only (every title, place, address and person below is invented), fixed dates, SQLite through the compat-DB shape
with migrations 0052 / 0078 / 0079 applied, the forget ledger on its in-memory backend (``ci_safe``). The tests that parse a feed
need the ``icalendar`` package (pinned in CI) and skip without it. This stage has no deletes: an absent key is counted, never
removed. Negative controls: the clamp and the generation fence.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

import account_secrets
import calendar_feed as feed
import calendar_forget
import calendar_read
import connected_accounts as ca
import db_compat
import member_identities as mi
import memory_forgotten
import user_erase_gate as erase_gate
from db_pool import _Cursor, _ExecResult
from test_feed_plumbing_stage0 import SVC, _migrate

pytestmark = pytest.mark.ci_safe
needs_ics = pytest.mark.skipif(importlib.util.find_spec("icalendar") is None, reason="icalendar is not installed")

NOW = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
SALT = "synthetic-ledger-salt-for-tests-only"


def run(coro):
    return asyncio.run(coro)


class _Db:
    """The compat connection's shape over sqlite3, rows addressable by index and by name (like the pooled rows)."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._txn = 0

    def execute(self, sql, params=()):
        async def go():
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            if not self._txn:
                self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(go())

    async def commit(self):
        if not self._txn:
            self.conn.commit()

    @contextlib.asynccontextmanager
    async def transaction(self):
        self._txn += 1
        try:
            yield self
        except BaseException:
            self._txn -= 1
            self.conn.rollback()
            raise
        self._txn -= 1
        if not self._txn:
            self.conn.commit()

    def rows(self, sql, params=()):
        return [tuple(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        return self.rows(sql, params)[0][0]


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
        c.exec_driver_sql("""CREATE TABLE events (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
            start_date TEXT NOT NULL, start_time TEXT, end_date TEXT, end_time TEXT, duration INTEGER, category TEXT DEFAULT 'general',
            location TEXT, all_day INTEGER DEFAULT 0, recurring TEXT, metadata TEXT, visibility TEXT NOT NULL DEFAULT 'family',
            deleted INTEGER DEFAULT 0, created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')""")
        c.exec_driver_sql("""CREATE TABLE pending_suggestions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
            action_type TEXT NOT NULL, description TEXT NOT NULL, list_type TEXT, when_hint TEXT, amount_hint TEXT,
            offer_phrase TEXT NOT NULL, pre_filled_slots TEXT, created_at TEXT NOT NULL, turns_elapsed INTEGER NOT NULL DEFAULT 0,
            expire_after_turns INTEGER NOT NULL DEFAULT 2, resolved INTEGER NOT NULL DEFAULT 0)""")
    for mig in ("0052_calendar_data_model.py", "0078_connected_accounts.py", "0079_calendar_feed.py"):
        _migrate(engine, mig)
    d = _Db(path)

    @contextlib.asynccontextmanager
    async def fake():
        yield d
    monkeypatch.setattr(db_compat, "get_compat_db", fake)
    monkeypatch.setenv("ZOE_FORGET_LEDGER_SALT", SALT)
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.delenv("ZOE_CALENDAR_FEED_MAX_BYTES", raising=False)
    monkeypatch.setattr(account_secrets, "remove", lambda _aid: asyncio.sleep(0, 0))
    memory_forgotten.set_backend(memory_forgotten.MemoryBackend())
    erase_gate.reset()
    yield d
    memory_forgotten.set_backend(None)
    erase_gate.reset()


# -- synthetic feeds --------------------------------------------------------------------------------------------------------------

def vevent(uid, summary, start="20261210T010000Z", end="20261210T020000Z", *, seq=0, cls=None, rrule=None, exdate=None,
           rid=None, location=None, attendees=(), description=None, alarm=False):
    lines = ["BEGIN:VEVENT"]
    if uid:
        lines.append(f"UID:{uid}")
    lines += ["DTSTAMP:20261130T000000Z", f"DTSTART:{start}", f"DTEND:{end}", f"SUMMARY:{summary}", f"SEQUENCE:{seq}"]
    if cls:
        lines.append(f"CLASS:{cls}")
    if rrule:
        lines.append(f"RRULE:{rrule}")
    if exdate:
        lines.append(f"EXDATE:{exdate}")
    if rid:
        lines.append(f"RECURRENCE-ID:{rid}")
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        lines.append(f"DESCRIPTION:{description}")
    for name, email in attendees:
        lines.append(f"ATTENDEE;CN={name};PARTSTAT=ACCEPTED:mailto:{email}")
    if alarm:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-PT30M", "DESCRIPTION:Reminder", "END:VALARM"]
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def ics(*events):
    return ("\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Synthetic//Test//EN", *events, "END:VCALENDAR"]) + "\r\n").encode()


THREE = (vevent("u-1@example.test", "Club practice", attendees=[("Pat Example", "pat@example.org")], alarm=True),
         vevent("u-2@example.test", "School assembly", "20261211T010000Z", "20261211T020000Z"),
         vevent("u-3@example.test", "Market day", "20261212T010000Z", "20261212T020000Z"))


class Feed:
    """A fetcher: serves ``body`` with an ETag, answers 304 to a matching If-None-Match (unless ``honour_etag`` is off)."""

    def __init__(self, body=b"", status="ok", **kw):
        self.body, self.status, self.kw = body, status, kw
        self.calls = 0
        self.honour_etag = True
        self.before = None                    # an async hook run inside the fetch (the race tests)

    @property
    def etag(self):
        import hashlib
        return '"' + hashlib.sha256(self.body).hexdigest()[:16] + '"'

    async def __call__(self, req: feed.FetchRequest) -> feed.FetchResult:
        self.calls += 1
        if self.before:
            await self.before()
        if self.status != "ok":
            return feed.FetchResult(self.status, **self.kw)
        if self.honour_etag and req.etag and req.etag == self.etag:
            return feed.FetchResult("not_modified")
        return feed.FetchResult("ok", body=self.body, etag=self.etag, content_type=self.kw.get("content_type", "text/calendar"))


def account(user="ann", subject="feed-subject-1"):
    return run(ca.create(user, "subscription", "calendar", subject, label="Synthetic"))


def sync(acct, fetcher, mode="on"):
    return run(feed.sync_account(acct, fetcher, run_mode=mode, now=NOW))


def events(d, user="ann"):
    return {r[0]: r[1:] for r in d.rows("SELECT title, visibility, source, read_only, series_id FROM events WHERE user_id = ?",
                                        (user,))}


def offer(d, user, event_id, oid):
    d.conn.execute("INSERT INTO pending_suggestions (id, user_id, session_id, action_type, description, offer_phrase, "
                   "pre_filled_slots, created_at) VALUES (?, ?, 'anticipation', 'anticipation', 'x', 'x', ?, 'x')",
                   (oid, user, json.dumps({"event_id": event_id})))
    d.conn.commit()


def eid(d, title):
    return d.one("SELECT id FROM events WHERE title = ?", (title,))


# -- create / unchanged / change -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

@needs_ics
def test_create_then_unchanged_by_304_and_by_same_body(db, monkeypatch):
    a = account()
    f = Feed(ics(*THREE))
    r = sync(a, f)
    assert (r.outcome, r.counts["created"], r.counts["seen"]) == ("ok", 3, 3)
    (cal_row,) = db.rows("SELECT id, source, default_visibility, writable, source_calendar_id FROM calendars")
    assert cal_row[1:] == ("subscription", "personal", 0, "feed")                  # a new feed calendar is PRIVATE and read-only
    assert {v[:3] for v in events(db).values()} == {("personal", "subscription", 1)}
    assert db.rows("SELECT DISTINCT account_generation FROM events") == [(1,)]
    assert db.one("SELECT COUNT(*) FROM events WHERE content_hash IS NULL") == 0
    assert db.one("SELECT COUNT(*) FROM event_attendees") == 1 and db.one("SELECT COUNT(*) FROM event_alarms") == 1

    import calendar_ical
    parses = []
    real = calendar_ical.parse_ics
    monkeypatch.setattr(calendar_ical, "parse_ics", lambda *a, **k: parses.append(1) or real(*a, **k))
    r = sync(a, f)                                                                  # 304
    assert (r.outcome, r.counts.get("unchanged"), parses) == ("ok", 3, [])
    f.honour_etag = False                                                          # the server ignores the validator
    r = sync(a, f)
    assert (r.outcome, r.counts.get("unchanged"), parses) == ("ok", 3, [])          # same sha256: parse skipped too
    assert db.rows("SELECT outcome, n_unchanged FROM sync_runs ORDER BY rowid")[-1] == ("ok", 3)
    assert db.one("SELECT last_success_at FROM calendars") is not None


@needs_ics
def test_sequence_and_title_changes_update_and_only_they_replace_attendees(db):
    a = account()
    f = Feed(ics(*THREE))
    sync(a, f)
    att_before = db.one("SELECT id FROM event_attendees")
    seq1 = vevent("u-2@example.test", "School assembly", "20261211T010000Z", "20261211T020000Z", seq=1)
    f.body = ics(THREE[0], seq1, THREE[2])
    r = sync(a, f)
    assert (r.counts.get("updated"), r.counts.get("unchanged"), r.counts.get("created", 0)) == (1, 2, 0)
    assert db.one("SELECT id FROM event_attendees") == att_before                   # an unchanged event keeps its attendee rows
    f.body = ics(vevent("u-1@example.test", "Club practice moved", attendees=[("Pat Example", "pat@example.org")], alarm=True),
                 seq1, THREE[2])
    r = sync(a, f)
    assert r.counts.get("updated") == 1 and "Club practice moved" in events(db)
    assert db.one("SELECT id FROM event_attendees") != att_before                   # the updated one's were replaced
    assert db.one("SELECT COUNT(*) FROM events") == 3                               # idempotent: never a duplicate


@needs_ics
def test_this_stage_leaves_an_absent_event_untouched_and_counts_it(db):
    """Deletes are not on yet: an event the feed no longer carries stays, counted ``absent``; even an empty feed deletes nothing."""
    a = account()
    f = Feed(ics(*THREE))
    sync(a, f)
    f.body = ics(*THREE[1:])
    r = sync(a, f)
    assert (r.outcome, r.counts.get("absent"), r.counts.get("deleted", 0)) == ("ok", 1, 0) and "Club practice" in events(db)
    f.body = ics()
    r = sync(a, f)
    assert (r.outcome, r.counts.get("absent")) == ("ok", 3) and db.one("SELECT COUNT(*) FROM events") == 3


# -- the visibility clamp ---------------------------------------------------------------------------------------------------------

@needs_ics
def test_a_provider_never_widens_a_private_calendar(db, monkeypatch):
    a = account()
    f = Feed(ics(vevent("p@example.test", "Public by provider", cls="PUBLIC"), vevent("q@example.test", "Private", cls="PRIVATE"),
                 vevent("c@example.test", "Confidential", cls="CONFIDENTIAL")))
    sync(a, f)
    assert {t: v[0] for t, v in events(db).items()} == {"Public by provider": "personal", "Private": "personal",
                                                         "Confidential": "personal"}
    # the member shares the calendar with the house: PUBLIC widens to the calendar's policy, PRIVATE / CONFIDENTIAL never do
    db.conn.execute("UPDATE calendars SET default_visibility = 'family', sync_cursor = NULL")
    db.conn.commit()
    sync(a, f)
    assert {t: v[0] for t, v in events(db).items()} == {"Public by provider": "family", "Private": "personal", "Confidential": "busy"}
    assert feed.clamp_visibility("personal", "family") == "personal" and feed.clamp_visibility("weird", "family") == "personal"


@needs_ics
def test_clamp_negative_control(db, monkeypatch):
    """Without the clamp, parse_ics's CLASS:PUBLIC -> family reaches the household from a PRIVATE calendar."""
    monkeypatch.setattr(feed, "clamp_visibility", lambda policy, ev: ev)
    sync(account(), Feed(ics(vevent("p@example.test", "Public by provider", cls="PUBLIC"))))
    assert events(db)["Public by provider"][0] == "family"


@needs_ics
def test_another_member_and_the_panel_see_nothing(db):
    sync(account(), Feed(ics(*THREE, vevent("p@example.test", "Public by provider", cls="PUBLIC"))))

    async def window(user):
        async with db_compat.get_compat_db() as d:
            return await calendar_read.events_in_window(d, user, "2026-12-01", "2026-12-31", include_busy=True)
    assert len(run(window("ann"))) == 4
    assert run(window("ben")) == [] and run(window("__household__")) == []        # panel_principal.HOUSEHOLD_VIEWER


def test_subscription_is_a_read_only_provider_source():
    import calendar_service
    row = {"source": "subscription", "read_only": 0}
    assert "subscription" in calendar_service.SOURCES and not calendar_read.is_writable(row)
    assert calendar_read.refusal(row) == "That one comes from a subscribed calendar; change it there."


# -- the fetch seam ---------------------------------------------------------------------------------------------------------------

def test_off_does_nothing_and_shadow_writes_only_the_run(db):
    a = account()
    f = Feed(ics(*THREE))
    r = run(feed.sync_account(a, f, now=NOW))                                      # the flag's default: off
    assert (r.outcome, f.calls, db.one("SELECT COUNT(*) FROM sync_runs")) == ("skipped", 0, 0)
    if importlib.util.find_spec("icalendar") is None:
        return
    r = sync(a, f, mode="shadow")
    assert (r.outcome, r.counts["created"]) == ("ok", 3)
    assert db.one("SELECT COUNT(*) FROM events") == 0 and db.one("SELECT COUNT(*) FROM calendars") == 0
    assert db.rows("SELECT outcome, n_seen, n_created FROM sync_runs") == [("ok", 3, 3)]
    sync(a, f)                                                                     # on: the rows land
    cursor = db.one("SELECT sync_cursor FROM calendars")
    f.body = ics(vevent("u-1@example.test", "Club practice renamed"), *THREE[1:])
    r = sync(a, f, mode="shadow")                                                  # shadow counts against the stored rows ...
    assert (r.counts.get("updated"), r.counts.get("unchanged"), r.counts.get("created", 0)) == (1, 2, 0)
    assert "Club practice" in events(db) and db.one("SELECT sync_cursor FROM calendars") == cursor   # ... and writes nothing


def _dump(d) -> str:
    """Every column of every row of every table in the scratch store, as text."""
    tables = [r[0] for r in d.rows("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return "\n".join(f"{t}: {row!r}" for t in tables for row in d.rows(f"SELECT * FROM {t}"))


def _logged(caplog) -> str:
    fmt = logging.Formatter("%(message)s %(exc_text)s")
    return "\n".join(fmt.format(r) + " " + repr(r.args) for r in caplog.records)


SENTINEL = "SENTINEL-7f3a9c2e"


@needs_ics
def test_no_secret_is_stored_anywhere(db, caplog):
    """The fetcher holds an address carrying a token; the engine is handed bytes only. Across a good sync, a fetch that RAISES with
    the address in its text, one that times out and a thread fetcher that raises, the token reaches no row of any table, no log
    record (message, args or traceback) and no report. The secret seam still refuses everything and gained no purpose."""
    caplog.set_level(logging.DEBUG)
    address = f"https://calendar.example/private/feed.ics?token={SENTINEL}"
    a = account()
    state = {"mode": "ok"}

    async def fetch(req):
        if state["mode"] == "raise":
            raise ConnectionError(f"GET {address} failed with 401")
        if state["mode"] == "hang":
            await asyncio.Event().wait()
        return feed.FetchResult("ok", body=ics(*THREE), etag='"e1"', content_type="text/calendar")

    def thread_fetch(req):
        raise RuntimeError(f"{address} could not be reached")

    reports = [sync(a, fetch)]
    state["mode"] = "raise"
    reports.append(sync(a, fetch))
    state["mode"] = "hang"
    reports.append(run(feed.sync_account(a, fetch, run_mode="on", now=NOW, fetch_timeout_s=0.05)))
    reports.append(sync(a, thread_fetch))
    assert [r.outcome for r in reports] == ["ok", "error", "error", "error"] and reports[2].detail == "fetch_timeout"
    assert db.one("SELECT COUNT(*) FROM events") == 3
    for where, text in (("the database", _dump(db)), ("the logs", _logged(caplog)), ("the reports", repr(reports))):
        assert SENTINEL not in text and "calendar.example" not in text, where
    assert "ConnectionError" in _logged(caplog)                                    # the failure is logged, by its type only
    # negative control: the dump does read every table - a planted value is found
    db.conn.execute("UPDATE calendars SET description = ?", (address,))
    db.conn.commit()
    assert SENTINEL in _dump(db)
    assert account_secrets.PURPOSES == ("oauth_refresh", "app_password") and not account_secrets.configured()
    with pytest.raises(account_secrets.SecretsUnavailable):
        run(account_secrets.put("acct_1", user_id="ann", purpose="app_password", secret=b"not-a-real-secret"))
    src = (SVC / "calendar_feed.py").read_text(encoding="utf-8")
    assert "account_secrets" not in src.split('"""', 2)[2]                            # the engine never calls the seam


# -- the engine's own fetch bounds -------------------------------------------------------------------------------------------------

def _until(cond, seconds=3.0):
    end = time.monotonic() + seconds
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    return cond()


@needs_ics
def test_a_fetch_that_never_returns_is_cut_off_and_other_feeds_still_sync(db):
    a, b = account(), account("ben", "ben-subject")

    async def hang(req):
        await asyncio.Event().wait()
    t0 = time.monotonic()
    r = run(feed.sync_account(a, hang, run_mode="on", now=NOW, fetch_timeout_s=0.05))
    assert (r.outcome, r.error_class, r.detail) == ("error", "network", "fetch_timeout") and time.monotonic() - t0 < 2
    assert a not in feed._outstanding                                              # an async fetch is cancelled at the bound

    gate, calls = threading.Event(), []

    def stuck(req):                                                                # a sync fetcher: its thread cannot be killed
        calls.append(1)
        gate.wait(10)
        raise ConnectionError("late")
    try:
        r = run(feed.sync_account(a, stuck, run_mode="on", now=NOW, fetch_timeout_s=0.05))
        assert (r.outcome, r.detail) == ("error", "fetch_timeout") and a in feed._outstanding
        r = run(feed.sync_account(a, stuck, run_mode="on", now=NOW, fetch_timeout_s=0.05))
        assert (r.outcome, r.detail, len(calls)) == ("error", "fetch_outstanding", 1)   # refused: never stacked on the stuck call
        r = sync(b, Feed(ics(*THREE)))                                             # another feed is not held up
        assert (r.outcome, r.counts["created"]) == ("ok", 3)
    finally:
        gate.set()
    assert _until(lambda: a not in feed._outstanding)
    assert sync(a, Feed(ics(*THREE))).counts["created"] == 3                       # once the thread returns, the feed syncs again
    assert db.rows("SELECT outcome FROM sync_runs WHERE account_id = ? ORDER BY rowid", (a,)) == [
        ("error",), ("error",), ("error",), ("ok",)]


@needs_ics
def test_fetch_bound_negative_control(db, monkeypatch):
    """Without the engine's bound, a fetcher that never returns holds the sync forever (the outer test timeout fires instead)."""
    async def unbounded(fetcher, req, timeout):
        return await fetcher(req)
    monkeypatch.setattr(feed, "_fetch", unbounded)

    async def hang(req):
        await asyncio.Event().wait()
    a = account()
    with pytest.raises(asyncio.TimeoutError):
        run(asyncio.wait_for(feed.sync_account(a, hang, run_mode="on", now=NOW, fetch_timeout_s=0.05), 0.3))


@needs_ics
def test_an_oversized_body_is_refused_whatever_its_status_and_nothing_is_written(db, monkeypatch):
    body = ics(*THREE)
    monkeypatch.setenv("ZOE_CALENDAR_FEED_MAX_BYTES", str(len(body) - 1))
    a = account()
    for status in ("ok", "server_error", "not_modified"):
        r = sync(a, lambda req, status=status: feed.FetchResult(status, body=body, content_type="text/calendar"))
        assert (r.outcome, r.error_class, r.error_code, r.n_bytes) == ("error", "quota", "too_large", len(body)), status
    for table in ("events", "event_attendees", "event_alarms", "calendars"):
        assert db.one(f"SELECT COUNT(*) FROM {table}") == 0, table
    monkeypatch.setenv("ZOE_CALENDAR_FEED_MAX_BYTES", str(len(body)))             # negative control: at the cap it is accepted
    assert sync(a, Feed(body)).counts["created"] == 3
