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

pytestmark = pytest.mark.ci_safe


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


def test_integer_prop_rejects_float():
    with pytest.raises(CardContractError):
        validate_component_tree({"component": "Grid", "columns": 2.7,
                                 "children": [{"component": "Text", "text": "x"}]})


def test_action_text_counts_toward_budget():
    big = "x" * 200
    kids = [{"component": "ActionButton", "action": {"label": big, "query": big}}
            for _ in range(15)]  # 15 * 400 = 6000 > MAX_TEXT_CHARS
    with pytest.raises(CardContractError):
        validate_component_tree(_card(kids))


def test_js_glyphs_match_python_catalog():
    """The JS renderer's GLYPHS dict must cover exactly the server's _GLYPHS enum —
    otherwise the server accepts a glyph the renderer silently falls back on."""
    import re
    from pathlib import Path
    import ui_catalog
    js = (Path(ui_catalog.__file__).resolve().parents[1] / "zoe-ui" / "dist" / "touch"
          / "js" / "zoe-compose.js").read_text(encoding="utf-8")
    block = js[js.index("var GLYPHS = {"):js.index("};", js.index("var GLYPHS = {"))]
    js_names = set(re.findall(r"^\s{8}([a-z]+):", block, re.M))
    from ui_catalog import CATALOG
    py_names = set(CATALOG["Glyph"]["props"]["name"]["enum"])
    assert js_names == py_names, f"glyph drift: js-only={js_names - py_names} py-only={py_names - js_names}"


# ── visual-richness v2: tones, Hero, leading icons, glyph sizing ──

def test_hero_primitive_validates():
    tree = _card([{"component": "Hero", "glyph": "weather", "value": "17",
                   "unit": "°C", "caption": "Clear in Geraldton", "tone": "cool"}])
    out = validate_component_tree(tree)
    assert out["children"][0]["component"] == "Hero"
    assert out["children"][0]["value"] == "17"


def test_hero_requires_value():
    with pytest.raises(CardContractError):
        validate_component_tree(_card([{"component": "Hero", "caption": "no value"}]))


def test_root_tone_accepted():
    out = validate_component_tree({"component": "Stack", "tone": "mint",
                                   "children": [{"component": "Text", "text": "x"}]})
    assert out["tone"] == "mint"


def test_invalid_tone_rejected():
    with pytest.raises(CardContractError):
        validate_component_tree({"component": "Stack", "tone": "rainbow",
                                 "children": [{"component": "Text", "text": "x"}]})


def test_listrow_icon_accepted():
    out = validate_component_tree(_card([{"component": "ListRow", "title": "Dentist",
                                          "detail": "9am", "icon": "calendar"}]))
    assert out["children"][0]["icon"] == "calendar"


def test_glyph_size_and_tone():
    out = validate_component_tree(_card([{"component": "Glyph", "name": "droplet",
                                          "size": "xl", "tone": "cool"}]))
    assert out["children"][0]["size"] == "xl"


def test_invalid_glyph_size_rejected():
    with pytest.raises(CardContractError):
        validate_component_tree(_card([{"component": "Glyph", "name": "star", "size": "huge"}]))


# ── long-tail shapes: Steps (auto-numbered) + Compare (labeled options) ──

def test_steps_and_step_validate():
    tree = _card([{"component": "Steps", "children": [
        {"component": "Step", "title": "Act fast", "detail": "Blot, don't rub."},
        {"component": "Step", "title": "Apply solution", "detail": "Vinegar + dish soap."},
    ]}])
    out = validate_component_tree(tree)
    steps = out["children"][0]
    assert steps["component"] == "Steps" and len(steps["children"]) == 2
    assert steps["children"][0]["component"] == "Step"


def test_step_requires_title():
    with pytest.raises(CardContractError):
        validate_component_tree(_card([{"component": "Steps", "children": [
            {"component": "Step", "detail": "no title"}]}]))


def test_compare_and_option_validate():
    tree = _card([{"component": "Compare", "children": [
        {"component": "Option", "label": "Drive", "value": "4h", "caption": "Flexible", "tone": "warm", "glyph": "home", "status": "Cheaper"},
        {"component": "Option", "label": "Fly", "value": "1h", "caption": "Fastest", "tone": "cool"},
    ]}])
    out = validate_component_tree(tree)
    comp = out["children"][0]
    assert comp["component"] == "Compare"
    assert comp["children"][0]["label"] == "Drive" and comp["children"][0]["status"] == "Cheaper"


def test_option_requires_label():
    with pytest.raises(CardContractError):
        validate_component_tree(_card([{"component": "Compare", "children": [
            {"component": "Option", "value": "no label"}]}]))


def test_steps_is_container_requires_children():
    with pytest.raises(CardContractError):
        validate_component_tree(_card([{"component": "Steps"}]))
