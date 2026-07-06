"""ui_catalog — schema generation + tree validation (incl. hostile payloads)."""
import json

import pytest

from card_contract import CardContractError
from ui_catalog import (
    CATALOG,
    LLM_TREE_DEPTH,
    MAX_TREE_DEPTH,
    MAX_TREE_NODES,
    catalog_doc,
    llm_schema,
    validate_component_tree,
)


# ── schema generation ─────────────────────────────────────────────────────────

def test_llm_schema_is_serializable_and_nonrecursive():
    schema = llm_schema()
    text = json.dumps(schema)  # must serialize cleanly
    # Non-recursive: only forward $refs to node2..nodeN, none to a node referencing itself.
    assert '"$defs"' in text
    for d in range(2, LLM_TREE_DEPTH + 1):
        assert f"node{d}" in text
    assert f"node{LLM_TREE_DEPTH + 1}" not in text


def test_llm_schema_deepest_level_has_no_containers():
    deepest = llm_schema()["$defs"][f"node{LLM_TREE_DEPTH}"]
    consts = [b["properties"]["component"].get("const") for b in deepest["anyOf"]]
    for container in ("Stack", "Row", "Grid"):
        assert container not in consts


def test_catalog_doc_mentions_every_component():
    doc = catalog_doc()
    for name in CATALOG:
        assert name in doc


# ── validation: happy paths ───────────────────────────────────────────────────

def _card(children):
    return {"component": "Stack", "children": children}


def test_valid_tree_normalizes():
    tree = _card([
        {"component": "Text", "text": "Good morning", "role": "title"},
        {"component": "Row", "children": [
            {"component": "Stat", "value": "19°", "label": "Geraldton"},
            {"component": "Badge", "text": "Clear", "tone": "success"},
        ]},
        {"component": "ActionButton", "action": {"label": "Details", "query": "show weather"}},
    ])
    out = validate_component_tree(tree)
    assert out["component"] == "Stack"
    assert out["children"][1]["children"][0]["value"] == "19°"
    # action normalized through validate_component_action (kind default applied)
    assert out["children"][2]["action"]["kind"] == "normal"


def test_same_origin_image_ok():
    out = validate_component_tree(_card([{"component": "Image", "src": "/touch/img/x.png"}]))
    assert out["children"][0]["src"].startswith("/")


# ── validation: hostile payloads ──────────────────────────────────────────────

def test_unknown_component_rejected():
    with pytest.raises(CardContractError):
        validate_component_tree(_card([{"component": "ScriptTag", "text": "x"}]))


def test_unknown_prop_rejected():
    with pytest.raises(CardContractError):
        validate_component_tree(_card([{"component": "Text", "text": "x", "onclick": "evil()"}]))


def test_root_must_be_container():
    with pytest.raises(CardContractError):
        validate_component_tree({"component": "Text", "text": "loose leaf"})


def test_leaf_cannot_have_children():
    with pytest.raises(CardContractError):
        validate_component_tree(_card([{"component": "Text", "text": "x",
                                        "children": [{"component": "Text", "text": "y"}]}]))


def test_depth_bomb_rejected():
    node = {"component": "Text", "text": "deep"}
    for _ in range(MAX_TREE_DEPTH + 1):
        node = {"component": "Stack", "children": [node]}
    with pytest.raises(CardContractError):
        validate_component_tree(node)


def test_node_bomb_rejected():
    kids = [{"component": "Text", "text": str(i)} for i in range(MAX_TREE_NODES + 1)]
    with pytest.raises(CardContractError):
        validate_component_tree(_card(kids))


def test_foreign_image_src_rejected():
    for src in ("https://evil.example/x.png", "//evil.example/x.png", "javascript:alert(1)"):
        with pytest.raises(CardContractError):
            validate_component_tree(_card([{"component": "Image", "src": src}]))


def test_action_without_query_or_intent_rejected():
    with pytest.raises(CardContractError):
        validate_component_tree(_card([{"component": "ActionButton", "action": {"label": "??"}}]))


def test_enum_violation_rejected():
    with pytest.raises(CardContractError):
        validate_component_tree(_card([{"component": "Badge", "text": "x", "tone": "sparkly"}]))


def test_progress_bounds():
    with pytest.raises(CardContractError):
        validate_component_tree(_card([{"component": "Progress", "value": 150}]))
