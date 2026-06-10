"""Tests for shopping/list intent canonical card emission."""

from intent_router import Intent
from routers.chat import _intent_action_form_payload, _intent_card_data, _normalized_list_items


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


def test_list_add_payload_preserves_compat_list_default():
    payload = _intent_card_data(Intent("list_add", {"text": "eggs"}))

    assert payload["data"] == {"list_name": "List", "item": "eggs"}
    assert payload["card"]["content"]["values"] == {"item": "eggs", "list_name": "List"}


def test_normalized_list_items_fallback_matches_service_shape(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "card_service":
            raise ImportError("forced fallback")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert _normalized_list_items({"items": [" milk ", 123, ""]}) == ["milk", "123"]


def test_list_action_form_uses_shared_item_normalization():
    payload = _intent_action_form_payload(Intent("list_show", {"text": " bread "}), panel_id="panel-1")

    assert payload == {
        "panel_type": "shopping_list",
        "title": "Shopping List",
        "data": {
            "list_name": "Shopping",
            "items": ["bread"],
            "item": "bread",
        },
        "panel_id": "panel-1",
    }
