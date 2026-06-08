"""Zoe card service foundation for Phase 3 card upgrade.

Implements build/validate/convert_emit against Phase 2 contract.
"""

from typing import Any
from card_contract import (
    validate_card_contract,
    CardContractError,
    renderer_accepts,
)


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


# Global singleton instance
card_service = CardService()