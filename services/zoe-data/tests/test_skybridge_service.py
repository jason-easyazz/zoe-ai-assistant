"""Tests for Skybridge real data card resolution."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import skybridge_service  # noqa: E402
from card_contract import validate_component  # noqa: E402
from skybridge_service import classify_skybridge_intent, resolve_skybridge_request  # noqa: E402


def _freeze_today(monkeypatch, frozen: date = date(2026, 6, 11)) -> None:
    """Freeze the service's clock seam.

    ``skybridge_service`` reads "today" through ``today_for_zoe_tz`` (imported
    into its module namespace), so that is the name to patch — patching the
    ``date`` class does nothing to the clock.
    """
    monkeypatch.setattr(skybridge_service, "today_for_zoe_tz", lambda: frozen)


class Cursor:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row


class FakeDb:
    def __init__(self, *, events=None, prefs=None, lists=None, items_by_list=None, people=None):
        self.events = events or []
        self.prefs = prefs
        self.lists = lists or []
        self.items_by_list = items_by_list or {}
        self.people = people or []
        self.fetch_args = None
        self.list_item_fetch_count = 0
        self.executed = []
        self.commits = 0

    async def fetch(self, *args):
        self.fetch_args = args
        sql = str(args[0])
        if "FROM events" in sql:
            return [event for event in self.events if not event.get("deleted")]
        if "FROM lists" in sql:
            if "WHERE id = $1" in sql:
                return [row for row in self.lists if row.get("id") == args[1]]
            if "lower(name) = lower($2)" in sql:
                user_id = args[1]
                name = str(args[2]).lower()
                return [row for row in self.lists if row.get("user_id") == user_id and str(row.get("name", "")).lower() == name]
            if "list_type = $2" in sql:
                user_id = args[1]
                list_type = args[2]
                rows = [
                    row for row in self.lists
                    if row.get("list_type") == list_type
                    and (row.get("visibility") == "family" or row.get("user_id") == user_id)
                    and not row.get("deleted")
                ]
                return sorted(
                    rows,
                    key=lambda row: (
                        0 if row.get("user_id") == user_id else 1,
                        -(row.get("updated_at") or 0),
                    ),
                )
            return self.lists
        if "FROM list_items" in sql:
            self.list_item_fetch_count += 1
            key = args[1]
            if isinstance(key, (list, tuple, set)):
                rows = []
                for list_id in key:
                    rows.extend(self.items_by_list.get(list_id, []))
                return rows
            return self.items_by_list.get(key, [])
        if "FROM people" in sql:
            return self.people
        return []

    recent_list_item_dup = None  # scripted result for the add replay-guard query

    async def fetchrow(self, *args):
        sql = str(args[0]) if args else ""
        if "FROM list_items" in sql:
            return self.recent_list_item_dup
        return self.prefs

    async def execute(self, *args):
        self.executed.append(args)
        sql = str(args[0])
        if "INSERT INTO events" in sql:
            self.events.append(
                {
                    "id": args[1],
                    "user_id": args[2],
                    "title": args[3],
                    "start_date": args[4],
                    "start_time": args[5],
                    "end_date": args[6],
                    "end_time": args[7],
                    "category": args[9],
                    "location": args[10],
                    "visibility": args[14],
                    "deleted": False,
                }
            )
            return "INSERT 0 1"
        if "UPDATE events SET start_time" in sql:
            updated = 0
            for event in self.events:
                if event.get("id") == args[2] and event.get("user_id") == args[3]:
                    event["start_time"] = args[1]
                    updated += 1
            return f"UPDATE {updated}"
        if "INSERT INTO lists" in sql:
            self.lists.append(
                {
                    "id": args[1],
                    "user_id": args[2],
                    "name": args[3],
                    "list_type": args[4],
                    "description": args[5],
                    "visibility": args[6],
                }
            )
            return "INSERT 0 1"
        if "INSERT INTO list_items" in sql:
            item = {
                "id": args[1],
                "list_id": args[2],
                "text": args[3],
                "priority": args[4],
                "category": args[5],
                "quantity": args[6],
                "completed": False,
            }
            self.items_by_list.setdefault(args[2], []).append(item)
            return "INSERT 0 1"
        if "UPDATE list_items SET deleted" in sql:
            item_id = args[1]
            list_id = args[2]
            removed = 0
            for item in self.items_by_list.get(list_id, []):
                if item.get("id") == item_id and not item.get("deleted"):
                    item["deleted"] = True
                    removed += 1
            # Drop soft-deleted rows so the authoritative re-read no longer sees them.
            self.items_by_list[list_id] = [
                item for item in self.items_by_list.get(list_id, []) if not item.get("deleted")
            ]
            return f"UPDATE {removed}"
        if "UPDATE list_items SET completed" in sql:
            completed_val = args[1]
            item_id = args[2]
            list_id = args[3]
            updated = 0
            for item in self.items_by_list.get(list_id, []):
                if item.get("id") == item_id and not item.get("deleted"):
                    item["completed"] = bool(completed_val)
                    updated += 1
            return f"UPDATE {updated}"
        if "UPDATE events SET deleted" in sql:
            event_id = args[1]
            user_id = args[2]
            removed = 0
            for event in self.events:
                if event.get("id") == event_id and event.get("user_id") == user_id and not event.get("deleted"):
                    event["deleted"] = True
                    removed += 1
            return f"UPDATE {removed}"
        if "INSERT INTO people" in sql:
            self.people.append(
                {
                    "id": args[1],
                    "user_id": args[2],
                    "name": args[3],
                    "relationship": args[4],
                    "circle": args[5],
                    "context": args[6],
                    "visibility": args[7],
                    "is_partial": bool(args[8]),
                }
            )
            return "INSERT 0 1"
        if "UPDATE people SET" in sql:
            updated = 0
            person_id = args[-2]
            for person in self.people:
                if person.get("id") == person_id:
                    idx = 1
                    if "notes =" in sql:
                        person["notes"] = args[idx]
                        idx += 1
                    if "birthday =" in sql:
                        person["birthday"] = args[idx]
                    updated += 1
            return f"UPDATE {updated}"
        return "UPDATE 0"

    async def commit(self):
        self.commits += 1


class GuardedGuestDb(FakeDb):
    async def fetch(self, *_args):
        raise AssertionError("guest calendar requests must not fetch family events")


def test_classify_calendar_and_weather_requests():
    assert classify_skybridge_intent("show my calendar").domain == "calendar"
    assert classify_skybridge_intent("show me the weather").domain == "weather"
    assert classify_skybridge_intent("show me the clock").domain == "clock"
    assert classify_skybridge_intent("what time is it").domain == "clock"
    assert classify_skybridge_intent("what is happening this week").domain == "calendar"
    assert classify_skybridge_intent("show my shopping list").domain == "lists"
    assert classify_skybridge_intent("what's on my shopping list").domain == "lists"
    assert classify_skybridge_intent("whats on my grocery list").domain == "lists"
    work_add = classify_skybridge_intent("add bread to the work list")
    assert work_add.action == "add_item"
    # Check-off (the tap gesture + voice): explicit direction, not confused with add/remove.
    check = classify_skybridge_intent("check off milk on the shopping list")
    assert check.domain == "lists" and check.action == "complete_item"
    assert check.item_text == "milk" and check.completed is True
    uncheck = classify_skybridge_intent("uncheck milk on the shopping list")
    assert uncheck.action == "complete_item" and uncheck.completed is False
    assert classify_skybridge_intent("tick off bread").action == "complete_item"
    assert classify_skybridge_intent("mark eggs as done").completed is True
    # "take milk off the list" must still be a removal, not a check-off.
    assert classify_skybridge_intent("take milk off the shopping list").action == "remove_item"
    assert work_add.item_text == "bread"
    assert work_add.list_type == "work"
    assert classify_skybridge_intent("show my lists").action == "overview"
    assert classify_skybridge_intent("new list").action == "create_list"
    list_context = {"intent": {"domain": "lists", "action": "show", "list_type": "shopping"}, "cards": []}
    contextual_create = classify_skybridge_intent("add a personal list", list_context)
    assert contextual_create.action == "create_list"
    assert contextual_create.list_type == "personal"
    assert classify_skybridge_intent("add dentist at 2pm", list_context) is None
    ambiguous_list_add = classify_skybridge_intent("add bread to the project list", list_context)
    assert ambiguous_list_add.action != "add_item"
    calendar_destination = classify_skybridge_intent("add a meeting to my calendar", list_context)
    contacts_destination = classify_skybridge_intent("add Sarah to my contacts", list_context)
    assert calendar_destination.action != "add_item"
    assert contacts_destination.action != "add_item"
    assert classify_skybridge_intent("create a work list called Projects").list_name == "Projects"
    assert classify_skybridge_intent("show my contacts").domain == "people"
    assert classify_skybridge_intent("find Sarah").domain == "people"
    assert classify_skybridge_intent("what is there to do this week") is None
    assert classify_skybridge_intent("open settings") is None


def test_clock_timezone_prefers_env(monkeypatch):
    monkeypatch.setenv("ZOE_SKYBRIDGE_TIMEZONE", "Australia/Melbourne")
    monkeypatch.setenv("TZ", "UTC")

    assert skybridge_service._default_clock_timezone() == "Australia/Melbourne"


def test_clock_timezone_uses_host_timezone_before_utc(monkeypatch):
    monkeypatch.delenv("ZOE_SKYBRIDGE_TIMEZONE", raising=False)
    monkeypatch.setenv("TZ", "Europe/London")

    assert skybridge_service._default_clock_timezone() == "Europe/London"


def test_clock_timezone_falls_back_to_utc(monkeypatch):
    monkeypatch.delenv("ZOE_SKYBRIDGE_TIMEZONE", raising=False)
    monkeypatch.setattr(skybridge_service, "_host_clock_timezone", lambda: None)

    assert skybridge_service._default_clock_timezone() == "UTC"


@pytest.mark.asyncio
async def test_clock_request_returns_public_live_clock_card():
    result = await resolve_skybridge_request("show me the clock", "guest", db=GuardedGuestDb())

    assert result["handled"] is True
    assert result["intent"]["domain"] == "clock"
    assert result["intent"]["action"] == "show"
    assert result["cards"][0]["component"] == "status"
    props = result["cards"][0]["props"]
    assert props["source"] == "clock_show"
    assert props["timezone"]
    assert props["iso"]
    assert "auth_required" not in result


def test_classify_who_am_i_routes_to_identity_not_a_settings_card():
    # "who am I" and friends must classify to a self-identity people intent so the
    # panel renders an identity card — not fall through (None) to the brain's fuzzy
    # "AI Training / Risk" settings match.
    for phrase in (
        "who am i",
        "who am i signed in as",
        "who's signed in",
        "who is signed in",
        "what's my name",
        "what is my name",
        "am i signed in",
        "who do you think i am",
    ):
        intent = classify_skybridge_intent(phrase)
        assert intent is not None, f"{phrase!r} should be a skybridge intent, not fall to the brain"
        assert intent.domain == "people", phrase
        assert intent.action == "identity", phrase

    # A people SEARCH ("who is Sarah") must NOT be captured by the identity branch.
    who_is = classify_skybridge_intent("who is Sarah")
    assert who_is is None or who_is.action != "identity"

    # An explicit directory ask must yield to the people branch, not be stolen by
    # the leading self-identity phrase.
    in_contacts = classify_skybridge_intent("what's my name in contacts")
    assert in_contacts is not None and in_contacts.domain == "people"
    assert in_contacts.action != "identity"


def test_identity_intent_is_not_forced_behind_signin():
    from skybridge_service import SkybridgeIntent, skybridge_intent_requires_identity

    identity = SkybridgeIntent(domain="people", action="identity")
    assert skybridge_intent_requires_identity(identity) is False
    # A regular people read still requires a signed-in user.
    assert skybridge_intent_requires_identity(SkybridgeIntent(domain="people", action="show")) is True


class _UsersDb(FakeDb):
    def __init__(self, *, user_row=None, **kwargs):
        super().__init__(**kwargs)
        self._user_row = user_row

    async def fetch(self, *args):
        if "FROM users" in str(args[0]):
            return [self._user_row] if self._user_row else []
        return await super().fetch(*args)


@pytest.mark.asyncio
async def test_who_am_i_returns_identity_card_for_signed_in_user():
    db = _UsersDb(user_row={"name": "Jason", "role": "admin"})
    result = await resolve_skybridge_request("who am i", "jason-user-1", db=db)

    assert result["handled"] is True
    assert result["intent"]["domain"] == "people"
    assert result["intent"]["action"] == "identity"
    assert "auth_required" not in result
    content = result["cards"][0]["content"]
    assert content["source"] == "person_profile"
    assert content["person"]["name"] == "Jason"


@pytest.mark.asyncio
async def test_who_am_i_answers_guests_without_signin_wall():
    result = await resolve_skybridge_request("who am i", "guest", db=GuardedGuestDb())

    assert result["handled"] is True
    assert "auth_required" not in result
    content = result["cards"][0]["content"]
    assert content["source"] == "person_profile"
    assert content["person"]["name"] == "Guest"


def test_classify_skybridge_action_requests():
    calendar_context = {"intent": {"domain": "calendar"}, "cards": []}

    add_item = classify_skybridge_intent("Can you add bread to the shopping list")
    assert add_item.domain == "lists"
    assert add_item.action == "add_item"
    assert add_item.item_text == "bread"
    assert add_item.list_type == "shopping"

    create_event = classify_skybridge_intent("Can you add pick up the groceries at 3pm", calendar_context)
    assert create_event.domain == "calendar"
    assert create_event.action == "create_event"
    assert create_event.title == "pick up the groceries"
    assert create_event.target_time == "15:00"

    # No-time add → all-day event with a CLEAN title (was "work to my calendar").
    notime = classify_skybridge_intent("add work to my calendar")
    assert notime.domain == "calendar" and notime.action == "create_event"
    assert notime.title == "work"
    assert notime.all_day is True and notime.target_time == ""
    assert classify_skybridge_intent("put dentist on the calendar").title == "dentist"
    assert classify_skybridge_intent("add a calendar event called Team Sync").title == "Team Sync"
    # must not hijack a list add or a timed calendar add
    assert classify_skybridge_intent("add bread to the shopping list").action != "create_event"
    timed = classify_skybridge_intent("add standup to my calendar at 9am")
    assert timed.action == "create_event" and timed.all_day is False and timed.target_time == "09:00"
    assert timed.title == "standup"

    move_event = classify_skybridge_intent("Can you change my appointment to 9am", calendar_context)
    assert move_event.domain == "calendar"
    assert move_event.action == "update_time"
    assert move_event.target_time == "09:00"

    remember = classify_skybridge_intent("Can you remember that Sarah likes flowers and her birthday is the 1st of may")
    assert remember.domain == "people"
    assert remember.action == "remember_fact"
    assert remember.person_name == "Sarah"
    assert remember.fact_text == "likes flowers"
    assert remember.birthday == "1 May"

    lowercase_remember = classify_skybridge_intent("remember that sarah likes flowers")
    assert lowercase_remember.domain == "people"
    assert lowercase_remember.action == "remember_fact"
    assert lowercase_remember.person_name == "Sarah"

    assert classify_skybridge_intent("remember that my colleague has a car") is None
    assert classify_skybridge_intent("remember that the manager likes coffee") is None


def test_calendar_create_requires_unambiguous_bare_time():
    calendar_context = {"intent": {"domain": "calendar"}, "cards": []}

    assert classify_skybridge_intent("Can you add dentist at 9", calendar_context) is None

    create_event = classify_skybridge_intent("Can you add dentist at 9am", calendar_context)
    assert create_event.domain == "calendar"
    assert create_event.action == "create_event"
    assert create_event.target_time == "09:00"


def test_classify_calendar_date_and_range_requests(monkeypatch):
    _freeze_today(monkeypatch)

    dated = classify_skybridge_intent("show me my calendar on the 17th of June")
    assert dated.domain == "calendar"
    assert dated.start_date == date(2026, 6, 17)
    assert dated.end_date == date(2026, 6, 17)
    assert dated.range_label == "17 June 2026"

    week = classify_skybridge_intent("what is happening this week")
    assert week.domain == "calendar"
    assert week.start_date == date(2026, 6, 11)
    assert week.end_date == date(2026, 6, 18)

    tomorrow = classify_skybridge_intent("show me the weather for tomorrow")
    assert tomorrow.domain == "weather"
    assert tomorrow.action == "forecast"

    next_week = classify_skybridge_intent("show my schedule next week")
    assert next_week.domain == "calendar"
    assert next_week.start_date == date(2026, 6, 15)
    assert next_week.end_date == date(2026, 6, 21)
    assert next_week.range_label == "next week"

    iso = classify_skybridge_intent("show my calendar on 2026-06-17")
    assert iso.domain == "calendar"
    assert iso.start_date == date(2026, 6, 17)
    assert iso.end_date == date(2026, 6, 17)
    assert iso.range_label == "2026-06-17"

    iso_after_count = classify_skybridge_intent("show my 10 events on 2026-06-17")
    assert iso_after_count.domain == "calendar"
    assert iso_after_count.start_date == date(2026, 6, 17)
    assert iso_after_count.end_date == date(2026, 6, 17)
    assert iso_after_count.range_label == "2026-06-17"

    weekend = classify_skybridge_intent("what is happening this weekend")
    assert weekend.domain == "calendar"
    assert weekend.range_label == "today"
    assert weekend.start_date == date(2026, 6, 11)
    assert weekend.end_date == date(2026, 6, 11)


@pytest.mark.asyncio
async def test_calendar_request_returns_real_event_card():
    event = {
        "id": "event-1",
        "user_id": "family-admin",
        "title": "Dentist",
        "start_date": date.today().isoformat(),
        "start_time": "09:00",
        "end_time": "09:30",
        "location": "Clinic",
        "category": "health",
        "visibility": "family",
        "deleted": False,
    }

    result = await resolve_skybridge_request("show my calendar", "family-admin", db=FakeDb(events=[event]))

    assert result["handled"] is True
    assert result["intent"]["domain"] == "calendar"
    card = result["cards"][0]
    assert card["producer"] == "zoe-calendar"
    assert card["content"]["source"] == "calendar_show"
    assert card["content"]["events"][0]["title"] == "Dentist"
    assert "Surface" not in str(card)


@pytest.mark.asyncio
async def test_calendar_update_time_refreshes_visible_calendar_card():
    event = {
        "id": "event-1",
        "user_id": "family-admin",
        "title": "Dentist appointment",
        "start_date": "2026-06-17",
        "start_time": "15:00",
        "end_time": "15:30",
        "category": "health",
        "visibility": "family",
        "deleted": False,
    }
    context = {
        "intent": {"domain": "calendar"},
        "cards": [{"content": {"source": "calendar_show", "start_date": "2026-06-17", "events": [event]}}],
    }
    db = FakeDb(events=[event])

    result = await resolve_skybridge_request("change my appointment to 9am", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "update_time"
    assert result["actions"][0]["type"] == "updated"
    assert result["cards"][0]["content"]["events"][0]["start_time"] == "09:00"
    assert result["skybridge_context"]["intent"]["action"] == "update_time"


@pytest.mark.asyncio
async def test_calendar_update_can_target_visible_event_by_start_time():
    event = {
        "id": "event-1",
        "user_id": "family-admin",
        "title": "School pickup",
        "start_date": "2026-06-17",
        "start_time": "15:00",
        "end_time": "15:30",
        "category": "family",
        "visibility": "family",
        "deleted": False,
    }
    context = {
        "intent": {"domain": "calendar"},
        "cards": [{"content": {"source": "calendar_show", "start_date": "2026-06-17", "events": [event]}}],
    }
    db = FakeDb(events=[event])

    result = await resolve_skybridge_request("move my 3pm to 4pm", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "update_time"
    assert result["cards"][0]["content"]["events"][0]["start_time"] == "16:00"


@pytest.mark.asyncio
async def test_calendar_update_defaults_bare_voice_hour_to_pm():
    event = {
        "id": "event-1",
        "user_id": "family-admin",
        "title": "School pickup",
        "start_date": "2026-06-17",
        "start_time": "15:00",
        "end_time": "15:30",
        "category": "family",
        "visibility": "family",
        "deleted": False,
    }
    context = {
        "intent": {"domain": "calendar"},
        "cards": [{"content": {"source": "calendar_show", "start_date": "2026-06-17", "events": [event]}}],
    }
    db = FakeDb(events=[event])

    result = await resolve_skybridge_request("move my 3pm to 4", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "update_time"
    assert result["cards"][0]["content"]["events"][0]["start_time"] == "16:00"


@pytest.mark.asyncio
async def test_calendar_update_does_not_confirm_family_event_owned_by_someone_else():
    event = {
        "id": "event-1",
        "user_id": "other-family-user",
        "title": "School pickup",
        "start_date": "2026-06-17",
        "start_time": "15:00",
        "end_time": "15:30",
        "category": "family",
        "visibility": "family",
        "deleted": False,
    }
    context = {
        "intent": {"domain": "calendar"},
        "cards": [{"content": {"source": "calendar_show", "start_date": "2026-06-17", "events": [event]}}],
    }
    db = FakeDb(events=[event])

    result = await resolve_skybridge_request("move my 3pm to 4pm", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert result["actions"] == []
    assert "cannot move" in result["spoken_summary"]
    assert db.events[0]["start_time"] == "15:00"


@pytest.mark.asyncio
async def test_calendar_context_add_event_refreshes_calendar_card(monkeypatch):
    _freeze_today(monkeypatch)
    context = {"intent": {"domain": "calendar"}, "cards": [{"content": {"source": "calendar_show", "start_date": "2026-06-17", "events": []}}]}
    db = FakeDb(events=[])

    result = await resolve_skybridge_request("add pick up the groceries at 3pm", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "create_event"
    event = result["cards"][0]["content"]["events"][0]
    assert event["title"] == "pick up the groceries"
    assert event["start_date"] == "2026-06-17"
    assert event["start_time"] == "15:00"
    assert event["end_time"] is None


@pytest.mark.asyncio
async def test_calendar_empty_state_still_returns_data_card():
    result = await resolve_skybridge_request("show my schedule today", "family-admin", db=FakeDb(events=[]))

    assert result["handled"] is True
    card = result["cards"][0]
    assert card["content"]["source"] == "calendar_show"
    assert card["content"]["events"] == []
    assert "0 events" in result["spoken_summary"]


@pytest.mark.asyncio
async def test_calendar_explicit_date_queries_requested_day(monkeypatch):
    _freeze_today(monkeypatch)

    db = FakeDb(events=[])
    result = await resolve_skybridge_request("show my calendar on the 17th of June", "family-admin", db=db)

    assert result["handled"] is True
    assert result["intent"]["range"] == "17 June 2026"
    assert db.fetch_args[2] == "2026-06-17"
    assert db.fetch_args[3] == "2026-06-17"
    content = result["cards"][0]["content"]
    assert content["qualifier"] == "17 June 2026"
    assert content["date"] == "2026-06-17"
    assert content["start_date"] == "2026-06-17"
    assert content["end_date"] == "2026-06-17"


@pytest.mark.asyncio
async def test_calendar_happening_this_week_queries_range(monkeypatch):
    _freeze_today(monkeypatch)

    db = FakeDb(events=[])
    result = await resolve_skybridge_request("what is happening this week", "family-admin", db=db)

    assert result["handled"] is True
    assert result["intent"]["range"] == "this week"
    assert db.fetch_args[2] == "2026-06-11"
    assert db.fetch_args[3] == (date(2026, 6, 11) + timedelta(days=7)).isoformat()
    content = result["cards"][0]["content"]
    assert content["start_date"] == "2026-06-11"
    assert content["end_date"] == "2026-06-18"


@pytest.mark.asyncio
async def test_guest_calendar_request_does_not_fetch_family_events():
    result = await resolve_skybridge_request("show my calendar", "guest", db=GuardedGuestDb())

    assert result["handled"] is True
    assert result["auth_required"] is True
    assert result["actions"][0]["type"] == "auth_required"
    assert result["cards"][0]["component"] == "auth_challenge"
    # auth_challenge renders a client-built profile picker (hideActions); the card
    # itself carries no props.actions and conforms to the component contract.
    validate_component(result["cards"][0])


@pytest.mark.asyncio
async def test_lists_request_returns_real_list_items():
    list_row = {
        "id": "list-1",
        "user_id": "family-admin",
        "name": "Groceries",
        "list_type": "shopping",
        "description": "Weekly shop",
        "visibility": "family",
    }
    item = {
        "id": "item-1",
        "list_id": "list-1",
        "text": "Milk",
        "completed": False,
        "priority": "high",
        "category": "dairy",
        "quantity": "2L",
    }

    result = await resolve_skybridge_request(
        "show my shopping list",
        "family-admin",
        db=FakeDb(lists=[list_row], items_by_list={"list-1": [item]}),
    )

    assert result["handled"] is True
    assert result["intent"] == {"domain": "lists", "action": "show", "list_type": "shopping"}
    card = result["cards"][0]
    assert card["producer"] == "zoe-shopping"
    assert card["content"]["source"] == "list_show"
    assert card["content"]["items"][0]["text"] == "Milk"
    assert card["content"]["open_count"] == 1
    assert "Surface" not in str(card)


@pytest.mark.asyncio
async def test_list_complete_item_ticks_off_and_refreshes_card():
    list_row = {
        "id": "list-1",
        "user_id": "family-admin",
        "name": "Groceries",
        "list_type": "shopping",
        "description": "Weekly shop",
        "visibility": "family",
    }
    item = {"id": "item-1", "list_id": "list-1", "text": "Milk", "completed": False}
    db = FakeDb(lists=[list_row], items_by_list={"list-1": [item]})

    result = await resolve_skybridge_request(
        "check off milk on the shopping list", "family-admin", db=db
    )

    assert result["handled"] is True
    assert result["intent"]["action"] == "complete_item"
    assert result["intent"]["completed"] is True
    assert item["completed"] is True  # persisted
    assert any("UPDATE list_items SET completed" in str(call[0]) for call in db.executed)
    card = result["cards"][0]
    assert card["content"]["source"] == "list_show"
    assert card["content"]["items"][0]["completed"] is True
    assert "ticked off" in result["spoken_summary"].lower()

    # And the reverse: tapping a done item restores it.
    restore = await resolve_skybridge_request(
        "uncheck milk on the shopping list", "family-admin", db=db
    )
    assert restore["intent"]["completed"] is False
    assert item["completed"] is False


@pytest.mark.asyncio
async def test_shopping_list_question_returns_list_card_not_calendar():
    list_row = {
        "id": "list-1",
        "user_id": "family-admin",
        "name": "Groceries",
        "list_type": "shopping",
        "description": "Weekly shop",
        "visibility": "family",
    }
    item = {
        "id": "item-1",
        "list_id": "list-1",
        "text": "Milk",
        "completed": False,
    }

    result = await resolve_skybridge_request(
        "What's on my shopping list?",
        "family-admin",
        db=FakeDb(lists=[list_row], items_by_list={"list-1": [item]}),
    )

    assert result["handled"] is True
    assert result["intent"]["domain"] == "lists"
    card = result["cards"][0]
    assert card["content"]["source"] == "list_show"
    assert card["content"]["items"][0]["text"] == "Milk"
    assert card["producer"] != "zoe-calendar"


@pytest.mark.asyncio
async def test_list_add_item_persists_and_refreshes_list_card():
    list_row = {
        "id": "list-1",
        "user_id": "family-admin",
        "name": "Groceries",
        "list_type": "shopping",
        "description": "Weekly shop",
        "visibility": "family",
    }
    db = FakeDb(lists=[list_row], items_by_list={"list-1": []})
    context = {"intent": {"domain": "lists"}, "cards": [{"content": {"source": "list_show", "list_id": "list-1", "list_type": "shopping"}}]}

    result = await resolve_skybridge_request("add bread to the shopping list", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "add_item"
    assert result["actions"][0]["domain"] == "lists"
    assert result["cards"][0]["content"]["items"][0]["text"] == "bread"
    assert result["cards"][0]["content"]["items"][0]["recent"] is True
    assert result["cards"][0]["content"]["recent_item_id"] == result["intent"]["item_id"]
    assert result["skybridge_context"]["cards"][0]["content"]["items"][0]["text"] == "bread"


@pytest.mark.asyncio
async def test_list_add_replay_within_window_skips_insert():
    """Retry idempotency: the voice daemon (or an HTTP retry) can re-submit an
    add the server already executed. The replay must not insert a second row —
    the reply still reads as a success and highlights the ORIGINAL item."""
    list_row = {
        "id": "list-1",
        "user_id": "family-admin",
        "name": "Groceries",
        "list_type": "shopping",
        "description": "Weekly shop",
        "visibility": "family",
    }
    existing = {
        "id": "item-original",
        "list_id": "list-1",
        "text": "bread",
        "priority": "normal",
        "category": "",
        "quantity": "",
        "completed": False,
    }
    db = FakeDb(lists=[list_row], items_by_list={"list-1": [existing]})
    db.recent_list_item_dup = {"id": "item-original"}
    context = {"intent": {"domain": "lists"}, "cards": [{"content": {"source": "list_show", "list_id": "list-1", "list_type": "shopping"}}]}

    result = await resolve_skybridge_request("add bread to the shopping list", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert not any("INSERT INTO list_items" in str(call[0]) for call in db.executed)
    assert result["intent"]["item_id"] == "item-original"
    assert "Added bread" in result["spoken_summary"]


@pytest.mark.asyncio
async def test_contextual_list_add_uses_visible_list_card():
    list_row = {
        "id": "work-list",
        "user_id": "jason",
        "name": "Work",
        "list_type": "work",
        "description": "Work tasks",
        "visibility": "personal",
    }
    db = FakeDb(lists=[list_row], items_by_list={"work-list": []})
    context = {
        "intent": {"domain": "lists", "action": "show", "list_type": "work"},
        "cards": [{"content": {"source": "list_show", "list_id": "work-list", "list_type": "work"}}],
    }

    result = await resolve_skybridge_request("add send proposal", "jason", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "add_item"
    assert result["intent"]["list_type"] == "work"
    assert result["cards"][0]["content"]["list_name"] == "Work"
    assert result["cards"][0]["content"]["items"][0]["text"] == "send proposal"
    assert result["cards"][0]["content"]["items"][0]["recent"] is True


@pytest.mark.asyncio
async def test_list_add_item_is_visible_when_list_is_already_long():
    list_row = {
        "id": "list-1",
        "user_id": "family-admin",
        "name": "Groceries",
        "list_type": "shopping",
        "description": "Weekly shop",
        "visibility": "family",
    }
    existing_items = [
        {"id": f"old-{index}", "list_id": "list-1", "text": f"old item {index}", "completed": False}
        for index in range(30)
    ]
    db = FakeDb(lists=[list_row], items_by_list={"list-1": existing_items})

    result = await resolve_skybridge_request("add bread to the shopping list", "family-admin", db=db)

    first_item = result["cards"][0]["content"]["items"][0]
    assert first_item["text"] == "bread"
    assert first_item["recent"] is True
    assert first_item["id"] == result["intent"]["item_id"]


@pytest.mark.asyncio
async def test_list_add_uses_existing_same_type_list_with_custom_name():
    rows = [
        {
            "id": "groceries-list",
            "user_id": "jason",
            "name": "Groceries",
            "list_type": "shopping",
            "visibility": "family",
            "deleted": 0,
            "updated_at": 10,
        },
    ]
    db = FakeDb(lists=rows, items_by_list={"groceries-list": []})

    result = await resolve_skybridge_request("add apples to the shopping list", "jason", db=db)

    assert result["handled"] is True
    assert result["actions"][0]["list_id"] == "groceries-list"
    assert db.items_by_list["groceries-list"][0]["text"] == "apples"
    assert not any(item["name"] == "Shopping" and item["id"] != "groceries-list" for item in db.lists)


@pytest.mark.asyncio
async def test_list_add_prefers_speaking_users_default_list_over_shared_lists():
    rows = [
        {
            "id": "guest-shopping",
            "user_id": "guest",
            "name": "Shopping",
            "list_type": "shopping",
            "visibility": "family",
            "deleted": 0,
        },
        {
            "id": "jason-shopping",
            "user_id": "jason",
            "name": "Shopping",
            "list_type": "shopping",
            "visibility": "family",
            "deleted": 0,
        },
    ]
    db = FakeDb(lists=rows, items_by_list={"guest-shopping": [], "jason-shopping": []})

    result = await resolve_skybridge_request("add mangoes to the shopping list", "jason", db=db)

    assert result["handled"] is True
    assert result["actions"][0]["list_id"] == "jason-shopping"
    assert db.items_by_list["jason-shopping"][0]["text"] == "mangoes"
    assert db.items_by_list["guest-shopping"] == []


@pytest.mark.asyncio
async def test_lists_request_seeds_default_lists_and_returns_switcher_actions():
    db = FakeDb(lists=[], items_by_list={})

    result = await resolve_skybridge_request("show my lists", "jason", db=db)

    assert result["handled"] is True
    names = {item["name"] for item in result["cards"][0]["content"]["lists"]}
    assert {"Shopping", "Work", "Personal"}.issubset(names)
    actions = result["cards"][0]["content"]["actions"]
    assert any(action["label"] == "New list" for action in actions)
    assert db.commits >= 1


@pytest.mark.asyncio
async def test_new_list_without_name_returns_action_form_prompt():
    result = await resolve_skybridge_request("new list", "jason", db=FakeDb())

    assert result["handled"] is True
    assert result["intent"]["action"] == "create_list"
    assert result["cards"][0]["component"] == "action_form"
    assert result["cards"][0]["props"]["actions"] == []
    assert "What should I name" in result["spoken_summary"]


@pytest.mark.asyncio
async def test_new_named_work_list_is_created_and_displayed():
    db = FakeDb(lists=[], items_by_list={})

    result = await resolve_skybridge_request("create a work list called Projects", "jason", db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "create_list"
    assert result["intent"]["list_type"] == "work"
    assert result["intent"]["list_name"] == "Projects"
    assert any(item["name"] == "Projects" and item["list_type"] == "work" for item in db.lists)
    assert result["cards"][0]["content"]["list_name"] == "Projects"
    assert result["spoken_summary"] == "Created Projects."
    assert result["actions"][0]["type"] == "created"


@pytest.mark.asyncio
async def test_create_existing_list_reports_existing_not_created():
    db = FakeDb(
        lists=[
            {
                "id": "existing-projects",
                "user_id": "jason",
                "name": "Projects",
                "list_type": "work",
                "visibility": "personal",
                "deleted": 0,
            }
        ],
        items_by_list={"existing-projects": []},
    )

    result = await resolve_skybridge_request("create a work list called Projects", "jason", db=db)

    assert result["handled"] is True
    assert result["spoken_summary"] == "You already have a Projects list."
    assert result["actions"][0]["type"] == "existing"
    assert len([item for item in db.lists if item["name"] == "Projects"]) == 1


@pytest.mark.asyncio
async def test_new_list_prompt_accepts_bare_name_followup():
    context = {"intent": {"domain": "lists", "action": "create_list", "list_type": "personal"}, "cards": []}
    db = FakeDb(lists=[], items_by_list={})

    result = await resolve_skybridge_request("Camping", "jason", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "create_list"
    assert result["intent"]["list_name"] == "Camping"
    assert result["intent"]["list_type"] == "personal"
    assert any(item["name"] == "Camping" and item["list_type"] == "personal" for item in db.lists)


@pytest.mark.asyncio
async def test_list_add_item_rejects_stale_context_list_id():
    context = {
        "intent": {"domain": "lists"},
        "cards": [{"content": {"source": "list_show", "list_id": "missing-list", "list_type": "shopping"}}],
    }
    db = FakeDb(lists=[], items_by_list={})

    result = await resolve_skybridge_request(
        "add bread to the shopping list",
        "family-admin",
        context=context,
        db=db,
    )

    assert result["handled"] is True
    assert result["actions"] == []
    assert "could not find" in result["spoken_summary"]
    assert not db.items_by_list


@pytest.mark.asyncio
async def test_lists_request_returns_overview_for_multiple_lists():
    rows = [
        {"id": "list-1", "name": "Groceries", "list_type": "shopping", "visibility": "family"},
        {"id": "list-2", "name": "Hardware", "list_type": "shopping", "visibility": "family"},
    ]

    db = FakeDb(
        lists=rows,
        items_by_list={
            "list-1": [{"id": "item-1", "list_id": "list-1", "text": "Milk", "completed": False}],
            "list-2": [{"id": "item-2", "list_id": "list-2", "text": "Tape", "completed": True}],
        },
    )

    result = await resolve_skybridge_request(
        "show my shopping lists",
        "family-admin",
        db=db,
    )

    card = result["cards"][0]
    assert card["content"]["source"] == "list_show"
    assert card["content"]["items"] == []
    assert card["content"]["lists"][0]["items"][0]["text"] == "Milk"
    assert card["content"]["lists"][0]["open_count"] == 1
    assert card["content"]["lists"][1]["completed_count"] == 1
    assert card["content"]["actions"][-1]["label"] == "New list"
    assert db.list_item_fetch_count == 1


@pytest.mark.asyncio
async def test_guest_lists_request_does_not_fetch_private_lists():
    result = await resolve_skybridge_request("show my shopping list", "guest", db=GuardedGuestDb())

    assert result["handled"] is True
    assert result["auth_required"] is True
    assert result["actions"][0]["domain"] == "lists"
    assert result["cards"][0]["component"] == "auth_challenge"
    # auth_challenge renders a client-built profile picker (hideActions); the card
    # itself carries no props.actions and conforms to the component contract.
    validate_component(result["cards"][0])


@pytest.mark.asyncio
async def test_people_request_returns_directory_card():
    person = {
        "id": "person-1",
        "user_id": "family-admin",
        "name": "Sarah Smith",
        "relationship": "Friend",
        "circle": "inner",
        "context": "personal",
        "email": "sarah@example.com",
        "health_score": 0.82,
        "visibility": "family",
    }

    result = await resolve_skybridge_request("show my contacts", "family-admin", db=FakeDb(people=[person]))

    assert result["handled"] is True
    assert result["intent"]["domain"] == "people"
    card = result["cards"][0]
    assert card["producer"] == "zoe-people"
    assert card["content"]["source"] == "people_directory"
    assert card["content"]["people"][0]["name"] == "Sarah Smith"
    assert "Surface" not in str(card)


@pytest.mark.asyncio
async def test_people_search_returns_profile_card_for_exact_match():
    person = {
        "id": "person-1",
        "user_id": "family-admin",
        "name": "Sarah",
        "relationship": "Friend",
        "circle": "inner",
        "context": "personal",
        "notes": "Met through school.",
        "visibility": "family",
    }

    result = await resolve_skybridge_request("find Sarah", "family-admin", db=FakeDb(people=[person]))

    card = result["cards"][0]
    assert result["intent"]["query"] == "sarah"
    assert card["producer"] == "zoe-people"
    assert card["content"]["source"] == "person_profile"
    assert card["content"]["person"]["name"] == "Sarah"


@pytest.mark.asyncio
async def test_guest_people_request_does_not_fetch_private_people():
    result = await resolve_skybridge_request("show my contacts", "guest", db=GuardedGuestDb())

    assert result["handled"] is True
    assert result["auth_required"] is True
    assert result["actions"][0]["domain"] == "people"
    assert result["cards"][0]["component"] == "auth_challenge"
    # auth_challenge renders a client-built profile picker (hideActions); the card
    # itself carries no props.actions and conforms to the component contract.
    validate_component(result["cards"][0])


@pytest.mark.asyncio
async def test_people_singular_contact_request_returns_directory_not_search():
    person = {
        "id": "person-1",
        "user_id": "family-admin",
        "name": "Sarah Smith",
        "relationship": "Friend",
        "visibility": "family",
    }

    intent = classify_skybridge_intent("show my contact")
    assert intent.domain == "people"
    assert intent.query == ""

    result = await resolve_skybridge_request("show my contact", "family-admin", db=FakeDb(people=[person]))

    assert result["handled"] is True
    assert result["intent"]["query"] == ""
    assert result["cards"][0]["content"]["source"] == "people_directory"


@pytest.mark.asyncio
async def test_people_remember_fact_updates_profile_card_and_memory(monkeypatch):
    person = {
        "id": "person-1",
        "user_id": "family-admin",
        "name": "Sarah",
        "relationship": "Friend",
        "circle": "inner",
        "context": "personal",
        "notes": "",
        "visibility": "family",
    }
    remembered = {}

    async def fake_memory(fact, **kwargs):
        remembered["fact"] = fact
        remembered.update(kwargs)
        return True

    monkeypatch.setattr(skybridge_service, "_store_skybridge_memory_fact", fake_memory)
    result = await resolve_skybridge_request(
        "remember that Sarah likes flowers and her birthday is the 1st of may",
        "family-admin",
        db=FakeDb(people=[person]),
    )

    assert result["handled"] is True
    assert result["intent"]["action"] == "remember_fact"
    profile = result["cards"][0]["content"]["person"]
    assert profile["name"] == "Sarah"
    assert profile["birthday"] == "1 May"
    assert "Likes flowers." in profile["notes"]
    assert remembered["fact"] == "Sarah likes flowers. Sarah's birthday is 1 May"
    assert remembered["person_id"] == "person-1"


@pytest.mark.asyncio
async def test_people_remember_fact_creates_visible_profile_card(monkeypatch):
    remembered = {}

    async def fake_memory(fact, **kwargs):
        remembered["fact"] = fact
        remembered.update(kwargs)
        return True

    db = FakeDb(people=[])
    monkeypatch.setattr(skybridge_service, "_store_skybridge_memory_fact", fake_memory)

    result = await resolve_skybridge_request(
        "remember that sarah likes flowers and her birthday is the 1st of may",
        "family-admin",
        db=db,
    )

    assert result["handled"] is True
    profile = result["cards"][0]["content"]["person"]
    assert profile["name"] == "Sarah"
    assert profile["birthday"] == "1 May"
    assert "Likes flowers." in profile["notes"]
    assert db.people[0]["is_partial"] is False
    assert remembered["person_id"] == db.people[0]["id"]


@pytest.mark.asyncio
async def test_people_remember_fact_leaves_profile_unchanged_when_memory_rejects(monkeypatch):
    person = {
        "id": "person-1",
        "user_id": "family-admin",
        "name": "Sarah",
        "relationship": "Friend",
        "circle": "inner",
        "context": "personal",
        "notes": "",
        "visibility": "family",
    }

    async def fake_memory(_fact, **_kwargs):
        return False

    db = FakeDb(people=[person])
    monkeypatch.setattr(skybridge_service, "_store_skybridge_memory_fact", fake_memory)

    result = await resolve_skybridge_request(
        "remember that Sarah likes flowers",
        "family-admin",
        db=db,
    )

    assert result["handled"] is True
    assert result["actions"] == []
    assert "not saved" in result["cards"][0]["props"]["title"].lower()
    assert db.people[0].get("notes", "") == ""


@pytest.mark.asyncio
async def test_people_remember_fact_does_not_update_family_profile_owned_by_someone_else(monkeypatch):
    person = {
        "id": "person-1",
        "user_id": "other-family-user",
        "name": "Sarah",
        "relationship": "Friend",
        "circle": "inner",
        "context": "personal",
        "notes": "",
        "visibility": "family",
    }
    remembered = {}

    async def fake_memory(fact, **kwargs):
        remembered["fact"] = fact
        remembered.update(kwargs)
        return True

    db = FakeDb(people=[person])
    monkeypatch.setattr(skybridge_service, "_store_skybridge_memory_fact", fake_memory)

    result = await resolve_skybridge_request(
        "remember that Sarah likes flowers",
        "family-admin",
        db=db,
    )

    assert result["handled"] is True
    assert result["actions"] == []
    assert "cannot update" in result["spoken_summary"]
    assert remembered == {}
    assert db.people[0].get("notes", "") == ""


@pytest.mark.asyncio
async def test_weather_current_request_returns_current_card(monkeypatch):
    async def fake_default(_db):
        return {"latitude": -28.7, "longitude": 114.6, "city": "Geraldton", "country": "AU"}

    async def fake_current(_lat, _lon, city, country):
        return {
            "temp": 23.4,
            "feels_like": 22.9,
            "humidity": 55,
            "wind_speed": 4.2,
            "description": "clear sky",
            "city": city,
            "country": country,
        }

    async def fake_forecast(_lat, _lon):
        return {"daily": [{"day": "2026-06-11", "high": 24, "low": 14, "description": "clear"}]}

    monkeypatch.setattr(skybridge_service, "_get_system_default_location", fake_default)
    monkeypatch.setattr(skybridge_service, "_get_current", fake_current)
    monkeypatch.setattr(skybridge_service, "_get_forecast", fake_forecast)

    result = await resolve_skybridge_request("show me the weather", "family-admin", db=FakeDb())

    assert result["handled"] is True
    assert result["intent"] == {"domain": "weather", "action": "current"}
    card = result["cards"][0]
    assert card["producer"] == "zoe-weather"
    assert card["content"]["source"] == "weather_current"
    assert card["content"]["current"]["temp"] == 23.4
    assert card["content"]["forecast"]["daily"][0]["high"] == 24


@pytest.mark.asyncio
async def test_weather_forecast_request_returns_forecast_card(monkeypatch):
    async def fake_default(_db):
        return {"latitude": -28.7, "longitude": 114.6, "city": "Geraldton", "country": "AU"}

    async def fake_current(_lat, _lon, city, country):
        return {"temp": 20, "description": "cloudy", "city": city, "country": country}

    async def fake_forecast(_lat, _lon):
        return {"daily": [{"day": "2026-06-11", "high": 21, "low": 12, "description": "cloudy"}]}

    monkeypatch.setattr(skybridge_service, "_get_system_default_location", fake_default)
    monkeypatch.setattr(skybridge_service, "_get_current", fake_current)
    monkeypatch.setattr(skybridge_service, "_get_forecast", fake_forecast)

    result = await resolve_skybridge_request("show weather forecast", "family-admin", db=FakeDb())

    card = result["cards"][0]
    assert result["intent"] == {"domain": "weather", "action": "forecast"}
    assert card["content"]["source"] == "weather_forecast"
    assert card["content"]["forecast"]["daily"][0]["description"] == "cloudy"


# ---------------------------------------------------------------------------
# Task A: list remove by voice
# ---------------------------------------------------------------------------


def test_classify_list_remove_requests():
    explicit = classify_skybridge_intent("take milk off the shopping list")
    assert explicit.domain == "lists"
    assert explicit.action == "remove_item"
    assert explicit.item_text == "milk"
    assert explicit.list_type == "shopping"

    delete_phrase = classify_skybridge_intent("remove bread from my grocery list")
    assert delete_phrase.action == "remove_item"
    assert delete_phrase.item_text == "bread"
    assert delete_phrase.list_type == "shopping"

    work = classify_skybridge_intent("delete proposal from the work list")
    assert work.action == "remove_item"
    assert work.list_type == "work"

    list_context = {"intent": {"domain": "lists", "action": "show", "list_type": "shopping"}, "cards": []}
    contextual = classify_skybridge_intent("remove eggs", list_context)
    assert contextual.action == "remove_item"
    assert contextual.item_text == "eggs"

    # Adding still wins over removing when the verb is "add".
    assert classify_skybridge_intent("add bread to the shopping list").action == "add_item"


@pytest.mark.asyncio
async def test_list_remove_item_removes_and_refreshes_card():
    list_row = {
        "id": "list-1",
        "user_id": "family-admin",
        "name": "Groceries",
        "list_type": "shopping",
        "description": "Weekly shop",
        "visibility": "family",
    }
    items = [
        {"id": "item-1", "list_id": "list-1", "text": "Milk", "completed": False},
        {"id": "item-2", "list_id": "list-1", "text": "Bread", "completed": False},
    ]
    db = FakeDb(lists=[list_row], items_by_list={"list-1": items})
    context = {"intent": {"domain": "lists"}, "cards": [{"content": {"source": "list_show", "list_id": "list-1", "list_type": "shopping"}}]}

    result = await resolve_skybridge_request("take milk off the shopping list", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "remove_item"
    assert result["actions"][0]["type"] == "deleted"
    assert result["actions"][0]["id"] == "item-1"
    # Authoritative re-read: removed item is gone, the other remains.
    texts = [item["text"] for item in result["cards"][0]["content"]["items"]]
    assert "Milk" not in texts
    assert "Bread" in texts
    assert "milk" in result["spoken_summary"].lower()


@pytest.mark.asyncio
async def test_list_remove_item_ambiguous_returns_confirmation():
    list_row = {
        "id": "list-1",
        "user_id": "family-admin",
        "name": "Groceries",
        "list_type": "shopping",
        "description": "Weekly shop",
        "visibility": "family",
    }
    items = [
        {"id": "item-1", "list_id": "list-1", "text": "almond milk", "completed": False},
        {"id": "item-2", "list_id": "list-1", "text": "oat milk", "completed": False},
    ]
    db = FakeDb(lists=[list_row], items_by_list={"list-1": items})
    context = {"intent": {"domain": "lists"}, "cards": [{"content": {"source": "list_show", "list_id": "list-1", "list_type": "shopping"}}]}

    result = await resolve_skybridge_request("take milk off the shopping list", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"].get("status") == "ambiguous"
    # No mutation should have run: both items survive.
    assert db.items_by_list["list-1"] == items
    assert result["actions"] == []
    # A confirmation/status card is surfaced before the list card.
    assert result["cards"][0]["props"]["title"] == "Which item should I remove?"


@pytest.mark.asyncio
async def test_list_remove_item_missing_target_returns_confirmation():
    list_row = {
        "id": "list-1",
        "user_id": "family-admin",
        "name": "Groceries",
        "list_type": "shopping",
        "description": "Weekly shop",
        "visibility": "family",
    }
    items = [{"id": "item-1", "list_id": "list-1", "text": "Bread", "completed": False}]
    db = FakeDb(lists=[list_row], items_by_list={"list-1": items})
    context = {"intent": {"domain": "lists"}, "cards": [{"content": {"source": "list_show", "list_id": "list-1", "list_type": "shopping"}}]}

    result = await resolve_skybridge_request("take caviar off the shopping list", "family-admin", context=context, db=db)

    # Zero matches must be distinguishable from a multi-match ambiguity.
    assert result["intent"].get("status") == "not_found"
    assert result["actions"] == []
    assert db.items_by_list["list-1"] == items


# ---------------------------------------------------------------------------
# Task B: readback enumeration in spoken_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_show_spoken_summary_enumerates_items():
    list_row = {
        "id": "list-1",
        "user_id": "family-admin",
        "name": "Shopping",
        "list_type": "shopping",
        "description": "Weekly shop",
        "visibility": "family",
    }
    items = [
        {"id": "i1", "list_id": "list-1", "text": "bread", "completed": False},
        {"id": "i2", "list_id": "list-1", "text": "milk", "completed": False},
        {"id": "i3", "list_id": "list-1", "text": "eggs", "completed": False},
    ]
    db = FakeDb(lists=[list_row], items_by_list={"list-1": items})

    result = await resolve_skybridge_request("show my shopping list", "family-admin", db=db)

    spoken = result["spoken_summary"]
    assert "bread" in spoken
    assert "milk" in spoken
    assert "eggs" in spoken
    # Oxford-free 'and' before the last item, and a spoken count.
    assert "bread, milk and eggs" in spoken
    assert "three things" in spoken


def test_enumerate_items_caps_with_and_more():
    items = [{"text": name} for name in ["a", "b", "c", "d", "e", "f", "g"]]
    spoken = skybridge_service._enumerate_items_for_speech("Shopping", items, cap=5)
    assert "...and 2 more" in spoken
    assert "seven things" in spoken


# ---------------------------------------------------------------------------
# Task C: tap-to-edit opens the existing editor cards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_edit_opens_item_editor_card():
    list_row = {
        "id": "list-1",
        "user_id": "family-admin",
        "name": "Groceries",
        "list_type": "shopping",
        "description": "Weekly shop",
        "visibility": "family",
    }
    items = [{"id": "item-1", "list_id": "list-1", "text": "Bread", "completed": False, "quantity": "1"}]
    db = FakeDb(lists=[list_row], items_by_list={"list-1": items})
    context = {"intent": {"domain": "lists"}, "cards": [{"content": {"source": "list_show", "list_id": "list-1", "list_type": "shopping"}}]}

    result = await resolve_skybridge_request("edit bread on the shopping list", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "edit_item"
    card = result["cards"][0]
    assert card["card_type"] == "action_form"
    assert card["content"]["form_id"] == "shopping_item_editor"
    assert card["content"]["item_id"] == "item-1"
    # The editor's remove action routes back through the existing remove_item utterance.
    queries = [a.get("query", "") for a in card["content"]["actions"]]
    assert any("take Bread off the shopping list" == q for q in queries)
    # No mutation happened just from opening the editor.
    assert db.items_by_list["list-1"] == items


@pytest.mark.asyncio
async def test_calendar_edit_opens_event_editor_card():
    event = {
        "id": "event-1",
        "user_id": "family-admin",
        "title": "Dentist",
        "start_date": date.today().isoformat(),
        "start_time": "15:00",
        "end_time": "15:30",
        "location": "Clinic",
        "category": "health",
        "visibility": "family",
        "deleted": False,
    }
    db = FakeDb(events=[event])
    context = {
        "intent": {"domain": "calendar", "action": "show"},
        "cards": [{"content": {"source": "calendar_show", "start_date": date.today().isoformat(), "events": [event]}}],
    }

    result = await resolve_skybridge_request("edit Dentist at 15:00", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "edit_event"
    card = result["cards"][0]
    assert card["card_type"] == "action_form"
    assert card["content"]["form_id"] == "calendar_event_editor"
    assert card["content"]["event_id"] == "event-1"
    assert card["content"]["title"] == "Edit Calendar Event"
    queries = [a.get("query", "") for a in card["content"]["actions"]]
    assert any("delete Dentist from my calendar" == q for q in queries)


@pytest.mark.asyncio
async def test_calendar_delete_event_removes_and_refreshes():
    event = {
        "id": "event-1",
        "user_id": "family-admin",
        "title": "Dentist",
        "start_date": date.today().isoformat(),
        "start_time": "15:00",
        "end_time": "15:30",
        "visibility": "family",
        "deleted": False,
    }
    db = FakeDb(events=[event])
    context = {
        "intent": {"domain": "calendar", "action": "show"},
        "cards": [{"content": {"source": "calendar_show", "start_date": date.today().isoformat(), "events": [event]}}],
    }

    result = await resolve_skybridge_request("delete Dentist from my calendar", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "delete_event"
    assert result["actions"][0]["type"] == "deleted"
    # Authoritative re-read: the event is gone from the refreshed calendar card.
    assert result["cards"][0]["content"]["events"] == []


@pytest.mark.asyncio
async def test_calendar_delete_all_day_disambiguates_by_date():
    """All-day events carry no time, so the tap query disambiguates by ISO date
    ('delete Trip on 2026-... from my calendar'). The scorer matches that date
    against start_date, so the correct same-title all-day event is deleted even
    when another all-day event shares the title on a different day."""
    wed = {
        "id": "trip-wed", "user_id": "family-admin", "title": "Trip",
        "start_date": "2026-06-24", "start_time": None, "all_day": True,
        "category": "bucket", "visibility": "family", "deleted": False,
    }
    fri = {
        "id": "trip-fri", "user_id": "family-admin", "title": "Trip",
        "start_date": "2026-06-26", "start_time": None, "all_day": True,
        "category": "bucket", "visibility": "family", "deleted": False,
    }
    db = FakeDb(events=[wed, fri])
    # A multi-day calendar card holds BOTH same-title all-day events in context.
    context = {
        "intent": {"domain": "calendar", "action": "show"},
        "cards": [{"content": {"source": "calendar_show", "start_date": "2026-06-24",
                               "events": [wed, fri]}}],
    }

    result = await resolve_skybridge_request(
        "delete Trip on 2026-06-26 from my calendar", "family-admin", context=context, db=db
    )

    assert result["handled"] is True
    assert result["intent"]["action"] == "delete_event"
    # The Friday instance was deleted, not the ambiguous Wednesday one.
    assert result["intent"]["event_id"] == "trip-fri"


def test_score_event_for_target_iso_date_does_not_leak_as_time():
    """The ISO date is stripped before the clock pass, so a December date's '12'
    can't be misread as noon and unfairly beat the tapped all-day event."""
    from skybridge_service import _score_event_for_target

    all_day = {"title": "Trip", "start_date": "2026-12-09", "start_time": None, "all_day": True}
    noon = {"title": "Trip", "start_date": "2026-12-09", "start_time": "12:00", "all_day": False}
    target = "Trip on 2026-12-09"
    # No spurious +4 for the noon event: both score identically (date + title only).
    assert _score_event_for_target(all_day, target) == _score_event_for_target(noon, target)


def test_convergence_gate_is_nonfatal_and_nonmutating():
    """Increment 2: the validation gate logs divergence but never raises, drops, or
    mutates a card (so it can't break the live panel)."""
    from skybridge_service import _card_as_component, _validate_cards_for_convergence

    cards = [
        {"component": "status", "props": {"title": "ok"}},                      # conforms
        {"component": "auth_challenge", "props": {"actions": [{"label": "Jason", "user_id": "u1"}]}},  # diverges
        {"card_type": "generic", "content": {"title": "envelope"}},             # card_service envelope, conforms
        "not-a-dict",                                                           # garbage
    ]
    import copy
    snapshot = copy.deepcopy(cards)
    _validate_cards_for_convergence(cards)  # must not raise
    assert cards == snapshot                # must not mutate/drop

    # _card_as_component maps both producer shapes to the canonical component
    assert _card_as_component({"component": "status", "props": {"x": 1}}) == {"component": "status", "props": {"x": 1}}
    assert _card_as_component({"card_type": "generic", "content": {"title": "t"}}) == {"component": "generic", "props": {"title": "t"}}


def test_convergence_gate_logs_divergence(caplog):
    """Increment 2 — the observability side: a non-conforming card (action lacks
    query/intent/route) must actually emit the measurement log, not just be silently
    tolerated. This is what lets us track the producers down to zero divergence."""
    import logging
    from skybridge_service import _validate_cards_for_convergence

    diverging = [{"component": "auth_challenge", "props": {"actions": [{"label": "Jason", "user_id": "u1"}]}}]
    with caplog.at_level(logging.INFO, logger="skybridge_service"):
        _validate_cards_for_convergence(diverging)
    assert any("non-conforming [convergence]" in r.message and "auth_challenge" in r.message
               for r in caplog.records), "expected the gate to log the divergent auth_challenge card"

    # A conforming card emits no divergence log.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="skybridge_service"):
        _validate_cards_for_convergence([{"component": "status", "props": {"title": "ok"}}])
    assert not any("non-conforming [convergence]" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_all_producers_conform_zero_divergence(caplog):
    """Increment 3 — convergence proof: the real resolver paths (incl. the
    auth_challenge that used to diverge) now emit ZERO non-conforming cards through
    the gate. Migrating auth_challenge was the last producer to fix; if any future
    change reintroduces a non-conforming card on these paths, this fails loudly."""
    import logging

    # Capture the gate exactly as it runs inside resolve_skybridge_request, which
    # calls _attach_skybridge_context -> _validate_cards_for_convergence on the way
    # out. Wrapping caplog around the resolver calls themselves observes the real
    # production pass (not a re-run), so the proof matches the live path.
    with caplog.at_level(logging.INFO, logger="skybridge_service"):
        results = await _gather_real_results()

    assert results, "expected the representative resolvers to produce handled results"
    diverged = [r.message for r in caplog.records if "non-conforming [convergence]" in r.message]
    assert diverged == [], f"producers must all conform; gate flagged: {diverged}"


async def _gather_real_results():
    """Drive a representative spread of resolvers, including the auth-required
    (auth_challenge) path that was the final divergent producer."""
    results = []
    # auth_challenge (guest hitting personal data) — the migrated producer
    results.append(await resolve_skybridge_request("show my calendar", "guest", db=GuardedGuestDb()))
    results.append(await resolve_skybridge_request("show my shopping list", "guest", db=GuardedGuestDb()))
    results.append(await resolve_skybridge_request("show my contacts", "guest", db=GuardedGuestDb()))
    # clock (no auth, no db)
    results.append(await resolve_skybridge_request("what time is it", "guest", db=FakeDb()))
    return [r for r in results if r and r.get("handled")]


# ── Timers tile regression (glass-verified bug): "show my timers" went to people ──

def test_show_my_timers_classifies_as_timer_status():
    """Dashboard Timers tile query. Was swallowed by the people 'show my X'
    fallback (singular-only \\btimer\\b guard) → empty people directory on glass."""
    for q in ("show my timers", "list my timers", "what timers do I have",
              "are my timers still going"):
        intent = classify_skybridge_intent(q, None)
        assert intent is not None and (intent.domain, intent.action) == ("timer", "status"), q


def test_timer_singular_paths_unchanged():
    for q, want in (("set a timer for 5 minutes", ("timer", "create")),
                    ("cancel the timers", ("timer", "cancel")),
                    ("how long left on the timer", ("timer", "status"))):
        intent = classify_skybridge_intent(q, None)
        assert intent is not None and (intent.domain, intent.action) == want, q


def test_people_and_lists_shows_not_stolen_by_timer_fix():
    for q, want in (("show my shopping list", ("lists", "show")),
                    ("show my calendar", ("calendar", "show")),
                    ("show my people", ("people", "show"))):
        intent = classify_skybridge_intent(q, None)
        assert intent is not None and (intent.domain, intent.action) == want, q
