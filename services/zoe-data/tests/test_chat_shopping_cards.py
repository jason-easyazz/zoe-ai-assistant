"""Tests for shopping/list intent canonical card emission."""

from intent_router import Intent
from routers.chat import _intent_card_data


def test_list_show_payload_keeps_compat_shape_and_adds_contract():
    payload = _intent_card_data(Intent("list_show", {"list_name": "Groceries", "items": ["milk"]}))

    assert payload["type"] == "list"
    assert payload["data"] == {"list_name": "Groceries", "items": ["milk"]}
    assert payload["card"]["card_type"] == "generic"
    assert payload["card"]["content"]["view"] == "list"
    assert payload["card"]["content"]["list_name"] == "Groceries"
    assert payload["card"]["content"]["items"] == ["milk"]


def test_list_add_payload_keeps_compat_shape_and_adds_editor_contract():
    payload = _intent_card_data(Intent("list_add", {"list_name": "Groceries", "item": "milk"}))

    assert payload["type"] == "list"
    assert payload["data"] == {"list_name": "Groceries", "item": "milk"}
    assert payload["card"]["card_type"] == "action_form"
    assert payload["card"]["content"]["form_id"] == "shopping_item_editor"
    assert payload["card"]["content"]["values"]["item"] == "milk"
    assert payload["card"]["content"]["values"]["list_name"] == "Groceries"


def test_list_add_payload_defaults_match_contract():
    payload = _intent_card_data(Intent("list_add", {"item": "milk"}))

    assert payload["data"]["list_name"] == "Shopping"
    assert payload["card"]["content"]["values"]["list_name"] == "Shopping"


def test_list_show_payload_singular_item_matches_contract():
    payload = _intent_card_data(Intent("list_show", {"item": "milk"}))

    assert payload["data"]["items"] == ["milk"]
    assert payload["card"]["content"]["items"] == ["milk"]


def test_list_show_payload_list_type_matches_contract():
    payload = _intent_card_data(Intent("list_show", {"list_type": "Hardware", "items": ["screws"]}))

    assert payload["data"]["list_name"] == "Hardware"
    assert payload["card"]["content"]["list_name"] == "Hardware"
