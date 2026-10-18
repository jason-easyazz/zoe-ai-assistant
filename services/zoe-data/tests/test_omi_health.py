"""Pendant HEALTH: the numbers-only heartbeat, the latest row per pendant, the owner-scoped read, and the five warnings that stop loss being
silent (gap audit P0-2; ``omi_health.py``, migration 0056, ``POST /api/omi/device-status``, ``GET /api/omi/health``).

Every safety rule has a named test AND a control arm (the opposite configuration, which must behave differently). Source mutations to
re-run by Edit, and the test each turns red:
  route ``device_status`` without ``Depends(_require_device)``                                  -> test_an_unauthenticated_report_is_refused_and_stores_nothing
  route ``my_pendant_health`` dropping the ``user_id`` filter in ``list_for``                    -> test_another_member_cannot_read_a_pendants_health
  ``list_for`` without the ``current_owner`` re-check                                            -> test_a_reassigned_pendant_vanishes_from_the_previous_owner_at_once
  ``_HealthReport`` with ``extra="allow"``                                                       -> test_a_report_cannot_carry_content
  ``overwritten_delta`` returning ``total`` for a first reading / a repeat                       -> test_the_overwrite_counter_is_a_baseline_then_a_delta_and_a_repeat_adds_nothing
  ``evaluate``: ``> NO_SYNC_H`` -> ``>=``, ``< BATTERY_LOW_PCT`` -> ``<=``, ``> RING_FILL_PCT`` -> ``>=`` -> test_each_threshold_fires_just_past_the_line_and_not_on_it
  ``evaluate`` letting no_sync through while the Pi is silent                                    -> test_a_silent_pi_is_its_own_warning_and_mutes_no_sync
  ``transition`` notifying while a kind is already active                                        -> test_a_warning_rings_once_when_it_arrives_not_on_every_report
  ``record_report`` / ``sweep`` notifying with ``ZOE_OMI_HEALTH`` off                            -> test_the_kill_switch_stops_the_warnings_but_not_the_numbers
  ``omi_devices.owner_of`` skipped for the report (attributing to the panel's user)             -> test_the_report_is_stored_under_the_pendants_owner_not_the_panels_member
  ``forget_user`` not wired into ``omi_recordings.forget_user``                                  -> test_erasing_a_member_erases_their_health_rows

Synthetic data only. Real SQLite built by migrations 0045 + 0047 + 0056 behind ``db_compat``; no hardware, no network.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import asyncio
import contextlib
import hashlib
import json
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import brief_first_turn as bft
import db_compat
import omi_devices
import omi_health as oh
import omi_recordings as omi
import user_erase_gate as erase_gate
from routers import omi_recordings as omi_router
from test_omi_recordings import TOKEN, _migrate, _Sqlite

T0 = datetime(2026, 10, 18, 6, 0, 0, tzinfo=timezone.utc)


def ident(address: str) -> str:
    return hashlib.sha256(("omi-sync:" + address.strip().lower()).encode()).hexdigest()[:16]


DEV_A, DEV_B = ident("AA:BB:CC:DD:EE:01"), ident("AA:BB:CC:DD:EE:02")
ANN = {"user_id": "ann", "role": "member", "username": "ann"}
BEN = {"user_id": "ben", "role": "member", "username": "ben"}
ADM = {"user_id": "adm", "role": "admin", "username": "adm"}
PANEL_AS_ANN = {"user_id": "ann", "role": "member", "username": "panel:p1", "panel_id": "p1", "device_token_role": "panel"}


def run(coro):
    return asyncio.run(coro)


def rep(**kw):
    base = dict(reachable=True, consecutive_misses=0, last_sync_age_s=0, battery_pct=80, firmware="3.0.21", ring_fill_pct=5.0,
                ring_fill_start_pct=40.0, ring_stored_bytes=1000, ring_capacity_bytes=20000, dropped_total=0)
    base.update(kw)
    return oh.Report(**base)


def body(dev=DEV_A, **kw):
    doc = {"device": dev, "reachable": True, "consecutive_misses": 0, "last_sync_age_s": 5, "battery_pct": 80, "firmware": "3.0.21",
           "ring_fill_pct": 5.0, "ring_fill_start_pct": 40.0, "ring_stored_bytes": 1000, "ring_capacity_bytes": 20000, "dropped_total": 0}
    doc.update(kw)
    return {k: v for k, v in doc.items() if v is not None}


# =============================================================================== pure: the counter, the merge, the thresholds
def test_the_overwrite_counter_is_a_baseline_then_a_delta_and_a_repeat_adds_nothing():
    assert oh.overwritten_delta(None, 500) == 0, "the first reading is a baseline: history before Zoe watched is not news"
    assert oh.overwritten_delta(500, 500) == 0, "a repeated report (a lost 200, a retry) adds nothing"
    assert oh.overwritten_delta(500, 620) == 120
    assert oh.overwritten_delta(500, 40) == 40, "the counter went DOWN: the pendant restarted its count, so 40 is what it lost since"
    assert oh.overwritten_delta(500, None) == 0 and oh.overwritten_delta(None, None) == 0
    # control arm: a naive 'new total minus zero' would call a baseline a loss
    assert oh.overwritten_delta(None, 500) != 500


def test_an_unreachable_report_keeps_what_we_last_knew_with_its_own_timestamp():
    row = oh.merge(None, rep(battery_pct=55, ring_fill_pct=30.0), T0)
    gone = oh.merge(row, oh.Report(reachable=False, consecutive_misses=6, last_sync_age_s=3600), T0 + timedelta(hours=2))
    assert gone["reachable"] == 0 and gone["consecutive_misses"] == 6
    assert gone["battery_pct"] == 55 and gone["battery_at"] == oh._iso(T0), "the last reading stays, dated"
    assert gone["ring_fill_pct"] == 30.0 and gone["ring_at"] == oh._iso(T0)
    assert gone["last_sync_at"] == oh._iso(T0 + timedelta(hours=1)), "derived from the AGE the Pi reported, so its clock never matters"
    assert gone["last_seen_at"] == oh._iso(T0) and gone["first_reported_at"] == oh._iso(T0)
    # control arm: a reachable report does replace them
    back = oh.merge(gone, rep(battery_pct=70, ring_fill_pct=1.0), T0 + timedelta(hours=3))
    assert back["battery_pct"] == 70 and back["ring_fill_pct"] == 1.0 and back["consecutive_misses"] == 0


def kinds(row, now):
    return [w["kind"] for w in oh.evaluate(row, now)]


def fresh_row(now=T0, **over):
    """A healthy row last reported 'now'."""
    row = oh.merge(None, rep(), now)
    row.update(over)
    return row


def test_each_threshold_fires_just_past_the_line_and_not_on_it():
    now = T0 + timedelta(hours=1)
    # battery: below 20 only
    assert kinds(fresh_row(now, battery_pct=20), now) == [] and kinds(fresh_row(now, battery_pct=19), now) == ["battery_low"]
    assert kinds(fresh_row(now, battery_pct=None), now) == [], "an unreadable battery is unknown, not low"
    # ring: above 75 only
    assert kinds(fresh_row(now, ring_fill_pct=75.0), now) == [] and kinds(fresh_row(now, ring_fill_pct=75.1), now) == ["ring_fill"]
    # no_sync: more than 36 h since the last clean sync, with the Pi reporting
    sync = lambda hours: oh._iso(now - timedelta(hours=hours))               # noqa: E731
    away = dict(reachable=0, reported_at=oh._iso(now))
    assert kinds(fresh_row(now, last_sync_at=sync(35.9), **away), now) == []
    assert kinds(fresh_row(now, last_sync_at=sync(36.0), **away), now) == []
    assert kinds(fresh_row(now, last_sync_at=sync(36.1), **away), now) == ["no_sync"]
    # overwrite: any overwrite in the last 72 h
    hit = lambda hours: dict(last_overwrite_at=oh._iso(now - timedelta(hours=hours)), last_overwritten_records=3000)   # noqa: E731
    assert kinds(fresh_row(now, **hit(71.9)), now) == ["overwrite"] and kinds(fresh_row(now, **hit(72.1)), now) == []
    # bridge silent: nothing from the Pi for more than 3 h
    quiet = lambda hours: fresh_row(now, reported_at=oh._iso(now - timedelta(hours=hours)))    # noqa: E731
    assert kinds(quiet(2.9), now) == [] and kinds(quiet(3.1), now) == ["bridge_silent"]


def test_every_warning_clears_when_the_data_does():
    now = T0 + timedelta(hours=1)
    bad = fresh_row(now, battery_pct=10, ring_fill_pct=90.0, last_sync_at=oh._iso(now - timedelta(hours=40)), reachable=0,
                    last_overwrite_at=oh._iso(now - timedelta(hours=1)), last_overwritten_records=500)
    assert kinds(bad, now) == ["overwrite", "no_sync", "ring_fill", "battery_low"], "most important first"
    healed = dict(bad, battery_pct=95, ring_fill_pct=2.0, last_sync_at=oh._iso(now), reachable=1)
    assert kinds(healed, now) == ["overwrite"], "all but the overwrite clear on a new reading"
    assert kinds(healed, now + timedelta(hours=73)) == ["bridge_silent"], "the overwrite ages out; only the (now silent) Pi remains"
    later = now + timedelta(hours=73)
    assert kinds(dict(healed, reported_at=oh._iso(later), last_sync_at=oh._iso(later)), later) == [], "a fresh report and sync: nothing left"


def test_a_never_synced_pendant_counts_from_the_first_report():
    now = T0 + timedelta(hours=40)
    row = dict(fresh_row(T0), last_sync_at=None, reported_at=oh._iso(now), reachable=0)
    assert kinds(row, now) == ["no_sync"], "no clean sync ever, first heard from 40 h ago, still not syncing"
    assert kinds(dict(row, first_reported_at=oh._iso(now - timedelta(hours=10))), now) == []


def test_a_silent_pi_is_its_own_warning_and_mutes_no_sync():
    now = T0 + timedelta(hours=60)
    row = fresh_row(T0, last_sync_at=oh._iso(T0), reachable=0)             # last heard 60 h ago
    got = kinds(row, now)
    assert got == ["bridge_silent"], "if the Pi is silent 'away' cannot be known - say so instead of guessing"
    assert "no_sync" in kinds(dict(row, reported_at=oh._iso(now)), now), "control arm: with the Pi reporting, the same data IS no_sync"
    text = oh.evaluate(row, now)[0]["text"]
    assert "2 days" in text and "pendant" in text


def test_warning_texts_are_counts_and_durations_only_and_fit_a_brief_line():
    now = T0 + timedelta(hours=1)
    row = fresh_row(now, battery_pct=7, ring_fill_pct=88.0, last_sync_at=oh._iso(now - timedelta(hours=50)), reachable=0,
                    last_overwrite_at=oh._iso(now), last_overwritten_records=int(30 * 60 / oh.SECONDS_PER_RECORD))
    texts = {w["kind"]: w["text"] for w in oh.evaluate(row, now)}
    assert "30 minutes" in texts["overwrite"] and "2 days" in texts["no_sync"] and "88%" in texts["ring_fill"] and "7%" in texts["battery_low"]
    assert all(len(t) <= 140 for t in texts.values()), "each fits the first-turn brief line without being cut mid-sentence"


# =============================================================================== transition: a warning rings once
def test_a_warning_rings_once_when_it_arrives_not_on_every_report():
    w = [{"kind": "battery_low", "text": "x"}]
    state, notify = oh.transition({"warn_state": "{}"}, w, T0)
    assert [n["kind"] for n in notify] == ["battery_low"] and state["battery_low"]["since"] == oh._iso(T0)
    state2, notify2 = oh.transition({"warn_state": json.dumps(state)}, w, T0 + timedelta(minutes=10))
    assert notify2 == [] and state2["battery_low"]["since"] == oh._iso(T0), "still active: no second bell, and 'since' is kept"
    # control arm: a different kind arriving beside it does ring
    _, notify3 = oh.transition({"warn_state": json.dumps(state)}, w + [{"kind": "ring_fill", "text": "y"}], T0 + timedelta(minutes=20))
    assert [n["kind"] for n in notify3] == ["ring_fill"]


def test_a_hovering_value_does_not_ring_every_cycle_but_a_real_return_does():
    w = [{"kind": "battery_low", "text": "x"}]
    state, _ = oh.transition({"warn_state": "{}"}, w, T0)
    cleared, n1 = oh.transition({"warn_state": json.dumps(state)}, [], T0 + timedelta(minutes=30))
    assert n1 == [] and cleared["battery_low"]["since"] is None, "cleared, but remembered for the cooldown"
    _, n2 = oh.transition({"warn_state": json.dumps(cleared)}, w, T0 + timedelta(hours=1))
    assert n2 == [], "19 % -> 21 % -> 19 % inside six hours is one bell"
    _, n3 = oh.transition({"warn_state": json.dumps(cleared)}, w, T0 + timedelta(hours=7))
    assert [n["kind"] for n in n3] == ["battery_low"], "control arm: the next day's low battery rings again"
    gone, _ = oh.transition({"warn_state": json.dumps(cleared)}, [], T0 + timedelta(hours=8))
    assert {k: v for k, v in gone.items() if not k.startswith("_")} == {}, "the cooldown memory is forgotten once it has expired"
    assert gone["_episodes"] == {"battery_low": 1}, "but the episode counter is never forgotten: the next bell id must not repeat an old one"
    # the episode rides with every notified warning, and a re-arm is a NEW episode (a new bell id)
    _, again = oh.transition({"warn_state": json.dumps(gone)}, w, T0 + timedelta(hours=9))
    assert again[0]["episode"] == 2 and oh.bell_id(DEV_A, "battery_low", 1) != oh.bell_id(DEV_A, "battery_low", 2)


# =============================================================================== the stored flow, through the routes
class Env:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.user = ANN
        self.app.dependency_overrides[auth.get_current_user] = lambda: self.user
        self.now = T0

    def as_(self, user):
        self.user = user
        return self

    def post(self, doc, token=True):
        return self.client.post("/api/omi/device-status", json=doc, headers=TOKEN if token else {})

    def get(self):
        return self.client.get("/api/omi/health")

    def rows(self):
        return {r["device_id"]: dict(r) for r in self.db.rows("SELECT * FROM omi_device_health")}

    def registry_owner(self, dev):
        rows = self.db.rows("SELECT user_id FROM omi_devices WHERE device_id = ?", (dev,))
        return rows[0]["user_id"] if rows else None

    def bells(self):
        return [(r["user_id"], r["message"], json.loads(r["data"])["kind"]) for r in self.db.rows("SELECT * FROM notifications ORDER BY created_at, id")]


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    _migrate(engine)
    _migrate(engine, "0047_omi_devices.py")
    _migrate(engine, "0056_omi_device_health.py")
    db = _Sqlite(path)
    db.conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
    db.conn.executemany("INSERT INTO users VALUES (?, ?, ?)", [("ann", "Ann", "member"), ("ben", "Ben", "member"), ("adm", "Admin", "admin")])
    db.conn.execute("CREATE TABLE notifications (id TEXT PRIMARY KEY, user_id TEXT, type TEXT, title TEXT, message TEXT, data TEXT, "
                    "delivered INTEGER DEFAULT 0, created_at TEXT)")
    db.conn.create_function("NOW", 0, lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f+00"))
    db.conn.commit()

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    sent = []

    class _B:
        async def broadcast(self, channel, event, data, user_id=None):
            sent.append((event, data.get("type"), user_id))

    import db_pool
    import push
    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setattr(db_pool, "get_db_ctx", fake_db)
    monkeypatch.setattr(push, "broadcaster", _B())
    monkeypatch.setenv("ZOE_OMI_SYNC_ENABLED", "1")
    monkeypatch.setenv("ZOE_OMI_DEVICE_OWNERS", "1")
    monkeypatch.delenv("ZOE_OMI_OWNER_USER_ID", raising=False)
    monkeypatch.delenv("ZOE_OMI_HEALTH", raising=False)
    monkeypatch.setenv("ZOE_OMI_AUDIT_PATH", str(tmp_path / "omi-audit.jsonl"))
    monkeypatch.setenv("ZOE_OMI_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    erase_gate.reset()
    omi_devices.reset_for_tests()

    import routers.panel_auth as pa
    # the panel's token is bound to BEN - and that must change nothing about whose pendant reports
    monkeypatch.setattr(pa, "lookup_device_token", lambda raw: {"panel_id": "p1", "user_id": "ben"} if raw == "tok" else None)
    app = FastAPI()
    assert omi_router.register(app) is True
    e = Env(db=db, client=TestClient(app), app=app, engine=engine, mp=monkeypatch, sent=sent, tmp=tmp_path)
    monkeypatch.setattr(oh, "_utcnow", lambda: e.now)
    yield e
    erase_gate.reset()
    omi_devices.reset_for_tests()


def register(env, dev, who="ann"):
    saved = env.user
    env.as_(ADM)
    r = env.client.put(f"/api/omi/devices/{dev}", json={"user_id": who})
    assert r.status_code == 200, r.text
    env.as_(saved)


def test_the_report_is_stored_under_the_pendants_owner_not_the_panels_member(env):
    register(env, DEV_A, "ann")
    register(env, DEV_B, "ben")
    env.as_(BEN)                                            # ben is signed in on the panel and the panel token is bound to ben
    assert env.post(body(DEV_A)).status_code == 200
    assert {d: r["user_id"] for d, r in env.rows().items()} == {DEV_A: "ann"}
    # control arm: ben's own pendant lands with ben
    assert env.post(body(DEV_B, battery_pct=44)).status_code == 200
    assert {d: r["user_id"] for d, r in env.rows().items()} == {DEV_A: "ann", DEV_B: "ben"}


def test_an_unauthenticated_report_is_refused_and_stores_nothing(env):
    register(env, DEV_A, "ann")
    assert env.post(body(), token=False).status_code == 401
    bad = env.client.post("/api/omi/device-status", json=body(), headers={"X-Device-Token": "wrong"})
    assert bad.status_code == 401
    assert env.client.post("/api/omi/device-status", json=body()).status_code == 401, "and a signed-in session is not a device token"
    assert env.rows() == {} and env.bells() == []
    assert env.post(body()).status_code == 200 and list(env.rows()) == [DEV_A], "control arm: the device token is accepted"


def test_a_pendant_nobody_owns_stores_nothing_and_says_so(env):
    r = env.post(body(DEV_A))
    assert r.status_code == 409 and r.json()["error"] == "device_not_registered"
    assert env.rows() == {}
    assert env.post(body("not-a-device-id")).status_code in (400, 409)
    assert env.rows() == {}


def test_a_report_cannot_carry_content(env):
    register(env, DEV_A, "ann")
    for extra in ({"transcript": "private words"}, {"audio": "AAAA"}, {"address": "AA:BB:CC:DD:EE:01"}, {"note": "hello"}):
        r = env.post(body(**extra))
        assert r.status_code == 422, extra
    assert env.rows() == {}
    for bad in ({"battery_pct": 101}, {"battery_pct": -1}, {"ring_fill_pct": 100.5}, {"consecutive_misses": -2},
                {"last_sync_age_s": -1}, {"firmware": "3.0.21 <b>"}, {"dropped_total": -5}):
        assert env.post(body(**bad)).status_code == 422, bad
    assert env.post(body()).status_code == 200, "control arm: the same body without the extra field is stored"


def test_another_member_cannot_read_a_pendants_health(env):
    register(env, DEV_A, "ann")
    register(env, DEV_B, "ben")
    env.post(body(DEV_A, battery_pct=11))
    env.post(body(DEV_B, battery_pct=90))
    env.as_(ANN)
    mine = env.get().json()["pendants"]
    assert [p["device_id"] for p in mine] == [DEV_A] and mine[0]["battery"]["pct"] == 11
    env.as_(BEN)
    theirs = env.get().json()["pendants"]
    assert [p["device_id"] for p in theirs] == [DEV_B], "ben sees only ben's pendant, never ann's"
    assert DEV_A not in json.dumps(theirs) and "ann" not in json.dumps(theirs)
    env.as_(ADM)
    assert env.get().json()["pendants"] == [], "and there is no admin override: a pendant's state is the wearer's"


def test_a_device_token_session_and_a_guest_cannot_read_it(env):
    register(env, DEV_A, "ann")
    env.post(body())
    env.as_(PANEL_AS_ANN)
    assert env.get().status_code == 403, "the panel's token bound to ann is still the panel, not ann"
    env.as_(ANN)
    assert env.get().status_code == 200, "control arm: ann herself"


def test_a_guest_cannot_read_it(env):
    register(env, DEV_A, "ann")
    env.post(body())
    env.as_({"user_id": "guest", "role": "guest", "username": "guest"})
    r = env.get()
    assert r.status_code in (401, 403) and "pendants" not in r.text
    env.as_(ANN)
    assert env.get().status_code == 200, "control arm: the owner"


def test_a_reassigned_pendant_vanishes_from_the_previous_owner_at_once(env):
    register(env, DEV_A, "ann")
    env.post(body())
    env.as_(ANN)
    assert len(env.get().json()["pendants"]) == 1
    register(env, DEV_A, "ben")                              # no new report has arrived yet
    env.as_(ANN)
    assert env.get().json()["pendants"] == [], "the row still says ann, but the registry says ben: ann sees nothing"
    env.as_(BEN)
    assert env.get().json()["pendants"] == [], "and ben sees nothing until the Pi reports under him (no row is ever shown to a guess)"
    env.post(body())
    assert [p["device_id"] for p in env.get().json()["pendants"]] == [DEV_A] and env.rows()[DEV_A]["user_id"] == "ben"


def test_the_read_is_the_latest_row_with_live_warnings_and_numbers_only(env):
    register(env, DEV_A, "ann")
    env.post(body(battery_pct=15, ring_fill_pct=80.0))
    env.as_(ANN)
    (p,) = env.get().json()["pendants"]
    assert p["status"] == "attention" and [w["kind"] for w in p["warnings"]] == ["ring_fill", "battery_low"]
    assert p["battery"]["pct"] == 15 and p["ring"]["fill_pct"] == 80.0 and p["firmware"] == "3.0.21"
    assert p["reachable"] is True and p["last_sync_at"] and p["warnings"][0]["since"]
    flat = json.dumps(p)
    assert "AA:BB" not in flat and "transcript" not in flat and "user_id" not in flat
    # a second report REPLACES the row (one row per pendant), and the warnings clear with the data
    env.now = T0 + timedelta(minutes=20)
    env.post(body(battery_pct=95, ring_fill_pct=1.0))
    assert len(env.rows()) == 1
    (p2,) = env.get().json()["pendants"]
    assert p2["status"] == "ok" and p2["warnings"] == [] and p2["battery"]["pct"] == 95


def test_time_passing_alone_raises_no_sync_on_the_read_and_the_sweep_rings_the_bell(env):
    register(env, DEV_A, "ann")
    env.post(body(last_sync_age_s=0))
    # the Pi keeps saying 'not there' for two days, a report every 15 minutes
    for k in range(1, 8 * 24):
        env.now = T0 + timedelta(minutes=15 * k)
        r = env.post({"device": DEV_A, "reachable": False, "consecutive_misses": 6, "last_sync_age_s": 15 * 60 * k})
        assert r.status_code == 200
    env.as_(ANN)
    (p,) = env.get().json()["pendants"]
    assert [w["kind"] for w in p["warnings"]] == ["no_sync"] and "out of range" in p["warnings"][0]["text"]
    bells = env.bells()
    assert [b[2] for b in bells] == ["no_sync"], "ONE bell for two days of the same condition"
    assert bells[0][0] == "ann" and "has not synced" in bells[0][1]
    assert env.sent and env.sent[0][1] == "omi_health" and env.sent[0][2] == "ann", "broadcast to the owner only"


def test_the_sweep_raises_a_silent_pi_with_no_report_at_all(env):
    register(env, DEV_A, "ann")
    env.post(body())
    assert run(oh.sweep(now=T0 + timedelta(hours=2))) == 0
    assert run(oh.sweep(now=T0 + timedelta(hours=4))) == 1
    assert run(oh.sweep(now=T0 + timedelta(hours=5))) == 0, "once, not every sweep"
    assert [b[2] for b in env.bells()] == ["bridge_silent"] and env.bells()[0][0] == "ann"
    # control arm: the Pi comes back and the warning clears; a later silence is a new event past the cooldown
    env.now = T0 + timedelta(hours=6)
    env.post(body())
    env.as_(ANN)
    assert env.get().json()["pendants"][0]["warnings"] == []
    assert run(oh.sweep(now=T0 + timedelta(hours=40))) == 1 and [b[2] for b in env.bells()] == ["bridge_silent", "bridge_silent"]


def test_the_sweep_does_not_warn_the_previous_owner_of_a_reassigned_pendant(env):
    register(env, DEV_A, "ann")
    env.post(body())
    register(env, DEV_A, "ben")
    assert run(oh.sweep(now=T0 + timedelta(hours=10))) == 0 and env.bells() == []


def test_a_warning_rings_once_when_the_report_that_causes_it_arrives(env):
    register(env, DEV_A, "ann")
    env.post(body(battery_pct=50))
    assert env.bells() == []
    for k in range(1, 5):
        env.now = T0 + timedelta(minutes=15 * k)
        env.post(body(battery_pct=18))
    assert [b[2] for b in env.bells()] == ["battery_low"] and "18%" in env.bells()[0][1]
    env.now = T0 + timedelta(hours=10)
    env.post(body(battery_pct=100))
    env.now = T0 + timedelta(hours=20)
    env.post(body(battery_pct=12))
    assert [b[2] for b in env.bells()] == ["battery_low", "battery_low"], "control arm: cleared, then a fresh low rings again"


def test_a_bell_insert_that_fails_saves_nothing_and_the_report_is_retried(env):
    """The bell and the new warn_state are one decision: if the bell cannot be written, NOTHING is saved and the report fails (503), so
    the Pi sends it again and the bell rings then. It is never marked delivered without a row."""
    register(env, DEV_A, "ann")
    env.post(body(battery_pct=50))                           # a stored row with no warning
    before = dict(env.rows()[DEV_A])
    real = oh._write_bells
    calls = []

    async def flaky(db, user_id, device_id, notify):
        calls.append([w["kind"] for w in notify])
        if len(calls) == 1:
            raise OSError("notifications insert failed")
        return await real(db, user_id, device_id, notify)

    env.mp.setattr(oh, "_write_bells", flaky)
    env.now = T0 + timedelta(minutes=15)
    r = env.post(body(battery_pct=10))
    assert r.status_code == 503 and env.bells() == []
    assert env.rows()[DEV_A] == before, "the failed report changed nothing: the warning is still un-notified"
    env.now = T0 + timedelta(minutes=30)
    assert env.post(body(battery_pct=10)).status_code == 200
    assert [b[2] for b in env.bells()] == ["battery_low"] and calls[0] == calls[1] == ["battery_low"]
    env.now = T0 + timedelta(minutes=45)
    env.post(body(battery_pct=10))
    assert len(env.bells()) == 1, "control arm: once it rang it is not rung again while the warning stays active"


def test_a_crash_between_the_bell_and_the_saved_state_loses_nothing_and_duplicates_nothing(env):
    """The bell row committed, then the process died before warn_state was saved. The retry decides to ring the same episode again: the
    bell id is derived from (pendant, kind, episode), so it is the SAME row - the bell is neither lost nor doubled."""
    register(env, DEV_A, "ann")
    env.post(body(battery_pct=50))
    real = oh._save
    n = {"calls": 0}

    async def crash(db, device_id, row):
        n["calls"] += 1
        if n["calls"] == 1:
            raise RuntimeError("process died after the bell insert")
        return await real(db, device_id, row)

    env.mp.setattr(oh, "_save", crash)
    env.now = T0 + timedelta(minutes=15)
    assert env.post(body(battery_pct=10)).status_code == 503
    assert [b[2] for b in env.bells()] == ["battery_low"], "the bell is already there"
    assert "battery_low" not in json.loads(env.rows()[DEV_A]["warn_state"]), "but the state does not claim it"
    env.now = T0 + timedelta(minutes=30)                     # a DIFFERENT clock reading than the crashed attempt
    assert env.post(body(battery_pct=10)).status_code == 200
    assert [b[2] for b in env.bells()] == ["battery_low"], "the retry found its own row: still exactly one bell"
    assert json.loads(env.rows()[DEV_A]["warn_state"])["battery_low"]["notified_at"]
    # control arm: after the warning clears and re-arms past the cooldown it is a NEW episode and a NEW bell
    env.now = T0 + timedelta(hours=2)
    env.post(body(battery_pct=90))
    env.now = T0 + timedelta(hours=10)
    env.post(body(battery_pct=5))
    assert [b[2] for b in env.bells()] == ["battery_low", "battery_low"]


def test_a_committed_bell_is_never_inserted_again_when_only_the_push_failed(env):
    """The notifications row committed; the live broadcast raised. The bell exists, so it counts as delivered: no retry, no duplicate."""
    register(env, DEV_A, "ann")

    class _Down:
        async def broadcast(self, *a, **k):
            raise ConnectionError("websocket hub down")

    import push
    env.mp.setattr(push, "broadcaster", _Down())
    r = env.post(body(battery_pct=10))
    assert r.status_code == 200 and [b[2] for b in env.bells()] == ["battery_low"], "the row is there and the report succeeded"
    for k in range(1, 4):
        env.now = T0 + timedelta(minutes=15 * k)
        env.post(body(battery_pct=10))
    assert len(env.bells()) == 1, "and four reports later it is still ONE bell"


def test_the_sweep_and_a_heartbeat_can_never_both_ring_one_warning(env):
    """Both read the same old warn_state (the report was stored with warnings off) and both want to ring. The per-member lock makes the
    second see the first's state. Counted at the transition itself, so the bell id's own idempotence cannot hide a second decision."""
    register(env, DEV_A, "ann")
    env.mp.setenv("ZOE_OMI_HEALTH", "0")
    env.post(body(battery_pct=10))
    env.mp.delenv("ZOE_OMI_HEALTH")
    decisions = []
    real = oh._write_bells

    async def counting(db, user_id, device_id, notify):
        if notify:
            decisions.append(([w["kind"] for w in notify], user_id))
            await asyncio.sleep(0)                           # a yield between the decision and the write: where an unlocked race lives
        return await real(db, user_id, device_id, notify)

    env.mp.setattr(oh, "_write_bells", counting)
    t = T0 + timedelta(minutes=1)

    async def both():
        return await asyncio.gather(oh.record_report("ann", DEV_A, rep(battery_pct=10), now=t), oh.sweep(now=t))

    run(both())
    assert decisions == [(["battery_low"], "ann")], "exactly one task decided to ring"
    assert [b[2] for b in env.bells()] == ["battery_low"]


def test_a_racing_heartbeat_cannot_recreate_a_health_row_for_a_forgotten_member(env):
    register(env, DEV_A, "ann")
    env.post(body())
    stale = erase_gate.generation("ann")
    run(omi.forget_user("ann"))
    assert env.rows() == {}
    register(env, DEV_A, "ann")                              # the pendant is hers again, so ONLY the generation can refuse the write
    with pytest.raises(oh.NotTheOwner):
        run(oh.record_report("ann", DEV_A, rep(), scheduled_at=stale))
    assert env.rows() == {}, "a write scheduled before the erase is dropped"
    run(oh.record_report("ann", DEV_A, rep(), scheduled_at=erase_gate.generation("ann")))
    assert list(env.rows()) == [DEV_A], "control arm: a write scheduled after it is stored"


def test_the_route_drops_a_heartbeat_whose_member_was_erased_after_the_owner_was_resolved(env):
    register(env, DEV_A, "ann")
    real = omi_router._upload_owner

    async def racing(device_id):
        out = await real(device_id)                          # the owner is resolved ...
        await omi.forget_user("ann")                         # ... then the member is erased before the write runs
        return out

    env.mp.setattr(omi_router, "_upload_owner", racing)
    r = env.post(body())
    assert r.status_code == 409 and r.json()["error"] == "device_not_registered"
    assert env.rows() == {} and env.bells() == [], "nothing of ann survives the erase"
    env.mp.setattr(omi_router, "_upload_owner", real)
    register(env, DEV_A, "ben")
    assert env.post(body()).status_code == 200 and env.rows()[DEV_A]["user_id"] == "ben", "control arm: no race, no problem"


def test_a_heartbeat_for_a_pendant_unassigned_meanwhile_is_dropped(env):
    register(env, DEV_A, "ann")
    real = omi_router._upload_owner

    async def racing(device_id):
        out = await real(device_id)
        await omi_devices.unassign(device_id, actor=ADM)      # reassigned/unassigned between the lookup and the write
        return out

    env.mp.setattr(omi_router, "_upload_owner", racing)
    r = env.post(body())
    assert r.status_code == 409 and env.rows() == {}, "ownership is re-checked inside the lock"


def test_a_reassigned_pendant_tells_the_new_owner_even_when_the_old_one_was_already_told(env):
    register(env, DEV_A, "ann")
    env.post(body(battery_pct=10))
    assert [(b[0], b[2]) for b in env.bells()] == [("ann", "battery_low")]
    register(env, DEV_A, "ben")
    env.now = T0 + timedelta(minutes=15)
    env.post(body(battery_pct=10))                           # the same low battery, now ben's pendant
    assert [(b[0], b[2]) for b in env.bells()] == [("ann", "battery_low"), ("ben", "battery_low")]
    state = json.loads(env.rows()[DEV_A]["warn_state"])
    assert state["battery_low"]["since"] == oh._iso(env.now), "ben's warning starts when HE was first told, not ann's timestamp"
    env.now = T0 + timedelta(minutes=30)
    env.post(body(battery_pct=10))
    assert len(env.bells()) == 2, "control arm: and once he is told it is not repeated"


def test_a_new_owner_starts_a_fresh_overwrite_baseline_and_inherits_none_of_the_old_history(env):
    """Follow-up (#2098): the new owner of a pendant inherited the previous owner's overwrite total, last-overwrite time and warning (and was
    told about audio lost on someone else's watch). The first report under the new owner is a baseline, like a first-ever one."""
    register(env, DEV_A, "ann")
    env.post(body(dropped_total=900))
    env.now = T0 + timedelta(hours=1)
    env.post(body(dropped_total=900 + 16_364))
    assert env.rows()[DEV_A]["overwritten_records_total"] == 16_364 and [(b[0], b[2]) for b in env.bells()] == [("ann", "overwrite")]
    register(env, DEV_A, "ben")
    env.now = T0 + timedelta(hours=2)
    env.post(body(dropped_total=900 + 16_364))               # the same cumulative counter, now reported under ben
    row = env.rows()[DEV_A]
    assert row["user_id"] == "ben" and row["overwritten_records_total"] == 0 and row["last_overwritten_records"] == 0
    assert row["last_overwrite_at"] is None and row["dropped_total"] == 900 + 16_364, "history gone, the pendant's own counter is the new baseline"
    assert [(b[0], b[2]) for b in env.bells()] == [("ann", "overwrite")], "ben is not told about audio lost before he owned it"
    env.as_(BEN)
    (p,) = env.get().json()["pendants"]
    assert p["overwritten"] == {"records_total": 0, "last_minutes": 0.0, "last_at": None} and p["warnings"] == []
    env.now = T0 + timedelta(hours=3)
    env.post(body(dropped_total=900 + 16_364 + 500))         # control arm: an overwrite on ben's watch counts and rings
    assert env.rows()[DEV_A]["overwritten_records_total"] == 500 and [(b[0], b[2]) for b in env.bells()][-1] == ("ben", "overwrite")
    # the battery / ring / sync facts describe the hardware and stay
    assert env.rows()[DEV_A]["battery_pct"] == 80


def test_the_same_owner_reporting_again_keeps_the_overwrite_history(env):
    register(env, DEV_A, "ann")
    env.post(body(dropped_total=900))
    env.now = T0 + timedelta(hours=1)
    env.post(body(dropped_total=900 + 16_364))
    env.now = T0 + timedelta(hours=2)
    env.post(body(dropped_total=900 + 16_364))
    assert env.rows()[DEV_A]["overwritten_records_total"] == 16_364, "no owner change, no rebaseline"
    register(env, DEV_A, "ann")                              # a relabel of the same owner is not a change of owner either
    env.now = T0 + timedelta(hours=3)
    env.post(body(dropped_total=900 + 16_364))
    assert env.rows()[DEV_A]["overwritten_records_total"] == 16_364


def _scenario(env, parked_call, racer):
    """Park ``oh._get_row`` (inside the device-locked section of ``parked_call``), run ``racer`` meanwhile, and report whether ``racer`` was
    held back while the parked call was between its owner check and its write."""
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        real = oh._get_row

        async def parked(db, device_id):
            entered.set()
            await release.wait()
            return await real(db, device_id)
        env.mp.setattr(oh, "_get_row", parked)
        first = asyncio.create_task(parked_call())
        await asyncio.wait_for(entered.wait(), 5)
        second = asyncio.create_task(racer())
        for _ in range(30):
            await asyncio.sleep(0)
        held_back = not second.done()
        release.set()
        out = await asyncio.gather(first, second, return_exceptions=True)
        env.mp.setattr(oh, "_get_row", real)
        return held_back, out
    return asyncio.run(go())


def test_a_reassignment_cannot_land_between_a_heartbeats_owner_check_and_its_save(env):
    """Follow-up (#2098): ``record_report`` checked the owner, a reassignment landed, and the save wrote the previous owner's id on the row.
    ``assign`` / ``unassign`` and the report now share a per-device lock."""
    register(env, DEV_A, "ann")
    env.post(body())
    held_back, out = _scenario(env, lambda: oh.record_report("ann", DEV_A, rep(battery_pct=33)),
                               lambda: omi_devices.assign(DEV_A, "ben", label=None, actor=ADM))
    assert held_back, "assign waits for the heartbeat that is mid-write"
    assert not any(isinstance(o, BaseException) for o in out)
    assert env.rows()[DEV_A]["user_id"] == "ann" and env.rows()[DEV_A]["battery_pct"] == 33 and env.registry_owner(DEV_A) == "ben"
    # unassign is serialised the same way
    register(env, DEV_B, "ann")
    env.post(body(DEV_B))
    held_back, out = _scenario(env, lambda: oh.record_report("ann", DEV_B, rep(battery_pct=44)), lambda: omi_devices.unassign(DEV_B, actor=ADM))
    assert held_back and out[1] is True and env.registry_owner(DEV_B) == ""


def test_the_sweep_holds_the_same_device_lock(env):
    register(env, DEV_A, "ann")
    env.post(body())
    held_back, out = _scenario(env, lambda: oh.sweep(now=T0 + timedelta(hours=5)), lambda: omi_devices.assign(DEV_A, "ben", label=None, actor=ADM))
    assert held_back and not any(isinstance(o, BaseException) for o in out)
    assert [b[0] for b in env.bells()] == ["ann"], "the bell went to the owner who owned the pendant when the sweep decided"


def test_a_heartbeat_waiting_on_a_reassignment_is_refused_afterwards(env):
    """The other order: the device lock is held by the reassignment, so the heartbeat re-reads the owner AFTER it and is refused."""
    register(env, DEV_A, "ann")
    env.post(body())

    async def go():
        async with omi_devices.device_lock(DEV_A):
            report = asyncio.create_task(oh.record_report("ann", DEV_A, rep(battery_pct=5)))
            for _ in range(30):
                await asyncio.sleep(0)
            waiting = not report.done()
            env.db.conn.execute("UPDATE omi_devices SET user_id = 'ben' WHERE device_id = ?", (DEV_A,))      # the reassignment, under the lock
            env.db.conn.commit()
        return waiting, (await asyncio.gather(report, return_exceptions=True))[0]
    waiting, outcome = asyncio.run(go())
    assert waiting and isinstance(outcome, oh.NotTheOwner)
    assert env.rows()[DEV_A]["user_id"] == "ann" and env.rows()[DEV_A]["battery_pct"] == 80, "nothing of the refused heartbeat was stored"


def test_the_kill_switch_hides_the_status_and_warnings_from_the_read_too(env):
    """Follow-up (#2098): ``public_view`` kept evaluating warnings (and a status of 'attention') with ``ZOE_OMI_HEALTH=0``."""
    register(env, DEV_A, "ann")
    env.post(body(battery_pct=5, ring_fill_pct=99.0))
    env.as_(ANN)
    on = env.get().json()
    assert on["pendants"][0]["status"] == "attention" and {w["kind"] for w in on["pendants"][0]["warnings"]} == {"battery_low", "ring_fill"}
    env.mp.setenv("ZOE_OMI_HEALTH", "0")
    off = env.get().json()
    (p,) = off["pendants"]
    assert off["warnings_enabled"] is False and p["warnings"] == [] and p["status"] == "warnings_off", "not 'ok': nothing is being checked"
    assert p["battery"]["pct"] == 5 and p["ring"]["fill_pct"] == 99.0, "the numbers are still read"
    env.mp.delenv("ZOE_OMI_HEALTH")
    assert env.get().json()["pendants"][0]["status"] == "attention", "control arm: back on"


def test_the_sweep_leaves_state_untouched_when_its_bell_cannot_be_written(env):
    register(env, DEV_A, "ann")
    env.post(body())
    real = oh._write_bells
    fail = {"on": True}

    async def flaky(db, user_id, device_id, notify):
        if fail["on"] and notify:
            raise OSError("notifications insert failed")
        return await real(db, user_id, device_id, notify)

    env.mp.setattr(oh, "_write_bells", flaky)
    state = env.rows()[DEV_A]["warn_state"]
    assert run(oh.sweep(now=T0 + timedelta(hours=4))) == 0 and env.bells() == []
    assert env.rows()[DEV_A]["warn_state"] == state, "nothing claimed as notified"
    fail["on"] = False
    assert run(oh.sweep(now=T0 + timedelta(hours=5))) == 1 and [b[2] for b in env.bells()] == ["bridge_silent"]
    assert run(oh.sweep(now=T0 + timedelta(hours=6))) == 0, "and then it is done"


def test_the_first_report_is_a_baseline_and_a_later_rise_is_an_overwrite_that_rings(env):
    register(env, DEV_A, "ann")
    env.post(body(dropped_total=900))
    assert env.bells() == [] and env.rows()[DEV_A]["overwritten_records_total"] == 0, "history before Zoe watched is not news"
    env.now = T0 + timedelta(minutes=15)
    env.post(body(dropped_total=900))
    assert env.rows()[DEV_A]["overwritten_records_total"] == 0, "a repeat adds nothing"
    env.now = T0 + timedelta(hours=1)
    env.post(body(dropped_total=900 + 16_364))               # about 30 minutes at 0.11 s a record
    row = env.rows()[DEV_A]
    assert row["overwritten_records_total"] == 16_364 and row["last_overwrite_at"] == oh._iso(env.now)
    assert [b[2] for b in env.bells()] == ["overwrite"] and "about 30 minutes" in env.bells()[0][1]
    env.now = T0 + timedelta(hours=1, minutes=15)
    env.post(body(dropped_total=900 + 16_364))               # the Pi resends the same counter
    assert env.rows()[DEV_A]["overwritten_records_total"] == 16_364 and len(env.bells()) == 1
    env.as_(ANN)
    (p,) = env.get().json()["pendants"]
    assert p["overwritten"]["records_total"] == 16_364 and p["overwritten"]["last_minutes"] == 30.0


def test_the_kill_switch_stops_the_warnings_but_not_the_numbers(env):
    env.mp.setenv("ZOE_OMI_HEALTH", "0")
    register(env, DEV_A, "ann")
    env.post(body(battery_pct=5, ring_fill_pct=99.0))
    assert env.bells() == [] and run(oh.sweep(now=T0 + timedelta(hours=9))) == 0
    assert run(oh.morning_items("ann")) == []
    assert env.rows()[DEV_A]["battery_pct"] == 5, "the numbers are still stored"
    env.as_(ANN)
    assert env.get().json()["warnings_enabled"] is False
    env.mp.delenv("ZOE_OMI_HEALTH")
    env.now = T0 + timedelta(minutes=15)
    env.post(body(battery_pct=5, ring_fill_pct=99.0))
    assert sorted(b[2] for b in env.bells()) == ["battery_low", "ring_fill"], "control arm: with it on, the same report rings"


def test_an_unmigrated_table_reads_as_no_data_and_a_report_is_retried(env):
    register(env, DEV_A, "ann")
    env.db.conn.execute("DROP TABLE omi_device_health")
    env.db.conn.commit()
    r = env.post(body())
    assert r.status_code == 503 and r.json()["error"] == "health_unavailable", "the Pi treats it as not delivered and tries again"
    env.as_(ANN)
    assert env.get().json()["pendants"] == []
    assert run(oh.morning_items("ann")) == []


# =============================================================================== legacy single-owner mode
@pytest.fixture
def legacy(env):
    env.mp.delenv("ZOE_OMI_DEVICE_OWNERS")
    env.mp.setenv("ZOE_OMI_OWNER_USER_ID", "ann")
    omi_devices.reset_for_tests()
    return env


def test_legacy_mode_attributes_to_the_configured_owner_and_only_the_owner_reads(legacy):
    assert legacy.post(body()).status_code == 200
    assert legacy.rows()[DEV_A]["user_id"] == "ann"
    legacy.as_(ANN)
    assert len(legacy.get().json()["pendants"]) == 1
    legacy.as_(BEN)
    assert legacy.get().status_code == 403, "owner only"
    assert legacy.post(body("garbage")).status_code == 400, "a malformed id is refused here too"
    legacy.mp.delenv("ZOE_OMI_OWNER_USER_ID")
    assert legacy.post(body()).status_code == 503, "no configured owner: nothing is stored under a guess"


# =============================================================================== the first-turn line + the erase cascade
def test_the_first_turn_brief_gets_at_most_one_pendant_line_and_none_when_all_is_well(env):
    register(env, DEV_A, "ann")
    env.post(body(battery_pct=12, ring_fill_pct=90.0, last_sync_age_s=0))
    items = run(oh.morning_items("ann"))
    assert [i["kind"] for i in items] == ["ring_fill"], "one line: the most important active warning"
    ctx = {"pendant_items": items}
    lines, critical = bft.day_items(ctx, T0)
    assert len(lines) == 1 and lines[0].startswith("About their pendant") and "90%" in lines[0] and critical == []
    # control arm: a healthy pendant adds nothing, and another member gets nothing of ann's
    env.now = T0 + timedelta(minutes=15)
    env.post(body(battery_pct=90, ring_fill_pct=2.0))
    assert run(oh.morning_items("ann")) == [] and bft.day_items({}, T0) == ([], [])
    env.post(body(battery_pct=3))
    assert run(oh.morning_items("ben")) == [], "ben has no pendant row, and ann's is not his"


def test_erasing_a_member_erases_their_health_rows(env):
    register(env, DEV_A, "ann")
    register(env, DEV_B, "ben")
    env.post(body(DEV_A))
    env.post(body(DEV_B))
    run(omi.forget_user("ann"))
    assert list(env.rows()) == [DEV_B], "ann's row is gone; ben's stays (control arm)"


def _plain(db):
    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    return ctx


def test_a_health_erase_failure_fails_closed_but_a_missing_table_does_not(env, caplog):
    """Only 'no such table' (0056 not applied: nothing was ever stored) is nothing to erase. Any other failure leaves the member's rows
    behind, so forget_user RAISES - after the recordings and the registrations are done - and the caller retries."""
    register(env, DEV_A, "ann")
    env.post(body())
    real = oh.forget_user

    async def boom(user_id):
        raise PermissionError("permission denied for table omi_device_health")

    env.mp.setattr(oh, "forget_user", boom)
    caplog.set_level("ERROR")
    with pytest.raises(omi.HealthEraseIncomplete):
        run(omi.forget_user("ann"))
    assert issubclass(omi.HealthEraseIncomplete, omi.VoiceEraseIncomplete)
    assert "OMI_HEALTH_ERASE_INCOMPLETE" in caplog.text
    assert env.registry_owner(DEV_A) == "", "the rest of the erase still ran (the registration is unassigned)"
    assert DEV_A in env.rows(), "and the unerased row is exactly what the raise reports"
    # control arm: the real function erases it, and an unmigrated table is not an error
    env.mp.setattr(oh, "forget_user", real)
    run(omi.forget_user("ann"))
    assert env.rows() == {}
    env.db.conn.execute("DROP TABLE omi_device_health")
    env.db.conn.commit()
    assert run(oh.forget_user("ann")) == 0
    run(omi.forget_user("ann"))                                # and the whole erase still completes without the table


def test_the_health_delete_itself_raises_on_anything_but_a_missing_table(env):
    register(env, DEV_A, "ann")
    env.post(body())
    real_db = env.db

    class Denied:
        def execute(self, sql, params=()):
            if sql.startswith("DELETE FROM omi_device_health"):
                raise PermissionError("permission denied for table omi_device_health")
            return real_db.execute(sql, params)

        async def commit(self):
            await real_db.commit()

    @contextlib.asynccontextmanager
    async def denied():
        yield Denied()

    env.mp.setattr(db_compat, "get_compat_db", denied)
    with pytest.raises(PermissionError):
        run(oh.forget_user("ann"))
    env.mp.setattr(db_compat, "get_compat_db", _plain(real_db))
    assert list(env.rows()) == [DEV_A], "the row survived the failed delete, and the raise said so"
    assert run(oh.forget_user("ann")) == 1


def test_migration_0056_is_reversible_and_rerunnable(env):
    _migrate(env.engine, "0056_omi_device_health.py")                  # IF NOT EXISTS: a rerun is safe
    _migrate(env.engine, "0056_omi_device_health.py", down=True)
    assert env.db.rows("SELECT name FROM sqlite_master WHERE name = 'omi_device_health'") == []
    _migrate(env.engine, "0056_omi_device_health.py")
    assert env.db.rows("SELECT name FROM sqlite_master WHERE name = 'omi_device_health'")


def test_the_routes_do_not_exist_while_the_omi_flag_is_off(env, monkeypatch):
    monkeypatch.delenv("ZOE_OMI_SYNC_ENABLED")
    app = FastAPI()
    assert omi_router.register(app) is False
    c = TestClient(app)
    assert c.post("/api/omi/device-status", json=body(), headers=TOKEN).status_code == 404 and c.get("/api/omi/health").status_code == 404
