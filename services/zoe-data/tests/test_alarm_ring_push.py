"""alarm_push: a push on every change of the RINGING set, to panel sockets only, carrying a seq and nothing else (ZOE_ALARMS_RINGING_PUSH).

Drives the REAL ring engine (alarms.tick / snooze / dismiss / update / delete on a migrated sqlite DB) through the real HOOKS wiring into
a real PushBroadcaster with fake sockets. Audience rule reused: the panel principal is answered every ringing alarm by
GET /api/alarms/ringing (``alarms.can_silence`` is true for ``Viewer(panel=True)``), so only sockets that PROVED a panel hear it.

Negative controls (run by hand when written; each turns the named test red):
  * send to every socket on ``all`` in ``broadcast_to_panel_sockets`` -> test_each_change_reaches_every_panel_and_nobody_else;
  * put ``alarm["label"]`` into the frame data -> test_each_change_reaches_every_panel_and_nobody_else (no label);
  * drop the flag read from ``alarm_push.enabled`` -> test_flag_off_sends_nothing_and_hello_has_no_capability;
  * drop ``_seq += 1`` -> test_each_change_reaches_every_panel_and_nobody_else (seq increases);
  * make ``_changes_ring_set`` true for every event -> test_edits_and_misses_that_do_not_touch_the_ringing_set_send_nothing.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alarm_push  # noqa: E402
import alarms  # noqa: E402
import push  # noqa: E402

pytestmark = pytest.mark.ci_safe

SEVEN = datetime(2026, 11, 3, 7, 0, tzinfo=ZoneInfo("Australia/Perth")).timestamp()
NIGHT = SEVEN - 9 * 3600
LABEL = "Synthetic private label"


class _WS:
    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def send_json(self, msg):
        self.sent.append(msg)

    def pushes(self):
        return [m for m in self.sent if str(m.get("type", "")).startswith("alarms_ringing")]


@pytest.fixture
def dbfile(tmp_path):
    p = str(tmp_path / "z.db")
    engine = sa.create_engine(f"sqlite:///{p}")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        for name in ("0063_alarms.py", "0070_alarm_overrides.py"):
            spec = importlib.util.spec_from_file_location("mig_" + name[:4], ROOT / "alembic/versions" / name)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.upgrade()
    engine.dispose()
    return p


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setenv("ZOE_ALARMS", "on")
    monkeypatch.delenv("ZOE_ALARMS_RINGING_PUSH", raising=False)  # the declared default (on)
    monkeypatch.delenv("ZOE_TIMEZONE", raising=False)
    b = push.PushBroadcaster()
    monkeypatch.setattr(push, "broadcaster", b)
    monkeypatch.setattr(alarms, "HOOKS", [])
    monkeypatch.setattr(alarm_push, "_seq", 0)
    monkeypatch.setattr(alarm_push, "_registered", False)
    alarm_push.register()
    return b


async def _sockets(b):
    s = {k: _WS() for k in ("kitchen", "hall", "member", "anon", "admin_by_name")}
    await b.connect_panel(s["kitchen"], "kitchen")
    await b.connect_panel(s["hall"], "hall")  # a guest at a panel IS this panel socket: the same principal the route answers
    await b.connect(s["member"], "all", user_id="ann-0001")
    await b.connect(s["anon"], "all")
    await b.connect(s["admin_by_name"], "panel_kitchen", user_id="admin-0001")
    return s


async def _db_do(path, fn):
    db = await aiosqlite.connect(path)
    try:
        return await fn(db)
    finally:
        await db.close()


def test_each_change_reaches_every_panel_and_nobody_else(bus, dbfile, caplog):
    async def go():
        s = await _sockets(bus)
        hh = await _db_do(dbfile, lambda db: alarms.create_alarm(db, alarms.HOUSEHOLD, time_hhmm="07:00", label=LABEL, now=NIGHT))
        mine = await _db_do(dbfile, lambda db: alarms.create_alarm(db, "bob-0002", time_hhmm="07:00", label=LABEL, now=NIGHT))
        # the poll's own rule: the panel principal is answered BOTH (a personal one masked), so the panels are not a wider audience
        assert all(alarms.can_silence(a, alarms.Viewer(user_id="", panel=True)) for a in (hh, mine))
        events = []
        events += [e for e, _ in await _db_do(dbfile, lambda db: alarms.tick(db, SEVEN))]               # rang x2
        a = await _db_do(dbfile, lambda db: alarms.get_alarm(db, hh["id"]))
        await _db_do(dbfile, lambda db: alarms.snooze(db, a, now=SEVEN + 5))                          # snoozed
        events += [e for e, _ in await _db_do(dbfile, lambda db: alarms.tick(db, SEVEN + 5 + 9 * 60))]  # rang again
        a = await _db_do(dbfile, lambda db: alarms.get_alarm(db, hh["id"]))
        await _db_do(dbfile, lambda db: alarms.dismiss(db, a, now=SEVEN + 600))                       # dismissed
        events += [e for e, _ in await _db_do(dbfile, lambda db: alarms.tick(db, SEVEN + alarms.RING_MAX_S))]  # bob's auto_stopped
        return s, events

    with caplog.at_level(logging.INFO, logger="alarm_push"):
        s, events = asyncio.run(go())
    assert events == ["rang", "rang", "rang", "auto_stopped"]
    for panel in ("kitchen", "hall"):
        got = s[panel].pushes()
        assert [m["type"] for m in got] == ["alarms_ringing_changed"] * 6, got
        assert [m["data"] for m in got] == [{"seq": n} for n in range(1, 7)], "one frame per change, seq strictly +1"
    for other in ("member", "anon", "admin_by_name"):
        assert s[other].pushes() == [], f"{other} learned of a ring the panels' route decides"
    blob = json.dumps([s[k].sent for k in s]) + caplog.text
    assert LABEL not in blob and "bob-0002" not in blob, "a push or its log line carries alarm detail"
    lines = [r.getMessage() for r in caplog.records if "ALARM_RING_PUSH" in r.getMessage()]
    assert len(lines) == 6 and all("sent=2 audience=2" in ln for ln in lines), lines


def test_edits_and_misses_that_do_not_touch_the_ringing_set_send_nothing(bus, dbfile):
    async def go():
        s = await _sockets(bus)
        a = await _db_do(dbfile, lambda db: alarms.create_alarm(db, alarms.HOUSEHOLD, time_hhmm="07:00", now=NIGHT))
        await _db_do(dbfile, lambda db: alarms.update_alarm(db, a, {"label": "x"}, now=NIGHT + 1))   # idle edit
        await _db_do(dbfile, lambda db: alarms.tick(db, SEVEN + alarms.MISSED_GRACE_S + 60))          # missed
        before = len(s["kitchen"].pushes())
        b = await _db_do(dbfile, lambda db: alarms.create_alarm(db, alarms.HOUSEHOLD, time_hhmm="08:00", now=SEVEN + 700))
        await _db_do(dbfile, lambda db: alarms.tick(db, SEVEN + 3600))                                 # rang
        b = await _db_do(dbfile, lambda db: alarms.get_alarm(db, b["id"]))
        await _db_do(dbfile, lambda db: alarms.update_alarm(db, b, {"enabled": False}, now=SEVEN + 3601))  # edit stops a ring
        await _db_do(dbfile, lambda db: alarms.delete_alarm(db, b["id"]))                              # delete
        return before, s["kitchen"].pushes()

    before, got = asyncio.run(go())
    assert before == 0, "an idle edit or a missed occurrence changed nothing on the ring screen"
    assert [m["data"]["seq"] for m in got] == [1, 2, 3], "rang, an edit that stopped the ring, a delete"


def test_flag_off_sends_nothing_and_hello_has_no_capability(bus, dbfile, monkeypatch):
    monkeypatch.setenv("ZOE_ALARMS_RINGING_PUSH", "0")

    async def go():
        s = await _sockets(bus)
        await _db_do(dbfile, lambda db: alarms.create_alarm(db, alarms.HOUSEHOLD, time_hhmm="07:00", now=NIGHT))
        await _db_do(dbfile, lambda db: alarms.tick(db, SEVEN))
        task = asyncio.create_task(alarm_push.beat_loop(0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        return s

    s = asyncio.run(go())
    assert all(s[k].pushes() == [] for k in s)
    hello = s["kitchen"].sent[0]
    assert hello["type"] == "connected" and "alarms_ringing_seq" not in hello, "a flag-off server must keep the panel's 2 s poll"
    monkeypatch.setenv("ZOE_ALARMS_RINGING_PUSH", "1")
    monkeypatch.setenv("ZOE_ALARMS", "off")
    assert alarm_push.hello_fields() == {}, "alarms off: no capability either"


def test_hello_carries_the_capability_and_the_beat_reaches_panels_only(bus):
    async def go():
        s = await _sockets(bus)
        task = asyncio.create_task(alarm_push.beat_loop(0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        return s

    s = asyncio.run(go())
    assert s["kitchen"].sent[0]["alarms_ringing_seq"] == 0
    beats = s["kitchen"].pushes()
    assert beats and all(m == {**m, "type": "alarms_ringing_beat", "data": {"seq": 0}} for m in beats)
    assert all(s[k].pushes() == [] for k in ("member", "anon", "admin_by_name"))
