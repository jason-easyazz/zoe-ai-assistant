"""Tests for shopping/list intent canonical card emission."""

from intent_router import Intent
from routers.chat import _intent_card_data


def test_list_show_payload_keeps_compat_shape_and_adds_contract():
    payload = _intent_card_data(
        Intent("list_show", {"list_name": "Groceries", "items": [" milk ", "eggs"]})
    )

    assert payload["type"] == "list"
    assert payload["data"] == {"list_name": "Groceries", "items": ["milk", "eggs"]}
    assert payload["card"]["card_type"] == "list"
    assert payload["card"]["content"]["list_name"] == "Groceries"
    assert payload["card"]["content"]["items"] == ["milk", "eggs"]


def test_list_show_payload_normalizes_single_item_slot():
    payload = _intent_card_data(Intent("list_show", {"item": " bread "}))

    assert payload["data"] == {"list_name": "Shopping", "items": ["bread"]}
    assert payload["card"]["content"]["summary"] == "1 item"


def test_list_add_payload_keeps_compat_shape_and_adds_editor_contract():
    payload = _intent_card_data(Intent("list_add", {"list_name": "Groceries", "item": "milk"}))

    assert payload["type"] == "list"
    assert payload["data"] == {"list_name": "Groceries", "item": "milk"}
    assert payload["card"]["card_type"] == "action_form"
    assert payload["card"]["content"]["form_id"] == "shopping_item_editor"
    assert payload["card"]["content"]["values"] == {"item": "milk", "list_name": "Groceries"}


def test_list_add_payload_defaults_to_shopping_list():
    payload = _intent_card_data(Intent("list_add", {"text": "eggs"}))

    assert payload["data"] == {"list_name": "Shopping", "item": "eggs"}
    assert payload["card"]["content"]["values"] == {"item": "eggs", "list_name": "Shopping"}
