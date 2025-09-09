#!/bin/bash
echo "🔧 Fixing AI personality integration..."

# Update the AI client to use personalities
docker exec zoe-core python3 << 'PYTHON'
import os

# First, let's check what AI client file exists
ai_files = ['/app/ai_client.py', '/app/ai_router.py', '/app/llm_client.py']
ai_file = None

for f in ai_files:
    if os.path.exists(f):
        ai_file = f
        print(f"Found AI file: {f}")
        break

if not ai_file:
    # Create a new AI client
    print("Creating new AI client with personalities...")
    with open('/app/ai_client.py', 'w') as f:
        f.write('''
from ai_personalities import get_personality
import httpx
import json

async def generate_response(message: str, context: dict = None) -> str:
    """Generate AI response with appropriate personality"""
    
    # Determine which personality to use
    mode = context.get("mode", "user") if context else "user"
    personality = get_personality(mode)
    
    # Build the prompt with personality
    system_prompt = personality["prompt"]
    full_prompt = f"{system_prompt}\\n\\nUser: {message}\\n{personality['name']}:"
    
    try:
        # Call Ollama with the personality-specific prompt
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "http://zoe-ollama:11434/api/generate",
                json={
                    "model": "llama3.2:3b",
                    "prompt": full_prompt,
                    "temperature": personality["temperature"],
                    "stream": False
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("response", f"I'm {personality['name']}, how can I help?")
    except Exception as e:
        print(f"Error: {e}")
        
    return f"Hi, I'm {personality['name']}! I'm having a moment, please try again."
''')
    print("✅ Created new AI client with personalities")

else:
    # Update existing AI client
    print(f"Updating existing {ai_file}...")
    with open(ai_file, 'r') as f:
        content = f.read()
    
    # Check if personalities are already integrated
    if 'ai_personalities' not in content:
        # Add import at the top
        import_line = "from ai_personalities import get_personality\n"
        
        # Find where to add the import
        if 'import' in content:
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if 'import' in line and not line.startswith('#'):
                    lines.insert(i+1, import_line)
                    break
            content = '\n'.join(lines)
        else:
            content = import_line + content
        
        with open(ai_file, 'w') as f:
            f.write(content)
        print(f"✅ Updated {ai_file} with personality import")

print("\n📝 Checking current setup...")
import sys
sys.path.append('/app')

try:
    from ai_personalities import get_personality
    zack = get_personality('developer')
    zoe = get_personality('user')
    print(f"✅ Zack personality: {zack['name']} (temp: {zack['temperature']})")
    print(f"✅ Zoe personality: {zoe['name']} (temp: {zoe['temperature']})")
except Exception as e:
    print(f"❌ Error loading personalities: {e}")
PYTHON

echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core
sleep 10

echo -e "\n🧪 Testing personalities..."
echo "Testing Zoe (user personality):"
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hi, who are you?"}'

echo -e "\n\nTesting Zack (developer personality):"
curl -X POST http://localhost:8080/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hi, who are you?"}'
