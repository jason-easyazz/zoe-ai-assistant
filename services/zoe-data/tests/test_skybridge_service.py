"""Tests for Skybridge real data card resolution."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import skybridge_service  # noqa: E402
from skybridge_service import classify_skybridge_intent, resolve_skybridge_request  # noqa: E402


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
            return self.events
        if "FROM lists" in sql:
            if "WHERE id = $1" in sql:
                return [row for row in self.lists if row.get("id") == args[1]]
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

    async def fetchrow(self, *_args):
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
    assert classify_skybridge_intent("what is happening this week").domain == "calendar"
    assert classify_skybridge_intent("show my shopping list").domain == "lists"
    assert classify_skybridge_intent("show my contacts").domain == "people"
    assert classify_skybridge_intent("find Sarah").domain == "people"
    assert classify_skybridge_intent("what is there to do this week") is None
    assert classify_skybridge_intent("open settings") is None


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


def test_classify_calendar_date_and_range_requests(monkeypatch):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 11)

    monkeypatch.setattr(skybridge_service, "date", FrozenDate)

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
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 11)

    monkeypatch.setattr(skybridge_service, "date", FrozenDate)
    context = {"intent": {"domain": "calendar"}, "cards": [{"content": {"source": "calendar_show", "start_date": "2026-06-17", "events": []}}]}
    db = FakeDb(events=[])

    result = await resolve_skybridge_request("add pick up the groceries at 3pm", "family-admin", context=context, db=db)

    assert result["handled"] is True
    assert result["intent"]["action"] == "create_event"
    event = result["cards"][0]["content"]["events"][0]
    assert event["title"] == "pick up the groceries"
    assert event["start_date"] == "2026-06-17"
    assert event["start_time"] == "15:00"


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
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 11)

    monkeypatch.setattr(skybridge_service, "date", FrozenDate)

    db = FakeDb(events=[])
    result = await resolve_skybridge_request("show my calendar on the 17th of June", "family-admin", db=db)

    assert result["handled"] is True
    assert result["intent"]["range"] == "17 June 2026"
    assert db.fetch_args[2] == "2026-06-17"
    assert db.fetch_args[3] == "2026-06-17"
    assert result["cards"][0]["content"]["qualifier"] == "17 June 2026"


@pytest.mark.asyncio
async def test_calendar_happening_this_week_queries_range(monkeypatch):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 11)

    monkeypatch.setattr(skybridge_service, "date", FrozenDate)

    db = FakeDb(events=[])
    result = await resolve_skybridge_request("what is happening this week", "family-admin", db=db)

    assert result["handled"] is True
    assert result["intent"]["range"] == "this week"
    assert db.fetch_args[2] == "2026-06-11"
    assert db.fetch_args[3] == (date(2026, 6, 11) + timedelta(days=7)).isoformat()


@pytest.mark.asyncio
async def test_guest_calendar_request_does_not_fetch_family_events():
    result = await resolve_skybridge_request("show my calendar", "guest", db=GuardedGuestDb())

    assert result["handled"] is True
    assert result["cards"][0]["content"]["events"] == []


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
    assert result["skybridge_context"]["cards"][0]["content"]["items"][0]["text"] == "bread"


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
    assert card["content"]["lists"][0]["items"] == []
    assert card["content"]["lists"][0]["open_count"] == 1
    assert card["content"]["lists"][1]["completed_count"] == 1
    assert db.list_item_fetch_count == 1


@pytest.mark.asyncio
async def test_guest_lists_request_does_not_fetch_private_lists():
    result = await resolve_skybridge_request("show my shopping list", "guest", db=GuardedGuestDb())

    assert result["handled"] is True
    assert result["cards"][0]["content"]["source"] == "list_show"
    assert result["cards"][0]["content"]["items"] == []


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
    assert result["cards"][0]["content"]["source"] == "people_directory"
    assert result["cards"][0]["content"]["people"] == []


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
