"""Alarms and timers go to the Zoe OS kernel (``ZOE_OS_ALARMS=kernel``; docs/knowledge/zoe-os-alarms-door.md).

* any other flag value: NO kernel call, no socket, no thread, no extra statement: every one of the 44 entry-point scenarios
  gives the record captured from ``main`` before the door existed;
* setting, editing and deleting an ALARM asks first: allowed = made and reported; refused = ``AlarmRefused``, nothing written;
  kernel down or slow = made, one ``not_checked`` line;
* SILENCING A RINGING ALARM OR TIMER IS NEVER HELD UP: snooze and dismiss, and every timer change, are made first and told to
  the kernel afterwards. A kernel that is down, slow or says no changes nothing about them and costs them no time;
* only ids, stamps and closed codes cross the socket and reach the log: never a label, a time or a sound;
* the erase stamps an ask carries are those of the moment its actor was made, before the alarm (and so its owner) was read;
* structure: every caller says who is acting, and makes that actor before anything it awaits.

A fake kernel on a real Unix socket (``test_kernel_door.AreaKernel``); alarms on a sqlite file migrated with the real migrations
(``alarms_timers_scenarios``). Each test names the control that was run against it. Invented ids and words only.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path

import pytest

import alarms
import alarms_timers_scenarios as scenarios
import kernel_door
import os_shadow
import user_erase_gate
from alarms_timers_scenarios import ANN, BOB, NIGHT, PANEL, SEVEN
from test_kernel_door import AreaKernel
from test_os_shadow import SVC

pytestmark = pytest.mark.ci_safe

ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "alarms_timers_as_on_main.json").read_text("utf-8"))
WORD = "womble"


@pytest.fixture
def door(monkeypatch):
    k = AreaKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [ANN, BOB]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_ALARMS", "kernel")
    for other in ("ZOE_OS_LISTS", "ZOE_OS_REMINDERS"):
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    yield k
    scenarios._restore()
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


def _wait(door, op, n, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(door.ops(op)) < n:
        time.sleep(0.01)
    return door.ops(op)


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()]


def _world(log=None):
    return scenarios._AlarmWorld(SVC, [] if log is None else log)


def _member(uid):
    return {"user_id": uid, "role": "user"}


async def _create(w, user=None, **payload):
    async with w.db() as db:
        return (await w.rest.create_alarm({"time_hhmm": "07:00", **payload}, None, user=user or _member(ANN), db=db))["alarm"]


async def _ring(w, at=SEVEN):
    w.clock["now"] = at
    async with w.db() as db:
        await w.alarms.tick(db, at)


# ---- flag off: main's behaviour, exactly ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "off", "shadow", "on", "KERNELS"])
def test_any_other_flag_value_is_main_exactly_for_every_entry_point(door, monkeypatch, value):
    """All 44 scenarios (every statement with its values, hook, receipt, timer snapshot, result, the store afterwards) equal the
    record captured from main, with every way into the kernel wired to fail the test. Control run: with the flag check removed
    from ``alarms._gate`` (always ask) the ``never`` stub raises in the first create."""
    def never(*a, **k):
        raise AssertionError("the kernel was reached with the flag off")

    if value is None:
        monkeypatch.delenv("ZOE_OS_ALARMS")
    else:
        monkeypatch.setenv("ZOE_OS_ALARMS", value)
    for name in ("ask_door", "record_door", "report_door", "door_unchecked", "stamp_snapshot"):
        monkeypatch.setattr(os_shadow, name, never)
    assert scenarios.run_all() == ON_MAIN
    assert door.calls == [] and door.connections == 0 and os_shadow._door_worker is None      # no call, no socket, no thread


def test_with_the_door_on_and_allowing_every_entry_point_still_issues_mains_statements(door):
    """The door adds no statement of its own even when it is on (every caller hands it the alarm it already holds), and nothing
    a person hears or the store holds changes when the kernel allows. Control run: with the delete door reading the row itself
    although the caller passed it, ``rest_delete`` gains a statement and this fails."""
    assert scenarios.run_all() == ON_MAIN
    wanted = {"alarm.create", "alarm.edit", "alarm.delete", "alarm.snooze", "alarm.stop", "timer.create", "timer.edit", "timer.cancel",
              "timer.stop"}
    end = time.monotonic() + 8                                  # the told-afterwards verbs reach the kernel on the door's own thread
    while time.monotonic() < end and not wanted <= {a["verb"] for a in door.ops("door_ask")}:
        time.sleep(0.02)
    assert wanted <= {a["verb"] for a in door.ops("door_ask")}


# ---- asked first: create, edit, delete ---------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_change_asks_with_ids_only_and_is_reported_after_the_write(door, caplog):
    """Control run: with ``_gate`` dropped from ``update_alarm`` there is no ``alarm.edit`` ask and this fails."""
    w = _world()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        a = await _create(w, label=WORD, sound_id="chime")
        async with w.db() as db:
            await w.rest.update_alarm(a["id"], {"label": WORD + " two", "time_hhmm": "06:45"}, None, user=_member(ANN), db=db)
            await w.rest.delete_alarm(a["id"], None, user=_member(ANN), db=db)
    asks = _wait(door, "door_ask", 3)
    assert [x["verb"] for x in asks] == ["alarm.create", "alarm.edit", "alarm.delete"]
    for x in asks:
        assert set(x) == {"area", "verb", "person", "target", "owner", "visibility", "stamps"}
        assert (x["area"], x["person"], x["owner"], x["visibility"], x["target"]) == ("alarms", ANN, ANN, "private", a["id"])
        assert x["stamps"] == {ANN: os_shadow.erase_stamp(ANN)}
    assert [r["outcome"] for r in _wait(door, "door_report", 3)] == ["done"] * 3
    sent = json.dumps(door.calls)
    assert all(word not in sent for word in (WORD, "07:00", "06:45", "chime"))               # never the label, a time or a sound
    lines = _lines(caplog)
    assert len(lines) == 3 and all("area=alarms" in line and "decision=allow reason=-" in line for line in lines)
    assert all(word not in "".join(lines) for word in (WORD, ANN, a["id"]))


@pytest.mark.asyncio
async def test_the_houses_alarm_names_no_owner_and_the_panel_is_never_named(door):
    """A household alarm is kept under a marker that is not a person: the ask says ``household`` and tells no owner. Control run:
    with the marker sent as the owner, the kernel would refuse every household alarm as ``unknown_person``; here ``owner`` appears."""
    w = _world()
    a = await _create(w, user=PANEL)
    async with w.db() as db:
        await w.rest.update_alarm(a["id"], {"label": WORD}, None, user=_member(BOB), db=db)
    first, second = _wait(door, "door_ask", 2)
    assert (first.get("via"), first["person"], first["visibility"]) == ("panel", "panel", "household") and "owner" not in first
    assert "stamps" not in first and "panel-zed" not in json.dumps(door.calls) and alarms.HOUSEHOLD not in json.dumps(door.calls)
    assert (second["person"], second["visibility"], set(second["stamps"])) == (BOB, "household", {BOB}) and "owner" not in second


@pytest.mark.parametrize("reason", ["not_owner", "unknown_person"])
@pytest.mark.asyncio
async def test_a_kernel_refusal_is_final_nothing_is_written_and_each_caller_says_so(door, reason):
    """REST answers 403 through the one app handler; a spoken turn says the sentence. Control run: with ``AlarmRefused`` not
    raised on a refusal (the decision ignored) the alarm is created and this fails."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from database import get_db
    from panel_principal import require_member_or_panel

    w = _world()
    door.door = f"refuse:{reason}"
    line = alarms.AlarmRefused(reason).line()
    with pytest.raises(alarms.AlarmRefused) as exc:
        await _create(w, label=WORD)
    assert exc.value.reason == reason and isinstance(exc.value, kernel_door.Refused) and WORD not in line and ANN not in line
    assert (await w.voice.resolve(w.voice.parse("set an alarm for 7 am", NIGHT), ANN))["spoken_summary"] == line
    assert (await w.rows())["alarms"] == [] and door.ops("door_report") == []
    door.door = "allow"
    a = await _create(w, repeat="daily")
    door.door = f"refuse:{reason}"
    async with w.db() as db:
        for call in (lambda: w.rest.update_alarm(a["id"], {"label": WORD}, None, user=_member(ANN), db=db),
                     lambda: w.rest.delete_alarm(a["id"], None, user=_member(ANN), db=db),
                     lambda: w.rest.set_override(a["id"], {"date": "2026-11-05", "skip": True}, None, user=_member(ANN), db=db)):
            with pytest.raises(alarms.AlarmRefused):
                await call()
    after = await w.rows()
    assert [r[0] for r in after["alarms"]] == [a["id"]] and after["alarms"][0][2] == "" and after["alarm_overrides"] == []

    app = FastAPI()
    app.include_router(w.rest.router)
    app.add_exception_handler(kernel_door.Refused, kernel_door.refused_response)
    app.dependency_overrides[require_member_or_panel] = lambda: _member(ANN)

    async def _db():
        async with w.db() as db:
            yield db

    app.dependency_overrides[get_db] = _db
    with TestClient(app) as client:
        out = client.post("/api/alarms", json={"time_hhmm": "08:00"})
        assert (out.status_code, out.json()) == (403, {"detail": line})


@pytest.mark.parametrize("how,reason", [("close", "unavailable"), ("old_kernel", "protocol"), ("slow", "timeout")])
@pytest.mark.asyncio
async def test_a_kernel_that_cannot_answer_lets_a_change_proceed_not_checked(door, caplog, how, reason):
    """Control run: with ``not_checked`` treated as a refusal in ``_gate`` all three raise."""
    if how == "slow":
        door.delay = 0.5
    else:
        door.door = how
    w = _world()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        a = await _create(w)
    assert a["id"] and len((await w.rows())["alarms"]) == 1
    assert "decision=not_checked" in _lines(caplog)[0] and "area=alarms verb=alarm.create" in _lines(caplog)[0]


@pytest.mark.asyncio
async def test_a_write_that_loses_its_race_is_reported_failed(door):
    """Control run: with the report made before the version check in ``update_alarm`` this sees ``done``."""
    w = _world()
    a = await _create(w)
    async with w.db() as db:
        stale = await alarms.get_alarm(db, a["id"])
        await alarms.update_alarm(db, stale, {"enabled": False}, now=NIGHT + 5, actor=alarms.Viewer(user_id=ANN))
        with pytest.raises(alarms.AlarmError):
            await alarms.update_alarm(db, stale, {"label": WORD}, now=NIGHT + 6, actor=alarms.Viewer(user_id=ANN))
    assert [r["outcome"] for r in _wait(door, "door_report", 3)] == ["done", "done", "failed"]


# ---- silencing is never held up -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kernel", ["allow", "refuse:not_owner", "close", "slow", "gone"])
@pytest.mark.parametrize("how", ["dismiss", "snooze"])
@pytest.mark.asyncio
async def test_a_ringing_alarm_is_silenced_at_once_whatever_the_kernel_does(door, caplog, monkeypatch, kernel, how):
    """The safety property: the write is made first and the kernel is told afterwards. Down, slow (0.8 s here), refusing or not
    there at all, the alarm is quiet when the call returns, and the call did not wait. Control run: with ``snooze`` / ``dismiss``
    asking FIRST (``await _gate`` before the write) the refusing kernel leaves the alarm ringing and the slow one makes the call
    take the whole ask budget; both fail here."""
    monkeypatch.setattr(os_shadow, "ASK_DEADLINE_S", 1.0)       # an ask-first silence would wait this long for the slow kernel
    w = _world()
    a = await _create(w)
    await _ring(w)
    if kernel == "slow":
        door.delay = 0.8
    elif kernel == "gone":
        door.close()
    else:
        door.door = kernel
    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        async with w.db() as db:
            out = await getattr(w.rest, f"{how}_alarm")(a["id"], None, user=PANEL, db=db)     # whoever is standing at the panel
        took = time.monotonic() - started
        state = (await w.rows())["alarms"][0][scenarios_col("state")]
        assert state == ("idle" if how == "dismiss" else "snoozed") and out["alarm"]["state"] == state
        assert took < 0.4, took                                     # a sqlite write and no wait (the slow kernel answers in 0.8 s)
        if kernel in ("allow", "refuse:not_owner"):
            ask = _wait(door, "door_ask", 2)[1]                     # ... and afterwards the kernel hears about it
            assert (ask["verb"], ask.get("via"), ask["owner"], ask["target"]) == (f"alarm.{'stop' if how == 'dismiss' else 'snooze'}",
                                                                                  "panel", ANN, a["id"])
            for _ in range(300):
                if len(_lines(caplog)) >= 2:
                    break
                await asyncio.sleep(0.01)
            line = _lines(caplog)[-1]
            assert ("decision=recorded" in line) is (kernel == "allow") and ("decision=objected reason=not_owner" in line) is (kernel != "allow")
            assert len(door.ops("door_report")) == (2 if kernel == "allow" else 1)          # an objection closes nothing


@pytest.mark.parametrize("kernel", ["allow", "refuse:not_owner", "slow", "gone"])
@pytest.mark.parametrize("change", [{"enabled": False}, {"label": WORD}])
@pytest.mark.asyncio
async def test_an_edit_that_silences_a_ringing_alarm_is_never_held_up(door, caplog, monkeypatch, kernel, change):
    """Every edit lands the alarm idle, so an edit made while it rings stops it: turning it off on the screen is how many people
    silence an alarm. That edit is made first and told afterwards, exactly as dismiss is: a slow kernel costs it no time, a
    refusing one cannot leave it ringing. Control run: with ``silences`` forced False in ``update_alarm`` (the edit asks first)
    the refusing kernel raises ``AlarmRefused`` with the alarm still ringing, and the slow one makes the call wait."""
    monkeypatch.setattr(os_shadow, "ASK_DEADLINE_S", 1.0)
    w = _world()
    a = await _create(w)
    await _ring(w)
    assert (await w.rows())["alarms"][0][scenarios_col("state")] == "ringing"
    if kernel == "slow":
        door.delay = 0.8
    elif kernel == "gone":
        door.close()
    else:
        door.door = kernel
    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        async with w.db() as db:
            out = await w.rest.update_alarm(a["id"], dict(change), None, user=_member(ANN), db=db)
        took = time.monotonic() - started
        row = (await w.rows())["alarms"][0]
        assert row[scenarios_col("state")] == "idle" and out["alarm"]["state"] == "idle" and took < 0.4, took
        if "enabled" in change:
            assert not row[scenarios_col("enabled")]
        if kernel in ("allow", "refuse:not_owner"):
            told = _wait(door, "door_ask", 2)[1]                    # ... and afterwards the kernel hears that it was stopped
            assert (told["verb"], told["person"], told["owner"], told["target"]) == ("alarm.stop", ANN, ANN, a["id"])
            for _ in range(300):
                if len(_lines(caplog)) >= 2:
                    break
                await asyncio.sleep(0.01)
            assert ("decision=recorded" in _lines(caplog)[-1]) is (kernel == "allow")
            assert ("decision=objected reason=not_owner" in _lines(caplog)[-1]) is (kernel != "allow")


@pytest.mark.parametrize("kernel", ["refuse:not_owner", "slow"])
@pytest.mark.asyncio
async def test_turning_an_alarm_off_is_never_held_up_and_any_other_edit_still_asks_first(door, monkeypatch, kernel):
    """Turning an alarm OFF takes effect at once even when it is not ringing (told afterwards as ``alarm.edit``); an edit that
    neither silences nor turns off still asks first and a refusal is final. Control run: with the ``"enabled" in ch and not on``
    half of ``silences`` dropped, the refusing kernel raises on the turn-off."""
    monkeypatch.setattr(os_shadow, "ASK_DEADLINE_S", 1.0)
    w = _world()
    a = await _create(w)
    if kernel == "slow":
        door.delay = 0.8
    else:
        door.door = kernel
    started = time.monotonic()
    async with w.db() as db:
        out = await w.rest.update_alarm(a["id"], {"enabled": False}, None, user=_member(ANN), db=db)
        assert out["alarm"]["enabled"] is False and time.monotonic() - started < 0.4
        if kernel != "slow":
            assert _wait(door, "door_ask", 2)[1]["verb"] == "alarm.edit"
            with pytest.raises(alarms.AlarmRefused):                 # turning it back on, or renaming it, is an ordinary edit
                await w.rest.update_alarm(a["id"], {"enabled": True}, None, user=_member(ANN), db=db)
            with pytest.raises(alarms.AlarmRefused):
                await w.rest.update_alarm(a["id"], {"label": WORD}, None, user=_member(ANN), db=db)
    assert not (await w.rows())["alarms"][0][scenarios_col("enabled")]


@pytest.mark.asyncio
async def test_a_yes_to_an_anticipation_offer_holds_the_alarms_report_until_its_block_has_ended(door, monkeypatch):
    """``anticipation.answer`` runs the alarm's create on its own connection; the door's report goes out only once that block
    has ended (``kernel_door.reports_after``), never while the row could still be undone. Proven on the create itself inside a
    held block, and ``answer`` is pinned to enter one. Controls run: with ``kernel_door.wrote`` ignoring the held block the
    report is seen inside it; with ``reports_after`` dropped from ``answer`` the last assertion fails."""
    import anticipation

    w = _world()
    seen = {}
    real = alarms.create_alarm

    async def create(db, owner, **kw):
        out = await real(db, owner, **kw)
        _wait(door, "door_ask", 1)
        await asyncio.sleep(0.05)
        seen["reports_inside"] = list(door.ops("door_report"))
        return out

    async with w.db() as db:
        async with kernel_door.reports_after():                      # what ``answer`` now enters around its connection
            monkeypatch.setattr(alarms, "create_alarm", create)
            await alarms.create_alarm(db, ANN, time_hhmm="07:00", actor=alarms.Viewer(user_id=ANN))
    assert seen["reports_inside"] == [] and [r["outcome"] for r in _wait(door, "door_report", 1)] == ["done"]
    src = Path(anticipation.__file__).read_text("utf-8")
    assert "async with kernel_door.reports_after(), _conn(db) as conn:" in src


def scenarios_col(name: str) -> int:
    return alarms.COLS.index(name)


@pytest.mark.asyncio
async def test_silencing_while_a_member_is_being_erased_is_done_and_not_recorded(door, caplog):
    """Control run: with the erase-gate read dropped from ``record_door`` AND the door thread's own, the note reaches the kernel."""
    w = _world()
    a = await _create(w)
    await _ring(w)
    asked = len(_wait(door, "door_ask", 1))
    with caplog.at_level(logging.INFO, logger="os_shadow"), user_erase_gate.erase_span(ANN):
        async with w.db() as db:
            await w.rest.dismiss_alarm(a["id"], None, user=PANEL, db=db)
    assert (await w.rows())["alarms"][0][scenarios_col("state")] == "idle"
    assert "verb=alarm.stop decision=not_checked reason=erasing" in _lines(caplog)[-1] and len(door.ops("door_ask")) == asked


# ---- timers: every change is told afterwards, from a path that cannot wait ----------------------------------------------------------
@pytest.mark.parametrize("kernel", ["allow", "refuse:not_owner", "slow", "gone"])
def test_every_timer_change_is_made_at_once_and_told_to_the_kernel_afterwards(door, caplog, monkeypatch, kernel):
    """The timer path is synchronous: create, add time, cancel, and the panel's dismiss of a ringing timer return at once with
    the store changed, whatever the kernel does. Control run: with ``_note`` waiting for the kernel's answer (a blocking ask) the
    slow case takes over a second; with it dropped from ``cancel_by_id`` no ``timer.stop`` is told."""
    monkeypatch.setattr(os_shadow, "ASK_DEADLINE_S", 1.0)
    log: list = []
    w = scenarios._TimerWorld(log)
    if kernel == "slow":
        door.delay = 0.8
    elif kernel == "gone":
        door.close()
    else:
        door.door = kernel
    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        t = w.sky._timers.create(ANN, WORD, 600)
        w.sky._timers.adjust(ANN, t["timer_id"], 300)
        assert w.sky.cancel_timer_by_id(ANN, t["timer_id"]) is not None                 # a running timer: cancelled
        guest = w.sky._timers.create("guest", "Timer", 60)                              # the house's timer (a guest is not a person)
        w.clock["now"] += 61
        assert w.sky.cancel_any_timer(guest["timer_id"]) is not None                    # ringing: the panel silences it
        took = time.monotonic() - started
        assert took < 0.4 and w.state() == {}, took
        if kernel in ("allow", "refuse:not_owner"):
            asks = _wait(door, "door_ask", 5)
            assert [a["verb"] for a in asks] == ["timer.create", "timer.edit", "timer.cancel", "timer.create", "timer.stop"]
            assert all(a["area"] == "timers" and set(a) <= {"area", "verb", "person", "via", "target", "owner", "visibility", "stamps"}
                       for a in asks)
            assert (asks[0]["person"], asks[0]["owner"], asks[0]["visibility"]) == (ANN, ANN, "private")
            assert (asks[3].get("via"), asks[3]["visibility"]) == ("panel", "household") and "owner" not in asks[3]
            assert (asks[4].get("via"), asks[4]["target"]) == ("panel", guest["timer_id"])
            assert WORD not in json.dumps(door.calls) and "guest" not in json.dumps(door.calls)
            end = time.monotonic() + 3
            while time.monotonic() < end and len(_lines(caplog)) < 5:
                time.sleep(0.01)
            wanted = "decision=recorded" if kernel == "allow" else "decision=objected reason=not_owner"
            assert len(_lines(caplog)) == 5 and all("area=timers" in line and wanted in line for line in _lines(caplog))
            assert WORD not in "".join(_lines(caplog))
    scenarios._restore()


def test_the_panels_tap_on_a_members_timer_names_the_member_it_is_for(door):
    """The panel acts (``via=panel``), the timer is the member's (``owner``): what the kernel's table is asked about. Control run:
    with ``by=""`` dropped from ``cancel_any`` the ask says the member cancelled their own timer."""
    w = scenarios._TimerWorld([])
    t = w.sky._timers.create(ANN, WORD, 600)
    assert w.sky.cancel_any_timer(t["timer_id"]) is not None
    create, cancel = _wait(door, "door_ask", 2)
    assert (cancel["verb"], cancel.get("via"), cancel["person"], cancel["owner"], set(cancel["stamps"])) == \
        ("timer.cancel", "panel", "panel", ANN, {ANN})
    scenarios._restore()


# ---- ordered against a forget ---------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_ask_carries_the_stamps_of_the_moment_its_actor_was_made(door):
    """A viewer is made first thing in a request; the alarm (and so who it is for) is read afterwards, by the caller. A forget of
    that member confirmed in between must not make the ask look newer than the forget. Control run: with ``_door_ids`` taking a
    fresh snapshot instead of the viewer's, the ask carries the moved-on stamp."""
    w = _world()
    a = await _create(w, user=_member(BOB))
    actor = alarms.Viewer(user_id=ANN)                         # made BEFORE ...
    before = os_shadow.erase_stamp(BOB)
    os_shadow._forget_marks[BOB] += 1                          # ... the kernel confirms a whole-member forget of BOB
    async with w.db() as db:
        row = await alarms.get_alarm(db, a["id"])              # ... and before the row that names BOB is read
        await alarms.update_alarm(db, row, {"label": WORD}, actor=actor)
    edit = _wait(door, "door_ask", 2)[1]
    assert edit["owner"] == BOB and edit["stamps"][BOB] == before and os_shadow.erase_stamp(BOB) > before
    assert alarms.Viewer(user_id=ANN).stamps_at[BOB] == 1 and alarms.Viewer(user_id=ANN) == alarms.Viewer(user_id=ANN)


@pytest.mark.asyncio
async def test_a_told_afterwards_change_triggers_the_roster_rewrite_for_every_member_it_names(door, monkeypatch):
    """As an asked-first change does. Control run: with the trigger dropped from ``record_door`` nothing is seen here."""
    seen = []
    monkeypatch.setattr(os_shadow, "_roster_wanted", lambda members: seen.append(set(members)))
    os_shadow.record_door("timers", "timer.stop", "member-new", target="t00000000001", owner=ANN)
    os_shadow.record_door("alarms", "alarm.stop", "", panel=True, target="a00000000001", owner=None, household=True)
    assert seen == [{"member-new", ANN}, set()]


def test_a_viewer_copies_nothing_with_every_door_off(monkeypatch):
    """Flag off: making a viewer reads flags and nothing else. Control run: with the snapshot taken unconditionally this fails."""
    for name in ("ZOE_OS_ALARMS", "ZOE_OS_LISTS", "ZOE_OS_REMINDERS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(os_shadow, "stamp_snapshot", lambda: (_ for _ in ()).throw(AssertionError("copied with every door off")))
    assert alarms.Viewer(user_id=ANN).stamps_at is None


def test_the_watch_only_alarm_hook_is_silent_while_the_door_is_on(door, monkeypatch):
    """Control run: with ``ALARM_SHADOW_VERBS`` emptied the first assertion fails."""
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "list_add,alarm_create")
    assert os_shadow.wants("alarm_create") is False and os_shadow.wants("list_add") is True
    monkeypatch.setenv("ZOE_OS_ALARMS", "off")
    assert os_shadow.wants("alarm_create") is True


# ---- structure --------------------------------------------------------------------------------------------------------------------
DOOR_FNS = {"create_alarm", "update_alarm", "delete_alarm", "snooze", "dismiss", "set_override", "clear_override"}


def _modules() -> list:
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return sorted(p for p in SVC.rglob("*.py") if not skip.intersection(p.relative_to(SVC).parts))


def callers_without_an_actor(src: str) -> list:
    """Functions that call an ``alarms.<door>`` writer without ``actor=``, or that make a ``Viewer`` after their first await.
    A module-level alias of ``alarms`` (``import alarms as x``; ``y = x``) is followed to a fixed point."""
    tree = ast.parse(src)
    names = {"alarms"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.asname for a in node.names if a.name == "alarms" and a.asname}
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    while True:
        found = {t.id for n in assigns if getattr(n.value, "id", "") in names for t in n.targets if isinstance(t, ast.Name)} - names
        if not found:
            break
        names |= found
    out = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        first_await = min([n.lineno for n in ast.walk(fn) if isinstance(n, ast.Await)] or [10 ** 9])
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]:
            base = getattr(call.func.value, "id", "")
            if base in names and call.func.attr in DOOR_FNS and not any(k.arg == "actor" for k in call.keywords):
                out.append((fn.name, call.func.attr))
            if base in names and call.func.attr == "Viewer" and call.lineno > first_await:
                out.append((fn.name, "Viewer after an await"))
    return sorted(set(out))


def test_every_caller_says_who_is_acting_and_makes_that_actor_before_anything_it_awaits():
    """Over EVERY module of the service. Control (planted below): a caller with no ``actor=``, one behind two aliases, and a
    viewer made after an await are all reported. Control run on the real tree: with ``actor=v`` dropped from the spoken
    ``dismiss`` this fails naming ``alarm_voice.py``."""
    modules = _modules()
    assert len(modules) > 100 and SVC / "routers" / "alarms.py" in modules and SVC / "alarm_voice.py" in modules
    seen = 0
    for path in modules:
        if path.relative_to(SVC).as_posix() == "alarms.py":
            continue
        src = path.read_text("utf-8")
        assert callers_without_an_actor(src) == [], path.name
        seen += sum(src.count(f"alarms.{fn}(") for fn in DOOR_FNS)
    assert seen >= 18                                          # the scan saw the callers it is about
    planted = ("import alarms\nimport alarms as al\nagain = al\n"
               "async def bare(db, a):\n    await alarms.dismiss(db, a)\n"
               "async def aliased(db, a):\n    await again.delete_alarm(db, a['id'])\n"
               "async def late(db, uid):\n    await db.fetch('x')\n    v = alarms.Viewer(user_id=uid)\n    await alarms.snooze(db, {}, actor=v)\n"
               "async def good(db, uid):\n    v = alarms.Viewer(user_id=uid)\n    await db.fetch('x')\n    await alarms.snooze(db, {}, actor=v)\n")
    assert callers_without_an_actor(planted) == [("aliased", "delete_alarm"), ("bare", "dismiss"), ("late", "Viewer after an await")]


def test_the_silencing_writers_never_await_the_kernel_and_the_asking_ones_ask_before_they_write():
    """In ``alarms.py``: ``snooze`` and ``dismiss`` hold no ``_gate`` and call ``_note`` only AFTER their write; ``create``,
    ``update``, ``delete`` and the override path call ``_gate`` BEFORE their first write. In the timer store every change calls
    ``_note`` after it and nothing awaits. Control (planted below): a silencer that asks first, and an asker that writes first."""
    def shape(src: str) -> dict:
        out = {}
        for fn in [n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            calls = [(n.lineno, getattr(n.func, "id", getattr(n.func, "attr", ""))) for n in ast.walk(fn) if isinstance(n, ast.Call)]
            writes = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                      and n.value.lstrip().upper().startswith(("INSERT INTO", "UPDATE ", "DELETE FROM"))]
            gate = [ln for ln, name in calls if name == "_gate"]
            note = [ln for ln, name in calls if name == "_note"]
            out[fn.name] = ("asks_first" if gate and writes and max(gate) < min(writes) else
                            "asks_late" if gate else
                            "tells_after" if note and writes and min(note) > min(writes) else
                            "tells_early" if note else "-")
        return out

    got = shape((SVC / "alarms.py").read_text("utf-8"))
    assert {got[n] for n in ("snooze", "dismiss")} == {"tells_after"}
    assert {got[n] for n in ("create_alarm", "update_alarm", "delete_alarm", "_reschedule")} == {"asks_first"} and got["tick"] == "-"
    planted = shape("async def snooze(db):\n    await _gate('v')\n    await db.execute('UPDATE alarms SET x = 1')\n"
                    "async def create_alarm(db):\n    await db.execute('INSERT INTO alarms (id) VALUES (1)')\n    await _gate('v')\n")
    assert planted == {"snooze": "asks_first", "create_alarm": "asks_late"}
    store = next(n for n in ast.walk(ast.parse((SVC / "skybridge_service.py").read_text("utf-8")))
                 if isinstance(n, ast.ClassDef) and n.name == "_TimerStore")
    assert not [n for n in ast.walk(store) if isinstance(n, (ast.Await, ast.AsyncFunctionDef))]          # the store cannot wait
    told = {m.name for m in store.body if isinstance(m, ast.FunctionDef)
            and any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "_note" for n in ast.walk(m))}
    assert told == {"create", "adjust", "cancel_by_id"}
