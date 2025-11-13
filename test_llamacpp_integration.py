#!/usr/bin/env python3
"""
Test llama.cpp integration with Zoe
"""
import sys
import os
sys.path.insert(0, '/home/zoe/assistant/services/zoe-core')

import asyncio
from llm_provider import get_llm_provider

async def test_llamacpp():
    print("🧪 Testing llama.cpp Integration")
    print("=" * 60)
    print()
    
    # Get provider (should auto-detect llamacpp on Jetson)
    print("1️⃣ Getting LLM provider...")
    provider = get_llm_provider()
    print(f"✅ Provider: {provider.__class__.__name__}")
    print()
    
    # Test generation
    print("2️⃣ Testing generation...")
    try:
        response = await provider.generate("Say hello in one word", temperature=0.7)
        print(f"✅ Response: '{response}'")
    except Exception as e:
        print(f"❌ Generation failed: {e}")
        return False
    print()
    
    # Test streaming
    print("3️⃣ Testing streaming...")
    try:
        print("   Stream: ", end="", flush=True)
        async for token in provider.generate_stream("Count to 5", temperature=0.7):
            print(token, end="", flush=True)
        print()
        print("✅ Streaming works")
    except Exception as e:
        print(f"❌ Streaming failed: {e}")
        return False
    print()
    
    print("=" * 60)
    print("🎉 All integration tests passed!")
    return True

if __name__ == "__main__":
    success = asyncio.run(test_llamacpp())
    sys.exit(0 if success else 1)


