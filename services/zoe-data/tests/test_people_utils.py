"""Focused unit tests for `people_utils.row_to_person`.

`row_to_person` is the canonical DB-row-to-API-dict formatter for the people
surface. It is pure (no I/O, no globals) and is consumed by both
`routers/people.py` and `skybridge_service.py`, so its defaults and JSON
parsing behaviour deserve direct coverage.

The realistic inputs are `dict` (callers that pre-convert via `dict(row)`)
and Mapping-like rows (asyncpg Records, SQLAlchemy Rows) that support
both `__getitem__` and `dict(row)` conversion.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import people_utils
from people_utils import row_to_person


# ── happy path ────────────────────────────────────────────────────────────


def test_returns_all_canonical_fields():
    row = {
        "id": "p-1",
        "user_id": "u-1",
        "name": "Alex",
        "relationship": "friend",
        "circle": "inner",
        "context": "shared",
        "email": "alex@example.com",
        "phone": "+1-555-0100",
        "birthday": "1990-04-12",
        "notes": "met at conf",
        "preferences": json.dumps({"channel": "sms", "tone": "warm"}),
        "visibility": "private",
        "health_score": 0.82,
        "notification_count": 3,
        "contact_count": 7,
        "last_contacted_at": "2026-06-01T12:00:00Z",
        "is_partial": 1,
        "how_we_met": "PyCon",
        "first_met_date": "2022-10-01",
        "introduced_by_person_id": "p-0",
        "created_at": "2022-10-02T00:00:00Z",
        "updated_at": "2026-06-10T00:00:00Z",
    }

    out = row_to_person(row)

    assert out["id"] == "p-1"
    assert out["user_id"] == "u-1"
    assert out["name"] == "Alex"
    assert out["relationship"] == "friend"
    assert out["circle"] == "inner"
    assert out["context"] == "shared"
    assert out["email"] == "alex@example.com"
    assert out["phone"] == "+1-555-0100"
    assert out["birthday"] == "1990-04-12"
    assert out["notes"] == "met at conf"
    assert out["preferences"] == {"channel": "sms", "tone": "warm"}
    assert out["visibility"] == "private"
    assert out["health_score"] == 0.82
    assert out["notification_count"] == 3
    assert out["contact_count"] == 7
    assert out["last_contacted_at"] == "2026-06-01T12:00:00Z"
    assert out["is_partial"] is True
    assert out["how_we_met"] == "PyCon"
    assert out["first_met_date"] == "2022-10-01"
    assert out["introduced_by_person_id"] == "p-0"
    assert out["created_at"] == "2022-10-02T00:00:00Z"
    assert out["updated_at"] == "2026-06-10T00:00:00Z"


def test_output_has_exact_canonical_keyset():
    """The helper returns a stable, documented keyset for the UI."""
    out = row_to_person({"id": "p-x"})
    expected = {
        "id", "user_id", "name", "relationship", "circle", "context",
        "email", "phone", "birthday", "notes", "preferences", "visibility",
        "health_score", "notification_count", "contact_count",
        "last_contacted_at", "is_partial", "how_we_met", "first_met_date",
        "introduced_by_person_id", "created_at", "updated_at",
    }
    assert set(out) == expected


# ── defaults ──────────────────────────────────────────────────────────────


def test_missing_optional_fields_get_safe_defaults():
    out = row_to_person({"id": "p-2", "name": "Bo"})

    assert out["id"] == "p-2"
    assert out["name"] == "Bo"
    assert out["circle"] == "circle"
    assert out["context"] == "personal"
    assert out["health_score"] == 0.5
    assert out["notification_count"] == 0
    assert out["contact_count"] == 0
    assert out["is_partial"] is False
    # fields with no documented default stay None when absent
    assert out["email"] is None
    assert out["phone"] is None
    assert out["birthday"] is None
    assert out["preferences"] is None
    assert out["visibility"] is None


def test_is_partial_coerces_truthy_and_falsy_ints():
    assert row_to_person({"id": "a"})["is_partial"] is False
    assert row_to_person({"id": "a", "is_partial": 0})["is_partial"] is False
    assert row_to_person({"id": "a", "is_partial": 1})["is_partial"] is True
    assert row_to_person({"id": "a", "is_partial": 2})["is_partial"] is True


# ── preferences parsing ──────────────────────────────────────────────────


def test_preferences_dict_passes_through_unchanged():
    prefs = {"channel": "email", "do_not_disturb": True}
    out = row_to_person({"id": "p-3", "preferences": prefs})
    assert out["preferences"] == prefs


def test_preferences_json_string_is_decoded():
    out = row_to_person({"id": "p-4", "preferences": '{"channel":"sms"}'})
    assert out["preferences"] == {"channel": "sms"}


def test_preferences_invalid_json_string_falls_back_to_none():
    out = row_to_person({"id": "p-5", "preferences": "{not-json"})
    assert out["preferences"] is None


def test_preferences_missing_returns_none():
    out = row_to_person({"id": "p-6"})
    assert out["preferences"] is None


def test_preferences_empty_string_passes_through_unchanged():
    """Empty string is falsy so the JSON decode branch is skipped; the
    raw empty string is preserved rather than normalised to None."""
    out = row_to_person({"id": "p-7", "preferences": ""})
    assert out["preferences"] == ""


# ── Mapping-shaped rows (asyncpg / SQLAlchemy style) ─────────────────────


class _MappingRow(Mapping):
    """Minimal Mapping that mirrors an asyncpg Record / SQLAlchemy Row."""

    def __init__(self, data: dict):
        self._data = dict(data)

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


def test_mapping_row_with_preferences_attribute_is_decoded():
    row = _MappingRow({
        "id": "p-8",
        "name": "Cy",
        "preferences": '{"channel":"push"}',
    })

    out = row_to_person(row)

    assert out["id"] == "p-8"
    assert out["name"] == "Cy"
    assert out["preferences"] == {"channel": "push"}
    # defaults applied for fields not present in the mapping
    assert out["circle"] == "circle"
    assert out["is_partial"] is False


def test_mapping_row_without_preferences_yields_none():
    row = _MappingRow({"id": "p-9", "name": "Dee"})
    out = row_to_person(row)
    assert out["id"] == "p-9"
    assert out["name"] == "Dee"
    assert out["preferences"] is None


def test_preferences_access_exception_is_swallowed():
    """If reading `preferences` blows up, the helper must not crash."""

    class _BadRow(_MappingRow):
        def __getitem__(self, key):
            if key == "preferences":
                raise KeyError("boom")
            return super().__getitem__(key)

    out = row_to_person(_BadRow({"id": "p-10"}))
    assert out["id"] == "p-10"
    assert out["preferences"] is None


# ── module surface ───────────────────────────────────────────────────────


def test_module_exposes_row_to_person():
    assert hasattr(people_utils, "row_to_person")
    assert callable(people_utils.row_to_person)
    assert people_utils.row_to_person is row_to_person
