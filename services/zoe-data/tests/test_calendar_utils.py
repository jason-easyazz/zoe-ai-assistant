"""Focused unit tests for the pure calendar row formatter in calendar_utils.

`row_to_event` is a stateless parser/formatter shared by the calendar router
and Skybridge service. These tests cover the parsing branches (metadata JSON,
boolean coercion) without touching DB, network, or time.
"""

import pytest
from calendar_utils import row_to_event

pytestmark = pytest.mark.ci_safe


# ---------------------------------------------------------------------------
# metadata parsing
# ---------------------------------------------------------------------------


def test_parses_metadata_json_string_into_dict():
    row = {
        "id": 1,
        "title": "Dentist",
        "metadata": '{"location": "clinic", "reminder_minutes": 30}',
    }

    event = row_to_event(row)

    assert event["metadata"] == {"location": "clinic", "reminder_minutes": 30}


def test_invalid_metadata_json_falls_back_to_none():
    row = {"id": 2, "title": "Broken", "metadata": "{not valid json"}

    event = row_to_event(row)

    assert event["metadata"] is None


def test_empty_string_metadata_becomes_none():
    row = {"id": 3, "title": "NoMeta", "metadata": ""}

    event = row_to_event(row)

    assert event["metadata"] is None


def test_none_metadata_stays_none():
    row = {"id": 4, "title": "NullMeta", "metadata": None}

    event = row_to_event(row)

    assert event["metadata"] is None


def test_already_parsed_metadata_dict_is_left_alone():
    row = {"id": 5, "title": "Pre", "metadata": {"already": "parsed"}}

    event = row_to_event(row)

    assert event["metadata"] == {"already": "parsed"}


def test_missing_metadata_key_is_left_alone():
    row = {"id": 6, "title": "NoKey"}

    event = row_to_event(row)

    assert "metadata" not in event


# ---------------------------------------------------------------------------
# all_day / deleted boolean coercion
# ---------------------------------------------------------------------------


def test_all_day_truthy_value_is_coerced_to_true():
    row = {"id": 7, "all_day": 1}

    assert row_to_event(row)["all_day"] is True


def test_all_day_falsy_value_is_coerced_to_false():
    row = {"id": 8, "all_day": 0}

    assert row_to_event(row)["all_day"] is False


def test_all_day_none_stays_none():
    row = {"id": 9, "all_day": None}

    assert row_to_event(row)["all_day"] is None


def test_all_day_missing_key_is_left_alone():
    row = {"id": 10}

    event = row_to_event(row)

    assert "all_day" not in event


def test_deleted_truthy_value_is_coerced_to_true():
    row = {"id": 11, "deleted": 1}

    assert row_to_event(row)["deleted"] is True


def test_deleted_falsy_value_is_coerced_to_false():
    row = {"id": 12, "deleted": 0}

    assert row_to_event(row)["deleted"] is False


def test_deleted_none_stays_none():
    row = {"id": 13, "deleted": None}

    assert row_to_event(row)["deleted"] is None


# ---------------------------------------------------------------------------
# integration / shape preservation
# ---------------------------------------------------------------------------


def test_full_row_preserves_unrelated_fields_and_normalizes_known_ones():
    row = {
        "id": 42,
        "user_id": "u-1",
        "title": "Team standup",
        "start_time": "2026-06-16T09:00:00",
        "end_time": "2026-06-16T09:15:00",
        "category": "work",
        "visibility": "family",
        "all_day": 0,
        "deleted": 0,
        "metadata": '{"join_url": "https://meet.example/x"}',
    }

    event = row_to_event(row)

    assert event == {
        "id": 42,
        "user_id": "u-1",
        "title": "Team standup",
        "start_time": "2026-06-16T09:00:00",
        "end_time": "2026-06-16T09:15:00",
        "category": "work",
        "visibility": "family",
        "all_day": False,
        "deleted": False,
        "metadata": {"join_url": "https://meet.example/x"},
    }


def test_row_copy_does_not_mutate_input():
    row = {
        "id": 99,
        "all_day": 1,
        "deleted": 0,
        "metadata": '{"k": "v"}',
    }

    row_to_event(row)

    # The input row should be untouched: it carried the raw SQL types
    # (int, JSON string) and we must not normalize them in place.
    assert row == {
        "id": 99,
        "all_day": 1,
        "deleted": 0,
        "metadata": '{"k": "v"}',
    }
