#!/usr/bin/env python3
"""Calendar subscription engine, end to end against a loopback feed (lab only; record: README.md beside this file).

Drives the REAL ``services/zoe-data/calendar_feed.py`` against:
  * a scratch SQLite database (a temp dir, migrations 0052 / 0078 / 0079 applied, removed afterwards);
  * a loopback ``http.server`` on 127.0.0.1 serving a SYNTHETIC .ics with an ETag (304 on a matching If-None-Match), which
    the script mutates between runs;
  * the forget ledger on its in-memory backend with a synthetic salt set in THIS process only.

The fetcher is the lab's own (the engine stores no link: it is handed bytes). The loopback address lives in this process only.

    python3 labs/calendar-feed/run_lab.py            # the scenario table
    python3 labs/calendar-feed/run_lab.py --big 5    # + parse time and peak RSS of a synthetic 5 MB feed (child processes)

Needs ``icalendar`` importable (not installed on the box: put a cached copy on PYTHONPATH, see README). Run heavy work under
``flock -x /tmp/zoe-test-lane.lock``. No network beyond 127.0.0.1, no live database, no flag, nothing persisted.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import http.server
import importlib.util
import json
import os
import resource
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SVC = REPO / "services" / "zoe-data"
NOW = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)


# -- the synthetic feed -----------------------------------------------------------------------------------------------------------

def vevent(uid, summary, day="20261210", *, seq=0, rrule=None, rid=None, desc=None):
    lines = ["BEGIN:VEVENT", f"UID:{uid}", "DTSTAMP:20261130T000000Z", f"DTSTART:{day}T010000Z", f"DTEND:{day}T020000Z",
             f"SUMMARY:{summary}", f"SEQUENCE:{seq}", "LOCATION:Synthetic Hall",
             "ATTENDEE;CN=Pat Example;PARTSTAT=ACCEPTED:mailto:pat@example.org"]
    if rrule:
        lines.append(f"RRULE:{rrule}")
    if rid:
        lines.append(f"RECURRENCE-ID:{rid}")
    if desc:
        lines.append(f"DESCRIPTION:{desc}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def ics(events) -> bytes:
    return ("\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Synthetic//Lab//EN", *events, "END:VCALENDAR"]) + "\r\n").encode()


class FeedServer:
    """A loopback server: GET /feed.ics -> the current body with a strong ETag; 304 when If-None-Match matches (unless told to
    ignore validators, as some providers do)."""

    def __init__(self):
        self.body = b""
        self.ignore_validators = False
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                etag = '"' + hashlib.sha256(outer.body).hexdigest()[:16] + '"'
                if not outer.ignore_validators and self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/calendar; charset=utf-8")
                self.send_header("ETag", etag)
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *_):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.httpd.shutdown()

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/feed.ics"


def loopback_fetcher(address: str):
    """The lab's fetcher: a conditional GET with a streamed size cap. The address stays in this closure."""
    import httpx

    from calendar_feed import FetchResult

    async def fetch(req):
        headers = {"If-None-Match": req.etag} if req.etag else {}
        timeout = httpx.Timeout(20.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream("GET", address, headers=headers) as resp:
                if resp.status_code == 304:
                    return FetchResult("not_modified")
                if resp.status_code != 200:
                    return FetchResult({401: "unauthorized", 403: "unauthorized", 404: "not_found", 429: "rate_limited"}.get(
                        resp.status_code, "server_error"))
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > req.max_bytes:
                        return FetchResult("too_large")
                return FetchResult("ok", body=bytes(buf), etag=resp.headers.get("etag"),
                                   content_type=resp.headers.get("content-type"))
    return fetch


# -- the scratch database ---------------------------------------------------------------------------------------------------------

class _Db:
    def __init__(self, path):
        from db_pool import _Cursor, _ExecResult
        self._Cursor, self._ExecResult = _Cursor, _ExecResult
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._txn = 0

    def execute(self, sql, params=()):
        async def go():
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            if not self._txn:
                self.conn.commit()
            return self._Cursor(rows, rowcount=cur.rowcount)
        return self._ExecResult(go())

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

    def count(self, table, where="1=1", params=()):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]


def scratch_db(tmp: Path) -> _Db:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = str(tmp / "lab.db")
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
    for fname in ("0052_calendar_data_model.py", "0078_connected_accounts.py", "0079_calendar_feed.py"):
        spec = importlib.util.spec_from_file_location(f"lab_mig_{fname[:4]}", SVC / "alembic/versions" / fname)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
    return _Db(path)


def setup_process(tmp: Path) -> _Db:
    """Point the engine at the scratch store and the in-memory ledger (this process only)."""
    sys.path.insert(0, str(SVC))
    os.environ["ZOE_FORGET_LEDGER_SALT"] = "synthetic-lab-ledger-salt-0123456789"
    os.environ["ZOE_TIMEZONE"] = "Australia/Perth"
    os.environ.pop("POSTGRES_URL", None)
    import db_compat
    import memory_forgotten

    d = scratch_db(tmp)

    @contextlib.asynccontextmanager
    async def fake():
        yield d
    db_compat.get_compat_db = fake
    memory_forgotten.set_backend(memory_forgotten.MemoryBackend())
    return d


# -- the scenario -----------------------------------------------------------------------------------------------------------------

BASE = [vevent("a@lab.test", "Practice"), vevent("b@lab.test", "Assembly", "20261211"), vevent("c@lab.test", "Market", "20261212"),
        vevent("s@lab.test", "Weekly swim", "20261203", rrule="FREQ=WEEKLY"),
        vevent("s@lab.test", "Swim moved", "20261218", rid="20261217T010000Z")]
COLS = ("outcome", "code", "seen", "created", "updated", "unchanged", "deleted", "pruned", "skipped", "suppressed", "rrule_unsupported")


async def scenario(d: _Db, srv: FeedServer) -> list:
    import calendar_feed as feed
    import connected_accounts as ca

    acct = await ca.create("lab-member", "subscription", "calendar", "lab-subject", label="Lab feed")
    fetch = loopback_fetcher(srv.address)
    rows = []

    async def step(name, body=None, ignore=False):
        if body is not None:
            srv.body = body
        srv.ignore_validators = ignore
        r = await feed.sync_account(acct, fetch, run_mode="on", now=NOW)
        events = d.count("events", "user_id = ?", ("lab-member",))
        rows.append((name, r.outcome, r.error_code or "-", *(r.counts.get(k, 0) for k in COLS[2:]), events))

    await step("1 create", ics(BASE))
    await step("2 unchanged (304)")
    await step("3 unchanged (same sha256)", ignore=True)
    changed = [vevent("a@lab.test", "Practice (new room)", seq=1)] + BASE[1:]
    await step("4 change", ics(changed))
    await step("5 remote delete", ics([e for e in changed if "c@lab.test" not in e]))
    await step("6 empty feed: guard holds", ics([]))
    await step("7 empty again: confirmed", ics([]))
    await step("8 back", ics(changed))
    target = d.conn.execute("SELECT id FROM events WHERE external_uid = 'b@lab.test'").fetchone()[0]
    await feed.forget_feed_event("lab-member", target)
    await step("9 forgotten event resent", ics(changed + [vevent("n@lab.test", "New thing", "20261220")]))
    n = await feed.disconnect_feed(acct)
    left = {t: d.count(t) for t in ("events", "event_attendees", "event_alarms", "calendars")}
    left["offers"] = d.count("pending_suggestions")
    rows.append(("10 disconnect", "rows removed", str(n), *("" for _ in COLS[2:]), sum(left.values())))
    return rows


def print_table(rows):
    head = ("step", *COLS[:2], *COLS[2:], "events")
    widths = [max(len(str(r[i])) for r in [head, *rows]) for i in range(len(head))]
    print(" | ".join(str(h).ljust(w) for h, w in zip(head, widths)))
    print("-|-".join("-" * w for w in widths))
    for r in rows:
        print(" | ".join(str(c).ljust(w) for c, w in zip(r, widths)))


# -- the big-feed measurement (child processes, so each peak RSS is its own) ------------------------------------------------------

def big_feed(mb: float) -> bytes:
    events, size, i = [], 0, 0
    while size < mb * 1024 * 1024:
        day = f"2027{1 + i % 12:02d}{1 + i % 28:02d}"
        ev = vevent(f"big-{i}@lab.test", f"Synthetic event {i}", day, rrule="FREQ=WEEKLY" if i % 10 == 0 else None,
                    desc="Synthetic description " + "x" * 120)
        events.append(ev)
        size += len(ev) + 2
        i += 1
    return ics(events)


def _child(which: str, path: str) -> None:
    sys.path.insert(0, str(SVC))
    body = Path(path).read_bytes()
    import calendar_ical  # noqa: F401 - imports before the baseline
    import icalendar  # noqa: F401
    base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t = time.perf_counter()
    if which == "parse":
        n = len(calendar_ical.parse_ics(body, default_visibility="personal", home_tz="Australia/Perth"))
    else:
        tmp = Path(tempfile.mkdtemp(prefix="calfeed-lab-"))
        try:
            setup_process(tmp)
            import calendar_feed as feed
            import connected_accounts as ca

            async def go():
                acct = await ca.create("lab-member", "subscription", "calendar", "big-subject")

                async def fetch(_req):
                    return feed.FetchResult("ok", body=body, etag='"big"', content_type="text/calendar")
                return await feed.sync_account(acct, fetch, run_mode="on", now=NOW)
            r = asyncio.run(go())
            n = r.counts.get("created", 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    dt = time.perf_counter() - t
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps({"which": which, "events": n, "seconds": round(dt, 2), "rss_before_mb": round(base / 1024, 1),
                      "peak_rss_mb": round(peak / 1024, 1)}))


def measure(mb: float) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="calfeed-big-"))
    try:
        p = tmp / "big.ics"
        p.write_bytes(big_feed(mb))
        print(f"\nsynthetic feed: {p.stat().st_size / 1024 / 1024:.2f} MB")
        for which in ("parse", "engine"):
            out = subprocess.run([sys.executable, __file__, "--child", which, str(p)], capture_output=True, text=True, check=True)
            print(out.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--big", type=float, default=0, help="also measure a synthetic feed of this many MB")
    ap.add_argument("--child", nargs=2, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        _child(*a.child)
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="calfeed-lab-"))
    try:
        d = setup_process(tmp)
        with FeedServer() as srv:
            rows = asyncio.run(scenario(d, srv))
        print_table(rows)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if a.big:
        measure(a.big)
    return 0


if __name__ == "__main__":
    sys.exit(main())
