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
  * send to panels one after another with no timeout (``for ws, pid in targets: await ws.send_json(...)``) ->
    test_a_stalled_panel_does_not_hold_back_the_others_and_is_dropped and test_the_beat_survives_a_stalled_panel;
  * ``PANEL_SEND_TIMEOUT_S = None`` (concurrent, unbounded) -> test_a_stalled_panel_does_not_hold_back_the_others_and_is_dropped.
  * no per-socket lock (a fresh lock per send) -> test_overlapping_sends_to_one_socket_are_serialised_not_dropped and
    test_the_beat_and_a_change_together_drop_no_socket;
  * a lock-wait timeout raises (drops) instead of skipping -> test_a_socket_busy_with_another_healthy_send_is_kept;
  * disconnect keeps the lock, or a late sender re-creates it -> test_disconnect_frees_the_send_lock;
  * ``except ValueError`` / ``except BaseException`` in on_event -> test_on_event_swallows_an_error_but_not_a_cancellation;
  * ``sleep(interval_s)`` after the send, or no slot skip -> test_the_beat_keeps_a_fixed_cadence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import time
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


class _Stuck(_WS):
    """A panel whose send never completes (a wedged TCP peer); records the close the broadcaster sends it."""

    closed = False

    async def send_json(self, msg):
        if msg.get("type") == "connected":
            return await super().send_json(msg)
        await asyncio.Event().wait()

    async def close(self, code=1000):
        self.closed = True


class _Broken(_WS):
    async def send_json(self, msg):
        if msg.get("type") == "connected":
            return await super().send_json(msg)
        raise RuntimeError("socket gone")

    async def close(self, code=1000):
        pass


class _Timed(_WS):
    async def send_json(self, msg):
        self.sent.append({**msg, "_at": time.monotonic()})


def test_a_stalled_panel_does_not_hold_back_the_others_and_is_dropped(bus):
    async def go():
        ok, stuck, broken = _Timed(), _Stuck(), _Broken()
        await bus.connect_panel(stuck, "stuck")
        await bus.connect_panel(ok, "kitchen")
        await bus.connect_panel(broken, "broken")
        t0 = time.monotonic()
        res = await asyncio.wait_for(bus.broadcast_to_panel_sockets("alarms_ringing_changed", {"seq": 1}), 2.0)
        took = time.monotonic() - t0
        await asyncio.sleep(0)  # let the fire-and-forget close run
        return ok, stuck, broken, res, took, t0

    ok, stuck, broken, res, took, t0 = asyncio.run(go())
    frame = [m for m in ok.sent if m["type"] == "alarms_ringing_changed"]
    assert frame and frame[0]["_at"] - t0 < 0.1, "the healthy panel's frame waited behind the stalled one"
    assert res == (1, 3) and took < push.PANEL_SEND_TIMEOUT_S + 0.4, (res, took)
    assert set(bus._ws_panels.values()) == {"kitchen"}, "a stalled or raising socket stays in the panel set"
    assert stuck.closed, "a dropped panel socket is closed so the panel reconnects instead of going deaf"


def test_a_healthy_set_is_unaffected(bus):
    async def go():
        a, b = _WS(), _WS()
        await bus.connect_panel(a, "kitchen")
        await bus.connect_panel(b, "hall")
        return await bus.broadcast_to_panel_sockets("alarms_ringing_beat", {"seq": 0}), a, b

    res, a, b = asyncio.run(go())
    assert res == (2, 2) and len(a.pushes()) == len(b.pushes()) == 1 and len(bus._ws_panels) == 2


def test_the_beat_survives_a_stalled_panel(bus):
    async def go():
        ok, stuck = _WS(), _Stuck()
        await bus.connect_panel(stuck, "stuck")
        await bus.connect_panel(ok, "kitchen")
        task = asyncio.create_task(alarm_push.beat_loop(0.05))
        await asyncio.sleep(1.0)
        alive = not task.done()
        task.cancel()
        return ok, alive

    ok, alive = asyncio.run(go())
    assert alive and len(ok.pushes()) >= 5, f"beats delivered to the healthy panel: {len(ok.pushes())}"


class _OneAtATime(_WS):
    """A real websocket rejects a send that starts while another is in flight: so does this one."""

    def __init__(self, dur=0.02):
        super().__init__()
        self.busy, self.dur, self.closed = False, dur, False

    async def send_json(self, msg):
        if self.busy:
            raise RuntimeError("overlapping send")
        self.busy = True
        try:
            await asyncio.sleep(self.dur)
            self.sent.append(msg)
        finally:
            self.busy = False

    async def close(self, code=1000):
        self.closed = True


def test_overlapping_sends_to_one_socket_are_serialised_not_dropped(bus):
    async def go():
        ws = _OneAtATime()
        await bus.connect_panel(ws, "kitchen")
        res = await asyncio.gather(
            bus.broadcast_to_panel_sockets("alarms_ringing_changed", {"seq": 1}),
            bus.broadcast_to_panel_sockets("alarms_ringing_beat", {"seq": 1}),
            bus.broadcast("panel_kitchen", "voice:done", {}),  # a voice frame on the same socket
            bus.send_to(ws, {"type": "pong"}),  # the socket loop's pong
        )
        return ws, res

    ws, res = asyncio.run(go())
    assert res[:3] == [(1, 1), (1, 1), 1] and set(bus._ws_panels.values()) == {"kitchen"} and not ws.closed, res
    alarm = [m for m in ws.sent if str(m["type"]).startswith("alarms_ringing")]
    assert [m["type"] for m in alarm] == ["alarms_ringing_changed", "alarms_ringing_beat"], "both frames, in order"
    assert len(ws.sent) == 5  # hello + 4


def test_the_beat_and_a_change_together_drop_no_socket(bus):
    async def go():
        socks = [_OneAtATime(0.01) for _ in range(3)]
        for i, ws in enumerate(socks):
            await bus.connect_panel(ws, f"p{i}")
        beat = asyncio.create_task(alarm_push.beat_loop(0.005))
        await asyncio.gather(*(alarm_push.on_event("rang", {}) for _ in range(5)))
        await asyncio.sleep(0.05)
        beat.cancel()
        return socks

    socks = asyncio.run(go())
    assert len(bus._ws_panels) == 3 and not any(ws.closed for ws in socks)
    assert all([m["data"]["seq"] for m in ws.sent if m["type"] == "alarms_ringing_changed"] == [1, 2, 3, 4, 5] for ws in socks)


def test_a_socket_busy_with_another_healthy_send_is_kept(bus):
    async def go():
        ws = _OneAtATime(push.PANEL_SEND_TIMEOUT_S + 0.2)  # a long but healthy voice frame holds the socket
        await bus.connect_panel(ws, "kitchen")
        voice = asyncio.create_task(bus.send_to(ws, {"type": "voice:responding"}))
        await asyncio.sleep(0)
        res = await bus.broadcast_to_panel_sockets("alarms_ringing_beat", {"seq": 0})
        await voice
        return ws, res

    ws, res = asyncio.run(go())
    assert res == (0, 1), "the beat is skipped for this socket while it is busy"
    assert set(bus._ws_panels.values()) == {"kitchen"} and not ws.closed, "busy is not broken"


def test_disconnect_frees_the_send_lock(bus):
    async def go():
        ws = _WS()
        await bus.connect_panel(ws, "kitchen")
        assert ws in bus._send_locks
        bus.disconnect(ws, "panel_kitchen")
        await bus.send_to(ws, {"type": "late"})  # a late sender after disconnect must not re-create it
        return ws

    ws = asyncio.run(go())
    assert ws not in bus._send_locks


def test_on_event_swallows_an_error_but_not_a_cancellation(bus, monkeypatch, caplog):
    async def boom(*a, **k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(bus, "broadcast_to_panel_sockets", boom)
    with caplog.at_level(logging.WARNING, logger="alarm_push"):
        assert asyncio.run(alarm_push.on_event("rang", {})) is None
    assert "ALARM_RING_PUSH failed" in caplog.text and "err=RuntimeError" in caplog.text

    async def hang(*a, **k):
        await asyncio.Event().wait()

    monkeypatch.setattr(bus, "broadcast_to_panel_sockets", hang)

    async def go():
        t = asyncio.create_task(alarm_push.on_event("rang", {}))
        await asyncio.sleep(0)
        t.cancel()
        await t

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(go())


@pytest.mark.parametrize("durations,want", [([0.4] * 5, [0.0, 2.0, 4.0, 6.0, 8.0]), ([0.4, 4.5, 0.4, 0.4], [0.0, 2.0, 8.0, 10.0])])
def test_the_beat_keeps_a_fixed_cadence(bus, monkeypatch, durations, want):
    clock = {"t": 0.0}
    starts = []

    async def slow_send(kind):
        starts.append(clock["t"])
        clock["t"] += durations[len(starts) - 1]
        return (1, 1)

    async def scripted_sleep(dt):
        if len(starts) >= len(durations):
            raise asyncio.CancelledError
        clock["t"] += dt

    monkeypatch.setattr(alarm_push, "_send", slow_send)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(alarm_push.beat_loop(2.0, clock=lambda: clock["t"], sleep=scripted_sleep))
    assert [round(s, 6) for s in starts] == want, "slow sends stretched the beat, or an overrun burst the missed slots"
