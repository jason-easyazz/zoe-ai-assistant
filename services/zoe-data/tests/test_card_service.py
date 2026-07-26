"""Tests for Zoe card service foundation."""

import pytest
from unittest.mock import Mock
from card_service import CardService, card_service, list_items
from card_contract import CardContractError, CardType

pytestmark = pytest.mark.ci_safe


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
        card_service.register_domain_builder("calendar_timeline", card_service.build_calendar_timeline_card)
        card_service.register_domain_builder("calendar_event_editor", card_service.build_calendar_event_editor_card)
        card_service.register_domain_builder("weather_current", card_service.build_weather_current_card)
        card_service.register_domain_builder("weather_forecast", card_service.build_weather_forecast_card)
        card_service.register_domain_builder("shopping_list", card_service.build_shopping_list_card)
        card_service.register_domain_builder("shopping_item_editor", card_service.build_shopping_item_editor_card)


def test_card_service_build_calendar_timeline_card():
    service = CardService()
    card = service.build_calendar_timeline_card({"qualifier": "tomorrow", "events": [{"title": "School"}]})

    assert card["card_type"] == CardType.GENERIC.value
    assert card["producer"] == "zoe-calendar"
    assert card["content"]["view"] == "timeline"
    assert card["content"]["qualifier"] == "tomorrow"
    assert card["content"]["events"] == [{"title": "School"}]


def test_card_service_build_calendar_event_editor_card():
    service = CardService()
    card = service.build_calendar_event_editor_card({"title": "Dentist", "date": "tomorrow", "time": "9am"})

    assert card["card_type"] == CardType.ACTION_FORM.value
    assert card["producer"] == "zoe-calendar"
    assert card["content"]["form_id"] == "calendar_event_editor"
    assert card["content"]["values"]["title"] == "Dentist"
    assert card["content"]["values"]["date"] == "tomorrow"
    assert {field["name"] for field in card["content"]["fields"]} >= {"title", "date", "time"}


def test_global_card_service_registers_calendar_builders():
    timeline = card_service.get_domain_builder("calendar_timeline")
    editor = card_service.get_domain_builder("calendar_event_editor")

    assert callable(timeline)
    assert callable(editor)
    assert timeline({"qualifier": "today"})["content"]["view"] == "timeline"
    assert editor({"title": "Dentist"})["content"]["form_id"] == "calendar_event_editor"


def test_list_items_normalizes_slots():
    assert list_items({"items": [" milk ", 123, ""]}) == ["milk", "123"]
    assert list_items({"item": " bread "}) == ["bread"]
    assert list_items({"text": " eggs "}) == ["eggs"]


def test_card_service_build_shopping_list_card():
    service = CardService()
    card = service.build_shopping_list_card({"list_name": "Groceries", "items": ["milk", "bread"]})

    assert card["card_type"] == CardType.GENERIC.value
    assert card["producer"] == "zoe-shopping"
    assert card["content"]["view"] == "list"
    assert card["content"]["list_name"] == "Groceries"
    assert card["content"]["items"] == ["milk", "bread"]
    assert card["content"]["item_count"] == 2


def test_card_service_build_shopping_list_card_accepts_float_string_counts():
    service = CardService()
    card = service.build_shopping_list_card(
        {
            "list_name": "Groceries",
            "items": ["milk"],
            "item_count": "3.0",
            "open_count": "2.0",
            "completed_count": "1.0",
        }
    )

    assert card["content"]["item_count"] == 3
    assert card["content"]["open_count"] == 2
    assert card["content"]["completed_count"] == 1


def test_card_service_build_shopping_item_editor_card():
    service = CardService()
    card = service.build_shopping_item_editor_card({"list_name": "Groceries", "item": "milk", "quantity": "2"})

    assert card["card_type"] == CardType.ACTION_FORM.value
    assert card["producer"] == "zoe-shopping"
    assert card["content"]["form_id"] == "shopping_item_editor"
    assert card["content"]["values"]["item"] == "milk"
    assert card["content"]["values"]["list_name"] == "Groceries"
    assert {field["name"] for field in card["content"]["fields"]} >= {"item", "list_name", "quantity"}


def test_card_service_build_shopping_item_editor_uses_items_slot():
    service = CardService()
    card = service.build_shopping_item_editor_card({"list_name": "Groceries", "items": [" milk "]})

    assert card["content"]["values"]["item"] == "milk"


def test_global_card_service_registers_shopping_builders():
    list_builder = card_service.get_domain_builder("shopping_list")
    editor_builder = card_service.get_domain_builder("shopping_item_editor")

    assert callable(list_builder)
    assert callable(editor_builder)
    assert list_builder({"items": ["milk"]})["content"]["view"] == "list"
    assert editor_builder({"item": "milk"})["content"]["form_id"] == "shopping_item_editor"
