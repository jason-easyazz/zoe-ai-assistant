"""Focused unit tests for the pure ``people_utils`` row formatter.

``row_to_person`` is the shared parser used by the people router and the
skybridge service to normalize persisted contact rows into the canonical
person dict consumed by UI surfaces. It must:

* parse the JSON ``preferences`` column when it arrives as a string,
* tolerate already-parsed dicts and ``None``,
* apply the documented defaults for ``circle`` / ``context`` / ``health_score``
  / ``notification_count`` / ``contact_count`` / ``is_partial``,
* coerce ``is_partial`` to a real boolean, and
* drop any columns outside the canonical schema (the return dict has a
  fixed key set — see ``test_extra_row_fields_are_dropped_by_design``).

The helper is intentionally pure — it takes a dict-like row, performs no I/O,
and returns a fresh dict. The tests below pin each branch independently.
"""

import pytest
import collections.abc

from people_utils import row_to_person

pytestmark = pytest.mark.ci_safe


# ---------------------------------------------------------------------------
# Full row normalization
# ---------------------------------------------------------------------------


def test_full_row_normalization_end_to_end():
    """Every documented field lands in the output at the right type."""
    person = row_to_person(
        {
            "id": 1,
            "user_id": 10,
            "name": "Alice",
            "relationship": "friend",
            "circle": "inner",
            "context": "work",
            "email": "alice@example.com",
            "phone": "+15551234567",
            "birthday": "1990-04-12",
            "notes": "Met at PyCon",
            "preferences": {"theme": "dark", "tz": "America/Los_Angeles"},
            "visibility": "shared",
            "health_score": 0.9,
            "notification_count": 3,
            "contact_count": 5,
            "last_contacted_at": "2026-05-01T12:00:00Z",
            "is_partial": 1,
            "how_we_met": "PyCon 2024",
            "first_met_date": "2024-05-10",
            "introduced_by_person_id": 42,
            "created_at": "2024-05-10T09:00:00Z",
            "updated_at": "2026-05-01T12:00:00Z",
        }
    )
    assert person == {
        "id": 1,
        "user_id": 10,
        "name": "Alice",
        "relationship": "friend",
        "circle": "inner",
        "context": "work",
        "email": "alice@example.com",
        "phone": "+15551234567",
        "birthday": "1990-04-12",
        "notes": "Met at PyCon",
        "preferences": {"theme": "dark", "tz": "America/Los_Angeles"},
        "visibility": "shared",
        "health_score": 0.9,
        "notification_count": 3,
        "contact_count": 5,
        "last_contacted_at": "2026-05-01T12:00:00Z",
        "is_partial": True,
        "how_we_met": "PyCon 2024",
        "first_met_date": "2024-05-10",
        "introduced_by_person_id": 42,
        "created_at": "2024-05-10T09:00:00Z",
        "updated_at": "2026-05-01T12:00:00Z",
    }


# ---------------------------------------------------------------------------
# ``preferences`` parsing
# ---------------------------------------------------------------------------


def test_json_string_preferences_are_parsed_into_dict():
    person = row_to_person(
        {"id": 1, "name": "Alice", "preferences": '{"favorite_color": "blue"}'}
    )
    assert person["preferences"] == {"favorite_color": "blue"}


def test_dict_preferences_are_passed_through_unchanged():
    """When the driver already returned a parsed dict, leave it alone."""
    person = row_to_person(
        {"id": 2, "name": "Bob", "preferences": {"favorite_color": "green"}}
    )
    assert person["preferences"] == {"favorite_color": "green"}


def test_none_preferences_stay_none():
    person = row_to_person({"id": 3, "name": "C", "preferences": None})
    assert person["preferences"] is None


def test_invalid_json_preferences_fall_back_to_none():
    """Malformed JSON in the column must not raise; it normalizes to ``None``."""
    person = row_to_person({"id": 4, "name": "D", "preferences": "not json{"})
    assert person["preferences"] is None


def test_empty_dict_preferences_are_preserved():
    """An empty dict is a valid (falsy) preferences value and is kept as-is."""
    person = row_to_person({"id": 5, "name": "E", "preferences": {}})
    assert person["preferences"] == {}


def test_empty_string_preferences_are_preserved_as_empty_string():
    """Pin the current behavior: empty string is falsy so it is not parsed
    and stays in the output as ``""``. Downstream code can choose to treat
    it the same as ``None`` if it wants — the helper is not opinionated.
    """
    person = row_to_person({"id": 6, "name": "F", "preferences": ""})
    assert person["preferences"] == ""


def test_missing_preferences_key_normalizes_to_none():
    """A row without a ``preferences`` column gets ``preferences=None`` in the
    output so the schema is stable for downstream consumers."""
    person = row_to_person({"id": 7, "name": "G"})
    assert person.get("preferences") is None


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------


def test_default_circle_is_circle():
    person = row_to_person({"id": 1, "name": "A"})
    assert person["circle"] == "circle"


def test_default_context_is_personal():
    person = row_to_person({"id": 1, "name": "A"})
    assert person["context"] == "personal"


def test_default_health_score_is_half():
    person = row_to_person({"id": 1, "name": "A"})
    assert person["health_score"] == 0.5


def test_default_notification_count_is_zero():
    person = row_to_person({"id": 1, "name": "A"})
    assert person["notification_count"] == 0


def test_default_contact_count_is_zero():
    person = row_to_person({"id": 1, "name": "A"})
    assert person["contact_count"] == 0


def test_explicit_defaults_are_not_overridden():
    """The defaults are only applied when the row does not provide a value."""
    person = row_to_person(
        {
            "id": 1,
            "name": "A",
            "circle": "public",
            "context": "work",
            "health_score": 0.1,
            "notification_count": 9,
            "contact_count": 4,
        }
    )
    assert person["circle"] == "public"
    assert person["context"] == "work"
    assert person["health_score"] == 0.1
    assert person["notification_count"] == 9
    assert person["contact_count"] == 4


# ---------------------------------------------------------------------------
# ``is_partial`` boolean coercion
# ---------------------------------------------------------------------------


def test_is_partial_is_coerced_from_int_to_bool():
    person_true = row_to_person({"id": 1, "name": "A", "is_partial": 1})
    assert person_true["is_partial"] is True

    person_false = row_to_person({"id": 2, "name": "B", "is_partial": 0})
    assert person_false["is_partial"] is False


def test_is_partial_coerces_truthy_values():
    """Non-int truthy values (e.g. a string) are still bool-coerced."""
    person = row_to_person({"id": 1, "name": "A", "is_partial": "yes"})
    assert person["is_partial"] is True


def test_is_partial_defaults_to_false_when_missing():
    person = row_to_person({"id": 1, "name": "A"})
    assert person["is_partial"] is False


def test_is_partial_explicit_false_is_preserved():
    person = row_to_person({"id": 1, "name": "A", "is_partial": False})
    assert person["is_partial"] is False


# ---------------------------------------------------------------------------
# Field preservation
# ---------------------------------------------------------------------------


def test_extra_row_fields_are_dropped_by_design():
    """``row_to_person`` returns a fixed schema: unknown columns on the input
    row are not surfaced in the output. Callers needing extra columns must
    handle that themselves; the helper only normalizes the documented
    person-record contract.
    """
    row = {
        "id": 1,
        "name": "A",
        "custom_field": "value",
        "another_field": 123,
    }
    person = row_to_person(row)
    assert "custom_field" not in person
    assert "another_field" not in person
    # And the recognized keys are still there with their values.
    assert person["id"] == 1
    assert person["name"] == "A"


def test_optional_fields_default_to_none_when_missing():
    """Fields like ``email`` / ``phone`` / ``birthday`` default to ``None`` when
    the row does not provide them, so UI code can rely on the key being
    present without a separate ``.get()``."""
    person = row_to_person({"id": 1, "name": "A"})
    for key in (
        "user_id",
        "relationship",
        "email",
        "phone",
        "birthday",
        "notes",
        "visibility",
        "last_contacted_at",
        "how_we_met",
        "first_met_date",
        "introduced_by_person_id",
        "created_at",
        "updated_at",
    ):
        assert key in person, f"missing key {key}"
        assert person[key] is None, f"{key} should default to None"


# ---------------------------------------------------------------------------
# Row input shapes
# ---------------------------------------------------------------------------


def test_dict_like_row_is_supported():
    """The helper accepts any mapping ``dict()`` can build from (psycopg2 /
    asyncpg / dataclasses.asdict all qualify because they expose either
    ``keys()`` or ``__iter__``)."""

    # Full collections.abc.Mapping protocol (not just keys/__getitem__) so this
    # mirrors real psycopg2/asyncpg rows and exercises the non-dict dict(row)
    # branch via iteration, like the production path.
    class DictLike(collections.abc.Mapping):
        def __init__(self, data):
            self._d = data

        def __getitem__(self, key):
            return self._d[key]

        def __iter__(self):
            return iter(self._d)

        def __len__(self):
            return len(self._d)

    row = DictLike({"id": 1, "name": "A", "preferences": '{"k": 1}'})
    assert not isinstance(row, dict)
    person = row_to_person(row)
    assert person["id"] == 1
    assert person["name"] == "A"
    assert person["preferences"] == {"k": 1}


def test_input_dict_is_not_mutated():
    """The helper must not mutate its input."""
    row = {
        "id": 1,
        "name": "A",
        "preferences": '{"k": 1}',
        "is_partial": 1,
    }
    snapshot = dict(row)
    snapshot_prefs = row["preferences"]
    person = row_to_person(row)
    assert row == snapshot
    assert row["preferences"] is snapshot_prefs
    # Output is a fresh dict, not the input itself.
    assert person is not row


def test_empty_dict_input_yields_full_default_schema():
    """An empty row still emits every key the helper knows about, populated
    with the documented defaults. Callers can rely on the schema being stable.
    """
    person = row_to_person({})
    # Required defaults are applied.
    assert person["id"] is None
    assert person["circle"] == "circle"
    assert person["context"] == "personal"
    assert person["health_score"] == 0.5
    assert person["notification_count"] == 0
    assert person["contact_count"] == 0
    assert person["is_partial"] is False
    # And optional fields are present-but-None (no fabricated values).
    assert person["preferences"] is None
    assert person["email"] is None
    assert person["phone"] is None
    # Output schema is the documented set of keys, nothing more, nothing less.
    expected_keys = {
        "id", "user_id", "name", "relationship", "circle", "context",
        "email", "phone", "birthday", "notes", "preferences", "visibility",
        "health_score", "notification_count", "contact_count",
        "last_contacted_at", "is_partial", "how_we_met", "first_met_date",
        "introduced_by_person_id", "created_at", "updated_at",
    }
    assert set(person.keys()) == expected_keys
