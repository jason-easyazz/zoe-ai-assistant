"""Focused unit tests for the pure ``row_to_event`` helper in calendar_utils.

The helper is a small data normalizer used by the calendar router and the
skybridge service to turn a raw DB row into the canonical event dict
returned by the API. It has three branches:

* ``metadata`` is a JSON string → parsed; empty string or invalid JSON
  becomes ``None``; a non-string value is left as-is.
* ``all_day`` and ``deleted`` are coerced to ``bool`` when present and
  not ``None``.

These tests pin all three branches (success, failure, and passthrough)
so future refactors cannot silently change the public shape of the
event dict.
"""
from __future__ import annotations

import calendar_utils


def test_row_to_event_passes_through_simple_row():
    """A row with no metadata/bool fields is returned as a shallow dict copy."""
    row = {"id": "evt-1", "title": "Lunch", "start_at": "2026-06-16T12:00:00Z"}

    result = calendar_utils.row_to_event(row)

    assert result == row
    assert result is not row  # the helper must hand back a fresh dict


def test_row_to_event_parses_valid_metadata_json():
    """A non-empty JSON string in ``metadata`` is decoded into a dict."""
    row = {"id": "evt-2", "metadata": '{"location": "home", "tags": ["weekly"]}'}

    result = calendar_utils.row_to_event(row)

    assert result["metadata"] == {"location": "home", "tags": ["weekly"]}


def test_row_to_event_clears_empty_string_metadata():
    """An empty string in ``metadata`` becomes ``None`` (no JSON to parse)."""
    row = {"id": "evt-3", "metadata": ""}

    result = calendar_utils.row_to_event(row)

    assert result["metadata"] is None


def test_row_to_event_clears_invalid_json_metadata():
    """Malformed JSON in ``metadata`` is dropped to ``None`` instead of raising."""
    row = {"id": "evt-4", "metadata": "{not-json"}

    result = calendar_utils.row_to_event(row)

    assert result["metadata"] is None


def test_row_to_event_leaves_non_string_metadata_untouched():
    """Dicts / lists already stored in ``metadata`` are passed through."""
    parsed = {"source": "ics", "count": 3}

    result = calendar_utils.row_to_event({"id": "evt-5", "metadata": parsed})

    assert result["metadata"] is parsed


def test_row_to_event_coerces_all_day_and_deleted_to_bool():
    """Integer truthy/falsy values for ``all_day`` and ``deleted`` become bool."""
    row = {"id": "evt-6", "all_day": 1, "deleted": 0}

    result = calendar_utils.row_to_event(row)

    assert result["all_day"] is True
    assert result["deleted"] is False
    assert isinstance(result["all_day"], bool)
    assert isinstance(result["deleted"], bool)


def test_row_to_event_preserves_existing_bools_for_all_day_and_deleted():
    """Pre-coerced bool values must survive the helper unchanged."""
    row = {"id": "evt-7", "all_day": True, "deleted": False}

    result = calendar_utils.row_to_event(row)

    assert result["all_day"] is True
    assert result["deleted"] is False


def test_row_to_event_keeps_none_for_all_day_and_deleted():
    """``None`` is the explicit "unknown" sentinel and must stay ``None``."""
    row = {"id": "evt-8", "all_day": None, "deleted": None}

    result = calendar_utils.row_to_event(row)

    assert result["all_day"] is None
    assert result["deleted"] is None


def test_row_to_event_ignores_missing_all_day_and_deleted():
    """Rows without ``all_day``/``deleted`` keys are returned as-is for those keys."""
    row = {"id": "evt-9", "title": "No flags"}

    result = calendar_utils.row_to_event(row)

    assert "all_day" not in result
    assert "deleted" not in result


def test_row_to_event_combines_metadata_and_bool_normalization():
    """The full happy path: JSON metadata + bool coercion in one row."""
    row = {
        "id": "evt-10",
        "title": "Dentist",
        "metadata": '{"room": "B2"}',
        "all_day": 0,
        "deleted": 1,
    }

    result = calendar_utils.row_to_event(row)

    assert result == {
        "id": "evt-10",
        "title": "Dentist",
        "metadata": {"room": "B2"},
        "all_day": False,
        "deleted": True,
    }
