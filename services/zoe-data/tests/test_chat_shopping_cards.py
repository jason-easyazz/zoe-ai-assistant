"""Tests for shopping/list intent canonical card emission."""

import pytest
import builtins

from intent_router import Intent
from routers.chat import _intent_action_form_payload, _intent_card_data, _normalized_list_items

pytestmark = pytest.mark.ci_safe


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


def test_list_add_payload_items_slot_matches_editor_contract():
    payload = _intent_card_data(Intent("list_add", {"list_name": "Groceries", "items": [" milk "]}))

    assert payload["data"]["item"] == "milk"
    assert payload["card"]["content"]["values"]["item"] == "milk"


def test_list_show_payload_singular_item_matches_contract():
    payload = _intent_card_data(Intent("list_show", {"item": "milk"}))

    assert payload["data"]["items"] == ["milk"]
    assert payload["card"]["content"]["items"] == ["milk"]


def test_list_show_payload_normalizes_items_for_compat_and_contract():
    payload = _intent_card_data(Intent("list_show", {"items": [" milk ", "", "bread"]}))

    assert payload["data"]["items"] == ["milk", "bread"]
    assert payload["card"]["content"]["items"] == ["milk", "bread"]


def test_list_show_payload_list_type_matches_contract():
    payload = _intent_card_data(Intent("list_show", {"list_type": "Hardware", "items": ["screws"]}))

    assert payload["data"]["list_name"] == "Hardware"
    assert payload["card"]["content"]["list_name"] == "Hardware"


def test_list_action_form_title_list_type_matches_data():
    payload = _intent_action_form_payload(Intent("list_show", {"list_type": "Hardware", "items": ["screws"]}))

    assert payload["title"] == "Hardware List"
    assert payload["data"]["list_name"] == "Hardware"


def test_list_action_form_uses_shared_item_normalization():
    payload = _intent_action_form_payload(Intent("list_add", {"items": [" eggs "]}))

    assert payload["data"]["items"] == ["eggs"]
    assert payload["data"]["item"] == "eggs"


def test_normalized_list_items_fallback_trims_and_filters(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "card_service":
            raise ImportError("boom")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert _normalized_list_items({"items": [" milk ", "", "bread"]}) == ["milk", "bread"]
