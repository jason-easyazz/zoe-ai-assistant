"""Tests for the interactive component contract (card_contract.validate_component)."""
import pytest

from card_contract import (
    CardContractError,
    validate_component,
    validate_component_action,
)

pytestmark = pytest.mark.ci_safe


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


# ── superset: the one contract validates Skybridge card shapes too ──────────

def test_route_action_accepted():
    a = validate_component_action({"label": "Open weather", "route": "/touch/weather.html"})
    assert a == {"label": "Open weather", "kind": "normal", "route": "/touch/weather.html"}


def test_action_requires_query_intent_or_route():
    with pytest.raises(CardContractError):
        validate_component_action({"label": "Nothing", "kind": "normal"})


def test_actions_nested_in_props_accepted():
    # Skybridge cards nest actions inside props rather than at the top level.
    out = validate_component({
        "component": "page",
        "props": {"title": "Weather", "actions": [{"label": "Open", "route": "/touch/weather.html"}]},
    })
    assert out["actions"] == [{"label": "Open", "kind": "normal", "route": "/touch/weather.html"}]


def test_top_level_actions_take_precedence_over_props():
    out = validate_component({
        "component": "list",
        "props": {"actions": [{"label": "nested", "query": "x"}]},
        "actions": [{"label": "top", "query": "y"}],
    })
    assert [a["label"] for a in out["actions"]] == ["top"]
