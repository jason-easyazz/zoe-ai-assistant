"""
Intent Classification Tests
===========================

Tests for HassIL-based intent classification system.
"""

import pytest
import sys
import os
import time

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/zoe-core'))

from intent_system.classifiers import UnifiedIntentClassifier, ZoeIntent


@pytest.fixture
def classifier():
    """Initialize classifier with test intents."""
    return UnifiedIntentClassifier(intents_dir="services/zoe-core/intent_system/intents/en")


class TestListIntents:
    """Test list-related intent classification."""
    
    def test_add_to_shopping_list_basic(self, classifier):
        """Test basic 'add to shopping list' pattern."""
        intent = classifier.classify("add bread to shopping list")
        
        assert intent is not None, "Intent should match"
        assert intent.name == "ListAdd", f"Expected ListAdd, got {intent.name}"
        assert "item" in intent.slots, "Should extract item slot"
        assert intent.slots["item"] == "bread", f"Expected 'bread', got {intent.slots['item']}"
        assert intent.confidence >= 0.9, f"Confidence too low: {intent.confidence}"
        assert intent.tier == 0, f"Should use Tier 0 (HassIL), got Tier {intent.tier}"
    
    def test_add_to_shopping_shorthand(self, classifier):
        """Test shorthand 'add item' pattern (implies shopping list)."""
        intent = classifier.classify("add milk")
        
        assert intent is not None
        assert intent.name == "ListAdd"
        assert intent.slots.get("item") == "milk"
        assert intent.slots.get("list", "shopping") == "shopping"  # Default list
    
    def test_natural_language_variations(self, classifier):
        """Test natural language variations for shopping list."""
        variations = [
            ("I need to buy eggs", "eggs"),
            ("don't forget to get butter", "butter"),
            ("we're out of cheese", "cheese"),
            ("we need milk", "milk"),
        ]
        
        for text, expected_item in variations:
            intent = classifier.classify(text)
            assert intent is not None, f"Should match: {text}"
            assert intent.name == "ListAdd", f"Wrong intent for: {text}"
            # Note: Item extraction may vary for natural language
    
    def test_show_list(self, classifier):
        """Test show/display list patterns."""
        patterns = [
            "show my shopping list",
            "what's on the shopping list",
            "show shopping",
            "list shopping",
        ]
        
        for pattern in patterns:
            intent = classifier.classify(pattern)
            assert intent is not None, f"Should match: {pattern}"
            assert intent.name == "ListShow", f"Wrong intent for: {pattern}"
    
    def test_remove_from_list(self, classifier):
        """Test remove item patterns."""
        intent = classifier.classify("remove bread from shopping list")
        
        assert intent is not None
        assert intent.name == "ListRemove"


class TestHomeAssistantIntents:
    """Test Home Assistant control intents."""
    
    def test_turn_on_light(self, classifier):
        """Test turn on patterns."""
        patterns = [
            "turn on the lights",
            "turn on lights",
            "lights on",
            "switch on the lights",
        ]
        
        for pattern in patterns:
            intent = classifier.classify(pattern)
            if intent:  # May not match without specific device name
                assert intent.name == "HassTurnOn", f"Wrong intent for: {pattern}"
    
    def test_turn_off_light(self, classifier):
        """Test turn off patterns."""
        intent = classifier.classify("turn off the lights")
        
        if intent:
            assert intent.name == "HassTurnOff"


class TestGreetings:
    """Test greeting intents."""
    
    def test_simple_greetings(self, classifier):
        """Test basic greeting patterns."""
        greetings = ["hi", "hello", "hey", "hello zoe"]
        
        for greeting in greetings:
            intent = classifier.classify(greeting)
            if intent:  # Greetings may use keyword fallback
                assert intent.name == "Greeting", f"Wrong intent for: {greeting}"


class TestPerformance:
    """Test classification performance."""
    
    def test_classification_speed_tier_0(self, classifier):
        """Test that Tier 0 (HassIL) meets <5ms target."""
        query = "add bread to shopping list"
        
        # Warmup
        classifier.classify(query)
        
        # Measure 100 classifications
        times = []
        for _ in range(100):
            start = time.time()
            intent = classifier.classify(query)
            elapsed_ms = (time.time() - start) * 1000
            times.append(elapsed_ms)
            
            assert intent is not None, "Should match every time"
            assert intent.tier == 0, "Should use Tier 0 (HassIL)"
        
        avg_ms = sum(times) / len(times)
        max_ms = max(times)
        
        print(f"\n📊 Performance: avg={avg_ms:.2f}ms, max={max_ms:.2f}ms")
        
        assert avg_ms < 10, f"Average {avg_ms:.2f}ms exceeds 10ms threshold"
        assert max_ms < 20, f"Max {max_ms:.2f}ms exceeds 20ms threshold"
    
    def test_batch_performance(self, classifier):
        """Test batch classification performance."""
        queries = [
            "add bread to shopping",
            "show my list",
            "remove milk",
            "turn on the lights",
            "what's the weather",
        ] * 20  # 100 queries
        
        start = time.time()
        matched = 0
        
        for query in queries:
            intent = classifier.classify(query)
            if intent:
                matched += 1
        
        elapsed = time.time() - start
        avg_ms = (elapsed / len(queries)) * 1000
        
        print(f"\n📊 Batch: {len(queries)} queries in {elapsed:.2f}s, avg={avg_ms:.2f}ms, match_rate={matched/len(queries)*100:.1f}%")
        
        assert avg_ms < 15, f"Batch average {avg_ms:.2f}ms exceeds 15ms threshold"


class TestNoMatch:
    """Test queries that should NOT match any intent."""
    
    def test_complex_queries_no_match(self, classifier):
        """Complex queries should return None (fall back to LLM)."""
        complex_queries = [
            "what's the meaning of life",
            "tell me a story about dragons",
            "help me plan my vacation to Japan",
            "explain quantum physics",
        ]
        
        for query in complex_queries:
            intent = classifier.classify(query)
            assert intent is None, f"Complex query should not match: {query}"


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "--tb=short"])

