"""Focused unit tests for the pure helpers in calendar_utils.

These tests target ``row_to_event``, a small formatter used by both
``routers/calendar.py`` and ``skybridge_service.py`` to normalize a calendar
row before it leaves the data layer. The helper has no direct test coverage
otherwise; the surrounding modules only exercise it indirectly through the
HTTP boundary.
"""

from calendar_utils import row_to_event


def _row(**fields):
    """Build a calendar row shaped like a dict from the data layer."""
    return {
        "id": "evt-1",
        "user_id": "u-1",
        "title": "Standup",
        "start_at": "2024-05-01T09:00:00",
        "end_at": "2024-05-01T09:30:00",
        "category": "work",
        "visibility": "personal",
        **fields,
    }


def test_row_to_event_passes_through_basic_fields():
    out = row_to_event(_row())
    assert out["id"] == "evt-1"
    assert out["title"] == "Standup"
    assert out["start_at"] == "2024-05-01T09:00:00"
    assert "metadata" not in out or out["metadata"] is None


def test_row_to_event_parses_json_metadata_string():
    row = _row(metadata='{"location": "Zoom", "agenda": ["a", "b"]}')
    out = row_to_event(row)
    assert out["metadata"] == {"location": "Zoom", "agenda": ["a", "b"]}


def test_row_to_event_empty_metadata_string_becomes_none():
    row = _row(metadata="")
    out = row_to_event(row)
    assert out["metadata"] is None


def test_row_to_event_invalid_json_metadata_falls_back_to_none():
    row = _row(metadata="{not valid json")
    out = row_to_event(row)
    assert out["metadata"] is None


def test_row_to_event_non_string_metadata_is_left_alone():
    payload = {"location": "Kitchen"}
    row = _row(metadata=payload)
    out = row_to_event(row)
    # Already-parsed dict is preserved verbatim.
    assert out["metadata"] is payload


def test_row_to_event_coerces_all_day_int_to_bool():
    assert row_to_event(_row(all_day=0))["all_day"] is False
    assert row_to_event(_row(all_day=1))["all_day"] is True


def test_row_to_event_coerces_deleted_int_to_bool():
    assert row_to_event(_row(deleted=0))["deleted"] is False
    assert row_to_event(_row(deleted=1))["deleted"] is True


def test_row_to_event_preserves_none_for_all_day_and_deleted():
    out = row_to_event(_row(all_day=None, deleted=None))
    assert out["all_day"] is None
    assert out["deleted"] is None


def test_row_to_event_omitted_all_day_and_deleted_keys_remain_absent():
    out = row_to_event(_row())
    # Keys not present in the source row are not synthesized.
    assert "all_day" not in out
    assert "deleted" not in out


def test_row_to_event_does_not_mutate_input_row():
    row = _row(metadata='{"k": 1}', all_day=0)
    snapshot = dict(row)
    row_to_event(row)
    assert row == snapshot


def test_row_to_event_accepts_dict_input_directly():
    out = row_to_event({"id": "x", "metadata": '{"a": 1}'})
    assert out["id"] == "x"
    assert out["metadata"] == {"a": 1}
