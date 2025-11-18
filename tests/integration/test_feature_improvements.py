"""
Feature Improvement Testing
Test each feature individually, measure impact, validate improvements
"""
import pytest
import sys
import time
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "services" / "zoe-core"))

# Mock ZoeIntent for testing
class ZoeIntent:
    def __init__(self, name, confidence, tier, original_text="", slots=None):
        self.name = name
        self.confidence = confidence
        self.tier = tier
        self.original_text = original_text
        self.slots = slots or {}


class TestP01ContextValidationImprovements:
    """Test P0-1: Measure actual latency improvements"""
    
    def test_tier0_latency_improvement(self):
        """Measure latency improvement for Tier 0 intents"""
        from intent_system.validation import ContextValidator
        
        intent = ZoeIntent(name="HassLightOn", confidence=0.95, tier=0)
        
        # Test context validation speed
        start = time.time()
        should_fetch = ContextValidator.should_retrieve_context(intent, "turn on lights")
        latency_ms = (time.time() - start) * 1000
        
        # Validation should be < 1ms (instant decision)
        assert latency_ms < 1.0, f"Context validation too slow: {latency_ms}ms"
        assert should_fetch == False, "Should skip context for Tier 0"
        
        print(f"✓ Context validation latency: {latency_ms:.4f}ms (target: <1ms)")
    
    def test_data_fetch_intents_still_get_context(self):
        """Ensure data-fetching intents get context (no regression)"""
        from intent_system.validation import ContextValidator
        
        data_intents = ["ListShow", "CalendarShow", "CalendarQuery"]
        
        for intent_name in data_intents:
            intent = ZoeIntent(name=intent_name, confidence=0.95, tier=0)
            should_fetch = ContextValidator.should_retrieve_context(intent, f"show {intent_name}")
            assert should_fetch == True, f"{intent_name} should get context"
        
        print(f"✓ All {len(data_intents)} data-fetching intents still get context")
    
    def test_memory_keywords_detection(self):
        """Test memory keyword detection accuracy"""
        from intent_system.validation import ContextValidator
        
        memory_queries = [
            "what did I tell you about Arduino",
            "do you remember my dog",
            "have I mentioned Python",
            "when did I say that"
        ]
        
        intent = ZoeIntent(name="Conversation", confidence=0.8, tier=1)
        
        for query in memory_queries:
            should_fetch = ContextValidator.should_retrieve_context(intent, query)
            assert should_fetch == True, f"Memory keyword not detected in: {query}"
        
        print(f"✓ Memory keywords detected in {len(memory_queries)}/{len(memory_queries)} queries")


class TestP02ConfidenceImprovements:
    """Test P0-2: Validate confidence expression quality"""
    
    def test_confidence_appropriateness(self):
        """Test that confidence qualifiers are appropriate"""
        from intent_system.formatters.response_formatter import ResponseFormatter
        
        test_cases = [
            # (response, confidence, should_have_qualifier)
            ("Paris is the capital of France", 0.95, False),
            ("You mentioned Arduino recently", 0.75, True),
            ("The population might be around 10000", 0.55, True),
            ("I don't have that information", 0.25, True),
        ]
        
        for response, confidence, should_have_qualifier in test_cases:
            formatted = ResponseFormatter.format_with_confidence(response, confidence)
            has_qualifier = formatted != response
            
            assert has_qualifier == should_have_qualifier, \
                f"Confidence {confidence}: expected qualifier={should_have_qualifier}, got={has_qualifier}"
        
        print(f"✓ Confidence qualifiers appropriate for {len(test_cases)}/{len(test_cases)} cases")
    
    def test_no_double_qualification(self):
        """Ensure we don't add qualifiers to already-qualified responses"""
        from intent_system.formatters.response_formatter import ResponseFormatter
        
        already_qualified = "Based on what I know, you like Python"
        formatted = ResponseFormatter.format_with_confidence(already_qualified, 0.75)
        
        # Should not double-qualify (case-insensitive check)
        assert formatted.lower().count("based on") == 1, "Double-qualified response"
        assert formatted == already_qualified, "Should return original when already qualified"
        
        print("✓ No double-qualification detected")


class TestP03TemperatureImprovements:
    """Test P0-3: Validate temperature appropriateness"""
    
    def test_temperature_ranges(self):
        """Test that temperature ranges are appropriate for intent types"""
        from intent_system.temperature_manager import TemperatureManager
        
        test_cases = [
            # (intent_type, tier, expected_range)
            ("HassLightOn", 0, (0.0, 0.0)),  # Deterministic
            ("TimeQuery", 0, (0.0, 0.3)),    # Factual
            ("Greeting", 1, (0.6, 0.8)),     # Conversational
            ("MemoryRecall", 2, (0.5, 0.7)), # Complex
        ]
        
        for intent_name, tier, (min_temp, max_temp) in test_cases:
            intent = ZoeIntent(name=intent_name, confidence=0.9, tier=tier)
            temp = TemperatureManager.get_temperature_for_intent(intent)
            
            assert min_temp <= temp <= max_temp, \
                f"{intent_name}: temp {temp} not in range [{min_temp}, {max_temp}]"
        
        print(f"✓ Temperature ranges appropriate for {len(test_cases)}/{len(test_cases)} intent types")
    
    def test_context_aware_adjustment(self):
        """Test that temperature adjusts based on context availability"""
        from intent_system.temperature_manager import TemperatureManager
        
        intent = ZoeIntent(name="Conversation", confidence=0.8, tier=1)
        
        temp_with_context = TemperatureManager.get_temperature_with_context(intent, has_context=True)
        temp_without_context = TemperatureManager.get_temperature_with_context(intent, has_context=False)
        
        # Temperature should be lower (more factual) when context is available
        assert temp_with_context <= temp_without_context, \
            "Temperature should decrease with available context"
        
        print(f"✓ Context-aware temperature adjustment working")


class TestP04GroundingImprovements:
    """Test P0-4: Validate grounding detection"""
    
    @pytest.mark.asyncio
    async def test_grounding_detection_accuracy(self):
        """Test grounding detection for various response types"""
        from grounding_validator import GroundingValidator
        
        validator = GroundingValidator()
        
        test_cases = [
            # (context, response, should_be_grounded)
            ({"memories": [{"fact": "User loves Python"}]}, "Python is great", True),
            ({}, "Yes", True),  # Short responses are safe
            ({}, "I don't know", True),  # Uncertainty is safe
            ({"memories": [{"fact": "User loves Python"}]}, "User loves Java", False),  # Contradicts
        ]
        
        correct = 0
        for context, response, expected_grounded in test_cases:
            is_grounded, score, explanation = await validator._verify_grounding(
                "test query", context, response
            )
            
            if is_grounded == expected_grounded:
                correct += 1
        
        accuracy = correct / len(test_cases)
        assert accuracy >= 0.75, f"Grounding detection accuracy too low: {accuracy*100:.0f}%"
        
        print(f"✓ Grounding detection accuracy: {accuracy*100:.0f}% ({correct}/{len(test_cases)})")


class TestP11BehavioralMemoryImprovements:
    """Test P1-1: Validate behavioral pattern extraction"""
    
    def test_pattern_extraction_speed(self):
        """Test that pattern extraction is fast enough for nightly jobs"""
        from behavioral_memory import RuleBasedPatternExtractor
        
        extractor = RuleBasedPatternExtractor()
        
        start = time.time()
        # Extract patterns (will return empty for test user, but tests speed)
        patterns = extractor.extract_patterns("test_user", min_conversations=1)
        latency = time.time() - start
        
        # Should complete in < 1 second even with no data
        assert latency < 1.0, f"Pattern extraction too slow: {latency:.2f}s"
        
        print(f"✓ Pattern extraction latency: {latency:.3f}s (target: <1s)")
    
    def test_pattern_types_coverage(self):
        """Test that all pattern types can be extracted"""
        from behavioral_memory import RuleBasedPatternExtractor
        
        extractor = RuleBasedPatternExtractor()
        
        # These methods should exist and not crash
        pattern_methods = [
            '_extract_timing_patterns',
            '_extract_interest_patterns',
            '_extract_communication_patterns',
            '_extract_task_patterns'
        ]
        
        for method_name in pattern_methods:
            assert hasattr(extractor, method_name), f"Missing method: {method_name}"
        
        print(f"✓ All {len(pattern_methods)} pattern extraction methods present")


class TestFeatureSafeguards:
    """Test safeguards and error handling"""
    
    def test_feature_flags_default_disabled(self):
        """Verify all features are disabled by default (safety)"""
        from config import FeatureFlags
        
        flags = FeatureFlags.get_active_features()
        
        for feature_name, enabled in flags.items():
            assert enabled == False, f"Feature {feature_name} should be disabled by default"
        
        print(f"✓ All {len(flags)} features disabled by default (safe)")
    
    def test_graceful_degradation(self):
        """Test that features fail gracefully when disabled"""
        from intent_system.validation import ContextValidator
        
        # Should work even with None intent
        result = ContextValidator.should_retrieve_context(None, "test query")
        assert result == True, "Should default to fetching context when uncertain"
        
        print("✓ Graceful degradation working (fails safe)")
    
    def test_platform_config_validation(self):
        """Test that platform configs are valid"""
        from config import FeatureFlags
        
        config = FeatureFlags.get_platform_config()
        
        required_keys = ["max_context_tokens", "grounding_method", "rag_results"]
        for key in required_keys:
            assert key in config, f"Missing required config key: {key}"
        
        # Validate ranges
        assert 0 < config["max_context_tokens"] <= 8192, "Invalid context token limit"
        assert config["grounding_method"] in ["async_llm", "embedding", "none"]
        
        print(f"✓ Platform config validated ({len(required_keys)} required keys)")


def test_integration_no_conflicts():
    """Test that all features can be imported without conflicts"""
    from config import FeatureFlags
    from intent_system.validation import ContextValidator
    from intent_system.formatters.response_formatter import ResponseFormatter
    from intent_system.temperature_manager import TemperatureManager
    from grounding_validator import GroundingValidator
    from behavioral_memory import behavioral_memory
    
    # All should be importable
    assert ContextValidator is not None
    assert ResponseFormatter is not None
    assert TemperatureManager is not None
    assert GroundingValidator is not None
    assert behavioral_memory is not None
    
    print("✓ No import conflicts detected")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

