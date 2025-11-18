"""
Temperature Manager for P0-3
Adjust LLM temperature based on intent type
Target: Hallucination rate -10%
"""
import logging

logger = logging.getLogger(__name__)


class TemperatureManager:
    """Manages context-aware temperature for LLM queries"""
    
    @staticmethod
    def get_temperature_for_intent(intent) -> float:
        """
        Get appropriate temperature based on intent type
        
        Args:
            intent: ZoeIntent object or None
            
        Returns:
            Temperature value 0.0-1.0
        """
        if not intent:
            # No intent - use moderate temperature
            return 0.6
        
        # Tier 0 (deterministic) - no creativity needed
        if intent.tier == 0:
            logger.debug(f"[Temperature] Tier 0 ({intent.name}): 0.0")
            return 0.0
        
        # Factual queries - low temperature
        factual_intents = ["TimeQuery", "WeatherQuery", "FactLookup", "CalendarQuery"]
        if intent.name in factual_intents:
            logger.debug(f"[Temperature] Factual ({intent.name}): 0.3")
            return 0.3
        
        # Home Assistant and tool-calling - moderate-low
        if intent.name.startswith("Hass") or "List" in intent.name:
            logger.debug(f"[Temperature] Tool-calling ({intent.name}): 0.5")
            return 0.5
        
        # Conversational - higher temperature for naturalness
        conversational_intents = ["Greeting", "Conversation", "Chat", "Acknowledgment"]
        if intent.name in conversational_intents or intent.tier == 1:
            logger.debug(f"[Temperature] Conversational ({intent.name}): 0.7")
            return 0.7
        
        # Complex reasoning (Tier 2+) - balanced
        if intent.tier >= 2:
            logger.debug(f"[Temperature] Tier {intent.tier} ({intent.name}): 0.6")
            return 0.6
        
        # Default - balanced
        return 0.6
    
    @staticmethod
    def get_temperature_with_context(intent, has_context: bool) -> float:
        """
        Get temperature with context awareness
        
        Args:
            intent: ZoeIntent object
            has_context: Whether context is available
            
        Returns:
            Adjusted temperature
        """
        base_temp = TemperatureManager.get_temperature_for_intent(intent)
        
        # Lower temperature when context is available (rely on facts)
        if has_context and base_temp > 0.5:
            adjusted = base_temp - 0.1
            logger.debug(f"[Temperature] Adjusted down for context: {base_temp} → {adjusted}")
            return adjusted
        
        return base_temp

