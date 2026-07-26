"""Tests for Zoe card contract validation."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from card_contract import (
    CardContractError,
    CardType,
    CONTENT_REQUIRED_FIELDS,
    parse_semver,
    renderer_accepts,
    reserved_field_names,
    validate_card_contract,
)

pytestmark = pytest.mark.ci_safe


FIXTURE_DIR = Path(__file__).parent / "fixtures"


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


def _load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_valid_card_contract_normalizes_core_fields():
    normalized = validate_card_contract(_contract(), supported_major=1)

    assert normalized["card_type"] == "action_form"
    assert normalized["card_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert normalized["created_at"] == "2026-06-08T04:00:00Z"
    assert normalized["idempotency_key"] == "card:reminder:1"


def test_valid_card_contract_fixtures_normalize_without_unknown_envelope_fields():
    cases = _load_fixture("card_contract_valid.json")
    assert cases, "card_contract_valid.json must contain at least one test case"

    for case in cases:
        normalized = validate_card_contract(case["card"], supported_major=1)

        assert normalized["card_id"] == case["card"]["card_id"]
        assert normalized["schema_version"] == case["card"]["schema_version"]
        assert normalized["card_type"] == case["card"]["card_type"]
        assert normalized["producer"] == case["card"]["producer"]
        assert "name" not in normalized


def test_invalid_card_contract_fixtures_return_actionable_errors():
    cases = _load_fixture("card_contract_invalid.json")
    assert cases, "card_contract_invalid.json must contain at least one test case"

    for case in cases:
        with pytest.raises(CardContractError) as exc_info:
            validate_card_contract(
                case["card"],
                supported_major=case.get("supported_major"),
            )

        message = str(exc_info.value)
        assert case["error_contains"] in message


def test_missing_required_field_is_rejected():
    payload = _contract()
    payload.pop("producer")

    with pytest.raises(CardContractError, match="missing required field"):
        validate_card_contract(payload)


def test_invalid_card_type_is_rejected():
    with pytest.raises(CardContractError, match="card_type must be one of"):
        validate_card_contract(_contract(card_type="unknown"))


@pytest.mark.parametrize("field", ["producer", "producer_version"])
def test_producer_fields_require_non_empty_text(field):
    with pytest.raises(CardContractError, match=field):
        validate_card_contract(_contract(**{field: None}))
    with pytest.raises(CardContractError, match=field):
        validate_card_contract(_contract(**{field: "   "}))


def test_idempotency_key_is_optional_but_not_blank():
    assert "idempotency_key" not in validate_card_contract(_contract(idempotency_key=None))
    with pytest.raises(CardContractError, match="idempotency_key"):
        validate_card_contract(_contract(idempotency_key="   "))


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


@pytest.mark.parametrize("card_type", list(CardType))
def test_every_card_type_has_minimal_valid_content(card_type):
    content = {
        field: [] if field in {"devices", "fields", "items", "sections"} else f"{field}-value"
        for field in CONTENT_REQUIRED_FIELDS[card_type]
    }

    normalized = validate_card_contract(_contract(card_type=card_type.value, content=content))

    assert normalized["card_type"] == card_type.value
    assert set(CONTENT_REQUIRED_FIELDS[card_type]).issubset(normalized["content"])


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


def test_validate_card_contract_always_rejects_invalid_semver():
    with pytest.raises(CardContractError, match="MAJOR.MINOR.PATCH"):
        validate_card_contract(_contract(schema_version="not-semver"))


def test_validate_card_contract_rejects_whitespace_padded_semver():
    with pytest.raises(CardContractError, match="numeric semver parts"):
        validate_card_contract(_contract(schema_version=" 1.0.0 "))


def test_validate_card_contract_rejects_leading_zero_semver_parts():
    with pytest.raises(CardContractError, match="leading-zero"):
        validate_card_contract(_contract(schema_version="01.0.0"))


def test_normalized_content_does_not_share_nested_mutable_values():
    payload = _contract(content={"form_id": "reminder", "title": "Reminder", "fields": []})

    normalized = validate_card_contract(payload)
    normalized["content"]["fields"].append({"name": "when"})

    assert payload["content"]["fields"] == []


def test_parse_semver_is_strict():
    assert parse_semver("1.2.3") == (1, 2, 3)
    with pytest.raises(CardContractError):
        parse_semver("1.2")


def test_reserved_field_names_identifies_zoe_extensions():
    assert reserved_field_names({"zoe_trace": "x", "_zoe_internal": True, "title": "Hi"}) == [
        "_zoe_internal",
        "zoe_trace",
    ]
