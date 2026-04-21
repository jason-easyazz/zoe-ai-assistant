"""
Response Formatter for P0-2
Add confidence-aware language to LLM responses
Target: User trust +20%
"""
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


class ResponseFormatter:
    """Formats LLM responses with confidence-aware language"""
    
    # Confidence thresholds
    CONFIDENCE_THRESHOLDS = {
        "high": 0.85,
        "medium": 0.70,
        "low": 0.50,
        "very_low": 0.30
    }
    
    @staticmethod
    def format_with_confidence(
        response: str,
        confidence: float,
        sources: Optional[List[str]] = None,
        uncertainty_reason: Optional[str] = None
    ) -> str:
        """
        Format response with confidence-aware language
        
        Args:
            response: Original LLM response
            confidence: Confidence score 0.0-1.0
            sources: Optional list of source references
            uncertainty_reason: Optional reason for low confidence
            
        Returns:
            Formatted response with appropriate qualifiers
        """
        response = response.strip()
        
        # High confidence (≥ 0.85): No qualifier needed
        if confidence >= ResponseFormatter.CONFIDENCE_THRESHOLDS["high"]:
            logger.debug(f"[Confidence] HIGH ({confidence:.2f}) - no qualifier")
            return response
        
        # Medium confidence (≥ 0.70): Soft qualifier
        if confidence >= ResponseFormatter.CONFIDENCE_THRESHOLDS["medium"]:
            logger.info(f"[Confidence] MEDIUM ({confidence:.2f}) - soft qualifier")
            # Don't double-qualify if already has a qualifier (case-insensitive)
            response_lower = response.lower()
            if any(phrase in response_lower for phrase in ["based on", "from what", "i think", "i believe", "from my understanding"]):
                logger.debug(f"[Confidence] Already qualified, skipping")
                return response
            return f"Based on what I know, {response[0].lower()}{response[1:]}"
        
        # Low confidence (≥ 0.50): Clear qualifier
        if confidence >= ResponseFormatter.CONFIDENCE_THRESHOLDS["low"]:
            logger.info(f"[Confidence] LOW ({confidence:.2f}) - clear qualifier")
            if any(phrase in response.lower() for phrase in ["i'm not sure", "not entirely sure", "uncertain"]):
                return response
            return f"I'm not entirely sure, but {response[0].lower()}{response[1:]}"
        
        # Very low confidence (< 0.50): Admit limitation
        logger.warning(f"[Confidence] VERY LOW ({confidence:.2f}) - admit limitation")
        if uncertainty_reason:
            return f"I don't have enough information about that. {uncertainty_reason}"
        return "I don't have enough information to answer that confidently."
    
    @staticmethod
    def should_add_qualifier(confidence: float) -> bool:
        """Check if response needs a confidence qualifier"""
        return confidence < ResponseFormatter.CONFIDENCE_THRESHOLDS["high"]
    
    @staticmethod
    def get_confidence_level(confidence: float) -> str:
        """Get confidence level name"""
        if confidence >= ResponseFormatter.CONFIDENCE_THRESHOLDS["high"]:
            return "high"
        elif confidence >= ResponseFormatter.CONFIDENCE_THRESHOLDS["medium"]:
            return "medium"
        elif confidence >= ResponseFormatter.CONFIDENCE_THRESHOLDS["low"]:
            return "low"
        else:
            return "very_low"
    
    @staticmethod
    def estimate_response_confidence(response: str, intent=None, context_present: bool = True) -> float:
        """
        Estimate confidence based on response and context
        
        Args:
            response: LLM response
            intent: ZoeIntent object
            context_present: Whether context was available
            
        Returns:
            Estimated confidence 0.0-1.0
        """
        confidence = 0.85  # Default high
        
        # Tier 0 intents are highly confident
        if intent and intent.tier == 0:
            confidence = 0.95
        
        # Response has uncertainty markers
        uncertainty_markers = ["i don't know", "i'm not sure", "i don't have", "unclear", "uncertain"]
        if any(marker in response.lower() for marker in uncertainty_markers):
            confidence = max(0.3, confidence - 0.4)
        
        # No context when expected
        if not context_present and intent and intent.tier >= 2:
            confidence = max(0.5, confidence - 0.3)
        
        # Short, direct answers are confident
        if len(response.split()) < 5 and intent and intent.tier == 0:
            confidence = 0.95
        
        # Hedging language
        hedging = ["might", "maybe", "possibly", "could be", "perhaps"]
        if any(hedge in response.lower() for hedge in hedging):
            confidence = max(0.6, confidence - 0.2)
        
        return confidence
