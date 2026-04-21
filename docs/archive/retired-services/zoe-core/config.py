"""
Feature Flags Configuration

Centralized feature flag management for Memory & Hallucination Reduction plan.
All features default to disabled (false) for safe rollout.

Usage:
    from config import FeatureFlags
    
    if FeatureFlags.USE_CONTEXT_VALIDATION:
        # Feature code here
        pass

Environment Variables:
    Set in docker-compose.yml or .env file:
    - USE_CONTEXT_VALIDATION=true
    - USE_CONFIDENCE_FORMATTING=true
    - USE_DYNAMIC_TEMPERATURE=true
    - USE_GROUNDING_CHECKS=true
    - USE_BEHAVIORAL_MEMORY=true
"""

import os
from typing import Dict
import logging

logger = logging.getLogger(__name__)


class FeatureFlags:
    """Central feature flag management for new memory & hallucination features"""
    
    # ===================
    # P0 Features (Days 8-19)
    # ===================
    
    USE_CONTEXT_VALIDATION = os.getenv("USE_CONTEXT_VALIDATION", "false").lower() == "true"
    """
    P0-1: Context Validation (Days 8-10)
    
    Skip context retrieval for deterministic Tier 0 intents.
    Target: Tier 0 latency < 10ms (from ~200-500ms)
    
    Default: false
    """
    
    USE_CONFIDENCE_FORMATTING = os.getenv("USE_CONFIDENCE_FORMATTING", "false").lower() == "true"
    """
    P0-2: Confidence Expression (Days 11-13)
    
    Add confidence-aware language to responses:
    - High (≥0.85): No qualifier
    - Medium (≥0.70): "Based on what I know..."
    - Low (≥0.50): "I'm not entirely sure, but..."
    - Very Low (<0.50): "I don't have information about that"
    
    Target: User trust +20%
    
    Default: false
    """
    
    USE_DYNAMIC_TEMPERATURE = os.getenv("USE_DYNAMIC_TEMPERATURE", "false").lower() == "true"
    """
    P0-3: Temperature Adjustment (Days 14-15)
    
    Adjust LLM temperature based on intent type:
    - Tier 0 (deterministic): 0.0
    - Factual queries: 0.3
    - Tool-calling: 0.5
    - Conversational: 0.7
    - Complex reasoning: 0.6
    
    Target: Hallucination rate -10%
    
    Default: false
    """
    
    USE_GROUNDING_CHECKS = os.getenv("USE_GROUNDING_CHECKS", "false").lower() == "true"
    """
    P0-4: Context Grounding Checks (Days 16-19)
    
    Verify LLM responses against retrieved context:
    - Jetson: Async LLM validation (no blocking)
    - Pi 5: Fast embedding similarity (< 10ms)
    
    Target: Catch 30%+ hallucinations
    
    Default: false
    """
    
    # ===================
    # P1 Features (Days 23-35)
    # ===================
    
    USE_BEHAVIORAL_MEMORY = os.getenv("USE_BEHAVIORAL_MEMORY", "false").lower() == "true"
    """
    P1-1: Behavioral Memory L1 (Days 23-29)
    
    Extract natural language patterns from conversations:
    - Timing patterns (active hours)
    - Interest patterns (top topics)
    - Communication patterns (response preferences)
    - Task patterns (routine behaviors)
    
    Implementation: Rule-based with optional LLM enhancement
    Target: 7+ patterns per active user, 70%+ accuracy
    
    Default: false
    """
    
    # ===================
    # Platform Configuration
    # ===================
    
    PLATFORM = os.getenv("PLATFORM", "jetson").lower()
    """
    Platform identifier: 'jetson' or 'pi5'
    
    Used for platform-specific optimizations:
    - Jetson: 8K context, GPU, full capabilities
    - Pi 5: 4K context, CPU, optimized
    
    Default: jetson
    """
    
    # ===================
    # Helper Methods
    # ===================
    
    @classmethod
    def get_active_features(cls) -> Dict[str, bool]:
        """
        Return dict of all feature flags for logging
        
        Returns:
            Dict mapping feature name to enabled status
        """
        return {
            "context_validation": cls.USE_CONTEXT_VALIDATION,
            "confidence_formatting": cls.USE_CONFIDENCE_FORMATTING,
            "dynamic_temperature": cls.USE_DYNAMIC_TEMPERATURE,
            "grounding_checks": cls.USE_GROUNDING_CHECKS,
            "behavioral_memory": cls.USE_BEHAVIORAL_MEMORY,
        }
    
    @classmethod
    def log_feature_status(cls):
        """Log current feature flag status on startup"""
        features = cls.get_active_features()
        enabled_features = [name for name, enabled in features.items() if enabled]
        disabled_features = [name for name, enabled in features.items() if not enabled]
        
        logger.info("=" * 70)
        logger.info("FEATURE FLAGS STATUS")
        logger.info("=" * 70)
        logger.info(f"Platform: {cls.PLATFORM}")
        logger.info(f"Enabled features ({len(enabled_features)}): {', '.join(enabled_features) if enabled_features else 'NONE'}")
        logger.info(f"Disabled features ({len(disabled_features)}): {', '.join(disabled_features) if disabled_features else 'NONE'}")
        logger.info("=" * 70)
    
    @classmethod
    def is_feature_enabled(cls, feature_name: str) -> bool:
        """
        Check if a specific feature is enabled
        
        Args:
            feature_name: Name of the feature (e.g., 'context_validation')
        
        Returns:
            True if enabled, False otherwise
        """
        features = cls.get_active_features()
        return features.get(feature_name, False)
    
    @classmethod
    def get_platform_config(cls) -> Dict:
        """
        Get platform-specific configuration
        
        Returns:
            Dict with platform-specific settings
        """
        if cls.PLATFORM == "jetson":
            return {
                "max_context_tokens": 8192,
                "rag_results": 10,
                "recent_messages": 20,
                "embedding_batch_size": 32,
                "behavioral_patterns": 10,
                "calendar_events": 15,
                "grounding_checks": True,
                "grounding_method": "async_llm",
                "grounding_sync": False,
                "compression_strategy": "minimal"
            }
        elif cls.PLATFORM == "pi5":
            return {
                "max_context_tokens": 4096,
                "rag_results": 5,
                "recent_messages": 10,
                "embedding_batch_size": 8,
                "behavioral_patterns": 5,
                "calendar_events": 8,
                "grounding_checks": True,
                "grounding_method": "embedding",
                "grounding_sync": True,
                "compression_strategy": "aggressive"
            }
        else:
            # Default to conservative settings
            logger.warning(f"Unknown platform '{cls.PLATFORM}', using conservative defaults")
            return {
                "max_context_tokens": 4096,
                "rag_results": 5,
                "recent_messages": 10,
                "embedding_batch_size": 8,
                "behavioral_patterns": 5,
                "calendar_events": 8,
                "grounding_checks": False,
                "grounding_method": "none",
                "grounding_sync": False,
                "compression_strategy": "aggressive"
            }


# ===================
# Logging on Import
# ===================

# Log feature status when module is imported
FeatureFlags.log_feature_status()


# ===================
# Convenience Functions
# ===================

def get_feature_flags() -> Dict[str, bool]:
    """
    Convenience function to get all feature flags
    
    Returns:
        Dict mapping feature name to enabled status
    """
    return FeatureFlags.get_active_features()


def is_enabled(feature_name: str) -> bool:
    """
    Convenience function to check if a feature is enabled
    
    Args:
        feature_name: Name of the feature
    
    Returns:
        True if enabled, False otherwise
    """
    return FeatureFlags.is_feature_enabled(feature_name)


def get_platform() -> str:
    """
    Get current platform identifier
    
    Returns:
        'jetson' or 'pi5'
    """
    return FeatureFlags.PLATFORM


def get_platform_config() -> Dict:
    """
    Get platform-specific configuration
    
    Returns:
        Dict with platform-specific settings
    """
    return FeatureFlags.get_platform_config()


# ===================
# Example Usage
# ===================

if __name__ == "__main__":
    # Example: Check feature flags
    print("Feature Flags Demo")
    print("=" * 70)
    
    print(f"\nPlatform: {get_platform()}")
    print(f"\nActive features:")
    for feature, enabled in get_feature_flags().items():
        status = "✓ ENABLED" if enabled else "✗ DISABLED"
        print(f"  {feature}: {status}")
    
    print(f"\nPlatform config:")
    config = get_platform_config()
    for key, value in config.items():
        print(f"  {key}: {value}")
    
    print("\n" + "=" * 70)
    print("\nTo enable features, set environment variables:")
    print("  export USE_CONTEXT_VALIDATION=true")
    print("  export USE_CONFIDENCE_FORMATTING=true")
    print("  export USE_DYNAMIC_TEMPERATURE=true")
    print("  export USE_GROUNDING_CHECKS=true")
    print("  export USE_BEHAVIORAL_MEMORY=true")
    print("\nOr in docker-compose.yml:")
    print("  environment:")
    print("    - USE_CONTEXT_VALIDATION=true")
    print("=" * 70)

