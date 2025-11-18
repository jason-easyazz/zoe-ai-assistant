"""
Context Validation for P0-1
Skip context retrieval for deterministic Tier 0 intents
Target: Tier 0 latency < 10ms
"""
import logging
from typing import List, Set

logger = logging.getLogger(__name__)

# Memory keywords that REQUIRE context
MEMORY_KEYWORDS = [
    "remember", "recall", "told", "said", "mentioned",
    "did i", "what did", "when did", "have i", "do you know",
    "forget", "previous", "earlier", "before", "last time"
]

# Intents that ALWAYS need context (data-fetching Tier 0 intents)
RETRIEVAL_REQUIRED_INTENTS: Set[str] = {
    "ListShow", "CalendarShow", "CalendarQuery",
    "MemoryRecall", "MemoryQuery", "PersonQuery"
}


class ContextValidator:
    """Determines if context retrieval is needed based on intent and query"""
    
    @staticmethod
    def should_retrieve_context(intent, query: str) -> bool:
        """
        Determine if context should be retrieved
        
        Args:
            intent: ZoeIntent object or None
            query: User query string
            
        Returns:
            True if context needed, False to skip
        """
        if not intent:
            # No intent classified - need context for LLM
            return True
        
        # Tier 0 deterministic intents
        if intent.tier == 0:
            # Data-fetching intents need context
            if intent.name in RETRIEVAL_REQUIRED_INTENTS:
                logger.info(f"[Context] REQUIRED - data-fetching intent: {intent.name}")
                return True
            
            # Pure action intents can skip context
            logger.info(f"[Context] SKIPPED - deterministic intent: {intent.name}")
            return False
        
        # Memory keywords always require context
        query_lower = query.lower()
        if any(kw in query_lower for kw in MEMORY_KEYWORDS):
            logger.info(f"[Context] REQUIRED - memory keyword detected")
            return True
        
        # Complex queries need context (>15 words or multiple questions)
        word_count = len(query.split())
        question_count = query.count("?")
        
        if word_count > 15 or question_count > 1:
            logger.info(f"[Context] REQUIRED - complex query (words={word_count}, questions={question_count})")
            return True
        
        # Explicit retrieval intents
        if intent.name in RETRIEVAL_REQUIRED_INTENTS:
            logger.info(f"[Context] REQUIRED - retrieval intent: {intent.name}")
            return True
        
        # Tier 1 (conversational) - may need context
        if intent.tier == 1:
            # Greetings don't need context
            if intent.name in ["Greeting", "Acknowledgment", "Cancellation"]:
                logger.info(f"[Context] SKIPPED - simple conversational: {intent.name}")
                return False
            # Other conversational may benefit from context
            logger.info(f"[Context] REQUIRED - conversational intent: {intent.name}")
            return True
        
        # Tier 2+ (memory/complex) always need context
        if intent.tier >= 2:
            logger.info(f"[Context] REQUIRED - Tier {intent.tier} intent")
            return True
        
        # Default: retrieve context
        return True
    
    @staticmethod
    def get_required_context_types(intent) -> List[str]:
        """
        Determine which context types are needed
        
        Args:
            intent: ZoeIntent object
            
        Returns:
            List of context types to fetch
        """
        if not intent:
            return ["memory", "temporal", "calendar"]
        
        context_types = []
        
        # Memory-related intents
        if intent.name in RETRIEVAL_REQUIRED_INTENTS or intent.tier >= 2:
            context_types.append("memory")
            context_types.append("temporal")
        
        # Calendar intents
        if "Calendar" in intent.name or "calendar" in intent.original_text.lower():
            context_types.append("calendar")
        
        # List intents
        if "List" in intent.name:
            context_types.append("lists")
        
        # Default: all context types
        if not context_types:
            context_types = ["memory", "temporal", "calendar"]
        
        return context_types

