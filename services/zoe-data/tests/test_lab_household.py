"""The cases QA round 10a marked BLOCKED (a synthetic demo user is not a member), run on the member-scoped lab household.

``lab_member.py`` says precisely what is real and what is faked. Every name is invented; addresses are TEST-NET.

Negative controls, to run by hand (NOT yet run: the shared test lane was closed when this was written); each should turn the
named test red: ``calendar_read.is_repeating_row`` returning False -> the edit-one / delete-one tests (the card write hits the
whole series); dropping the ``visibility = 'family' OR user_id = ?`` filter in ``routers/lists.py`` -> the child/guest list tests; ``_ring_when_done`` returning at once -> the timer-announce
test; ``alarms.dismiss`` doing nothing -> the alarm test. ``test_the_lab_store_rejects_what_postgres_would`` is the fixture's own
control: a statement SQLite cannot run is recorded, not swallowed.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import calendar_read  # noqa: E402
import lab_member  # noqa: E402

pytestmark = pytest.mark.ci_safe

SERIES_START = "2026-11-16"        # a Monday
WINDOW = {"start_date": "2026-11-16", "end_date": "2026-12-07"}


@pytest.fixture
def hh(monkeypatch, tmp_path):
    import skybridge_service as sky

    house = lab_member.install(monkeypatch, tmp_path)
    monkeypatch.setattr(sky, "today_for_zoe_tz", lambda: date(2026, 11, 23))
    yield house
    lab_member.teardown(house)


# ── the fixture itself ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_household_is_members_a_guest_and_a_registered_panel(hh):
    users = {r["id"]: r["role"] for r in hh.db.rows("SELECT id, role FROM users")}
    assert users == {"family-admin": "admin", "lab-ari": "member", "lab-bo": "member", "lab-cy": "child"}
    assert hh.db.rows("SELECT panel_id, ip_address FROM panels") == [{"panel_id": "lab-panel", "ip_address": "192.0.2.60"}]
    assert hh.http("ari", "POST", "/api/calendar/events", {"title": "Lab check", "start_date": "2026-11-20"}).status_code == 200
    assert hh.http("panel", "POST", "/api/calendar/events", {"title": "Lab bins", "start_date": "2026-11-20"}).status_code == 200
    assert hh.http("guest", "POST", "/api/calendar/events", {"title": "Nope", "start_date": "2026-11-20"}).status_code == 403
    owners = sorted(r["user_id"] for r in hh.db.rows("SELECT user_id FROM events"))
    assert owners == ["family-admin", "lab-ari"]          # the panel writes as the household, never as a person
    assert hh.brain_calls == [] and hh.db.errors == []


def test_the_lab_store_rejects_what_postgres_would(hh):
    with pytest.raises(sqlite3.Error):
        hh.db.con.execute("SELECT * FROM no_such_table")
    with pytest.raises(sqlite3.Error):
        hh.db._run("SELECT * FROM no_such_table WHERE id = $1", ("x",))
    assert hh.db.errors and "no_such_table" in hh.db.errors[0][0]
    assert hh.db.adapt("SELECT 1 WHERE a = $2 AND b = $1 FOR UPDATE", ("one", "two")) == ("SELECT 1 WHERE a = ? AND b = ?", ("two", "one"))


# ── calendar: a repeating event ─────────────────────────────────────────────────────────────────────────────────────────────

def _series(hh, who="ari", title="Lab swim"):
    r = hh.http(who, "POST", "/api/calendar/events",
                {"title": title, "start_date": SERIES_START, "start_time": "17:00", "recurring": "weekly", "visibility": "family"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _occurrences(hh, who="ari", title="Lab swim"):
    r = hh.http(who, "GET", "/api/calendar/events", **WINDOW)
    assert r.status_code == 200, r.text
    return [(e["occurrence_date"], e["title"]) for e in r.json()["events"] if title in e["title"]]


def _card(event_id, title, day="2026-11-23"):
    return {"intent": {"domain": "calendar"},
            "cards": [{"content": {"source": "calendar_show", "start_date": day,
                                   "events": [{"id": event_id, "title": title, "start_date": day, "start_time": "17:00"}]}}]}


def test_repeating_event_create_expands_to_every_week(hh):
    _series(hh)
    assert [d for d, _ in _occurrences(hh)] == ["2026-11-16", "2026-11-23", "2026-11-30", "2026-12-07"]
    assert [d for d, _ in _occurrences(hh, who="bo")] == ["2026-11-16", "2026-11-23", "2026-11-30", "2026-12-07"]  # household


def test_repeating_event_edit_one_is_refused_and_changes_nothing(hh):
    sid = _series(hh)
    before = hh.db.rows("SELECT * FROM events")
    out = hh.say("ari", "change my lab swim to 9am", context=_card(sid, "Lab swim"))
    assert out["spoken_summary"] == calendar_read.REPEATING_MOVE_LINE and out["actions"] == []
    assert hh.db.rows("SELECT * FROM events") == before


def test_repeating_event_edit_all_renames_every_occurrence(hh):
    sid = _series(hh)
    r = hh.http("ari", "PUT", f"/api/calendar/events/{sid}", {"title": "Lab swim squad"})
    assert r.status_code == 200, r.text
    assert {t for _, t in _occurrences(hh)} == {"Lab swim squad"} and len(_occurrences(hh)) == 4


def test_repeating_event_delete_one_is_refused_and_deletes_nothing(hh):
    sid = _series(hh)
    out = hh.say("ari", "delete lab swim from my calendar", context=_card(sid, "Lab swim"))
    assert out["spoken_summary"] == calendar_read.REPEATING_DELETE_LINE and out["actions"] == []
    assert len(_occurrences(hh)) == 4


def test_repeating_event_delete_all_removes_the_series(hh):
    sid = _series(hh)
    assert hh.http("ari", "DELETE", f"/api/calendar/events/{sid}").status_code == 200
    assert _occurrences(hh) == []


def test_another_members_private_repeating_event_is_invisible_and_unwritable(hh):
    r = hh.http("bo", "POST", "/api/calendar/events",
                {"title": "Lab therapy", "start_date": SERIES_START, "recurring": "weekly", "visibility": "personal"})
    sid = r.json()["id"]
    assert _occurrences(hh, who="ari", title="Lab therapy") == []
    assert _occurrences(hh, who="cy", title="Lab therapy") == []
    assert hh.http("ari", "DELETE", f"/api/calendar/events/{sid}").status_code == 404
    assert len(_occurrences(hh, who="bo", title="Lab therapy")) == 4


# ── timers ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_a_voice_timer_at_the_panel_is_seen_by_the_panel_and_announced_once_when_it_ends(hh):
    out = await hh.voice("ari", "set a timer for 5 minutes called pasta")
    tid = out["cards"][0]["props"]["timer_id"]
    seen = hh.http("panel", "GET", "/api/skybridge/timers").json()["timers"]
    assert [t["timer_id"] for t in seen] == [tid]
    assert hh.http("bo", "GET", "/api/skybridge/timers").json()["timers"] == []          # another member's bucket
    assert hh.http("guest", "GET", "/api/skybridge/timers").json()["timers"] == []       # not the panel
    await hh.ring_timer(tid)
    await hh.ring_timer(tid)                                                             # a replay adds nothing
    assert hh.announcements() == [{"user_id": "lab-ari", "panel_id": "lab-panel", "message": "Your Pasta timer is done.",
                                   "trigger_type": "timer_done", "expired": 0}]


@pytest.mark.xfail(strict=True, reason="FINDING: a name before the word timer ('a pasta timer') is dropped by _parse_timer_label")
async def test_a_name_before_the_word_timer_is_the_timers_label(hh):
    out = await hh.voice("ari", "set a pasta timer for 5 minutes")
    await hh.ring_timer(out["cards"][0]["props"]["timer_id"])
    assert [a["message"] for a in hh.announcements()] == ["Your Pasta timer is done."]


async def test_a_cancelled_timer_never_rings(hh):
    out = await hh.voice("ari", "set a timer for 3 minutes")
    tid = out["cards"][0]["props"]["timer_id"]
    assert hh.http("guest", "POST", "/api/skybridge/timers/cancel", {"timer_id": tid}).json()["ok"] is False
    assert hh.http("ari", "POST", "/api/skybridge/timers/cancel", {"timer_id": tid}).json()["ok"] is True
    assert hh.http("panel", "GET", "/api/skybridge/timers").json()["timers"] == []
    assert hh.announcements() == []


# ── alarms ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_an_alarm_rings_at_the_panel_and_is_dismissed(hh):
    r = hh.http("ari", "POST", "/api/alarms", {"time_hhmm": "06:30", "repeat": "once", "label": "Lab early start"})
    assert r.status_code == 200, r.text
    aid = r.json()["alarm"]["id"]
    fire_at = hh.db.rows("SELECT next_fire_at FROM alarms WHERE id = ?", (aid,))[0]["next_fire_at"]
    events = await hh.tick_alarms(fire_at + 1)
    assert [(e, a["id"]) for e, a in events] == [("rang", aid)]
    ringing = hh.http("panel", "GET", "/api/alarms/ringing").json()["ringing"]
    assert [a["id"] for a in ringing] == [aid] and ringing[0]["label"] == ""             # the panel never reads a personal label
    assert hh.http("guest", "GET", "/api/alarms/ringing").status_code == 403
    assert hh.http("panel", "POST", f"/api/alarms/{aid}/dismiss").status_code == 200
    assert hh.http("panel", "GET", "/api/alarms/ringing").json()["ringing"] == []
    assert hh.db.rows("SELECT state FROM alarms WHERE id = ?", (aid,)) == [{"state": "idle"}]


# ── lists: private until shared; the fridge rule ────────────────────────────────────────────────────────────────────────────

def _list(hh, who, name, vis):
    r = hh.http(who, "POST", "/api/lists/personal", {"name": name, "visibility": vis})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _names(hh, who, ltype="personal"):
    r = hh.http(who, "GET", f"/api/lists/{ltype}")
    assert r.status_code == 200, r.text
    return sorted(x["name"] for x in r.json()["lists"])


def test_a_child_cannot_see_an_adults_private_list_until_it_is_shared(hh):
    lid = _list(hh, "ari", "Lab gift ideas", "personal")
    assert "Lab gift ideas" not in _names(hh, "cy")
    assert "Lab gift ideas" not in _names(hh, "bo")
    assert hh.http("cy", "GET", f"/api/lists/personal/{lid}").status_code == 404
    assert hh.http("ari", "PUT", f"/api/lists/personal/{lid}", {"visibility": "family"}).status_code == 200
    assert "Lab gift ideas" in _names(hh, "cy")


def test_the_fridge_rule_the_panel_edits_items_but_never_deletes_the_list(hh):
    r = hh.http("ari", "POST", "/api/lists/shopping", {"name": "Lab shopping", "visibility": "family"})
    lid = r.json()["id"]
    assert hh.http("panel", "POST", f"/api/lists/shopping/{lid}/items", {"text": "lab oats"}).status_code == 200
    assert hh.http("panel", "DELETE", f"/api/lists/shopping/{lid}").status_code == 403
    items = hh.http("cy", "GET", f"/api/lists/shopping/{lid}/items").json()
    texts = [i["text"] for i in (items.get("items") if isinstance(items, dict) else items)]
    assert texts == ["lab oats"]


# ── a guest sees nothing ────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_a_guest_sees_nothing_of_the_household(hh):
    _list(hh, "ari", "Lab private", "personal")
    hh.http("ari", "POST", "/api/reminders/", {"title": "Lab pills", "due_date": "2026-11-20", "visibility": "personal"})
    hh.http("bo", "POST", "/api/calendar/events", {"title": "Lab clinic", "start_date": "2026-11-20", "visibility": "personal"})
    hh.http("ari", "POST", "/api/alarms", {"time_hhmm": "06:00", "repeat": "once"})
    await hh.voice("ari", "set a timer for 4 minutes")
    for path in ("/api/lists/personal", "/api/reminders/", "/api/calendar/events", "/api/skybridge/timers", "/api/alarms"):
        r = hh.http("guest", "GET", path, **(WINDOW if "calendar" in path else {}))
        body = r.json() if r.status_code == 200 else {}
        assert r.status_code in (200, 403), (path, r.status_code)
        assert not any(body.get(k) for k in ("lists", "reminders", "events", "timers", "alarms")), (path, body)
        assert "Lab" not in r.text, path


def test_a_members_private_reminder_is_not_another_members(hh):
    r = hh.http("bo", "POST", "/api/reminders/", {"title": "Lab refill", "due_date": "2026-11-20", "visibility": "personal"})
    assert r.status_code == 200, r.text
    assert "Lab refill" not in hh.http("ari", "GET", "/api/reminders/").text
    assert "Lab refill" in hh.http("bo", "GET", "/api/reminders/").text
