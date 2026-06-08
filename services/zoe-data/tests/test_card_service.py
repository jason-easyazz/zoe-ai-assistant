"""Tests for Zoe card service foundation."""

import pytest
from unittest.mock import Mock
from card_service import CardService, card_service
from card_contract import CardContractError, CardType


def test_card_service_build_valid_card():
    """Test building a valid card contract."""
    service = CardService()
    
    content = {
        "title": "Test Card",
        "message": "This is a test"
    }
    
    card = service.build_card("generic", content)
    
    assert "card_id" in card
    assert card["card_type"] == "generic"
    assert card["schema_version"] == "1.0.0"
    assert card["content"] == content
    assert card["producer"] == "zoe-card-service"
    assert "producer_version" in card
    assert "created_at" in card


def test_card_service_validate_valid_contract():
    """Test validating a valid card contract."""
    service = CardService()
    
    valid_contract = {
        "card_id": "123e4567-e89b-12d3-a456-426614174000",
        "schema_version": "1.0.0",
        "card_type": "generic",
        "content": {"title": "Test"},
        "producer": "test",
        "producer_version": "1.0.0",
        "created_at": "2026-01-01T00:00:00Z"
    }
    
    validated = service.validate_card(valid_contract)
    assert validated["card_id"] == valid_contract["card_id"]
    assert validated["card_type"] == "generic"


def test_card_service_validate_invalid_contract():
    """Test validating an invalid card contract raises error."""
    service = CardService()
    
    invalid_contract = {
        "card_id": "invalid",
        "schema_version": "1.0.0",
        "card_type": "generic",
        "content": {},
        "producer": "test",
        "producer_version": "1.0.0",
        "created_at": "2026-01-01T00:00:00Z"
    }
    
    with pytest.raises(CardContractError):
        service.validate_card(invalid_contract)


def test_card_service_convert_emit_compatible():
    """Test converting compatible contract for emission."""
    service = CardService()
    
    contract = {
        "card_id": "123e4567-e89b-12d3-a456-426614174000",
        "schema_version": "1.0.0",
        "card_type": "generic",
        "content": {"title": "Test"},
        "producer": "test",
        "producer_version": "1.0.0",
        "created_at": "2026-01-01T00:00:00Z"
    }
    
    emitted = service.convert_emit(contract, target_major=1)
    assert emitted == contract


def test_card_service_convert_emit_incompatible():
    """Test converting incompatible contract raises error."""
    service = CardService()
    
    contract = {
        "card_id": "123e4567-e89b-12d3-a456-426614174000",
        "schema_version": "2.0.0",
        "card_type": "generic",
        "content": {"title": "Test"},
        "producer": "test",
        "producer_version": "1.0.0",
        "created_at": "2026-01-01T00:00:00Z"
    }
    
    with pytest.raises(CardContractError):
        service.convert_emit(contract, target_major=1)


def test_card_service_convert_emit_missing_schema_version_raises_card_error():
    service = CardService()
    with pytest.raises(CardContractError, match="schema_version is required"):
        service.convert_emit({"card_type": "generic"}, target_major=1)


def test_card_service_domain_builder_registry():
    """Test domain builder registry functionality."""
    service = CardService()
    
    # Test registry is initially empty
    assert service.get_domain_builder("generic") is None
    
    # Register a builder
    builder = Mock()
    service.register_domain_builder("generic", builder)
    
    # Verify retrieval
    assert service.get_domain_builder("generic") is builder
    assert service.get_domain_builder("nonexistent") is None


def test_global_card_service_instance():
    """Test global card service instance."""
    assert isinstance(card_service, CardService)

    card_service._domain_builders.clear()
    try:
        # Test domain builder registry on global instance
        assert card_service.get_domain_builder("generic") is None

        builder = Mock()
        card_service.register_domain_builder("generic", builder)
        assert card_service.get_domain_builder("generic") is builder
    finally:
        card_service._domain_builders.clear()
