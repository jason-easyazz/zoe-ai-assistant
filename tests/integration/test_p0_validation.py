"""
P0 Feature Validation Tests
Tests all P0 features against targets
"""
import pytest
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "services" / "zoe-core"))

from intent_system.validation import ContextValidator
from intent_system.formatters.response_formatter import ResponseFormatter
from intent_system.temperature_manager import TemperatureManager
from grounding_validator import GroundingValidator, FastGroundingValidator

# Mock ZoeIntent for testing (avoiding hassil dependency)
class ZoeIntent:
    def __init__(self, name, confidence, tier, original_text="", slots=None):
        self.name = name
        self.confidence = confidence
        self.tier = tier
        self.original_text = original_text
        self.slots = slots or {}


class TestP01ContextValidation:
    """Test P0-1: Context Validation"""
    
    def test_tier0_deterministic_skip_context(self):
        """Tier 0 deterministic intents should skip context"""
        intent = ZoeIntent(
            name="HassLightOn",
            confidence=0.95,
            tier=0,
            original_text="turn on lights"
        )
        
        should_fetch = ContextValidator.should_retrieve_context(intent, "turn on lights")
        assert should_fetch == False, "Tier 0 deterministic should skip context"
    
    def test_tier0_data_fetch_requires_context(self):
        """Data-fetching Tier 0 intents need context"""
        intent = ZoeIntent(
            name="ListShow",
            confidence=0.95,
            tier=0,
            original_text="show my list"
        )
        
        should_fetch = ContextValidator.should_retrieve_context(intent, "show my list")
        assert should_fetch == True, "Data-fetching Tier 0 needs context"
    
    def test_memory_keywords_require_context(self):
        """Memory keywords should always require context"""
        intent = ZoeIntent(
            name="Conversation",
            confidence=0.8,
            tier=1,
            original_text="what did I tell you about Arduino"
        )
        
        should_fetch = ContextValidator.should_retrieve_context(
            intent,
            "what did I tell you about Arduino"
        )
        assert should_fetch == True, "Memory keywords require context"
    
    def test_complex_query_requires_context(self):
        """Complex queries (>15 words) should require context"""
        intent = ZoeIntent(
            name="Conversation",
            confidence=0.7,
            tier=1,
            original_text="can you help me understand what I need to do today based on my schedule"
        )
        
        should_fetch = ContextValidator.should_retrieve_context(
            intent,
            "can you help me understand what I need to do today based on my schedule"
        )
        assert should_fetch == True, "Complex queries require context"


class TestP02ConfidenceExpression:
    """Test P0-2: Confidence Expression"""
    
    def test_high_confidence_no_qualifier(self):
        """High confidence (≥0.85) should have no qualifier"""
        response = ResponseFormatter.format_with_confidence(
            "Paris is the capital of France.",
            confidence=0.95
        )
        
        assert response == "Paris is the capital of France.", "High confidence should not add qualifier"
    
    def test_medium_confidence_soft_qualifier(self):
        """Medium confidence (≥0.70) should add soft qualifier"""
        response = ResponseFormatter.format_with_confidence(
            "You mentioned Arduino last week.",
            confidence=0.75
        )
        
        assert response.startswith("Based on what I know"), "Medium confidence should add soft qualifier"
    
    def test_low_confidence_clear_qualifier(self):
        """Low confidence (≥0.50) should add clear qualifier"""
        response = ResponseFormatter.format_with_confidence(
            "The population is around 10,000.",
            confidence=0.55
        )
        
        assert "not entirely sure" in response.lower(), "Low confidence should add clear qualifier"
    
    def test_very_low_confidence_admits_limitation(self):
        """Very low confidence (<0.50) should admit limitation"""
        response = ResponseFormatter.format_with_confidence(
            "Something",
            confidence=0.25
        )
        
        assert "don't have" in response.lower(), "Very low confidence should admit limitation"
    
    def test_estimate_confidence_tier0(self):
        """Tier 0 intents should have high confidence"""
        intent = ZoeIntent(name="TimeQuery", confidence=0.95, tier=0)
        
        confidence = ResponseFormatter.estimate_response_confidence(
            "It's 3:45 PM",
            intent=intent,
            context_present=True
        )
        
        assert confidence >= 0.9, "Tier 0 responses should be highly confident"


class TestP03TemperatureAdjustment:
    """Test P0-3: Temperature Adjustment"""
    
    def test_tier0_zero_temperature(self):
        """Tier 0 intents should have 0.0 temperature"""
        intent = ZoeIntent(name="HassLightOn", confidence=0.95, tier=0)
        
        temp = TemperatureManager.get_temperature_for_intent(intent)
        assert temp == 0.0, "Tier 0 should have 0.0 temperature"
    
    def test_factual_low_temperature(self):
        """Factual queries should have low temperature"""
        intent = ZoeIntent(name="TimeQuery", confidence=0.9, tier=0)
        
        temp = TemperatureManager.get_temperature_for_intent(intent)
        assert temp <= 0.3, "Factual queries should have low temperature"
    
    def test_conversational_higher_temperature(self):
        """Conversational intents should have higher temperature"""
        intent = ZoeIntent(name="Greeting", confidence=0.8, tier=1)
        
        temp = TemperatureManager.get_temperature_for_intent(intent)
        assert temp >= 0.6, "Conversational should have higher temperature"
    
    def test_tool_calling_moderate_temperature(self):
        """Tool calling should have moderate temperature"""
        intent = ZoeIntent(name="HassLightOn", confidence=0.9, tier=0)
        
        temp = TemperatureManager.get_temperature_for_intent(intent)
        assert 0.0 <= temp <= 0.5, "Tool calling should be moderate-low temperature"


class TestP04GroundingChecks:
    """Test P0-4: Grounding Checks"""
    
    @pytest.mark.asyncio
    async def test_grounding_fast_similarity(self):
        """Fast grounding should use word overlap"""
        validator = FastGroundingValidator()
        
        context = {
            "memories": [
                {"fact": "User loves Python programming"}
            ]
        }
        
        # Grounded response
        is_grounded, score = await validator.verify_grounding_fast(
            context,
            "Python is your favorite language"
        )
        
        assert is_grounded or score > 0.3, "Should detect grounded response"
    
    @pytest.mark.asyncio
    async def test_grounding_short_responses_safe(self):
        """Short responses should be considered safe"""
        validator = GroundingValidator()
        
        context = {"memories": []}
        response = "Yes"
        
        is_grounded, score, explanation = await validator._verify_grounding(
            "Is it sunny?",
            context,
            response
        )
        
        assert is_grounded == True, "Short responses should be safe"
    
    @pytest.mark.asyncio
    async def test_grounding_uncertainty_safe(self):
        """Responses with uncertainty are safe"""
        validator = GroundingValidator()
        
        context = {}
        response = "I don't know the exact number"
        
        is_grounded, score, explanation = await validator._verify_grounding(
            "What's the population?",
            context,
            response
        )
        
        assert is_grounded == True, "Uncertainty responses are safe"


def test_all_features_importable():
    """Verify all features can be imported"""
    from config import FeatureFlags
    from intent_system.validation import ContextValidator
    from intent_system.formatters.response_formatter import ResponseFormatter
    from intent_system.temperature_manager import TemperatureManager
    from grounding_validator import GroundingValidator
    
    assert ContextValidator is not None
    assert ResponseFormatter is not None
    assert TemperatureManager is not None
    assert GroundingValidator is not None


def test_feature_flags_status():
    """Verify feature flags are properly configured"""
    from config import FeatureFlags
    
    # All features should be disabled by default
    assert FeatureFlags.USE_CONTEXT_VALIDATION == False, "Should be disabled by default"
    assert FeatureFlags.USE_CONFIDENCE_FORMATTING == False
    assert FeatureFlags.USE_DYNAMIC_TEMPERATURE == False
    assert FeatureFlags.USE_GROUNDING_CHECKS == False
    
    # Platform should be set
    assert FeatureFlags.PLATFORM in ["jetson", "pi5"], "Platform should be valid"
    
    # Config should be accessible
    platform_config = FeatureFlags.get_platform_config()
    assert "max_context_tokens" in platform_config
    assert "grounding_method" in platform_config


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

