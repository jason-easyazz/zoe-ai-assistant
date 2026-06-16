"""Focused unit tests for the pure people row normalization helper."""

from __future__ import annotations

import pytest

from people_utils import row_to_person


def _full_row() -> dict:
    """Return a fully populated people row as a dict."""
    return {
        "id": 42,
        "user_id": "user-1",
        "name": "Ada Lovelace",
        "relationship": "friend",
        "circle": "inner",
        "context": "work",
        "email": "ada@example.com",
        "phone": "555-0100",
        "birthday": "1815-12-10",
        "notes": "Met at the conference.",
        "preferences": {"channel": "email", "quiet_hours": "22:00-07:00"},
        "visibility": "shared",
        "health_score": 0.87,
        "notification_count": 3,
        "contact_count": 9,
        "last_contacted_at": "2024-05-01T12:00:00Z",
        "is_partial": 0,
        "how_we_met": "Conference 2023",
        "first_met_date": "2023-09-15",
        "introduced_by_person_id": 7,
        "created_at": "2023-09-15T09:00:00Z",
        "updated_at": "2024-05-01T12:00:00Z",
    }


def test_dict_input_round_trip():
    row = _full_row()
    person = row_to_person(row)
    assert person["id"] == 42
    assert person["name"] == "Ada Lovelace"
    assert person["relationship"] == "friend"
    assert person["circle"] == "inner"
    assert person["context"] == "work"
    assert person["email"] == "ada@example.com"
    assert person["phone"] == "555-0100"
    assert person["birthday"] == "1815-12-10"
    assert person["notes"] == "Met at the conference."
    assert person["preferences"] == {
        "channel": "email",
        "quiet_hours": "22:00-07:00",
    }
    assert person["visibility"] == "shared"
    assert person["health_score"] == 0.87
    assert person["notification_count"] == 3
    assert person["contact_count"] == 9
    assert person["last_contacted_at"] == "2024-05-01T12:00:00Z"
    assert person["is_partial"] is False
    assert person["how_we_met"] == "Conference 2023"
    assert person["first_met_date"] == "2023-09-15"
    assert person["introduced_by_person_id"] == 7
    assert person["created_at"] == "2023-09-15T09:00:00Z"
    assert person["updated_at"] == "2024-05-01T12:00:00Z"


def test_dict_input_uses_full_canonical_key_set():
    """The output dict must always carry the full canonical key set."""
    person = row_to_person({"id": 1, "name": "x"})
    assert set(person.keys()) == {
        "id",
        "user_id",
        "name",
        "relationship",
        "circle",
        "context",
        "email",
        "phone",
        "birthday",
        "notes",
        "preferences",
        "visibility",
        "health_score",
        "notification_count",
        "contact_count",
        "last_contacted_at",
        "is_partial",
        "how_we_met",
        "first_met_date",
        "introduced_by_person_id",
        "created_at",
        "updated_at",
    }


def test_dict_subclass_is_accepted():
    """A dict subclass (mapping-protocol row) should normalize correctly."""

    class Row(dict):
        pass

    row = Row(_full_row())
    person = row_to_person(row)
    assert person["name"] == "Ada Lovelace"
    assert person["preferences"]["channel"] == "email"
    # Output is always a plain dict regardless of input class.
    assert type(person) is dict


def test_defaults_for_missing_fields():
    person = row_to_person({})
    # The two defaulted scalar fields use canonical Zoe defaults.
    assert person["circle"] == "circle"
    assert person["context"] == "personal"
    assert person["health_score"] == 0.5
    assert person["notification_count"] == 0
    assert person["contact_count"] == 0
    # All other fields should be None.
    for key in (
        "id",
        "user_id",
        "name",
        "relationship",
        "email",
        "phone",
        "birthday",
        "notes",
        "preferences",
        "visibility",
        "last_contacted_at",
        "how_we_met",
        "first_met_date",
        "introduced_by_person_id",
        "created_at",
        "updated_at",
    ):
        assert person[key] is None
    # is_partial defaults to 0 and must be coerced to a bool.
    assert person["is_partial"] is False


def test_is_partial_is_coerced_to_bool():
    # Truthy values should map to True; falsy/0 should map to False.
    assert row_to_person({"is_partial": 1})["is_partial"] is True
    assert row_to_person({"is_partial": "yes"})["is_partial"] is True
    assert row_to_person({"is_partial": True})["is_partial"] is True
    assert row_to_person({"is_partial": 0})["is_partial"] is False
    assert row_to_person({"is_partial": ""})["is_partial"] is False
    assert row_to_person({"is_partial": None})["is_partial"] is False


def test_preferences_json_string_is_parsed():
    row = {"name": "Lin", "preferences": '{"channel": "sms", "tz": "UTC"}'}
    person = row_to_person(row)
    assert person["preferences"] == {"channel": "sms", "tz": "UTC"}


def test_preferences_dict_passes_through():
    prefs = {"channel": "push"}
    person = row_to_person({"preferences": prefs})
    assert person["preferences"] is prefs


def test_invalid_json_preferences_become_none():
    person = row_to_person({"preferences": "{not valid json"})
    assert person["preferences"] is None


def test_none_preferences_remain_none():
    person = row_to_person({"preferences": None})
    assert person["preferences"] is None


def test_empty_preferences_string_is_preserved():
    """An empty string is falsy, so the helper preserves it unchanged."""
    person = row_to_person({"preferences": ""})
    assert person["preferences"] == ""


def test_non_string_preferences_pass_through():
    """Non-string, non-dict preferences should be passed through unchanged."""
    person = row_to_person({"preferences": [1, 2, 3]})
    assert person["preferences"] == [1, 2, 3]


def test_does_not_mutate_input_dict():
    row = _full_row()
    snapshot = dict(row)
    row_to_person(row)
    assert row == snapshot
