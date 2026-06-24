"""Zoe card contract schema helpers.

This module defines the stable envelope that Zoe producers should emit and
renderers should accept before card-specific adoption work begins.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID


class CardContractError(ValueError):
    """Raised when a card contract does not satisfy the Zoe card schema."""


class CardType(str, Enum):
    """Renderer-facing card payload families."""

    GENERIC = "generic"
    ACTION_FORM = "action_form"
    MEDIA = "media"
    RESEARCH_REPORT = "research_report"
    SMART_HOME = "smart_home"
    FORM = "form"
    STREAM_TEXT = "stream_text"
    LIST = "list"
    FIELD_UPDATE = "field_update"
    CLOSE_ACTION_FORM = "close_action_form"


REQUIRED_FIELDS = {
    "card_id",
    "schema_version",
    "card_type",
    "content",
    "producer",
    "producer_version",
    "created_at",
}

RESERVED_PREFIXES = ("zoe_", "_zoe_")

CONTENT_REQUIRED_FIELDS: dict[CardType, set[str]] = {
    CardType.GENERIC: {"title"},
    CardType.ACTION_FORM: {"form_id", "title", "fields"},
    CardType.MEDIA: {"title", "items"},
    CardType.RESEARCH_REPORT: {"title", "sections"},
    CardType.SMART_HOME: {"title", "devices"},
    CardType.FORM: {"form_id", "title", "fields"},
    CardType.STREAM_TEXT: {"stream_id", "text"},
    CardType.LIST: {"list_id", "items"},
    CardType.FIELD_UPDATE: {"field_id", "value"},
    CardType.CLOSE_ACTION_FORM: {"form_id"},
}


def parse_semver(version: str) -> tuple[int, int, int]:
    """Parse strict MAJOR.MINOR.PATCH semver into integers."""
    parts = str(version or "").split(".")
    if len(parts) != 3:
        raise CardContractError("schema_version must use MAJOR.MINOR.PATCH")
    if any(part != part.strip() for part in parts):
        raise CardContractError("schema_version must contain numeric semver parts")
    if any(len(part) > 1 and part.startswith("0") for part in parts):
        raise CardContractError("schema_version cannot contain leading-zero semver parts")
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError as exc:
        raise CardContractError("schema_version must contain numeric semver parts") from exc
    if major < 0 or minor < 0 or patch < 0:
        raise CardContractError("schema_version cannot contain negative parts")
    return major, minor, patch


def renderer_accepts(contract_version: str, *, supported_major: int) -> bool:
    """Return True when a renderer can accept this contract major version.

    Compatibility rule: renderers accept a card iff their supported MAJOR is
    greater than or equal to the contract's MAJOR. MINOR is additive optional
    surface area; PATCH is clarification only.
    """
    contract_major, _minor, _patch = parse_semver(contract_version)
    return int(supported_major) >= contract_major


def _parse_created_at(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise CardContractError("created_at is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CardContractError("created_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise CardContractError("created_at must include timezone")
    return parsed.astimezone(timezone.utc)


def _validate_uuid(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except ValueError as exc:
        raise CardContractError("card_id must be a UUID string") from exc


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise CardContractError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise CardContractError(f"{field_name} cannot be empty")
    return text


def _card_type(value: str) -> CardType:
    try:
        return CardType(str(value))
    except ValueError as exc:
        allowed = ", ".join(card_type.value for card_type in CardType)
        raise CardContractError(f"card_type must be one of: {allowed}") from exc


def _validate_content(card_type: CardType, content: Any) -> dict[str, Any]:
    if not isinstance(content, dict):
        raise CardContractError("content must be an object")
    missing = sorted(CONTENT_REQUIRED_FIELDS[card_type] - set(content))
    if missing:
        raise CardContractError(
            f"content for {card_type.value} is missing required field(s): {', '.join(missing)}"
        )
    return deepcopy(content)


def validate_card_contract(contract: dict[str, Any], *, supported_major: int | None = None) -> dict[str, Any]:
    """Validate and normalize a Zoe card contract.

    Unknown fields are tolerated and ignored for validation purposes so renderers
    can remain forward-compatible. Producers should keep Zoe-internal fields
    under ``zoe_*`` or ``_zoe_*`` reserved prefixes.
    """
    if not isinstance(contract, dict):
        raise CardContractError("card contract must be an object")
    missing = sorted(REQUIRED_FIELDS - set(contract))
    if missing:
        raise CardContractError(f"missing required field(s): {', '.join(missing)}")

    schema_version = str(contract["schema_version"])
    parse_semver(schema_version)
    if supported_major is not None and not renderer_accepts(schema_version, supported_major=supported_major):
        raise CardContractError(
            f"renderer supports MAJOR {supported_major}, cannot accept schema_version {schema_version}"
        )

    card_type = _card_type(contract["card_type"])
    normalized = {
        "card_id": _validate_uuid(contract["card_id"]),
        "schema_version": schema_version,
        "card_type": card_type.value,
        "content": _validate_content(card_type, contract["content"]),
        "producer": _required_text(contract["producer"], "producer"),
        "producer_version": _required_text(contract["producer_version"], "producer_version"),
        "created_at": _parse_created_at(contract["created_at"]).isoformat().replace("+00:00", "Z"),
    }
    if contract.get("idempotency_key") is not None:
        normalized["idempotency_key"] = _required_text(contract["idempotency_key"], "idempotency_key")
    return normalized


def reserved_field_names(fields: dict[str, Any]) -> list[str]:
    """Return field names using Zoe-reserved extension prefixes."""
    return sorted(
        name
        for name in fields
        if any(str(name).startswith(prefix) for prefix in RESERVED_PREFIXES)
    )


# ── Interactive component contract ──────────────────────────────────────────
# A "component" is a renderer-agnostic interactive payload the brain (or the
# fast path) emits and chat/touch/orb render identically: a display `component`
# + `props`, plus optional `actions` that re-enter Zoe's abilities. An action is
# either a natural-language re-dispatch (`query`) or a typed intent dispatch
# (`intent` + `args`). Actions are ALWAYS re-validated server-side before
# execution — the client payload is never trusted to perform the action itself.

ALLOWED_ACTION_KINDS = {"primary", "normal", "warn"}


def validate_component_action(action: Any) -> dict[str, Any]:
    """Validate one interactive component action.

    Requires `label` and at least one target: `query` (NL re-dispatch), `intent`
    (typed dispatch, with optional `args`), or `route` (in-surface navigation, as
    Skybridge cards use). `kind` styles the control. This is a SUPERSET so the one
    contract validates both the chat `zoe.component` actions and the existing
    Skybridge card actions.
    """
    if not isinstance(action, dict):
        raise CardContractError("component action must be an object")
    label = _required_text(action.get("label"), "action.label")
    kind = str(action.get("kind") or "normal")
    if kind not in ALLOWED_ACTION_KINDS:
        raise CardContractError(
            f"action.kind must be one of: {', '.join(sorted(ALLOWED_ACTION_KINDS))}"
        )
    query = str(action.get("query") or "").strip()
    intent = str(action.get("intent") or "").strip()
    route = str(action.get("route") or "").strip()
    if not query and not intent and not route:
        raise CardContractError("component action requires 'query', 'intent', or 'route'")
    normalized: dict[str, Any] = {"label": label, "kind": kind}
    if query:
        normalized["query"] = query
    if route:
        normalized["route"] = route
    if intent:
        normalized["intent"] = intent
        args = action.get("args")
        if args is None:
            args = action.get("slots") or {}
        if not isinstance(args, dict):
            raise CardContractError("component action args must be an object")
        normalized["args"] = deepcopy(args)
    return normalized


def validate_component(payload: Any) -> dict[str, Any]:
    """Validate and normalize an interactive component payload.

    Shape: ``{component: str, props: dict, actions?: [action]}``. This is the
    renderer-agnostic envelope carried in the AG-UI ``zoe.component`` CUSTOM
    event and the Skybridge ``{component, props}`` card shape.
    """
    if not isinstance(payload, dict):
        raise CardContractError("component must be an object")
    component = _required_text(payload.get("component"), "component")
    props = payload.get("props")
    if props is None:
        props = {}
    if not isinstance(props, dict):
        raise CardContractError("component props must be an object")
    # Accept actions at the top level (chat zoe.component) OR nested in props
    # (Skybridge cards put them there) — one contract, both shapes.
    raw_actions = payload.get("actions")
    if raw_actions is None:
        raw_actions = props.get("actions")
    if raw_actions is None:
        raw_actions = []
    if not isinstance(raw_actions, list):
        raise CardContractError("component actions must be a list")
    actions = [validate_component_action(a) for a in raw_actions]
    normalized: dict[str, Any] = {"component": component, "props": deepcopy(props)}
    if actions:
        normalized["actions"] = actions
    return normalized
