"""
Grounding Validator for P0-4
Verify LLM responses against retrieved context
Target: Catch 30%+ hallucinations
"""
import logging
import asyncio
from typing import Dict, Tuple, Optional
import numpy as np
from config import FeatureFlags

logger = logging.getLogger(__name__)


class GroundingValidator:
    """Async LLM-based grounding validation (Jetson)"""
    
    def __init__(self, llm_client=None):
        self.llm_client = llm_client
        self.platform_config = FeatureFlags.get_platform_config()
    
    async def verify_response_grounding_async(
        self, 
        query: str, 
        context: Dict, 
        response: str, 
        user_id: str
    ):
        """
        Async grounding check (non-blocking)
        
        Args:
            query: User query
            context: Retrieved context
            response: LLM response
            user_id: User ID
        """
        if not self.platform_config.get("grounding_checks"):
            return
        
        if self.platform_config.get("grounding_method") != "async_llm":
            return
        
        try:
            is_grounded, score, explanation = await self._verify_grounding(query, context, response)
            
            if not is_grounded:
                logger.warning(
                    f"[Grounding] User {user_id}: Response not grounded "
                    f"(score={score:.2f}, reason={explanation})"
                )
                await self._store_grounding_failure(user_id, query, context, response, score, explanation)
            else:
                logger.debug(f"[Grounding] User {user_id}: Response grounded (score={score:.2f})")
        except Exception as e:
            logger.error(f"[Grounding] Async validation failed: {e}")
    
    async def _verify_grounding(
        self,
        query: str,
        context: Dict,
        response: str
    ) -> Tuple[bool, float, str]:
        """
        Verify if response is grounded in context
        
        Returns:
            (is_grounded, confidence_score, explanation)
        """
        # Extract context text
        context_text = self._extract_context_text(context)
        
        if not context_text:
            # No context to verify against
            return (True, 1.0, "No context available")
        
        # Simple heuristic check (replace with LLM call in production)
        # Check if response introduces facts not in context
        response_lower = response.lower()
        context_lower = context_text.lower()
        
        # Check for specific fact claims
        score = 0.8  # Default moderate confidence
        
        # Very short responses are usually safe
        if len(response.split()) < 5:
            return (True, 0.9, "Short factual response")
        
        # Check for uncertainty in response (usually safe)
        uncertainty_markers = ["i don't know", "i'm not sure", "i don't have"]
        if any(marker in response_lower for marker in uncertainty_markers):
            return (True, 0.95, "Response admits uncertainty")
        
        # Check for made-up details (simple heuristic)
        # In production, use LLM to verify
        suspicious_patterns = [
            "according to", "studies show", "research indicates",
            "it is known that", "scientists discovered"
        ]
        if any(pattern in response_lower for pattern in suspicious_patterns):
            if not any(pattern in context_lower for pattern in suspicious_patterns):
                return (False, 0.3, "Response claims facts not in context")
        
        return (True, score, "Response appears grounded")
    
    def _extract_context_text(self, context: Dict) -> str:
        """Extract text from context dictionary"""
        text_parts = []
        
        if "memories" in context:
            memories = context["memories"]
            if isinstance(memories, list):
                text_parts.extend([m.get("fact", "") for m in memories if isinstance(m, dict)])
            elif isinstance(memories, str):
                text_parts.append(memories)
        
        if "temporal" in context:
            temporal = context["temporal"]
            if isinstance(temporal, str):
                text_parts.append(temporal)
        
        if "calendar" in context:
            calendar = context["calendar"]
            if isinstance(calendar, str):
                text_parts.append(calendar)
        
        return " ".join(text_parts)
    
    async def _store_grounding_failure(
        self,
        user_id: str,
        query: str,
        context: Dict,
        response: str,
        score: float,
        explanation: str
    ):
        """Store hallucination for analysis"""
        # TODO: Store in database for metrics
        logger.info(
            f"[Grounding] Logged hallucination: user={user_id}, "
            f"query='{query[:50]}...', score={score:.2f}"
        )


class FastGroundingValidator:
    """Fast embedding-based grounding validation (Pi 5)"""
    
    def __init__(self, embedding_model=None):
        self.embedding_model = embedding_model
        self.platform_config = FeatureFlags.get_platform_config()
    
    async def verify_grounding_fast(
        self,
        context: Dict,
        response: str,
        threshold: float = 0.7
    ) -> Tuple[bool, float]:
        """
        Fast embedding similarity check
        
        Args:
            context: Retrieved context
            response: LLM response
            threshold: Similarity threshold
            
        Returns:
            (is_grounded, similarity_score)
        """
        if not self.platform_config.get("grounding_checks"):
            return (True, 1.0)
        
        if self.platform_config.get("grounding_method") != "embedding":
            return (True, 1.0)
        
        try:
            context_text = self._extract_context_text(context)
            
            if not context_text or not self.embedding_model:
                return (True, 1.0)
            
            # Simple cosine similarity (fast)
            similarity = self._cosine_similarity(context_text, response)
            is_grounded = similarity >= threshold
            
            if not is_grounded:
                logger.warning(f"[Grounding] Low similarity: {similarity:.2f} < {threshold}")
            else:
                logger.debug(f"[Grounding] Similarity OK: {similarity:.2f}")
            
            return (is_grounded, similarity)
        except Exception as e:
            logger.error(f"[Grounding] Fast validation failed: {e}")
            return (True, 1.0)  # Fail open
    
    def _cosine_similarity(self, text1: str, text2: str) -> float:
        """Calculate cosine similarity between two texts"""
        if not self.embedding_model:
            return 0.8  # Default moderate similarity
        
        try:
            # Simple word overlap for now (replace with embeddings in production)
            words1 = set(text1.lower().split())
            words2 = set(text2.lower().split())
            
            if not words1 or not words2:
                return 0.5
            
            intersection = len(words1 & words2)
            union = len(words1 | words2)
            
            return intersection / union if union > 0 else 0.0
        except Exception as e:
            logger.error(f"[Grounding] Similarity calculation failed: {e}")
            return 0.8
    
    def _extract_context_text(self, context: Dict) -> str:
        """Extract text from context dictionary"""
        text_parts = []
        
        if "memories" in context:
            memories = context["memories"]
            if isinstance(memories, list):
                text_parts.extend([m.get("fact", "") for m in memories if isinstance(m, dict)])
        
        if "temporal" in context:
            text_parts.append(str(context["temporal"]))
        
        return " ".join(text_parts)

