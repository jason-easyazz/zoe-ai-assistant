"""Tests for calendar intent canonical card emission."""

import pytest
from intent_router import Intent
from routers.chat import _intent_card_data

pytestmark = pytest.mark.ci_safe


def test_calendar_show_payload_keeps_compat_shape_and_adds_contract():
    payload = _intent_card_data(Intent("calendar_show", {"qualifier": "tomorrow"}))

    assert payload["type"] == "calendar"
    assert payload["data"] == {"action": "Showing calendar", "qualifier": "tomorrow"}
    assert payload["card"]["card_type"] == "generic"
    assert payload["card"]["content"]["view"] == "timeline"
    assert payload["card"]["content"]["qualifier"] == "tomorrow"


def test_calendar_create_payload_keeps_compat_shape_and_adds_editor_contract():
    payload = _intent_card_data(
        Intent("calendar_create", {"title": "Dentist", "date": "tomorrow", "time": "9am"})
    )

    assert payload["type"] == "calendar"
    assert payload["data"] == {
        "action": "Event added",
        "title": "Dentist",
        "date": "tomorrow",
        "time": "9am",
    }
    assert payload["card"]["card_type"] == "action_form"
    assert payload["card"]["content"]["form_id"] == "calendar_event_editor"
    assert payload["card"]["content"]["values"]["title"] == "Dentist"
