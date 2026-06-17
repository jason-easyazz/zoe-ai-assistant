"""Focused unit tests for the pure ``calendar_utils`` row formatter.

``row_to_event`` is the shared parser used by the calendar router and the
skybridge service to normalize persisted rows into event dicts. It must
deserialize the JSON ``metadata`` column, coerce ``all_day`` / ``deleted``
into real booleans, and leave the remaining event fields untouched.
"""

from calendar_utils import row_to_event


def test_parses_json_metadata_into_dict():
    event = row_to_event(
        {
            "id": 1,
            "title": "Standup",
            "start": "2026-01-01T10:00:00Z",
            "metadata": '{"room": "A", "attendees": ["zoe", "alex"]}',
        }
    )
    assert event["metadata"] == {"room": "A", "attendees": ["zoe", "alex"]}
    assert event["title"] == "Standup"
    assert event["start"] == "2026-01-01T10:00:00Z"


def test_none_metadata_stays_none():
    event = row_to_event({"id": 2, "title": "Lunch", "metadata": None})
    assert event["metadata"] is None


def test_empty_string_metadata_becomes_none():
    """An empty string in the metadata column should not raise and should
    normalize to ``None`` so downstream consumers see a single sentinel."""
    event = row_to_event({"id": 3, "title": "Call", "metadata": ""})
    assert event["metadata"] is None


def test_invalid_json_metadata_falls_back_to_none():
    event = row_to_event({"id": 4, "title": "Call", "metadata": "not json{"})
    assert event["metadata"] is None


def test_dict_metadata_is_passed_through():
    """When the driver already returned a parsed dict, leave it alone."""
    event = row_to_event(
        {"id": 5, "title": "Retro", "metadata": {"room": "Z"}}
    )
    assert event["metadata"] == {"room": "Z"}


def test_missing_metadata_key_is_preserved():
    """Rows without a metadata column should not gain a fabricated key."""
    event = row_to_event({"id": 6, "title": "Y"})
    assert "metadata" not in event


def test_all_day_coerced_from_int_to_bool():
    event = row_to_event({"id": 7, "title": "Holiday", "all_day": 1})
    assert event["all_day"] is True

    event_false = row_to_event({"id": 8, "title": "Meeting", "all_day": 0})
    assert event_false["all_day"] is False


def test_all_day_none_is_not_coerced():
    """A NULL all_day column stays ``None`` so UI code can distinguish
    "unspecified" from "explicitly false"."""
    event = row_to_event({"id": 9, "title": "X", "all_day": None})
    assert event["all_day"] is None
    assert "all_day" in event  # key present, value None


def test_missing_all_day_key_is_preserved():
    event = row_to_event({"id": 10, "title": "X"})
    assert "all_day" not in event


def test_deleted_coerced_from_int_to_bool():
    event = row_to_event({"id": 11, "title": "Old", "deleted": 1})
    assert event["deleted"] is True


def test_extra_event_fields_are_preserved():
    """``row_to_event`` must not drop columns it does not recognize."""
    row = {
        "id": 12,
        "title": "Offsite",
        "start": "2026-03-01T09:00:00Z",
        "end": "2026-03-01T17:00:00Z",
        "location": "Office",
        "notes": "Bring laptop",
        "calendar_id": "work",
    }
    event = row_to_event(row)
    for key, value in row.items():
        assert event[key] == value


def test_full_event_normalization_end_to_end():
    """Integration of all branches in a single realistic row."""
    event = row_to_event(
        {
            "id": 13,
            "title": "All-day holiday",
            "start": "2026-12-25",
            "end": "2026-12-25",
            "metadata": '{"reminder": "none"}',
            "all_day": 1,
            "deleted": 0,
        }
    )
    assert event == {
        "id": 13,
        "title": "All-day holiday",
        "start": "2026-12-25",
        "end": "2026-12-25",
        "metadata": {"reminder": "none"},
        "all_day": True,
        "deleted": False,
    }
