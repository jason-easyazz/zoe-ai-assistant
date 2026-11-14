"""The calendar subscription engine (``calendar_feed.py``, Stage 1 core): fetch seam, parse, reconcile, guards, forget, disconnect.

Synthetic feeds only (every title, place, address and person below is invented), fixed dates, SQLite through the compat-DB shape
with migrations 0052 / 0078 / 0079 applied, the forget ledger on its in-memory backend (``ci_safe``). The tests that parse a feed
need the ``icalendar`` package (pinned in CI) and skip without it. One negative control per guard: clamp, mass-delete guard, forget
shield and the generation fence.
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


# -- create / unchanged / change / delete -------------------------------------------------------------------------------------------

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
def test_a_removed_event_is_deleted_with_its_children_and_offers(db):
    a = account()
    f = Feed(ics(*THREE))
    sync(a, f)
    gone = eid(db, "Club practice")
    offer(db, "ann", gone, "off-1")
    offer(db, "ann", eid(db, "Market day"), "off-2")
    f.body = ics(*THREE[1:])
    r = sync(a, f)
    assert (r.outcome, r.counts.get("deleted"), r.counts.get("unchanged")) == ("ok", 1, 2)
    assert "Club practice" not in events(db)
    assert db.one("SELECT COUNT(*) FROM event_attendees") == 0 and db.one("SELECT COUNT(*) FROM event_alarms") == 0
    assert db.rows("SELECT id FROM pending_suggestions") == [("off-2",)]


@needs_ics
def test_overrides_are_stored_rows_linked_to_their_master_and_exdate_holds(db):
    a = account()
    master = vevent("series@example.test", "Weekly swim", "20261203T010000Z", "20261203T020000Z", rrule="FREQ=WEEKLY",
                    exdate="20261210T010000Z")
    moved = vevent("series@example.test", "Weekly swim (moved)", "20261218T030000Z", "20261218T040000Z",
                   rid="20261217T010000Z")
    f = Feed(ics(master))
    sync(a, f)
    f.body = ics(master, moved)
    r = sync(a, f)
    assert (r.counts.get("created"), r.counts.get("unchanged")) == (1, 1)
    mid = eid(db, "Weekly swim")
    assert events(db)["Weekly swim (moved)"][3] == mid                              # linked by series_id
    assert json.loads(db.one("SELECT exdates FROM events WHERE id = ?", (mid,))) == ["2026-12-10T01:00:00Z"]

    async def window():
        async with db_compat.get_compat_db() as d:
            return await calendar_read.events_in_window(d, "ann", "2026-12-01", "2026-12-31")
    occ = [(o["date"], o["title"]) for o in run(window())]
    assert ("2026-12-10", "Weekly swim") not in occ                                 # EXDATE
    assert ("2026-12-17", "Weekly swim") not in occ and ("2026-12-18", "Weekly swim (moved)") in occ   # the override replaces
    f.body = ics(master)
    r = sync(a, f)
    assert r.counts.get("deleted") == 1 and "Weekly swim (moved)" not in events(db)


@needs_ics
def test_no_uid_is_skipped_and_a_duplicate_keeps_the_highest_sequence(db):
    a = account()
    f = Feed(ics(vevent(None, "No identity"), vevent("dup@example.test", "Older", seq=1),
                 vevent("dup@example.test", "Newer", seq=2), vevent("dup@example.test", "Oldest", seq=0)))
    r = sync(a, f)
    assert r.counts["created"] == 1 and r.skipped == {"no_uid": 1, "duplicate": 2} and r.counts["skipped"] == 3
    assert set(events(db)) == {"Newer"}


@needs_ics
def test_window_prune_and_unsupported_rules_are_counted(db):
    a = account()
    old = vevent("old@example.test", "Long ago", "20200101T010000Z", "20200101T020000Z")
    odd = vevent("odd@example.test", "Odd yearly", "20260308T010000Z", "20260308T020000Z", rrule="FREQ=YEARLY;BYMONTH=3;BYDAY=2SU")
    f = Feed(ics(THREE[0], old, odd))
    r = sync(a, f)
    assert r.skipped == {"out_of_window": 1} and r.counts["rrule_unsupported"] == 1 and "Odd yearly" in events(db)
    assert db.rows("SELECT n_rrule_unsupported, n_skipped FROM sync_runs") == [(1, 1)]
    # two months on, the December event has left the window: it is pruned, not "deleted by the provider"
    f.body = ics(THREE[0], odd, vevent("feb@example.test", "February thing", "20270210T010000Z", "20270210T020000Z"))
    r = run(feed.sync_account(a, f, run_mode="on", now=datetime(2027, 2, 1, tzinfo=timezone.utc)))
    assert (r.counts.get("pruned"), r.counts.get("deleted"), r.counts.get("created")) == (1, 0, 1)
    assert set(events(db)) == {"Odd yearly", "February thing"}


@needs_ics
def test_an_attendee_who_is_a_member_is_linked(db):
    run(mi.add("ben", "email", "Ben.Member@example.org"))
    a = account()
    sync(a, Feed(ics(vevent("m@example.test", "Planning", attendees=[("Ben", "ben.member@example.org"),
                                                                       ("Visitor", "visitor@example.org")]))))
    assert sorted(db.rows("SELECT email, member_user_id FROM event_attendees")) == [
        ("ben.member@example.org", "ben"), ("visitor@example.org", None)]


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


# -- the mass-delete guard --------------------------------------------------------------------------------------------------------

@needs_ics
def test_an_empty_feed_deletes_nothing_until_it_repeats(db):
    a = account()
    f = Feed(ics(*THREE))
    sync(a, f)
    f.body = ics()
    r = sync(a, f)
    assert (r.outcome, r.error_class, r.error_code, r.counts.get("deleted", 0)) == ("partial", "provider", "suspect_empty", 0)
    assert db.one("SELECT COUNT(*) FROM events") == 3
    assert db.rows("SELECT last_error_code FROM connected_accounts") == [("suspect_empty",)]
    r = sync(a, f)                                                                  # the same empty body again: confirmed
    assert (r.outcome, r.counts.get("deleted")) == ("ok", 3) and db.one("SELECT COUNT(*) FROM events") == 0


@needs_ics
def test_a_big_drop_is_held_and_a_small_one_is_not(db):
    a = account()
    many = [vevent(f"m{i}@example.test", f"Fixture {i}", f"202612{10 + i % 15:02d}T010000Z", f"202612{10 + i % 15:02d}T020000Z")
            for i in range(30)]
    f = Feed(ics(*many))
    sync(a, f)
    f.body = ics(*many[:25])                                                       # 5 of 30: an ordinary delete
    assert sync(a, f).counts.get("deleted") == 5
    f.body = ics(*many[:3])                                                        # 22 of 25: held
    r = sync(a, f)
    assert (r.outcome, r.error_code) == ("partial", "suspect_empty") and db.one("SELECT COUNT(*) FROM events") == 25
    f.body = ics(*many[:4])                                                        # a DIFFERENT body is not a confirmation
    assert sync(a, f).error_code == "suspect_empty" and db.one("SELECT COUNT(*) FROM events") == 25
    assert sync(a, f).counts.get("deleted") == 21                                  # the same body twice in a row is


@needs_ics
def test_mass_delete_guard_negative_control(db, monkeypatch):
    a = account()
    f = Feed(ics(*THREE))
    sync(a, f)
    monkeypatch.setattr(feed, "mass_delete_suspect", lambda *_: False)
    f.body = ics()
    assert sync(a, f).counts.get("deleted") == 3 and db.one("SELECT COUNT(*) FROM events") == 0


def test_the_guard_rule():
    assert feed.mass_delete_suspect(0, 3, 3) and not feed.mass_delete_suspect(0, 0, 0)
    assert feed.mass_delete_suspect(5, 40, 21) and not feed.mass_delete_suspect(5, 50, 21) and not feed.mass_delete_suspect(5, 30, 20)


# -- the forget shield ------------------------------------------------------------------------------------------------------------

def _ledger_rows():
    return list(memory_forgotten.get_backend().rows.values())


@needs_ics
def test_a_forgotten_feed_event_never_comes_back(db):
    a = account()
    f = Feed(ics(*THREE))
    sync(a, f)
    target = eid(db, "Club practice")
    offer(db, "ann", target, "off-1")
    assert run(feed.forget_feed_event("ann", target)) is True
    assert "Club practice" not in events(db) and db.one("SELECT COUNT(*) FROM pending_suggestions") == 0
    rows = _ledger_rows()
    assert [r["scope"] for r in rows] == ["feed_item"]
    assert not any("Club" in str(v) or "u-1" in str(v) for r in rows for v in r.values())    # a hash, never the title or UID
    f.honour_etag = False
    f.body = ics(*THREE, vevent("new@example.test", "Something new"))
    r = sync(a, f)
    assert (r.counts.get("suppressed"), r.counts.get("created")) == (1, 1) and "Club practice" not in events(db)
    assert run(feed.forget_feed_event("ben", eid(db, "Market day"))) is False      # not Ben's: nothing happens
    assert "Market day" in events(db)


@needs_ics
def test_forgetting_a_master_shields_its_whole_series(db):
    a = account()
    master = vevent("series@example.test", "Weekly swim", "20261203T010000Z", "20261203T020000Z", rrule="FREQ=WEEKLY")
    moved = vevent("series@example.test", "Weekly swim (moved)", "20261218T030000Z", "20261218T040000Z", rid="20261217T010000Z")
    f = Feed(ics(master, moved))
    sync(a, f)
    run(feed.forget_feed_event("ann", eid(db, "Weekly swim")))
    assert events(db) == {}
    f.honour_etag = False
    f.body = ics(master, moved, THREE[0])
    r = sync(a, f)
    assert r.counts.get("suppressed") == 2 and set(events(db)) == {"Club practice"}


@needs_ics
def test_forget_shield_negative_control(db, monkeypatch):
    a = account()
    f = Feed(ics(*THREE))
    sync(a, f)
    run(feed.forget_feed_event("ann", eid(db, "Club practice")))
    monkeypatch.setattr(feed, "_shielded", lambda *_: False)
    f.honour_etag = False
    f.body = ics(*THREE, vevent("new@example.test", "Something new"))
    sync(a, f)
    assert "Club practice" in events(db)                                           # without the check it is re-imported


@needs_ics
def test_an_unconfigured_ledger_refuses_the_sync_and_the_forget(db, monkeypatch):
    a = account()
    f = Feed(ics(*THREE))
    sync(a, f)                                                                     # control: configured, it syncs
    target = eid(db, "Club practice")
    monkeypatch.delenv("ZOE_FORGET_LEDGER_SALT")
    f.calls = 0
    f.body = ics(*THREE, vevent("new@example.test", "Something new"))
    r = sync(a, f)
    assert (r.outcome, r.error_class, r.error_code, f.calls) == ("error", "config", "ledger_unavailable", 0)
    assert "Something new" not in events(db)
    with pytest.raises(feed.FeedForgetRefused):
        run(feed.forget_feed_event("ann", target))
    assert "Club practice" in events(db)                                           # refused loudly, nothing deleted


@needs_ics
def test_a_ledger_outage_refuses_the_sync(db, monkeypatch):
    class Down(memory_forgotten.MemoryBackend):
        async def active_hashes(self, user_id, now_iso):
            raise ConnectionError("synthetic outage")
    memory_forgotten.set_backend(Down())
    r = sync(account(), Feed(ics(*THREE)))
    assert (r.outcome, r.error_code) == ("error", "ledger_unavailable") and events(db) == {}


@needs_ics
def test_a_forget_shield_that_cannot_be_made_durable_deletes_nothing(db):
    a = account()
    sync(a, Feed(ics(*THREE)))

    class ReadOnly(memory_forgotten.MemoryBackend):
        async def upsert(self, *a, **k):
            raise ConnectionError("synthetic write failure")
    memory_forgotten.set_backend(ReadOnly())
    with pytest.raises(feed.FeedForgetRefused):
        run(feed.forget_feed_event("ann", eid(db, "Club practice")))
    assert "Club practice" in events(db)


# -- the fence, the erase gate, disconnect and forget-me ----------------------------------------------------------------------------

@needs_ics
def test_a_disconnect_during_the_fetch_fences_the_run(db):
    a = account()
    f = Feed(ics(*THREE))
    f.before = lambda: ca.disconnect(a)
    r = sync(a, f)
    assert r.outcome == "fenced" and events(db) == {}
    assert db.rows("SELECT outcome FROM sync_runs") == [("fenced",)]


@needs_ics
def test_a_disconnect_between_batches_stops_the_rest(db, monkeypatch):
    """BATCH=1: the calendar step and the first event commit; the disconnect lands before the second batch's fence check.
    Negative control: the same run with no disconnect writes all three."""
    monkeypatch.setattr(feed, "BATCH", 1)
    a = account()
    real, calls = feed._gated, []

    async def gated(run_, step, **kw):
        calls.append(1)
        if len(calls) == 3:                                                        # after the calendar step and batch 1
            await ca.disconnect(run_.account_id)
        return await real(run_, step, **kw)
    monkeypatch.setattr(feed, "_gated", gated)
    r = sync(a, Feed(ics(*THREE)))
    assert r.outcome == "fenced" and len(events(db)) == 1
    monkeypatch.setattr(feed, "_gated", real)
    b = account("ben", "other-subject")
    assert sync(b, Feed(ics(*THREE))).counts["created"] == 3


@needs_ics
def test_fence_negative_control(db, monkeypatch):
    """Without the generation check a batch already in flight writes after the disconnect."""
    a = account()
    f = Feed(ics(*THREE))
    f.before = lambda: ca.disconnect(a)

    async def always(*_):
        return True
    monkeypatch.setattr(ca, "still_current", always)
    sync(a, f)
    assert len(events(db)) == 3


@needs_ics
def test_forget_me_during_the_fetch_leaves_nothing(db):
    a = account()
    f = Feed(ics(*THREE))
    sync(a, f)
    offer(db, "ann", eid(db, "Market day"), "off-1")

    async def forget_me():
        await calendar_forget.forget_user("ann")
        await ca.forget_user("ann")
    f.before = forget_me
    f.honour_etag = False
    f.body = ics(*THREE, vevent("new@example.test", "Something new"))
    r = sync(a, f)
    assert r.outcome == "erased"
    for table in ("events", "event_attendees", "event_alarms", "calendars", "sync_runs", "connected_accounts", "pending_suggestions"):
        assert db.one(f"SELECT COUNT(*) FROM {table}") == 0, table


@needs_ics
def test_disconnect_leaves_zero_rows_and_zero_offers(db):
    a = account()
    b = account("ben", "ben-subject")
    sync(a, Feed(ics(*THREE)))
    sync(b, Feed(ics(*THREE)))
    for i, title in enumerate(("Club practice", "Market day")):
        offer(db, "ann", db.one("SELECT id FROM events WHERE user_id = 'ann' AND title = ?", (title,)), f"off-{i}")
    db.conn.execute("INSERT INTO pending_suggestions (id, user_id, session_id, action_type, description, offer_phrase, created_at) "
                    "VALUES ('keep', 'ann', 's', 'list_add', 'x', 'x', 'x')")
    db.conn.commit()
    assert run(feed.disconnect_feed(a)) > 0
    for table in ("events", "event_attendees", "event_alarms", "calendars"):
        assert {r[0] for r in db.rows(f"SELECT user_id FROM {table}")} == {"ben"}, table
    assert db.rows("SELECT id FROM pending_suggestions") == [("keep",)]
    assert db.rows("SELECT status FROM connected_accounts WHERE id = ?", (a,)) == [("revoked",)]
    r = sync(a, Feed(ics(*THREE)))                                                 # a revoked account syncs nothing
    assert r.outcome == "fenced" and {r[0] for r in db.rows("SELECT user_id FROM events")} == {"ben"}


@needs_ics
def test_forget_me_after_a_sync_reaches_every_feed_row_and_offer(db):
    a = account()
    sync(a, Feed(ics(*THREE)))
    offer(db, "ann", eid(db, "Club practice"), "off-1")
    run(calendar_forget.forget_user("ann"))
    run(ca.forget_user("ann"))
    for table in ("events", "event_attendees", "event_alarms", "calendars", "sync_runs", "connected_accounts", "pending_suggestions"):
        assert db.one(f"SELECT COUNT(*) FROM {table}") == 0, table


def test_the_offer_action_is_anticipations():
    src = (SVC / "anticipation.py").read_text(encoding="utf-8")
    assert re.search(r'^ACTION = "([a-z_]+)"', src, re.M).group(1) == calendar_forget.OFFER_ACTION


# -- the fetch seam ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status,cls,code", [
    ("unauthorized", "auth", "revoked_link"), ("not_found", "auth", "http_404"), ("too_large", "quota", "too_large"),
    ("network", "network", None), ("server_error", "provider", None), ("blocked", "config", "ssrf_blocked")])
def test_fetch_failures_map_to_a_class_and_a_code(db, status, cls, code):
    a = account()
    r = sync(a, Feed(status=status))
    assert (r.outcome, r.error_class, r.error_code) == ("error", cls, code)
    assert db.rows("SELECT status, last_error_class, last_error_code FROM connected_accounts") == [("error", cls, code)]
    assert db.rows("SELECT outcome, error_code, backoff_until FROM sync_runs") == [("error", code, "2026-12-01T00:30:00Z")]


def test_a_429_waits_out_retry_after_and_failures_back_off(db):
    r = sync(account(), Feed(status="rate_limited", retry_after_s=7200))
    assert (r.error_class, r.backoff_until) == ("rate_limited", "2026-12-01T02:00:00Z")
    assert [feed.backoff_seconds(n) for n in (1, 2, 3, 9)] == [1800, 3600, 7200, 21600]


@needs_ics
def test_consecutive_failures_count_on_the_calendar_and_reset_on_success(db):
    a = account()
    f = Feed(ics(*THREE))
    sync(a, f)
    f.status = "server_error"
    sync(a, f)
    r = sync(a, f)
    assert db.one("SELECT consecutive_failures FROM calendars") == 2 and r.backoff_until == "2026-12-01T01:00:00Z"
    f.status = "ok"
    sync(a, f)
    assert db.one("SELECT consecutive_failures FROM calendars") == 0


def test_an_oversize_or_non_calendar_body_is_refused_before_any_parse(db, monkeypatch):
    monkeypatch.setenv("ZOE_CALENDAR_FEED_MAX_BYTES", "64")
    a = account()
    r = sync(a, Feed(b"BEGIN:VCALENDAR\r\n" + b"X" * 200))
    assert (r.error_class, r.error_code, r.n_bytes) == ("quota", "too_large", 217)
    monkeypatch.delenv("ZOE_CALENDAR_FEED_MAX_BYTES")
    r = sync(a, Feed(b"<!doctype html><html><body>Sign in</body></html>", content_type="text/html"))
    assert (r.error_class, r.error_code) == ("provider", "not_calendar")
    r = sync(a, Feed(b"BEGIN:VCALENDAR\r\n", content_type="text/html; charset=utf-8"))
    assert r.error_code == "not_calendar" and events(db) == {}


@needs_ics
def test_a_malformed_calendar_is_a_parse_failure(db):
    r = sync(account(), Feed(b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:not-a-date\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"))
    assert (r.outcome, r.error_class, r.error_code) == ("error", "provider", "parse_failed")


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


def test_a_sync_fetcher_runs_off_the_loop_and_a_bad_return_is_a_bug(db):
    a = account()
    seen = []

    def blocking(req):
        seen.append(req.account_id)
        return feed.FetchResult("server_error")
    assert sync(a, blocking).error_class == "provider" and seen == [a]
    with pytest.raises(TypeError):
        sync(a, lambda req: b"raw bytes are not a result")


def test_is_due_honours_the_interval_and_the_backoff(monkeypatch):
    monkeypatch.delenv("ZOE_CALENDAR_FEED_INTERVAL_MIN", raising=False)
    assert feed.is_due(None, None, NOW) and not feed.is_due("2026-11-30T23:45:00Z", None, NOW)
    assert feed.is_due("2026-11-30T23:30:00Z", None, NOW) and not feed.is_due(None, "2026-12-01T01:00:00Z", NOW)


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


# -- the log privacy sweep --------------------------------------------------------------------------------------------------------

PLANTED = ("Quillon Marsh", "Tangerine Lane", "Velma Quist", "velma@example.org", "allergy clinic", "calendar.example",
           "private-token-abc123")


def _leaks(caplog):
    return [m for m in (r.getMessage() for r in caplog.records) if any(p.lower() in m.lower() for p in PLANTED)]


@needs_ics
def test_no_title_place_person_address_or_body_reaches_a_log_line(db, caplog):
    caplog.set_level(logging.DEBUG)
    a = account()
    planted = vevent("secret-uid@example.test", "Quillon Marsh allergy clinic", location="17 Tangerine Lane",
                     attendees=[("Velma Quist", "velma@example.org")], description="Bring the private-token-abc123 letter")
    f = Feed(ics(planted, *THREE))
    sync(a, f)                                                                     # create
    f.body = ics(planted.replace("SEQUENCE:0", "SEQUENCE:1"), *THREE)
    sync(a, f)                                                                     # update
    f.body = ics()
    sync(a, f)                                                                     # guard trip
    f.body = ics(*THREE)
    sync(a, f)                                                                     # delete
    f.body = ics(planted, *THREE)
    sync(a, f)
    run(feed.forget_feed_event("ann", eid(db, "Quillon Marsh allergy clinic")))    # forget
    sync(a, f)                                                                     # shield

    async def boom():
        raise ConnectionError("GET https://calendar.example/private-token-abc123 failed for Quillon Marsh")
    f.before = boom
    sync(a, f)                                                                     # a fetcher error carrying a URL
    f.before = None
    sync(a, Feed(b"<html>Quillon Marsh</html>", content_type="text/html"))
    run(feed.disconnect_feed(a))
    assert any("CALENDAR_FEED" in r.getMessage() for r in caplog.records)
    assert _leaks(caplog) == []
    # negative control: the sweep sees a planted title when one IS logged
    logging.getLogger("calendar_feed").info("CALENDAR_FEED title=%s", "Quillon Marsh allergy clinic")
    assert _leaks(caplog)
