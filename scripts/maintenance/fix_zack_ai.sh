#!/bin/bash
# FIX_ZACK_AI.sh - Fix the AI client integration

echo "🔧 FIXING ZACK'S AI INTEGRATION"
echo "================================"

cd /home/pi/zoe

# Fix the AI client calls in developer.py
docker exec zoe-core python3 << 'PYTHON_FIX'
import re

# Read the current developer.py
with open('/app/routers/developer.py', 'r') as f:
    content = f.read()

# Fix the generate_response calls - remove temperature parameter
content = re.sub(
    r'await ai_client\.generate_response\([^)]+\)',
    lambda m: m.group(0).replace(', temperature=0.3', '').replace(', temperature=0.2', '').replace(', temperature=0.4', '').replace(', max_tokens=2000', '').replace(', max_tokens=2500', '').replace(', max_tokens=3000', ''),
    content
)

# Simplify the AI call to match actual interface
content = content.replace(
    'ai_response = await ai_client.generate_response(\n                prompt,\n                temperature=0.3,\n                max_tokens=2000\n            )',
    'ai_response = await ai_client.generate_response(prompt)'
)

# Fix all other instances
content = content.replace(
    'await ai_client.generate_response(prompt, temperature=0.2, max_tokens=3000)',
    'await ai_client.generate_response(prompt)'
)
content = content.replace(
    'await ai_client.generate_response(prompt, temperature=0.4, max_tokens=2500)',
    'await ai_client.generate_response(prompt)'
)

# Write back
with open('/app/routers/developer.py', 'w') as f:
    f.write(content)

print("✅ Fixed AI client calls")

# Also check what the actual AI client looks like
print("\n📋 Checking AI client interface:")
try:
    with open('/app/ai_client.py', 'r') as f:
        lines = f.readlines()
        for i, line in enumerate(lines):
            if 'def generate_response' in line or 'async def generate_response' in line:
                print(f"Found at line {i}: {line.strip()}")
                # Print next few lines to see parameters
                for j in range(1, 5):
                    if i+j < len(lines):
                        print(f"  {lines[i+j].strip()}")
                break
except:
    print("Could not read ai_client.py")
PYTHON_FIX

# Restart
docker restart zoe-core
sleep 8

# Test the fix
echo ""
echo "🧪 Testing Zack's AI now:"
curl -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What are the top 3 improvements we should make to our system?"}' | jq -r '.response' | head -50
