"""Focused tests for the people row→person pure helper."""

import json

from people_utils import row_to_person


# Canonical schema: every person record surfaced to UI must expose this set.
EXPECTED_KEYS = {
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


def test_returns_canonical_schema_from_dict_row():
    row = {
        "id": 42,
        "user_id": "u1",
        "name": "Ada Lovelace",
        "relationship": "friend",
        "email": "ada@example.com",
        "phone": "+1-555-0100",
        "birthday": "1815-12-10",
        "notes": "Met at the math salon.",
        "preferences": json.dumps({"likes": "tea", "dislikes": "rain"}),
        "visibility": "private",
        "health_score": 0.91,
        "notification_count": 3,
        "contact_count": 17,
        "last_contacted_at": "2026-06-01T12:00:00Z",
        "is_partial": 1,
        "how_we_met": "math salon",
        "first_met_date": "2024-01-01",
        "introduced_by_person_id": None,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2026-06-01T12:00:00Z",
        "extra_field": "should be dropped from canonical schema",
    }

    person = row_to_person(row)

    assert set(person.keys()) == EXPECTED_KEYS
    assert person["id"] == 42
    assert person["user_id"] == "u1"
    assert person["name"] == "Ada Lovelace"
    assert person["relationship"] == "friend"
    assert person["email"] == "ada@example.com"
    assert person["phone"] == "+1-555-0100"
    assert person["birthday"] == "1815-12-10"
    assert person["notes"] == "Met at the math salon."
    assert person["preferences"] == {"likes": "tea", "dislikes": "rain"}
    assert person["visibility"] == "private"
    assert person["health_score"] == 0.91
    assert person["notification_count"] == 3
    assert person["contact_count"] == 17
    assert person["last_contacted_at"] == "2026-06-01T12:00:00Z"
    assert person["is_partial"] is True
    assert person["how_we_met"] == "math salon"
    assert person["first_met_date"] == "2024-01-01"
    assert person["introduced_by_person_id"] is None
    assert person["created_at"] == "2024-01-01T00:00:00Z"
    assert person["updated_at"] == "2026-06-01T12:00:00Z"


def test_applies_canonical_defaults_for_minimal_row():
    person = row_to_person({"id": 1, "name": "Grace"})

    assert person["id"] == 1
    assert person["name"] == "Grace"
    assert person["circle"] == "circle"
    assert person["context"] == "personal"
    assert person["health_score"] == 0.5
    assert person["notification_count"] == 0
    assert person["contact_count"] == 0
    assert person["is_partial"] is False
    # Optional fields default to None.
    for optional in (
        "user_id",
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
        assert person[optional] is None


def test_decodes_json_string_preferences():
    raw = '{"likes":"tea","dislikes":"rain"}'
    person = row_to_person({"id": 2, "name": "Linus", "preferences": raw})

    assert person["preferences"] == {"likes": "tea", "dislikes": "rain"}


def test_invalid_json_string_preferences_become_none():
    person = row_to_person({"id": 3, "name": "X", "preferences": "not-json{"})

    assert person["preferences"] is None


def test_dict_preferences_passed_through_untouched():
    prefs = {"likes": "coffee", "spice": "cardamom"}
    person = row_to_person({"id": 4, "name": "Y", "preferences": prefs})

    assert person["preferences"] is prefs


def test_none_preferences_stays_none():
    person = row_to_person({"id": 5, "name": "Z", "preferences": None})

    assert person["preferences"] is None


def test_dict_like_row_supports_mapping_style_items():
    # Records produced by psycopg2 / asyncpg behave like dicts but are not ``dict``
    # instances. The helper must coerce them via ``dict(row)`` while preserving
    # the canonical schema.
    class MappingRow(dict):
        pass

    row = MappingRow(
        id=7,
        user_id="u9",
        name="Margaret",
        relationship="colleague",
        circle="inner",
        context="work",
        email="margaret@example.com",
        preferences=None,
        visibility="shared",
        health_score=0.7,
        notification_count=2,
        contact_count=9,
        is_partial=0,
    )

    person = row_to_person(row)

    assert isinstance(row, MappingRow)
    assert person["id"] == 7
    assert person["name"] == "Margaret"
    assert person["circle"] == "inner"
    assert person["context"] == "work"
    assert person["visibility"] == "shared"
    assert person["health_score"] == 0.7
    assert person["is_partial"] is False


def test_is_partial_truthiness_for_various_inputs():
    # Truthy int from DB should become True; falsy values should stay False.
    assert row_to_person({"id": 1, "name": "a", "is_partial": 1})["is_partial"] is True
    assert row_to_person({"id": 2, "name": "b", "is_partial": True})["is_partial"] is True
    assert row_to_person({"id": 3, "name": "c", "is_partial": 0})["is_partial"] is False
    assert row_to_person({"id": 4, "name": "d", "is_partial": False})["is_partial"] is False
