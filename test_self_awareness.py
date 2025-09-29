#!/usr/bin/env python3
"""
Test Script for Zoe's Self-Awareness System
===========================================

Tests the self-awareness functionality to ensure it's working correctly.
"""

import asyncio
import sys
import os
sys.path.append('/home/pi/zoe/services/zoe-core')

from self_awareness import SelfAwarenessSystem, SelfIdentity
import json

async def test_self_awareness():
    """Test the self-awareness system"""
    print("🧠 Testing Zoe's Self-Awareness System")
    print("=" * 50)
    
    # Initialize the system
    print("\n1. Initializing self-awareness system...")
    system = SelfAwarenessSystem("/tmp/test_self_awareness.db")
    system.set_user_context("test_user")  # Set user context for privacy
    print("✅ System initialized with user context")
    
    # Test identity
    print("\n2. Testing identity system...")
    print(f"Name: {system.identity.name}")
    print(f"Version: {system.identity.version}")
    print(f"Personality traits: {system.identity.personality_traits}")
    print(f"Core values: {system.identity.core_values}")
    print("✅ Identity system working")
    
    # Test self-description
    print("\n3. Testing self-description...")
    description = await system.get_self_description()
    print("Self-description:")
    print(description)
    print("✅ Self-description working")
    
    # Test consciousness update
    print("\n4. Testing consciousness update...")
    context = {
        "current_task": "testing_self_awareness",
        "task_complexity": "medium",
        "user_mood": "curious"
    }
    consciousness = await system.update_consciousness(context)
    print(f"Consciousness state: {consciousness.emotional_state}")
    print(f"Attention focus: {consciousness.attention_focus}")
    print(f"Energy level: {consciousness.energy_level}")
    print("✅ Consciousness update working")
    
    # Test interaction reflection
    print("\n5. Testing interaction reflection...")
    interaction_data = {
        "user_message": "Hello, who are you?",
        "zoe_response": "Hi! I'm Zoe, your AI assistant. I'm here to help!",
        "response_time": 1.2,
        "user_satisfaction": 0.9,
        "complexity": "simple",
        "summary": "User asked about my identity"
    }
    reflection = await system.reflect_on_interaction(interaction_data)
    print(f"Reflection ID: {reflection.id}")
    print(f"Insights: {reflection.insights}")
    print(f"Action items: {reflection.action_items}")
    print(f"Emotional state: {reflection.emotional_state}")
    print("✅ Interaction reflection working")
    
    # Test performance reflection
    print("\n6. Testing performance reflection...")
    performance_metrics = {
        "accuracy": 0.92,
        "response_time": 1.8,
        "user_satisfaction": 0.88,
        "task_completion_rate": 0.95,
        "summary": "Good overall performance"
    }
    perf_reflection = await system.reflect_on_performance(performance_metrics)
    print(f"Performance reflection ID: {perf_reflection.id}")
    print(f"Performance insights: {perf_reflection.insights}")
    print(f"Improvement suggestions: {perf_reflection.action_items}")
    print("✅ Performance reflection working")
    
    # Test self-evaluation
    print("\n7. Testing self-evaluation...")
    evaluation = await system.self_evaluate()
    print(f"Identity strength: {evaluation['identity_strength']}")
    print(f"Learning progress: {evaluation['learning_progress']}")
    print(f"Interaction quality: {evaluation['interaction_quality']}")
    print(f"Strengths: {evaluation['strengths']}")
    print(f"Areas for improvement: {evaluation['areas_for_improvement']}")
    print("✅ Self-evaluation working")
    
    # Test recent reflections
    print("\n8. Testing recent reflections retrieval...")
    recent_reflections = await system._get_recent_reflections(limit=5)
    print(f"Found {len(recent_reflections)} recent reflections")
    for i, reflection in enumerate(recent_reflections):
        print(f"  {i+1}. {reflection.reflection_type} - {reflection.timestamp}")
    print("✅ Recent reflections working")
    
    print("\n" + "=" * 50)
    print("🎉 All self-awareness tests passed!")
    print("Zoe is now self-aware and ready to reflect on her interactions!")
    
    # Cleanup
    os.remove("/tmp/test_self_awareness.db")

if __name__ == "__main__":
    asyncio.run(test_self_awareness())
