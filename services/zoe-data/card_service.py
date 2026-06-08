"""Zoe card service foundation for Phase 3 card upgrade.

Implements build/validate/convert_emit against Phase 2 contract.
"""

from typing import Any
from card_contract import (
    validate_card_contract,
    CardContractError,
    CardType,
    renderer_accepts,
)


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def build_calendar_timeline_content(slots: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build canonical content for a calendar timeline summary card."""
    slots = slots or {}
    qualifier = _text(slots.get("qualifier") or slots.get("date"), "today")
    events = slots.get("events") or []
    if not isinstance(events, list):
        events = [events]
    return {
        "title": f"Calendar for {qualifier}",
        "summary": "Showing calendar",
        "qualifier": qualifier,
        "events": events,
        "view": "timeline",
        "source": "calendar_show",
    }


def build_weather_current_content(slots: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build canonical content for current weather data."""
    slots = slots or {}
    current = slots.get("current") or {}
    forecast = slots.get("forecast") or {}
    location = slots.get("location") or {}
    city = _text(location.get("city") or current.get("city"), "Weather")
    country = _text(location.get("country") or current.get("country"))
    return {
        "title": f"Weather in {city}",
        "summary": _text(current.get("description"), "Current conditions"),
        "source": "weather_current",
        "view": "current",
        "location": {"city": city, "country": country},
        "current": {k: v for k, v in current.items() if not str(k).startswith("_")},
        "forecast": forecast,
    }


def build_weather_forecast_content(slots: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build canonical content for weather forecast data."""
    slots = slots or {}
    current = slots.get("current") or {}
    forecast = slots.get("forecast") or {}
    location = slots.get("location") or {}
    city = _text(location.get("city") or current.get("city"), "Weather")
    country = _text(location.get("country") or current.get("country"))
    return {
        "title": f"Forecast for {city}",
        "summary": "Upcoming weather",
        "source": "weather_forecast",
        "view": "forecast",
        "location": {"city": city, "country": country},
        "current": {k: v for k, v in current.items() if not str(k).startswith("_")},
        "forecast": forecast,
    }


def build_calendar_event_editor_content(slots: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build canonical content for a calendar event editor card."""
    slots = slots or {}
    title = _text(slots.get("title") or slots.get("event"), "New event")
    return {
        "form_id": "calendar_event_editor",
        "title": "New Calendar Event",
        "fields": [
            {"name": "title", "label": "Title", "type": "text", "value": title},
            {"name": "date", "label": "Date", "type": "date", "value": _text(slots.get("date"))},
            {"name": "time", "label": "Time", "type": "time", "value": _text(slots.get("time"))},
            {"name": "duration", "label": "Duration", "type": "text", "value": _text(slots.get("duration"))},
            {"name": "location", "label": "Location", "type": "text", "value": _text(slots.get("location"))},
            {"name": "notes", "label": "Notes", "type": "textarea", "value": _text(slots.get("notes"))},
        ],
        "values": {
            "title": title,
            "date": _text(slots.get("date")),
            "time": _text(slots.get("time")),
            "duration": _text(slots.get("duration")),
            "location": _text(slots.get("location")),
            "notes": _text(slots.get("notes")),
            "category": _text(slots.get("category"), "general"),
        },
        "source": "calendar_create",
    }


def _list_items(slots: dict[str, Any]) -> list[str]:
    raw_items = slots.get("items")
    if isinstance(raw_items, list):
        items = raw_items
    elif raw_items:
        items = [raw_items]
    else:
        item = slots.get("item") or slots.get("text")
        items = [item] if item else []
    return [_text(item) for item in items if _text(item)]


def build_shopping_list_content(slots: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build canonical content for a shopping/list view card."""
    slots = slots or {}
    list_name = _text(slots.get("list_name") or slots.get("list_type"), "Shopping")
    items = _list_items(slots)
    return {
        "title": f"{list_name} List",
        "list_name": list_name,
        "items": items,
        "item_count": len(items),
        "view": "list",
        "source": "list_show",
    }


def build_shopping_item_editor_content(slots: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build canonical content for a shopping/list item editor card."""
    slots = slots or {}
    list_name = _text(slots.get("list_name") or slots.get("list_type"), "Shopping")
    item = _text(slots.get("item") or slots.get("text"))
    return {
        "form_id": "shopping_item_editor",
        "title": "Add List Item",
        "fields": [
            {"name": "item", "label": "Item", "type": "text", "value": item},
            {"name": "list_name", "label": "List", "type": "text", "value": list_name},
            {"name": "quantity", "label": "Quantity", "type": "text", "value": _text(slots.get("quantity"))},
            {"name": "notes", "label": "Notes", "type": "textarea", "value": _text(slots.get("notes"))},
        ],
        "values": {
            "item": item,
            "list_name": list_name,
            "list_type": _text(slots.get("list_type"), "shopping"),
            "quantity": _text(slots.get("quantity")),
            "notes": _text(slots.get("notes")),
        },
        "source": "list_add",
    }


class CardService:
    """Service layer for Zoe card operations."""

    def __init__(self):
        self._domain_builders = {}

    def build_card(self, card_type: str, content: dict[str, Any], **kwargs) -> dict[str, Any]:
        """Build a Zoe card contract from domain-specific content.
        
        Args:
            card_type: CardType string
            content: Card-specific content payload
            **kwargs: Additional envelope fields (producer, producer_version, etc.)
            
        Returns:
            Validated and normalized card contract
        """
        from uuid import uuid4
        from datetime import datetime, timezone
        
        contract = {
            "card_id": str(uuid4()),
            "schema_version": "1.0.0",
            "card_type": card_type,
            "content": content,
            "producer": kwargs.get("producer", "zoe-card-service"),
            "producer_version": kwargs.get("producer_version", "1.0.0"),
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        
        if "idempotency_key" in kwargs:
            contract["idempotency_key"] = kwargs["idempotency_key"]
            
        return self.validate_card(contract)

    def validate_card(self, contract: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize a card contract against Phase 2 schema.
        
        Args:
            contract: Raw card contract
            
        Returns:
            Validated and normalized contract
            
        Raises:
            CardContractError: If contract fails validation
        """
        return validate_card_contract(contract)

    def convert_emit(self, contract: dict[str, Any], target_major: int = 1) -> dict[str, Any]:
        """Convert card contract for emission to renderers.
        
        Args:
            contract: Validated card contract
            target_major: Target major version for renderer compatibility
            
        Returns:
            Contract ready for emission
            
        Raises:
            CardContractError: If renderer cannot accept the contract
        """
        schema_version = contract.get("schema_version")
        if schema_version is None:
            raise CardContractError("schema_version is required")
        if not renderer_accepts(str(schema_version), supported_major=target_major):
            raise CardContractError(
                f"Renderer MAJOR {target_major} cannot accept schema_version {schema_version}"
            )
        return contract

    def register_domain_builder(self, card_type: str, builder_func) -> None:
        """Register a domain-specific card builder function.
        
        Args:
            card_type: CardType string
            builder_func: Function that returns card content
        """
        self._domain_builders[card_type] = builder_func

    def get_domain_builder(self, card_type: str):
        """Get registered domain builder for card type.
        
        Args:
            card_type: CardType string
            
        Returns:
            Builder function or None if not registered
        """
        return self._domain_builders.get(card_type)


    def build_calendar_timeline_card(self, slots: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.build_card(
            CardType.GENERIC.value,
            build_calendar_timeline_content(slots),
            producer="zoe-calendar",
        )

    def build_weather_current_card(self, slots: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.build_card(
            CardType.GENERIC.value,
            build_weather_current_content(slots),
            producer="zoe-weather",
        )

    def build_weather_forecast_card(self, slots: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.build_card(
            CardType.GENERIC.value,
            build_weather_forecast_content(slots),
            producer="zoe-weather",
        )

    def build_calendar_event_editor_card(self, slots: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.build_card(
            CardType.ACTION_FORM.value,
            build_calendar_event_editor_content(slots),
            producer="zoe-calendar",
        )


    def build_shopping_list_card(self, slots: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.build_card(
            CardType.GENERIC.value,
            build_shopping_list_content(slots),
            producer="zoe-shopping",
        )

    def build_shopping_item_editor_card(self, slots: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.build_card(
            CardType.ACTION_FORM.value,
            build_shopping_item_editor_content(slots),
            producer="zoe-shopping",
        )



# Global singleton instance
card_service = CardService()
card_service.register_domain_builder("calendar_timeline", card_service.build_calendar_timeline_card)
card_service.register_domain_builder("calendar_event_editor", card_service.build_calendar_event_editor_card)
card_service.register_domain_builder("weather_current", card_service.build_weather_current_card)
card_service.register_domain_builder("weather_forecast", card_service.build_weather_forecast_card)
card_service.register_domain_builder("shopping_list", card_service.build_shopping_list_card)
card_service.register_domain_builder("shopping_item_editor", card_service.build_shopping_item_editor_card)
