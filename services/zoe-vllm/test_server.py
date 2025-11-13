#!/usr/bin/env python3
"""
Test vLLM server endpoints
"""
import requests
import json
import sys

BASE_URL = "http://localhost:11434"

print("🔬 Testing vLLM Server Endpoints")
print("=" * 60)
print("")

# Test 1: Health check
print("1️⃣ Testing health endpoint...")
try:
    response = requests.get(f"{BASE_URL}/health", timeout=5)
    if response.status_code == 200:
        print(f"✅ Health check passed: {response.status_code}")
    else:
        print(f"⚠️  Health check returned: {response.status_code}")
except Exception as e:
    print(f"❌ Health check failed: {e}")
    print("   Is the server running?")
    sys.exit(1)

# Test 2: Models endpoint
print("\n2️⃣ Testing models endpoint...")
try:
    response = requests.get(f"{BASE_URL}/v1/models", timeout=5)
    if response.status_code == 200:
        models = response.json()
        print(f"✅ Models endpoint working")
        print(f"   Available models: {json.dumps(models, indent=2)}")
    else:
        print(f"⚠️  Models endpoint returned: {response.status_code}")
except Exception as e:
    print(f"❌ Models endpoint failed: {e}")

# Test 3: Simple completion
print("\n3️⃣ Testing completion endpoint...")
try:
    response = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        json={
            "model": "llama-3.2-3b",
            "messages": [{"role": "user", "content": "Say hello in one word"}],
            "max_tokens": 5
        },
        timeout=30
    )
    
    if response.status_code == 200:
        result = response.json()
        content = result['choices'][0]['message']['content']
        print(f"✅ Completion successful!")
        print(f"   Request: 'Say hello in one word'")
        print(f"   Response: '{content}'")
    else:
        print(f"❌ Completion failed with status: {response.status_code}")
        print(f"   Response: {response.text}")
        sys.exit(1)
        
except Exception as e:
    print(f"❌ Completion failed: {e}")
    sys.exit(1)

# Test 4: Streaming (if server supports it)
print("\n4️⃣ Testing streaming...")
try:
    response = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        json={
            "model": "llama-3.2-3b",
            "messages": [{"role": "user", "content": "Count to 3"}],
            "max_tokens": 20,
            "stream": True
        },
        stream=True,
        timeout=30
    )
    
    tokens = []
    for line in response.iter_lines():
        if line:
            tokens.append(line.decode())
    
    if len(tokens) > 0:
        print(f"✅ Streaming successful!")
        print(f"   Received {len(tokens)} chunks")
    else:
        print(f"⚠️  Streaming returned no data")
        
except Exception as e:
    print(f"⚠️  Streaming test failed (may not be critical): {e}")

print("\n" + "=" * 60)
print("🎉 SERVER TESTS COMPLETE!")
print("")


