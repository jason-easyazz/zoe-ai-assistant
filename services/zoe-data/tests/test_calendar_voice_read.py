"""A member's calendar read is HEARD and SEEN, not counted (live 2026-10-14, a member signed in by a fresh PIN).

"what's on my calendar?" answered only "You have 1 event today."; "what is it?" (twice) went to the brain with no context,
"what's the one event on my calendar?" got the count again, and "schedule a meeting for 10 o'clock tonight" was recognised
as a calendar create but VETOED by a router verdict of chat @ 0.65 ``low_conf`` (9.4 s on the brain). Owner standing intent:
"if I ask what is on my calendar, I want to see the results as well as hear them" - for an identified member; a guest hears
and sees nothing private. Pinned here, each with a negative control (the opposite input turns the assertion over):

* the Skybridge read-back names the events for a member (<= 3 with time + title, the rest as "and N more on screen") and
  the card still carries ALL of them; a guest gets neither the titles nor the count;
* a short follow-up inside the window ("what is it", "what are they", "which one", "what's the one event") re-runs the
  SAME member-scoped read; outside the window, after another answer, as a guest, or as chat ("what is it about you",
  "what time is it") it does not;
* a calendar create with a parsed time is not vetoed by a ``low_conf`` chat verdict (and "10 o'clock tonight" is 22:00),
  while a confident chat verdict, a time-less create, a read and a verdict under the unsure floor all still are.

Invented names only; no DB, no network, no models.
"""
from __future__ import annotations

import time

import pytest

import conversation_cues
import lexicons
import skybridge_service as sky
import voice_cards
from test_skybridge_router_veto import _two_stage
from test_skybridge_service import FakeDb, _freeze_today

pytestmark = pytest.mark.ci_safe

TODAY = "2026-06-11"


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    _freeze_today(monkeypatch)


def _event(title, start="09:00", *, day=TODAY, all_day=False, eid=None):
    return {"id": eid or f"ev-{title}", "user_id": "casey", "title": title, "start_date": day, "start_time": start,
            "end_time": None, "category": "personal", "visibility": "family", "deleted": False, "all_day": all_day}


async def _read(text, user="casey", events=(), context=None):
    return await sky.resolve_skybridge_request(text, user, context=context, db=FakeDb(events=list(events)))


# -- 1. the read-back names the events for a member ------------------------------------------------------------------

async def test_a_member_hears_the_one_event_by_name_and_its_time():
    result = await _read("what's on my calendar?", events=[_event("Team meeting", "22:00")])
    assert result["spoken_summary"] == "Just one today: Team meeting at 10 PM."
    assert result["cards"][0]["content"]["events"][0]["title"] == "Team meeting"      # and the card carries it


async def test_a_member_hears_up_to_three_and_the_rest_are_on_screen():
    rows = [_event(t, s) for t, s in (("Dentist", "09:00"), ("Lunch", "12:30"), ("Piano", "15:00"),
                                      ("Dinner", "18:30"), ("Film", "20:00"))]
    result = await _read("what's on my calendar today", events=rows)
    assert result["spoken_summary"] == ("You have 5 events today: Dentist at 9 AM, Lunch at 12:30 PM, Piano at 3 PM "
                                        "and 2 more on screen.")
    assert [e["title"] for e in result["cards"][0]["content"]["events"]] == ["Dentist", "Lunch", "Piano", "Dinner", "Film"]
    assert "Dinner" not in result["spoken_summary"] and "Film" not in result["spoken_summary"]


async def test_exactly_three_events_are_all_named_with_no_more_clause():
    rows = [_event("Dentist", "09:00"), _event("Lunch", "12:30"), _event("Piano", "15:00")]
    spoken = (await _read("what's on my calendar", events=rows))["spoken_summary"]
    assert spoken == "You have 3 events today: Dentist at 9 AM, Lunch at 12:30 PM and Piano at 3 PM."


async def test_an_empty_day_and_an_all_day_event_read_naturally():
    assert (await _read("what's on my calendar"))["spoken_summary"] == "You have nothing today."
    spoken = (await _read("what's on my calendar", events=[_event("Sports day", all_day=True)]))["spoken_summary"]
    assert spoken == "Just one today: Sports day."


async def test_a_week_read_names_the_day_of_each_event():
    rows = [_event("Dentist", "09:00", day="2026-06-12")]
    spoken = (await _read("what's on my calendar this week", events=rows))["spoken_summary"]
    assert spoken == "Just one this week: Dentist on Friday at 9 AM."


async def test_a_guest_hears_no_title_and_no_count():
    """Negative control of the member reads above: the same rows, no identity."""
    rows = [_event("Therapist")]
    for sentinel in ("guest", "voice-guest", "", None):          # every shape of "nobody", not just the two named ones
        out = await sky._resolve_calendar(sky.SkybridgeIntent("calendar", "show", "today", None, None), sentinel, FakeDb(events=rows))
        spoken = out["spoken_summary"]
        assert "Therapist" not in spoken and "1" not in spoken and "event" not in spoken.lower(), sentinel
        assert "private" in spoken.lower() and out["cards"][0]["content"]["events"] == [], sentinel
    # and through the public entry point a guest is challenged before any row is read
    result = await _read("what's on my calendar?", user="guest", events=rows)
    assert result.get("auth_required") is True and "Therapist" not in str(result)


def test_the_count_is_the_floor_when_the_lexicon_has_no_templates(monkeypatch):
    monkeypatch.setattr(voice_cards.voice_session, "text_for", lambda *a, **k: k.get("default"))
    assert voice_cards.spoken_events([_event("X")], "today") is None


# -- 2. follow-ups inside the window ---------------------------------------------------------------------------------

FOLLOWUPS = ["what is it", "What is it?", "what are they", "what's that", "which one",
             "what's the one event on my calendar", "I was wondering what the one event for today is",
             "can you tell me what it is", "read them out"]


@pytest.mark.parametrize("utterance", FOLLOWUPS)
async def test_a_followup_after_the_count_names_the_events(utterance):
    rows = [_event("Team meeting", "22:00")]
    first = await _read("what's on my calendar?", events=rows)
    assert sky._CALENDAR_READ_KEY in first["skybridge_context"]
    second = await _read(utterance, events=rows, context=first["skybridge_context"])
    assert second["handled"] is True and second["intent"]["domain"] == "calendar"
    assert second["spoken_summary"] == "Just one today: Team meeting at 10 PM."


async def test_the_followup_reads_the_range_just_answered_not_today():
    rows = [_event("Dentist", "09:00", day="2026-06-12")]
    first = await _read("what's on my calendar tomorrow", events=rows)
    second = await _read("what is it", events=rows, context=first["skybridge_context"])
    assert second["intent"]["range"] == "tomorrow"
    assert second["spoken_summary"] == "Just one tomorrow: Dentist at 9 AM."


async def test_the_followup_window_is_bounded(monkeypatch):
    rows = [_event("Team meeting", "22:00")]
    first = await _read("what's on my calendar?", events=rows)
    ctx = first["skybridge_context"]
    assert sky.classify_skybridge_intent("what is it", ctx) is not None                      # control: inside
    monkeypatch.setenv("ZOE_CALENDAR_FOLLOWUP_TTL_S", "90")
    ctx[sky._CALENDAR_READ_KEY]["ts"] = time.time() - 91
    assert sky.classify_skybridge_intent("what is it", ctx) is None                          # outside
    monkeypatch.setenv("ZOE_CALENDAR_FOLLOWUP_TTL_S", "600")
    assert sky.classify_skybridge_intent("what is it", ctx) is not None                      # the window is the knob


async def test_no_followup_without_a_calendar_answer_before_it():
    assert sky.classify_skybridge_intent("what is it", None) is None
    assert sky.classify_skybridge_intent("what are they", {"intent": {"domain": "lists", "action": "show"}, "cards": []}) is None


async def test_another_answer_clears_the_calendar_memory():
    rows = [_event("Team meeting", "22:00")]
    first = await _read("what's on my calendar?", events=rows)
    clock = await _read("what time is it", events=rows, context=first["skybridge_context"])
    assert clock["intent"]["domain"] == "clock" and sky._CALENDAR_READ_KEY not in clock["skybridge_context"]
    assert sky.classify_skybridge_intent("what is it", clock["skybridge_context"]) is None


async def test_a_guest_followup_is_the_challenge_never_the_events():
    rows = [_event("Therapist", "09:00")]
    first = await _read("what's on my calendar?", events=rows)                       # a member read ...
    second = await _read("what is it", user="guest", events=rows, context=first["skybridge_context"])   # ... a guest asks
    assert second.get("auth_required") is True and "Therapist" not in str(second)


@pytest.mark.parametrize("utterance", [
    "what is it about you", "what time is it", "what is the weather", "what is the capital of france",
    "what is it like on mars", "which one is better", "what is the one thing you like about music", "play it",
    "turn it off", "what is it you do"])
async def test_chat_inside_the_window_is_not_a_calendar_followup(utterance):
    first = await _read("what's on my calendar?", events=[_event("Team meeting", "22:00")])
    got = sky.classify_skybridge_intent(utterance, first["skybridge_context"])
    assert got is None or (got.domain, got.action) != ("calendar", "show"), utterance


def test_the_lexicons_calendar_examples_resolve_and_non_examples_do_not():
    sec = lexicons.load("en")["calendar_followup"]
    assert all(conversation_cues.calendar_followup(x) for x in sec["examples"])
    assert not any(conversation_cues.calendar_followup(x) for x in sec["not_followups"])
    assert lexicons.load("fr").get("calendar_followup") is None and not conversation_cues.calendar_followup("")


# -- 3. the router veto does not eat a create it fully understood -----------------------------------------------------

CREATE = "schedule a meeting for 10 o'clock tonight"
LIVE_VERDICT = _two_stage("chat", conf=0.65, gated=True, head_top="calendar", reason="low_conf")


def test_the_live_create_parses_to_ten_at_night():
    intent = sky.classify_skybridge_intent(CREATE, None)
    assert (intent.domain, intent.action, intent.target_time, intent.start_date.isoformat()) == (
        "calendar", "create_event", "22:00", TODAY)
    assert sky.classify_skybridge_intent("schedule a meeting at 7 tonight", None).target_time == "19:00"
    assert sky.classify_skybridge_intent("schedule a meeting for 10 o'clock tomorrow", None).target_time == "10:00"   # control
    assert sky.classify_skybridge_intent("schedule a meeting at 9 am tonight", None).target_time == "09:00"          # explicit wins
    assert sky.classify_skybridge_intent("schedule a meeting for 11 o'clock tonight", None).target_time == "23:00"
    # "12 tonight" is midnight (tomorrow's date): not guessed as noon - no create is claimed
    for text in ("schedule a meeting for 12 o'clock tonight", "schedule a meeting at 12 tonight"):
        got = sky.classify_skybridge_intent(text, None)
        assert got is None or got.action != "create_event", text


def test_a_low_conf_chat_verdict_does_not_veto_a_parsed_create():
    intent = sky.classify_skybridge_intent(CREATE, None)
    assert sky.skybridge_router_gate(CREATE, intent, LIVE_VERDICT)[:2] == ("allow", "write_parsed_time")


@pytest.mark.parametrize("verdict,why", [
    (_two_stage("chat", conf=0.8, gated=True, head_top="chat", reason="chat_top"), "a confident chat verdict"),
    (_two_stage("chat", conf=0.8), "stage 1 said chat, not gated"),
    (_two_stage("lists"), "a confident OTHER domain"),
    (_two_stage("chat", conf=0.30, gated=True, head_top="calendar", reason="low_conf"), "under the unsure floor"),
])
def test_everything_else_still_vetoes_the_create(verdict, why):
    intent = sky.classify_skybridge_intent(CREATE, None)
    assert sky.skybridge_router_gate(CREATE, intent, verdict)[0] == "veto", why


def test_a_timeless_create_and_a_read_keep_the_low_conf_veto():
    notime = sky.classify_skybridge_intent("add dentist to my calendar", None)
    assert notime.action == "create_event" and notime.all_day is True
    assert sky.skybridge_router_gate("add dentist to my calendar", notime, LIVE_VERDICT)[0] == "veto"
    read = sky.classify_skybridge_intent("what's on my calendar", None)
    assert sky.skybridge_router_gate("what's on my calendar", read, LIVE_VERDICT)[0] == "veto"
    assert sky.skybridge_router_gate("x", sky.SkybridgeIntent(domain="calendar", action="delete_event"), LIVE_VERDICT)[0] == "veto"


async def test_the_live_create_is_handled_on_the_fast_path_with_a_receipt():
    db = FakeDb(events=[])
    result = await sky.resolve_skybridge_request(CREATE, "casey", db=db, router_decision=LIVE_VERDICT)
    assert result["handled"] is True and not result.get("vetoed")
    assert result["intent"]["action"] == "create_event"
    assert [(e["title"], e["start_time"], e["start_date"]) for e in db.events] == [("Meeting", "22:00", TODAY)]
    assert "10" in result["spoken_summary"]
    # control: with the router's verdict a confident chat, the same words go to the brain and nothing is written
    db2 = FakeDb(events=[])
    vetoed = await sky.resolve_skybridge_request(
        CREATE, "casey", db=db2, router_decision=_two_stage("chat", conf=0.8, gated=True, head_top="chat", reason="chat_top"))
    assert vetoed["handled"] is False and vetoed["vetoed"] is True and db2.events == []
