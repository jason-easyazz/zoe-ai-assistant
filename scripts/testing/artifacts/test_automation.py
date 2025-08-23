#!/usr/bin/env python3
import requests
import json

# Test different types of requests
requests_to_test = [
    ("Generate a daily backup script", "script generation"),
    ("Check system health", "monitoring"),
    ("Optimize Docker performance", "optimization"),
    ("Create a task scheduler", "automation")
]

for prompt, category in requests_to_test:
    print(f"\n📝 Testing {category}:")
    print(f"   Prompt: {prompt}")
    
    response = requests.post(
        "http://localhost:8000/api/developer/chat",
        json={"message": prompt},
        timeout=30
    )
    
    if response.status_code == 200:
        data = response.json()
        print(f"   ✅ Model: {data.get('model_used')}")
        print(f"   Response length: {len(data.get('response', ''))} chars")
    else:
        print(f"   ❌ Error: {response.status_code}")

print("\n✨ Automation test complete!")
