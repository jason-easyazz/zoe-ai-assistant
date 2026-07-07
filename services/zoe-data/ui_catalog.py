"""ui_catalog — the single source of truth for Zoe's composable UI primitives.

One catalog dict drives three things, so they can never drift:
  1. ``llm_schema()``      — a JSON Schema (non-recursive, depth-unrolled) handed to
                             llama-server's ``response_format: json_schema`` so the
                             brain can only *emit* valid component trees
                             (grammar-constrained decoding; proven live 2026-07-03).
  2. ``validate_component_tree()`` — server-side validation for any tree before it
                             is rendered (defence in depth: the renderer also only
                             knows catalog components).
  3. ``catalog_doc()``     — the prompt block describing the vocabulary to the brain.

Security model (A2UI-aligned): the model can only reference this pre-approved
catalog — never HTML, never scripts, never foreign URLs. Actions re-use the
shipped ``card_contract.validate_component_action`` envelope and are re-validated
server-side when fired; image sources must be same-origin relative paths.
"""
from __future__ import annotations

import copy
from typing import Any

from card_contract import CardContractError, validate_component_action

# ── Tunables ─────────────────────────────────────────────────────────────────
MAX_TREE_DEPTH = 6        # validator bound (generous)
LLM_TREE_DEPTH = 4        # unrolled depth in the LLM schema (grammar-safe: no recursion)
MAX_TREE_NODES = 60
MAX_TEXT_CHARS = 4000     # total across the tree — a card, not a novel

_GAP = ["sm", "md", "lg"]
_TEXT_ROLES = ["title", "body", "caption", "kicker"]
_BADGE_TONES = ["neutral", "accent", "warn", "success"]
_GLYPHS = ["calendar", "list", "weather", "person", "clock", "home", "music", "camera", "timer", "note",
           "droplet", "heart", "check", "star", "dollar", "chart", "flame", "leaf"]
# Card themes — a tone tints the whole card with a subtle gradient wash and sets
# the accent every child (Badge/Progress/Hero/Glyph) coordinates to. This is how
# a composed card gets the "designed" look of the hand-built hero scenes.
_TONES = ["neutral", "cool", "warm", "mint", "violet", "sunny"]
_GLYPH_SIZES = ["sm", "md", "lg", "xl"]

# component -> {"props": {name: schema-ish spec}, "required": [...], "container": bool}
# Spec forms: {"enum": [...]}, {"type": "string"|"boolean"}, {"type": "integer"|"number",
# "minimum": x, "maximum": y}, {"action": True} (validated via validate_component_action),
# {"src": True} (same-origin relative path).
CATALOG: dict[str, dict[str, Any]] = {
    # Containers accept an optional card-level `tone` (only themes when on the root).
    "Stack": {"container": True, "props": {"gap": {"enum": _GAP}, "tone": {"enum": _TONES}}},
    "Row": {"container": True, "props": {"gap": {"enum": _GAP}, "align": {"enum": ["start", "center", "end", "between"]}, "tone": {"enum": _TONES}}},
    "Grid": {"container": True, "props": {"columns": {"type": "integer", "minimum": 1, "maximum": 4}, "tone": {"enum": _TONES}}},
    "Text": {"props": {"text": {"type": "string"}, "role": {"enum": _TEXT_ROLES}}, "required": ["text"]},
    # Hero — the premium headline banner: a big glyph + a large display value(+unit)
    # + caption on a gradient wash. This is the composed-card answer to the weather
    # hero's headline moment.
    "Hero": {"props": {"value": {"type": "string"}, "unit": {"type": "string"},
                        "caption": {"type": "string"}, "glyph": {"enum": _GLYPHS},
                        "tone": {"enum": _TONES}}, "required": ["value"]},
    "Stat": {"props": {"value": {"type": "string"}, "label": {"type": "string"}}, "required": ["value"]},
    "Badge": {"props": {"text": {"type": "string"}, "tone": {"enum": _BADGE_TONES}}, "required": ["text"]},
    "ListRow": {"props": {"title": {"type": "string"}, "detail": {"type": "string"},
                           "checked": {"type": "boolean"}, "variant": {"enum": ["plain", "check"]},
                           "icon": {"enum": _GLYPHS}},
                 "required": ["title"]},
    "Progress": {"props": {"value": {"type": "number", "minimum": 0, "maximum": 100},
                            "label": {"type": "string"}}, "required": ["value"]},
    "Glyph": {"props": {"name": {"enum": _GLYPHS}, "size": {"enum": _GLYPH_SIZES}, "tone": {"enum": _TONES}}, "required": ["name"]},
    "Image": {"props": {"src": {"src": True}, "alt": {"type": "string"}}, "required": ["src"]},
    "Divider": {"props": {}},
    "Spacer": {"props": {"size": {"enum": _GAP}}},
    "ActionButton": {"props": {"action": {"action": True}}, "required": ["action"]},
    "MediaTile": {"props": {"src": {"src": True}, "title": {"type": "string"},
                             "subtitle": {"type": "string"}}, "required": ["src"]},
}

_CONTAINERS = frozenset(name for name, spec in CATALOG.items() if spec.get("container"))
_ROOTS = ("Stack", "Row", "Grid")  # a card body is always a layout container


# ── 1) LLM schema (depth-unrolled — llama.cpp grammar-safe, no recursion) ────

def _prop_json_schema(spec: dict[str, Any]) -> dict[str, Any]:
    if spec.get("action"):
        # The LLM only needs the emit shape; full semantics re-validated server-side.
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "label": {"type": "string"},
                "kind": {"enum": ["primary", "normal", "warn"]},
                "query": {"type": "string"},
            },
            "required": ["label", "query"],
        }
    if spec.get("src"):
        # llama.cpp's grammar converter requires fully-anchored patterns (^...$).
        # This only nudges the LLM toward same-origin paths; the real gate is
        # _validate_prop's server-side check (which also rejects '//').
        return {"type": "string", "pattern": "^/.*$"}
    return {k: v for k, v in spec.items() if k in ("type", "enum", "minimum", "maximum")}


def _node_schema(depth: int) -> dict[str, Any]:
    """Schema for one node at ``depth`` — containers reference depth+1 ($defs, non-recursive)."""
    branches: list[dict[str, Any]] = []
    for name, spec in CATALOG.items():
        props: dict[str, Any] = {"component": {"const": name}}
        for pname, pspec in spec.get("props", {}).items():
            props[pname] = _prop_json_schema(pspec)
        required = ["component"] + list(spec.get("required", []))
        node: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
            "required": required,
        }
        if spec.get("container"):
            if depth >= LLM_TREE_DEPTH:
                continue  # at max depth containers are omitted — leaves only
            node["properties"]["children"] = {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {"$ref": f"#/$defs/node{depth + 1}"},
            }
            node["required"].append("children")
        branches.append(node)
    return {"anyOf": branches}


def llm_schema() -> dict[str, Any]:
    """The JSON Schema handed to llama-server as ``response_format.json_schema.schema``."""
    defs = {f"node{d}": _node_schema(d) for d in range(2, LLM_TREE_DEPTH + 1)}
    root_props: dict[str, Any] = {
        "component": {"enum": list(_ROOTS)},
        "children": {"type": "array", "minItems": 1, "maxItems": 12,
                      "items": {"$ref": "#/$defs/node2"}},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": root_props,
        "required": ["component", "children"],
        "$defs": defs,
    }


# ── 2) Server-side tree validation (defence in depth) ────────────────────────

def _fail(msg: str) -> None:
    raise CardContractError(msg)


def _validate_prop(component: str, pname: str, pspec: dict[str, Any], value: Any) -> Any:
    if pspec.get("action"):
        return validate_component_action(value)
    if pspec.get("src"):
        if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
            _fail(f"{component}.{pname} must be a same-origin path starting with '/'")
        return value
    if "enum" in pspec:
        if value not in pspec["enum"]:
            _fail(f"{component}.{pname} must be one of {pspec['enum']}")
        return value
    t = pspec.get("type")
    if t == "string":
        if not isinstance(value, str):
            _fail(f"{component}.{pname} must be a string")
    elif t == "boolean":
        if not isinstance(value, bool):
            _fail(f"{component}.{pname} must be a boolean")
    elif t in ("integer", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail(f"{component}.{pname} must be a number")
        if t == "integer" and not isinstance(value, int):
            _fail(f"{component}.{pname} must be an integer")
        lo, hi = pspec.get("minimum"), pspec.get("maximum")
        if lo is not None and value < lo:
            _fail(f"{component}.{pname} below minimum {lo}")
        if hi is not None and value > hi:
            _fail(f"{component}.{pname} above maximum {hi}")
    return value


def validate_component_tree(tree: Any) -> dict[str, Any]:
    """Validate + normalize a composed component tree against the catalog.

    Returns a deep-copied, normalized tree. Raises CardContractError on any
    violation — unknown component, unknown/invalid prop, depth/node/text bombs,
    or an invalid action payload.
    """
    if not isinstance(tree, dict):
        _fail("component tree must be an object")
    counters = {"nodes": 0, "text": 0}

    def walk(node: Any, depth: int) -> dict[str, Any]:
        if not isinstance(node, dict):
            _fail("tree node must be an object")
        if depth > MAX_TREE_DEPTH:
            _fail(f"tree depth exceeds {MAX_TREE_DEPTH}")
        counters["nodes"] += 1
        if counters["nodes"] > MAX_TREE_NODES:
            _fail(f"tree exceeds {MAX_TREE_NODES} nodes")
        name = node.get("component")
        spec = CATALOG.get(name)  # type: ignore[arg-type]
        if spec is None:
            _fail(f"unknown component: {name!r}")
        out: dict[str, Any] = {"component": name}
        prop_specs = spec.get("props", {})
        for key, value in node.items():
            if key in ("component", "children"):
                continue
            pspec = prop_specs.get(key)
            if pspec is None:
                _fail(f"{name} does not accept prop {key!r}")
            out[key] = _validate_prop(name, key, pspec, value)
            validated = out[key]
            if isinstance(validated, str):
                counters["text"] += len(validated)
            elif isinstance(validated, dict):  # action payloads: count their text too
                counters["text"] += sum(len(v) for v in validated.values() if isinstance(v, str))
            if counters["text"] > MAX_TEXT_CHARS:
                _fail(f"tree text exceeds {MAX_TEXT_CHARS} chars")
        for req in spec.get("required", []):
            if req not in out:
                _fail(f"{name} missing required prop {req!r}")
        children = node.get("children")
        if children is not None:
            if name not in _CONTAINERS:
                _fail(f"{name} cannot have children")
            if not isinstance(children, list) or not children:
                _fail(f"{name}.children must be a non-empty list")
            out["children"] = [walk(child, depth + 1) for child in children]
        elif name in _CONTAINERS:
            _fail(f"{name} requires children")
        return out

    if tree.get("component") not in _ROOTS:
        _fail(f"tree root must be one of {list(_ROOTS)}")
    return copy.deepcopy(walk(tree, 1))


# ── 3) Prompt vocabulary block ────────────────────────────────────────────────

def catalog_doc() -> str:
    """Compact vocabulary description for the composition prompt."""
    lines = ["You compose a card as a JSON tree using ONLY these components:"]
    for name, spec in CATALOG.items():
        bits = []
        for pname, pspec in spec.get("props", {}).items():
            if "enum" in pspec:
                bits.append(f"{pname}∈{{{','.join(map(str, pspec['enum']))}}}")
            elif pspec.get("action"):
                bits.append(f"{pname}={{label,query}}")
            else:
                bits.append(pname)
        req = set(spec.get("required", []))
        rendered = ", ".join(("*" + b if b.split("∈")[0].split("=")[0] in req else b) for b in bits)
        kind = "container(children)" if spec.get("container") else "leaf"
        lines.append(f"- {name} [{kind}] {rendered}")
    lines.append("Root must be Stack, Row, or Grid. Keep cards glanceable: prefer Stat/Badge/ListRow over long Text.")
    return "\n".join(lines)
