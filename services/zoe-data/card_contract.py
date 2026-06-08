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
