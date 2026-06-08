"""Tests for Zoe card contract validation."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from card_contract import (
    CardContractError,
    CardType,
    parse_semver,
    renderer_accepts,
    reserved_field_names,
    validate_card_contract,
)


def _contract(**overrides):
    payload = {
        "card_id": "550e8400-e29b-41d4-a716-446655440000",
        "schema_version": "1.0.0",
        "card_type": CardType.ACTION_FORM.value,
        "content": {"form_id": "reminder", "title": "Reminder", "fields": []},
        "producer": "zoe-data",
        "producer_version": "2026.06.08",
        "created_at": "2026-06-08T04:00:00Z",
        "idempotency_key": "card:reminder:1",
    }
    payload.update(overrides)
    return payload


def test_valid_card_contract_normalizes_core_fields():
    normalized = validate_card_contract(_contract(), supported_major=1)

    assert normalized["card_type"] == "action_form"
    assert normalized["card_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert normalized["created_at"] == "2026-06-08T04:00:00Z"
    assert normalized["idempotency_key"] == "card:reminder:1"


def test_missing_required_field_is_rejected():
    payload = _contract()
    payload.pop("producer")

    with pytest.raises(CardContractError, match="missing required field"):
        validate_card_contract(payload)


def test_invalid_card_type_is_rejected():
    with pytest.raises(CardContractError, match="card_type must be one of"):
        validate_card_contract(_contract(card_type="unknown"))


def test_content_required_fields_are_per_card_type():
    with pytest.raises(CardContractError, match="fields"):
        validate_card_contract(_contract(content={"form_id": "x", "title": "Missing fields"}))

    normalized = validate_card_contract(
        _contract(
            card_type=CardType.LIST.value,
            content={"list_id": "shopping", "items": ["milk"]},
        )
    )
    assert normalized["content"]["items"] == ["milk"]


def test_unknown_fields_are_tolerated_for_forward_compatibility():
    normalized = validate_card_contract(
        _contract(
            future_renderer_hint="compact",
            content={
                "form_id": "reminder",
                "title": "Reminder",
                "fields": [],
                "future_field": True,
            },
        )
    )

    assert normalized["content"]["future_field"] is True
    assert "future_renderer_hint" not in normalized


def test_renderer_accepts_contract_by_major_version():
    assert renderer_accepts("1.2.3", supported_major=1)
    assert renderer_accepts("1.2.3", supported_major=2)
    assert not renderer_accepts("2.0.0", supported_major=1)


def test_validate_card_contract_rejects_unsupported_major():
    with pytest.raises(CardContractError, match="cannot accept"):
        validate_card_contract(_contract(schema_version="2.0.0"), supported_major=1)


def test_parse_semver_is_strict():
    assert parse_semver("1.2.3") == (1, 2, 3)
    with pytest.raises(CardContractError):
        parse_semver("1.2")


def test_reserved_field_names_identifies_zoe_extensions():
    assert reserved_field_names({"zoe_trace": "x", "_zoe_internal": True, "title": "Hi"}) == [
        "_zoe_internal",
        "zoe_trace",
    ]
