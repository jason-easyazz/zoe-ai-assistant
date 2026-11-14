"""Lab: the real fetcher end to end with the engine's ``sync_account`` on a SCRATCH store. Loopback only; no live database.

    python3 labs/calendar-fetcher/lab_happy_path.py --lab

Refuses to run without ``--lab``. It starts a tiny HTTP server on 127.0.0.1 (a random port) serving a SYNTHETIC calendar with
an ETag, builds a throwaway SQLite store with migrations 0052 / 0078 / 0079 in a temp directory (the engine tests' own shape),
creates one synthetic subscription account, and drives ``calendar_feed.sync_account(..., run_mode="on")`` through
``calendar_fetcher.engine_fetcher`` with ``CalendarFetcher(lab_loopback_http=True)``:

  1. first sync           -> events created (a 200 with an ETag)
  2. second sync          -> unchanged (the ETag goes back; the server answers 304)
  3. the feed changes     -> updated / created
  4. a link to 10.0.0.1   -> refused by the fetcher (``blocked``), recorded by the engine as config / ssrf_blocked, nothing sent
  5. a sentinel token in the link's path and query appears in no table and no log record.

The engine parses with ``icalendar`` (pinned 7.3.0 in CI, not on the box): put it on ``PYTHONPATH`` first, e.g.
``pip install --target <scratch>/pydeps icalendar==7.3.0``. Exit 0 = every expectation held.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import logging
import sys
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SVC = REPO / "services/zoe-data"
NOW = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
TOKEN = "LABSENTINEL5e1d"


def _feed(version: int) -> bytes:
    ev = [("u-1@example.test", "Synthetic practice", "20261210T010000Z"),
          ("u-2@example.test", "Synthetic assembly", "20261211T010000Z")]
    if version >= 2:
        ev[0] = ("u-1@example.test", "Synthetic practice (moved)", "20261210T030000Z")
        ev.append(("u-3@example.test", "Synthetic market", "20261212T010000Z"))
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Synthetic//Lab//EN"]
    for uid, title, start in ev:
        lines += ["BEGIN:VEVENT", f"UID:{uid}", "DTSTAMP:20261130T000000Z", f"DTSTART:{start}",
                  f"DTEND:{start[:9]}{int(start[9:11]) + 1:02d}{start[11:]}", f"SUMMARY:{title}", f"SEQUENCE:{version if uid.startswith('u-1') else 0}",
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return ("\r\n".join(lines) + "\r\n").encode()


class _State:
    version = 1
    log: list = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):          # the stdlib server would print the path (and so the token)
        pass

    def do_GET(self):
        body = _feed(_State.version)
        etag = f'"v{_State.version}"'
        inm = self.headers.get("If-None-Match")
        _State.log.append({"path_ok": self.path == f"/feed/{TOKEN}.ics?k={TOKEN}", "inm": inm})
        if inm == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", action="store_true", help="required: allow http to 127.0.0.1 for this run only")
    args = ap.parse_args()
    if not args.lab:
        print("refused: pass --lab (this script only ever talks to 127.0.0.1)", file=sys.stderr)
        return 2
    if importlib.util.find_spec("icalendar") is None:
        print("icalendar is not importable; see the module docstring", file=sys.stderr)
        return 2

    sys.path[:0] = [str(HERE), str(SVC), str(SVC / "tests")]
    import sqlalchemy as sa

    import calendar_feed as feed
    import calendar_fetcher as cf
    import connected_accounts as ca
    import db_compat
    import memory_forgotten
    import user_erase_gate as erase_gate
    from test_calendar_feed import _Db
    from test_feed_plumbing_stage0 import _migrate

    import os
    os.environ.update({"ZOE_FORGET_LEDGER_SALT": "synthetic-lab-salt", "ZOE_TIMEZONE": "UTC"})
    os.environ.pop("ZOE_CALENDAR_FEED_MAX_BYTES", None)

    records: list = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage() + repr(record.args))
    logging.getLogger().addHandler(_Keep())
    logging.getLogger().setLevel(logging.DEBUG)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/feed/{TOKEN}.ics?k={TOKEN}"

    with tempfile.TemporaryDirectory(prefix="calendar-fetcher-lab-") as tmp:
        path = str(Path(tmp) / "scratch.db")
        engine = sa.create_engine(f"sqlite:///{path}")
        with engine.begin() as c:
            c.exec_driver_sql("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
            c.exec_driver_sql("""CREATE TABLE events (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
                start_date TEXT NOT NULL, start_time TEXT, end_date TEXT, end_time TEXT, duration INTEGER,
                category TEXT DEFAULT 'general', location TEXT, all_day INTEGER DEFAULT 0, recurring TEXT, metadata TEXT,
                visibility TEXT NOT NULL DEFAULT 'family', deleted INTEGER DEFAULT 0, created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '')""")
        for mig in ("0052_calendar_data_model.py", "0078_connected_accounts.py", "0079_calendar_feed.py"):
            _migrate(engine, mig)
        d = _Db(path)

        @contextlib.asynccontextmanager
        async def scratch():
            yield d
        db_compat.get_compat_db = scratch                       # this process only: the scratch store, never the live one
        memory_forgotten.set_backend(memory_forgotten.MemoryBackend())
        erase_gate.reset()

        async def scenario():
            good = await ca.create("lab-member-a", "subscription", "calendar", "lab-subject-1", label="Lab")
            bad = await ca.create("lab-member-a", "subscription", "calendar", "lab-subject-2", label="Lab 2")
            links = {good: url, bad: f"http://10.0.0.1:{port}/feed/{TOKEN}.ics"}
            fetch = cf.engine_fetcher(links.get, feed.FetchResult, cf.CalendarFetcher(lab_loopback_http=True))
            out = []
            for step in ("first", "second"):
                out.append((step, await feed.sync_account(good, fetch, run_mode="on", now=NOW)))
            _State.version = 2
            out.append(("changed", await feed.sync_account(good, fetch, run_mode="on", now=NOW)))
            out.append(("private_link", await feed.sync_account(bad, fetch, run_mode="on", now=NOW)))
            return out

        reports = asyncio.run(scenario())
        titles = sorted(r[0] for r in d.rows("SELECT title FROM events"))
        dump = "\n".join(repr(row) for t in ("events", "calendars", "connected_accounts", "sync_runs")
                         for row in d.rows(f"SELECT * FROM {t}"))
    server.shutdown()

    summary = {step: {"outcome": r.outcome, "class": r.error_class, "code": r.error_code, "bytes": r.n_bytes,
                      "counts": {k: v for k, v in r.counts.items() if v}} for step, r in reports}
    summary["events_stored"] = titles
    summary["server_requests"] = _State.log
    leak_db = TOKEN in dump
    leak_log = any(TOKEN in m or "127.0.0.1" in m or "10.0.0.1" in m for m in records)
    summary["token_in_db"], summary["token_or_address_in_logs"] = leak_db, leak_log
    print(json.dumps(summary, indent=2, default=str))

    r = dict(reports)
    checks = [
        r["first"].outcome == "ok" and r["first"].counts.get("created") == 2,
        r["second"].outcome == "ok" and r["second"].counts.get("unchanged") == 2 and _State.log[1]["inm"] == '"v1"',
        r["changed"].outcome == "ok" and r["changed"].counts.get("updated") == 1 and r["changed"].counts.get("created") == 1,
        (r["private_link"].outcome, r["private_link"].error_class, r["private_link"].error_code) == ("error", "config",
                                                                                                      "ssrf_blocked"),
        len(_State.log) == 3 and all(x["path_ok"] for x in _State.log),      # the private link never reached any server
        len(titles) == 3, not leak_db, not leak_log,
    ]
    print("checks:", ["pass" if c else "FAIL" for c in checks])
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
