"""Tests for the interactive component contract (card_contract.validate_component)."""
import pytest

from card_contract import (
    CardContractError,
    validate_component,
    validate_component_action,
)


def test_minimal_component_normalizes():
    out = validate_component({"component": "status", "props": {"title": "Hi"}})
    assert out == {"component": "status", "props": {"title": "Hi"}}


def test_props_default_to_empty_object():
    out = validate_component({"component": "status"})
    assert out["props"] == {}


def test_missing_component_raises():
    with pytest.raises(CardContractError):
        validate_component({"props": {}})


def test_props_must_be_object():
    with pytest.raises(CardContractError):
        validate_component({"component": "status", "props": ["not", "a", "dict"]})


def test_query_action_normalizes():
    out = validate_component(
        {
            "component": "list",
            "props": {"title": "Shopping"},
            "actions": [{"label": "Add item", "query": "add bread to my shopping list"}],
        }
    )
    assert out["actions"] == [
        {"label": "Add item", "kind": "normal", "query": "add bread to my shopping list"}
    ]


def test_intent_action_carries_args():
    a = validate_component_action(
        {"label": "Delete", "kind": "warn", "intent": "lists.remove_item", "args": {"item": "milk"}}
    )
    assert a == {
        "label": "Delete",
        "kind": "warn",
        "intent": "lists.remove_item",
        "args": {"item": "milk"},
    }


def test_action_requires_query_or_intent():
    with pytest.raises(CardContractError):
        validate_component_action({"label": "Nothing"})


def test_action_requires_label():
    with pytest.raises(CardContractError):
        validate_component_action({"query": "do thing"})


def test_invalid_action_kind_raises():
    with pytest.raises(CardContractError):
        validate_component_action({"label": "X", "query": "y", "kind": "explode"})


def test_actions_must_be_list():
    with pytest.raises(CardContractError):
        validate_component({"component": "list", "actions": {"label": "x", "query": "y"}})


def test_slots_alias_for_args():
    a = validate_component_action(
        {"label": "Go", "intent": "calendar.update_time", "slots": {"time": "4pm"}}
    )
    assert a["args"] == {"time": "4pm"}
