"""alarms (P1 backend): migration 0063, schedule maths, the ring lifecycle on a REAL sqlite DB, the HTTP surface, visibility, the
voice grammar and its Skybridge wiring. Flag-dark ``ZOE_ALARMS``. Synthetic ids only.

Negative controls (run by hand when this was written; each turns the named test red):
  * drop ``AND next_fire_at = ?`` from the idle->ringing UPDATE in ``alarms.tick`` -> test_two_engines_ring_an_occurrence_once;
  * drop the ``MISSED_GRACE_S`` branch -> test_an_occurrence_missed_while_down_is_skipped_not_rung_late;
  * make ``can_silence`` mirror ``can_edit`` -> test_the_panel_can_silence_any_ringing_alarm_but_edit_only_household;
  * remove the ``_on`` router gate -> test_flag_off_is_404_everywhere;
  * drop the "asks_or_mentions" guard in ``alarm_voice.parse`` -> test_voice_refuses_questions_and_other_alarms;
  * replace the ``alarm["state"] != "ringing"`` check in ``alarms.snooze`` with ``if False`` -> test_snooze_needs_a_ringing_alarm_and_has_a_budget.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alarm_voice  # noqa: E402
import alarms  # noqa: E402
from database import get_db  # noqa: E402
from panel_principal import require_member_or_panel  # noqa: E402

pytestmark = pytest.mark.ci_safe

MIG = ROOT / "alembic/versions/0063_alarms.py"
PERTH = ZoneInfo("Australia/Perth")
ANN, BOB = "ann-0001", "bob-0002"


def ts(y, mo, d, h=0, mi=0, s=0, tz=PERTH) -> float:
    return datetime(y, mo, d, h, mi, s, tzinfo=tz).timestamp()


NIGHT = ts(2026, 11, 2, 22, 0)  # Monday 22:00 Perth
SEVEN = ts(2026, 11, 3, 7, 0)  # Tuesday 07:00 Perth


def _mig():
    spec = importlib.util.spec_from_file_location("mig_0063", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _migrate(path: str, fn: str = "upgrade") -> None:
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(_mig(), fn)()
    engine.dispose()


@pytest.fixture(autouse=True)
def _tz(monkeypatch):
    monkeypatch.delenv("ZOE_TIMEZONE", raising=False)


@pytest.fixture
def dbfile(tmp_path):
    p = str(tmp_path / "z.db")
    _migrate(p)
    return p


def run(coro):
    return asyncio.run(coro)


async def _open(path):
    return await aiosqlite.connect(path)


async def _mk(path, owner=ANN, now=NIGHT, **kw):
    db = await _open(path)
    try:
        kw.setdefault("time_hhmm", "07:00")
        return await alarms.create_alarm(db, owner, now=now, **kw)
    finally:
        await db.close()


async def _tick(path, now):
    db = await _open(path)
    try:
        return await alarms.tick(db, now)
    finally:
        await db.close()


async def _get(path, aid):
    db = await _open(path)
    try:
        return await alarms.get_alarm(db, aid)
    finally:
        await db.close()


async def _events(path, aid):
    db = await _open(path)
    try:
        async with db.execute("SELECT event FROM alarm_events WHERE alarm_id = ? ORDER BY ts, rowid", (aid,)) as cur:
            return [r[0] for r in await cur.fetchall()]
    finally:
        await db.close()


# ── migration ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_migration_is_linear_rerunnable_and_reversible(tmp_path):
    mod = _mig()
    revs, downs = set(), set()
    for f in (ROOT / "alembic/versions").glob("*.py"):
        src = f.read_text()
        r = re.search(r'^revision = "(\w+)"', src, re.M)
        d = re.search(r'^down_revision = "(\w+)"', src, re.M)
        if r:
            revs.add(r.group(1))
        if d:
            downs.add(d.group(1))
    assert mod.revision == "0063" and mod.down_revision in revs
    assert len(revs - downs) == 1, f"more than one alembic head: {sorted(revs - downs)}"
    p = str(tmp_path / "m.db")
    _migrate(p)
    _migrate(p)  # a rerun is a no-op
    con = sa.create_engine(f"sqlite:///{p}").connect()
    cols = {r[1] for r in con.exec_driver_sql("PRAGMA table_info(alarms)")}
    assert set(alarms.COLS) == cols
    assert {r[1] for r in con.exec_driver_sql("PRAGMA table_info(alarm_events)")} == {"id", "alarm_id", "ts", "event", "detail"}
    con.close()
    _migrate(p, "downgrade")
    con = sa.create_engine(f"sqlite:///{p}").connect()
    assert not list(con.exec_driver_sql("SELECT name FROM sqlite_master WHERE name IN ('alarms','alarm_events')"))
    con.close()


# ── schedule maths ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_next_occurrence_rules():
    assert alarms.next_occurrence("07:00", "daily", None, NIGHT) == SEVEN
    mon_7 = ts(2026, 11, 2, 7, 0)
    assert alarms.next_occurrence("07:00", "daily", None, mon_7 - 60) == mon_7  # still ahead today
    assert alarms.next_occurrence("07:00", "daily", None, mon_7) == SEVEN  # strictly AFTER: today's is spent
    fri = ts(2026, 11, 6, 7, 0)
    sat_night = ts(2026, 11, 7, 22, 0)
    assert alarms.next_occurrence("07:00", "weekdays", None, ts(2026, 11, 6, 8, 0)) == ts(2026, 11, 9, 7, 0)  # Fri -> Mon
    assert alarms.next_occurrence("07:00", "weekends", None, ts(2026, 11, 2, 8, 0)) == ts(2026, 11, 7, 7, 0)
    assert alarms.next_occurrence("07:00", "weekdays", None, fri - 1) == fri
    assert alarms.next_occurrence("09:30", "days:1,3", None, sat_night) == ts(2026, 11, 9, 9, 30)
    assert alarms.next_occurrence("07:00", "once", "2026-11-03", NIGHT) == SEVEN
    assert alarms.next_occurrence("07:00", "once", "2026-11-01", NIGHT) is None


def test_a_nonexistent_dst_time_still_schedules(monkeypatch):
    """02:30 does not exist on the US spring-forward day; the alarm must land somewhere sane, never crash or skip the day."""
    monkeypatch.setenv("ZOE_TIMEZONE", "America/New_York")
    ny = ZoneInfo("America/New_York")
    t = alarms.next_occurrence("02:30", "daily", None, ts(2026, 3, 8, 0, 0, tz=ny))
    assert t is not None and 0 < t - ts(2026, 3, 8, 0, 0, tz=ny) < 6 * 3600


def test_repeat_and_time_validation():
    assert alarms.normalise_repeat("days: 5,1") == "days:1,5"
    assert alarms.normalise_repeat("days:1,2,3,4,5,6,7") == "daily"
    assert alarms.normalise_time("7:05") == "07:05"
    for bad in ("25:00", "7", "", "7:5", "noon"):
        with pytest.raises(alarms.AlarmError):
            alarms.normalise_time(bad)
    for bad in ("hourly", "days:0", "days:8", "days:"):
        with pytest.raises(alarms.AlarmError):
            alarms.normalise_repeat(bad)


def test_one_off_takes_the_next_such_time_and_refuses_the_past(dbfile):
    a = run(_mk(dbfile, time_hhmm="07:00"))  # 22:00 Monday -> Tuesday
    assert a["once_date"] == "2026-11-03" and a["next_fire_at"] == SEVEN
    b = run(_mk(dbfile, time_hhmm="23:00"))  # still ahead today
    assert b["once_date"] == "2026-11-02"
    with pytest.raises(alarms.AlarmError, match="passed"):
        run(_mk(dbfile, time_hhmm="07:00", once_date="2026-11-01"))


def test_input_is_validated(dbfile):
    for kw in ({"sound_id": "nope"}, {"sound_kind": "video"}, {"sound_kind": "music", "sound_id": ""}, {"snooze_minutes": 0},
               {"snooze_minutes": 99}, {"ramp_seconds": -1}):
        with pytest.raises(alarms.AlarmError):
            run(_mk(dbfile, **kw))
    a = run(_mk(dbfile, label="  Gym   time  ", sound_kind="music", sound_id="playlist:Morning", sound_name="Morning"))
    assert a["label"] == "Gym time" and a["snooze_minutes"] == 9 and a["ramp_seconds"] == 30 and a["sound_name"] == "Morning"


# ── ring lifecycle ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_rings_at_the_time_not_before_and_a_one_off_switches_itself_off(dbfile):
    a = run(_mk(dbfile))
    assert run(_tick(dbfile, SEVEN - 1)) == []
    out = run(_tick(dbfile, SEVEN))
    assert [e for e, _ in out] == ["rang"]
    row = run(_get(dbfile, a["id"]))
    assert row["state"] == "ringing" and row["enabled"] == 0 and row["next_fire_at"] is None and row["last_fired_at"] == SEVEN


def test_a_repeating_alarm_advances_to_its_next_day_as_it_rings(dbfile):
    a = run(_mk(dbfile, repeat="daily"))
    run(_tick(dbfile, SEVEN))
    row = run(_get(dbfile, a["id"]))
    assert row["state"] == "ringing" and row["enabled"] == 1 and row["next_fire_at"] == SEVEN + 86400


def test_two_engines_ring_an_occurrence_once(dbfile):
    a = run(_mk(dbfile, repeat="daily"))

    async def both():
        c1, c2 = await _open(dbfile), await _open(dbfile)
        try:
            return await asyncio.gather(alarms.tick(c1, SEVEN + 1), alarms.tick(c2, SEVEN + 1))
        finally:
            await c1.close()
            await c2.close()

    r1, r2 = run(both())
    assert len(r1) + len(r2) == 1
    assert run(_events(dbfile, a["id"])).count("rang") == 1
    assert run(_tick(dbfile, SEVEN + 2)) == []  # and a later tick of the same occurrence is a no-op


def test_a_restart_in_the_middle_of_a_ring_keeps_ringing(dbfile):
    a = run(_mk(dbfile))
    run(_tick(dbfile, SEVEN))

    async def after_restart():
        db = await _open(dbfile)  # a brand-new connection = a new process
        try:
            return [x["id"] for x in await alarms.ringing(db)], await alarms.tick(db, SEVEN + 30)
        finally:
            await db.close()

    ids, out = run(after_restart())
    assert ids == [a["id"]] and out == []  # still ringing, not rung a second time


def test_an_occurrence_missed_while_down_is_skipped_not_rung_late(dbfile):
    a = run(_mk(dbfile, repeat="daily"))
    out = run(_tick(dbfile, SEVEN + alarms.MISSED_GRACE_S + 60))
    assert [e for e, _ in out] == ["missed"]
    row = run(_get(dbfile, a["id"]))
    assert row["state"] == "idle" and row["next_fire_at"] == SEVEN + 86400
    assert run(_events(dbfile, a["id"]))[-1] == "missed"


def test_a_slightly_late_find_still_rings_and_says_so(dbfile):
    a = run(_mk(dbfile))
    assert [e for e, _ in run(_tick(dbfile, SEVEN + 120))] == ["rang"]
    assert run(_events(dbfile, a["id"]))[-1] == "rang"


def test_a_missed_one_off_just_ends(dbfile):
    a = run(_mk(dbfile))
    run(_tick(dbfile, SEVEN + 3600))
    row = run(_get(dbfile, a["id"]))
    assert row["enabled"] == 0 and row["state"] == "idle" and row["next_fire_at"] is None


def test_an_unanswered_ring_stops_by_itself_and_logs_it(dbfile):
    a = run(_mk(dbfile, repeat="daily"))
    run(_tick(dbfile, SEVEN))
    assert run(_tick(dbfile, SEVEN + alarms.RING_MAX_S - 1)) == []
    out = run(_tick(dbfile, SEVEN + alarms.RING_MAX_S))
    assert [e for e, _ in out] == ["auto_stopped"]
    row = run(_get(dbfile, a["id"]))
    assert row["state"] == "idle" and row["next_fire_at"] == SEVEN + 86400
    assert run(_events(dbfile, a["id"]))[-2:] == ["rang", "auto_stopped"]


def test_snooze_rings_again_after_the_snooze_length(dbfile):
    a = run(_mk(dbfile, repeat="daily", snooze_minutes=5))
    run(_tick(dbfile, SEVEN))

    async def snz(now):
        db = await _open(dbfile)
        try:
            return await alarms.snooze(db, await alarms.get_alarm(db, a["id"]), now=now)
        finally:
            await db.close()

    s = run(snz(SEVEN + 10))
    assert s["state"] == "snoozed" and s["snooze_until"] == SEVEN + 10 + 300 and s["snooze_count"] == 1
    assert run(_tick(dbfile, SEVEN + 309)) == []
    assert [e for e, _ in run(_tick(dbfile, SEVEN + 310))] == ["rang"]
    row = run(_get(dbfile, a["id"]))
    assert row["state"] == "ringing" and row["snooze_count"] == 1


def test_snooze_needs_a_ringing_alarm_and_has_a_budget(dbfile):
    a = run(_mk(dbfile, repeat="daily"))

    async def snz(now):
        db = await _open(dbfile)
        try:
            return await alarms.snooze(db, await alarms.get_alarm(db, a["id"]), now=now)
        finally:
            await db.close()

    with pytest.raises(alarms.AlarmError) as e:
        run(snz(SEVEN))  # idle
    assert e.value.status == 409
    run(_tick(dbfile, SEVEN))
    t = SEVEN
    for _ in range(alarms.MAX_SNOOZES):
        t += 5
        run(snz(t))
        t += 9 * 60 + 1
        assert run(_tick(dbfile, t))
    with pytest.raises(alarms.AlarmError, match="as much"):
        run(snz(t + 1))


def test_dismiss_ends_the_ring_and_keeps_the_schedule(dbfile):
    a = run(_mk(dbfile, repeat="weekdays"))
    run(_tick(dbfile, SEVEN))

    async def off():
        db = await _open(dbfile)
        try:
            row = await alarms.dismiss(db, await alarms.get_alarm(db, a["id"]), now=SEVEN + 5)
            return row, await alarms.dismiss(db, row, now=SEVEN + 6)  # idempotent
        finally:
            await db.close()

    row, again = run(off())
    assert row["state"] == "idle" and row["enabled"] == 1 and row["next_fire_at"] == ts(2026, 11, 4, 7, 0)
    assert again["state"] == "idle" and run(_events(dbfile, a["id"])).count("dismissed") == 1


def test_editing_or_disabling_a_ringing_alarm_silences_it(dbfile):
    a = run(_mk(dbfile, repeat="daily"))
    run(_tick(dbfile, SEVEN))

    async def edit(**ch):
        db = await _open(dbfile)
        try:
            return await alarms.update_alarm(db, await alarms.get_alarm(db, a["id"]), ch, now=SEVEN + 3)
        finally:
            await db.close()

    off = run(edit(enabled=False))
    assert off["state"] == "idle" and off["enabled"] == 0 and off["next_fire_at"] is None
    on = run(edit(enabled=True, time_hhmm="06:15"))
    assert on["enabled"] == 1 and on["next_fire_at"] == ts(2026, 11, 4, 6, 15)
    assert run(_tick(dbfile, SEVEN + 4)) == []


def test_per_owner_cap(dbfile, monkeypatch):
    monkeypatch.setattr(alarms, "MAX_ALARMS_PER_OWNER", 2)
    run(_mk(dbfile))
    run(_mk(dbfile))
    with pytest.raises(alarms.AlarmError) as e:
        run(_mk(dbfile))
    assert e.value.status == 409
    run(_mk(dbfile, owner=BOB))  # per owner, not global


# ── visibility ───────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _al(owner):
    return {"user_id": owner}


def test_the_panel_can_silence_any_ringing_alarm_but_edit_only_household():
    ann, bob, panel = alarms.Viewer(ANN), alarms.Viewer(BOB), alarms.Viewer("", panel=True)
    mine, house = _al(ANN), _al(alarms.HOUSEHOLD)
    assert alarms.can_see(mine, ann) and not alarms.can_see(mine, bob) and alarms.can_see(mine, panel)
    assert alarms.can_edit(mine, ann) and not alarms.can_edit(mine, bob) and not alarms.can_edit(mine, panel)
    assert alarms.can_edit(house, ann) and alarms.can_edit(house, bob) and alarms.can_edit(house, panel)
    assert alarms.can_silence(mine, panel) and not alarms.can_silence(mine, bob) and alarms.can_silence(house, bob)
    assert panel.owner_id == alarms.HOUSEHOLD and ann.owner_id == ANN


def test_personal_alarms_are_masked_on_the_panel(dbfile):
    a = run(_mk(dbfile, label="Private thing", sound_kind="music", sound_id="search:x", sound_name="X"))
    panel = alarms.Viewer("", panel=True)
    shown = alarms.present(a, panel, NIGHT)
    assert shown["masked"] and shown["label"] == "" and shown["sound_name"] == "" and shown["time_hhmm"] == "07:00"
    assert not shown["editable"]
    own = alarms.present(a, alarms.Viewer(ANN), NIGHT)
    assert own["label"] == "Private thing" and own["editable"] and not own.get("masked")


# ── HTTP ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def box(dbfile, monkeypatch):
    monkeypatch.setenv("ZOE_ALARMS", "on")
    from routers.alarms import router

    app = FastAPI()
    app.include_router(router)
    state = {"user": {"user_id": ANN, "role": "user"}}

    async def db_dep():
        conn = await aiosqlite.connect(dbfile)
        try:
            yield conn
        finally:
            await conn.close()

    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[require_member_or_panel] = lambda: state["user"]
    state["client"], state["path"] = TestClient(app), dbfile
    return state


def as_member(box, uid):
    box["user"] = {"user_id": uid, "role": "user"}


def as_panel(box):
    box["user"] = {"user_id": "guest", "role": "guest", "panel_id": "zoe-touch"}


def test_flag_off_is_404_everywhere(box, monkeypatch):
    monkeypatch.setenv("ZOE_ALARMS", "off")
    c = box["client"]
    assert c.get("/api/alarms").status_code == 404
    assert c.post("/api/alarms", json={"time": "07:00"}).status_code == 404
    assert c.get("/api/alarms/ringing").status_code == 404
    assert c.post("/api/alarms/x/snooze").status_code == 404


def test_member_crud(box):
    c = box["client"]
    r = c.post("/api/alarms", json={"time_hhmm": "06:30", "repeat": "weekdays", "label": "Gym", "snooze_minutes": 5})
    assert r.status_code == 200, r.text
    al = r.json()["alarm"]
    assert al["mine"] and al["editable"] and not al["household"] and al["repeat_phrase"] == "on weekdays"
    assert c.get("/api/alarms").json()["alarms"][0]["id"] == al["id"]
    r = c.patch(f"/api/alarms/{al['id']}", json={"time_hhmm": "06:45", "enabled": False})
    assert r.json()["alarm"]["time_hhmm"] == "06:45" and r.json()["alarm"]["enabled"] is False
    assert c.delete(f"/api/alarms/{al['id']}").json() == {"ok": True}
    assert c.get("/api/alarms").json()["alarms"] == []


def test_bad_input_is_a_400_with_a_speakable_reason(box):
    c = box["client"]
    for body in ({"time": "25:00"}, {"time": "07:00", "repeat": "hourly"}, {"time": "07:00", "sound_id": "zzz"},
                 {"time": "07:00", "once_date": "2020-01-01"}, {}):
        r = c.post("/api/alarms", json=body)
        assert r.status_code == 400 and r.json()["detail"], body


def test_another_member_cannot_see_or_touch_a_personal_alarm(box):
    c = box["client"]
    aid = c.post("/api/alarms", json={"time": "07:00"}).json()["alarm"]["id"]
    as_member(box, BOB)
    assert c.get("/api/alarms").json()["alarms"] == []
    for call in (lambda: c.patch(f"/api/alarms/{aid}", json={"label": "x"}), lambda: c.delete(f"/api/alarms/{aid}"),
                 lambda: c.post(f"/api/alarms/{aid}/snooze"), lambda: c.post(f"/api/alarms/{aid}/dismiss")):
        assert call().status_code == 404


def test_household_alarms_are_shared_and_the_panel_creates_them(box):
    c = box["client"]
    as_panel(box)
    al = c.post("/api/alarms", json={"time": "07:00", "label": "School run"}).json()["alarm"]
    assert al["household"] and al["editable"]
    as_member(box, BOB)
    seen = c.get("/api/alarms").json()["alarms"]
    assert [x["id"] for x in seen] == [al["id"]]
    assert c.patch(f"/api/alarms/{al['id']}", json={"label": "Bus"}).status_code == 200


def test_the_panel_sees_personal_alarms_masked_and_cannot_edit_them(box):
    c = box["client"]
    aid = c.post("/api/alarms", json={"time": "07:00", "label": "Private"}).json()["alarm"]["id"]
    as_panel(box)
    shown = c.get("/api/alarms").json()["alarms"]
    assert len(shown) == 1 and shown[0]["masked"] and shown[0]["label"] == ""
    assert c.patch(f"/api/alarms/{aid}", json={"label": "x"}).status_code == 403
    assert c.delete(f"/api/alarms/{aid}").status_code == 403


def test_ring_snooze_and_dismiss_over_http(box):
    c = box["client"]
    aid = c.post("/api/alarms", json={"time": "07:00", "repeat": "daily", "label": "Wake", "snooze_minutes": 7}).json()["alarm"]["id"]
    assert c.get("/api/alarms/ringing").json()["ringing"] == []
    run(_tick(box["path"], SEVEN))  # the engine rings it
    as_panel(box)
    rg = c.get("/api/alarms/ringing").json()["ringing"]
    assert len(rg) == 1 and rg[0]["id"] == aid and rg[0]["label"] == "Wake" and rg[0]["can_snooze"]  # unmasked: the room hears it
    r = c.post(f"/api/alarms/{aid}/snooze")
    assert r.status_code == 200 and r.json()["alarm"]["state"] == "snoozed"
    assert c.get("/api/alarms/ringing").json()["ringing"] == []
    assert c.post(f"/api/alarms/{aid}/snooze").status_code == 409  # not ringing any more
    run(_tick(box["path"], SEVEN + 7 * 60 + 1))
    assert c.post(f"/api/alarms/{aid}/dismiss").json()["alarm"]["state"] == "idle"
    assert c.get("/api/alarms/ringing").json()["ringing"] == []


def test_a_member_does_not_hear_another_members_ring_in_their_feed(box):
    c = box["client"]
    c.post("/api/alarms", json={"time": "07:00"})
    run(_tick(box["path"], SEVEN))
    as_member(box, BOB)
    assert c.get("/api/alarms/ringing").json()["ringing"] == []


# ── voice ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text,hhmm,repeat", [
    ("set an alarm for 7", "07:00", "once"),
    ("Set an alarm for 7 am.", "07:00", "once"),
    ("set an alarm for 6:30 pm", "18:30", "once"),
    ("wake me up at seven thirty", "07:30", "once"),
    ("wake me at 7", "07:00", "once"),
    ("set my alarm for half past six every weekday", "06:30", "weekdays"),
    ("set an alarm for quarter to seven", "06:45", "once"),
    ("set an alarm for 7 on weekends", "07:00", "weekends"),
    ("set an alarm for 8 every monday and wednesday", "08:00", "days:1,3"),
    ("alarm for 5:45 every day", "05:45", "daily"),
    ("set an alarm for 730", "07:30", "once"),
    ("set an alarm for 14:20", "14:20", "once"),
    ("set an alarm for noon", "12:00", "once"),
])
def test_voice_set(text, hhmm, repeat):
    cmd = alarm_voice.parse(text, NIGHT)
    assert cmd is not None and cmd.action == "set", text
    assert (cmd.time_hhmm, cmd.repeat) == (hhmm, repeat)


def test_no_meridiem_means_the_next_time_on_the_clock_and_wake_means_morning():
    at_8am = ts(2026, 11, 2, 8, 0)
    assert alarm_voice.parse("set an alarm for 7", at_8am).time_hhmm == "19:00"  # 7 pm is the next 7 o'clock
    assert alarm_voice.parse("set an alarm for 7", NIGHT).time_hhmm == "07:00"  # 22:00: the next 7 is tomorrow morning
    assert alarm_voice.parse("wake me at 7", at_8am).time_hhmm == "07:00"  # a wake request is the morning, always
    tom = alarm_voice.parse("set an alarm for 6 tomorrow", at_8am)
    assert tom.time_hhmm == "06:00" and tom.once_date.isoformat() == "2026-11-03"


def test_voice_label_and_sound():
    c = alarm_voice.parse("set an alarm for 6:30 called gym", NIGHT)
    assert c.label == "Gym" and c.sound_kind == "tone"
    m = alarm_voice.parse("wake me at 7 with music", NIGHT)
    assert (m.sound_kind, m.sound_id) == ("music", "last")
    j = alarm_voice.parse("wake me up at 7 with some jazz", NIGHT)
    assert j.sound_kind == "music" and j.sound_id == "search:jazz"
    t = alarm_voice.parse("set an alarm for 7 with the bell tone", NIGHT)
    assert (t.sound_kind, t.sound_id) == ("tone", "bell")


@pytest.mark.parametrize("text,action", [
    ("what alarms do I have", "list"), ("when is my next alarm", "list"), ("show my alarms", "list"),
    ("cancel my alarm", "cancel"), ("delete the gym alarm", "cancel"), ("cancel all my alarms", "cancel"),
    ("turn off the alarm", "off"), ("stop the alarm", "off"), ("turn off my 7 am alarm", "off"),
    ("turn my 7 am alarm back on", "on"), ("snooze", "snooze"), ("snooze the alarm", "snooze"),
    ("snooze for ten minutes", "snooze"), ("five more minutes", "snooze"),
])
def test_voice_other_actions(text, action):
    cmd = alarm_voice.parse(text, NIGHT)
    assert cmd is not None and cmd.action == action, text


def test_voice_snooze_minutes_and_targets():
    assert alarm_voice.parse("snooze for ten minutes", NIGHT).snooze_minutes == 10
    assert alarm_voice.parse("five more minutes", NIGHT).snooze_minutes == 5
    assert alarm_voice.parse("cancel my 7 am alarm", NIGHT).target_hhmm == "07:00"
    assert alarm_voice.parse("delete the gym alarm", NIGHT).target_label == "gym"
    assert alarm_voice.parse("cancel all my alarms", NIGHT).all_alarms


@pytest.mark.parametrize("text", [
    "should I set an alarm for 7", "why did you cancel my alarm", "the smoke alarm is beeping", "set a timer for 5 minutes",
    "what's the alarm code", "set the alarm system", "I set an alarm yesterday", "play some music", "tell me a joke",
    "my alarm clock is broken", "don't cancel the alarm", "wake up", "can I cancel the alarm",
])
def test_voice_refuses_questions_and_other_alarms(text):
    cmd = alarm_voice.parse(text, NIGHT)
    assert cmd is None or cmd.action == "list" and "code" not in text, (text, cmd)
    if cmd is not None:  # the only tolerated hit is a read-only list
        assert cmd.action == "list"


def test_skybridge_classifies_alarms_only_when_flagged(monkeypatch):
    import skybridge_service as sb

    monkeypatch.setenv("ZOE_ALARMS", "off")
    off = sb.classify_skybridge_intent("set an alarm for 7")
    assert off is None or off.domain != "alarm"
    monkeypatch.setenv("ZOE_ALARMS", "on")
    on = sb.classify_skybridge_intent("set an alarm for 7")
    assert on is not None and on.domain == "alarm" and on.action == "set" and on.alarm.time_hhmm
    t = sb.classify_skybridge_intent("set a timer for 5 minutes")
    assert t.domain == "timer"  # the timer grammar is untouched


@pytest.fixture
def voice(dbfile, monkeypatch):
    import contextlib

    import panel_principal

    @contextlib.asynccontextmanager
    async def ctx():
        db = await aiosqlite.connect(dbfile)
        try:
            yield db
        finally:
            await db.close()

    monkeypatch.setattr(alarm_voice, "_db", ctx)
    monkeypatch.setattr(panel_principal, "in_panel_turn", lambda: True)
    return dbfile


def say(text, uid=ANN):
    cmd = alarm_voice.parse(text, NIGHT)
    assert cmd is not None, text
    return run(alarm_voice.resolve(cmd, uid))


def test_voice_end_to_end_set_list_cancel(voice):
    r = say("set an alarm for 6:30 every weekday")
    assert r["handled"] and "6:30 am" in r["spoken_summary"] and "weekdays" in r["spoken_summary"]
    assert "one alarm" in say("what alarms do I have")["spoken_summary"]
    say("set an alarm for 8")
    assert "2 alarms" in say("what alarms do I have")["spoken_summary"]
    assert "Which one" in say("cancel my alarm")["spoken_summary"]
    assert "Cancelled your 6:30 am" in say("cancel my 6:30 am alarm")["spoken_summary"]
    assert "Cancelled 1 alarm" in say("cancel all my alarms")["spoken_summary"]
    assert "no alarms" in say("what alarms do I have")["spoken_summary"]


def test_voice_snooze_and_off_while_ringing(voice):
    say("set an alarm for 7")
    run(_tick(voice, SEVEN))
    assert "Snoozed for 9 minutes" in say("snooze")["spoken_summary"]
    assert "Snoozed for 5 minutes" not in say("snooze the alarm")["spoken_summary"]  # already snoozed: nothing is ringing
    run(_tick(voice, SEVEN + 9 * 60 + 1))
    assert "Alarm off" in say("turn off the alarm")["spoken_summary"]
    assert "No alarm is ringing" in say("snooze")["spoken_summary"]


def test_voice_off_and_on_a_repeating_alarm(voice):
    say("set an alarm for 6:30 every day")
    assert "Turned off" in say("turn off my alarm")["spoken_summary"]
    assert "no alarms set" in say("what alarms do I have")["spoken_summary"]
    assert "back on" in say("turn my 6:30 am alarm back on")["spoken_summary"]


def test_a_guest_at_the_panel_gets_household_alarms_and_never_sees_personal_ones(voice):
    say("set an alarm for 7", ANN)  # Ann's personal alarm
    say("set an alarm for 8", "guest")  # the panel (guest): household
    assert "one alarm" in say("what alarms do I have", "guest")["spoken_summary"]  # only the household one
    assert "2 alarms" in say("what alarms do I have", ANN)["spoken_summary"]  # Ann: hers + the household one
    assert "no alarms to change" not in say("cancel my 8 am alarm", "guest")["spoken_summary"]
    gone = say("cancel all my alarms", "guest")["spoken_summary"]
    assert "no alarms" in gone  # the household one is gone already; Ann's is out of the panel's reach
    assert "one alarm" in say("what alarms do I have", ANN)["spoken_summary"]


def test_a_guest_off_the_panel_is_asked_to_sign_in(voice, monkeypatch):
    import panel_principal

    monkeypatch.setattr(panel_principal, "in_panel_turn", lambda: False)
    assert "Sign in" in say("set an alarm for 7", "guest")["spoken_summary"]
    assert run(_count(voice)) == 0


async def _count(path):
    db = await _open(path)
    try:
        async with db.execute("SELECT COUNT(*) FROM alarms") as cur:
            return (await cur.fetchone())[0]
    finally:
        await db.close()
