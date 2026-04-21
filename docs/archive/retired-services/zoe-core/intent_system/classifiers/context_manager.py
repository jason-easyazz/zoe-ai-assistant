"""
Conversation Context Manager
============================

Tracks conversation context for resolving pronouns and references:
- "add those" → resolves "those" to previously mentioned items
- "turn it off" → resolves "it" to last mentioned device
- "that time" → resolves to previously mentioned time
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    """
    Conversation context for a user session.
    
    Tracks recent mentions of entities that can be referenced
    with pronouns like "it", "that", "those", "them".
    """
    user_id: str
    session_id: str
    
    # Recent mentions (for pronoun resolution)
    last_items: List[str] = field(default_factory=list)  # "bread", "milk"
    last_device: Optional[str] = None  # "living room light"
    last_list: Optional[str] = None  # "shopping"
    last_area: Optional[str] = None  # "kitchen"
    last_time: Optional[str] = None  # "3pm", "tomorrow"
    last_intent: Optional[str] = None  # "ListAdd"
    
    # Context expiry
    last_updated: datetime = field(default_factory=datetime.now)
    ttl_seconds: int = 300  # 5 minutes
    
    def is_expired(self) -> bool:
        """Check if context has expired."""
        return (datetime.now() - self.last_updated).seconds > self.ttl_seconds
    
    def update(
        self,
        items: Optional[List[str]] = None,
        device: Optional[str] = None,
        list_name: Optional[str] = None,
        area: Optional[str] = None,
        time_ref: Optional[str] = None,
        intent_name: Optional[str] = None
    ):
        """Update context with new mentions."""
        if items:
            self.last_items = items[-5:]  # Keep last 5 items
        if device:
            self.last_device = device
        if list_name:
            self.last_list = list_name
        if area:
            self.last_area = area
        if time_ref:
            self.last_time = time_ref
        if intent_name:
            self.last_intent = intent_name
        
        self.last_updated = datetime.now()


class ContextManager:
    """
    Manages conversation contexts for all users.
    
    Provides context resolution for pronouns and references.
    """
    
    def __init__(self):
        """Initialize context manager."""
        self.contexts: Dict[str, ConversationContext] = {}
        logger.info("Initialized ContextManager")
    
    def get_context(self, user_id: str, session_id: str = "default") -> ConversationContext:
        """
        Get or create context for user.
        
        Args:
            user_id: User identifier
            session_id: Session identifier (default: "default")
            
        Returns:
            ConversationContext for the user
        """
        key = f"{user_id}:{session_id}"
        
        # Clean expired contexts
        self._cleanup_expired()
        
        # Get or create
        if key not in self.contexts:
            self.contexts[key] = ConversationContext(
                user_id=user_id,
                session_id=session_id
            )
        
        return self.contexts[key]
    
    def update_from_intent(
        self,
        user_id: str,
        intent_name: str,
        slots: Dict[str, Any],
        session_id: str = "default"
    ):
        """
        Update context from an executed intent.
        
        Args:
            user_id: User identifier
            intent_name: Intent that was executed
            slots: Intent slots (extracted parameters)
            session_id: Session identifier
        """
        context = self.get_context(user_id, session_id)
        
        # Extract relevant information from slots
        items = None
        if "item" in slots:
            items = [slots["item"]]
        elif "items" in slots:
            items = slots["items"] if isinstance(slots["items"], list) else [slots["items"]]
        
        device = slots.get("device") or slots.get("name")
        list_name = slots.get("list")
        area = slots.get("area")
        time_ref = slots.get("time") or slots.get("when")
        
        # Update context
        context.update(
            items=items,
            device=device,
            list_name=list_name,
            area=area,
            time_ref=time_ref,
            intent_name=intent_name
        )
        
        logger.debug(f"Updated context for {user_id}: intent={intent_name}, slots={slots}")
    
    def resolve_pronouns(
        self,
        text: str,
        user_id: str,
        session_id: str = "default"
    ) -> str:
        """
        Resolve pronouns in text using context.
        
        Args:
            text: User input with pronouns
            user_id: User identifier
            session_id: Session identifier
            
        Returns:
            Text with pronouns resolved
        """
        context = self.get_context(user_id, session_id)
        
        if context.is_expired():
            logger.debug(f"Context expired for {user_id}, cannot resolve pronouns")
            return text
        
        text_lower = text.lower()
        resolved = text
        
        # Resolve "it"
        if " it " in text_lower or text_lower.startswith("it ") or text_lower.endswith(" it"):
            if context.last_device:
                resolved = resolved.replace(" it ", f" {context.last_device} ")
                resolved = resolved.replace("It ", f"{context.last_device} ")
                logger.debug(f"Resolved 'it' to '{context.last_device}'")
        
        # Resolve "that"
        if " that " in text_lower:
            if context.last_device:
                resolved = resolved.replace(" that ", f" {context.last_device} ")
            elif context.last_items:
                resolved = resolved.replace(" that ", f" {context.last_items[-1]} ")
        
        # Resolve "those" / "them"
        if " those " in text_lower or " them " in text_lower:
            if context.last_items:
                items_str = ", ".join(context.last_items)
                resolved = resolved.replace(" those ", f" {items_str} ")
                resolved = resolved.replace(" them ", f" {items_str} ")
                logger.debug(f"Resolved 'those/them' to '{items_str}'")
        
        # Resolve "there" (area)
        if " there " in text_lower:
            if context.last_area:
                resolved = resolved.replace(" there ", f" {context.last_area} ")
        
        return resolved
    
    def _cleanup_expired(self):
        """Remove expired contexts."""
        expired = [
            key for key, context in self.contexts.items()
            if context.is_expired()
        ]
        
        for key in expired:
            del self.contexts[key]
        
        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired contexts")
    
    def clear_context(self, user_id: str, session_id: str = "default"):
        """Clear context for a user/session."""
        key = f"{user_id}:{session_id}"
        if key in self.contexts:
            del self.contexts[key]
            logger.info(f"Cleared context for {user_id}:{session_id}")


# Global singleton instance
_context_manager: Optional[ContextManager] = None


def get_context_manager() -> ContextManager:
    """Get global ContextManager singleton."""
    global _context_manager
    if _context_manager is None:
        _context_manager = ContextManager()
    return _context_manager

