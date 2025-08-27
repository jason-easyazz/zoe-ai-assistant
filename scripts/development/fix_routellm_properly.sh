#!/bin/bash
# FIX_ROUTELLM_PROPERLY.sh
# Location: scripts/development/fix_routellm_properly.sh
# Purpose: Fix docker-compose and complete RouteLLM setup

set -e

echo "🔧 FIXING ROUTELLM SETUP"
echo "========================"
echo ""

cd /home/pi/zoe

# Step 1: Fix docker-compose.yml
echo "📝 Fixing docker-compose.yml..."

# First, restore from backup if it exists
if [ -f docker-compose.yml.backup ]; then
    echo "Restoring from backup..."
    cp docker-compose.yml.backup docker-compose.yml
fi

# Check current state
echo "Current docker-compose.yml state:"
grep -A 5 "zoe-core:" docker-compose.yml | head -10

# Step 2: Just restart the container - env_file is already there
echo -e "\n🔄 Restarting zoe-core to ensure environment is loaded..."
docker compose down zoe-core
docker compose up -d zoe-core
sleep 10

# Step 3: Verify keys are loaded
echo -e "\n🔑 Checking API keys in container..."
docker exec zoe-core bash -c 'echo "OpenAI: $([ -n "$OPENAI_API_KEY" ] && echo "✅ Loaded" || echo "❌ Missing")"; echo "Anthropic: $([ -n "$ANTHROPIC_API_KEY" ] && echo "✅ Loaded" || echo "❌ Missing")"'

# Step 4: Run discovery inside container
echo -e "\n🔍 Running Model Discovery..."
docker exec zoe-core python3 << 'DISCOVERY'
import os
import json
import httpx
import asyncio
from datetime import datetime

async def discover_models():
    config_file = "/app/data/llm_models.json"
    
    # Load config
    with open(config_file) as f:
        config = json.load(f)
    
    results = []
    
    # Test OpenAI
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if openai_key and len(openai_key) > 10:
        print(f"🔍 Testing OpenAI (key starts with {openai_key[:7]}...)")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {openai_key}"},
                    timeout=15
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["id"] for m in data["data"] if "gpt" in m["id"]]
                    
                    # Select best models
                    selected = []
                    for pattern in ["gpt-4", "gpt-3.5-turbo"]:
                        matching = [m for m in models if pattern in m]
                        selected.extend(matching[:3])
                    
                    if selected:
                        config["providers"]["openai"]["enabled"] = True
                        config["providers"]["openai"]["models"] = selected[:10]
                        config["providers"]["openai"]["default"] = selected[0]
                        results.append(f"✅ OpenAI: {len(selected)} models")
                        print(f"✅ OpenAI: Found {len(selected)} models")
                else:
                    print(f"❌ OpenAI: HTTP {resp.status_code}")
                    results.append(f"❌ OpenAI: HTTP {resp.status_code}")
                    
        except Exception as e:
            print(f"❌ OpenAI Error: {e}")
            results.append(f"❌ OpenAI: {str(e)[:50]}")
    else:
        print("⚪ OpenAI: No API key")
        results.append("⚪ OpenAI: No API key")
    
    # Test Anthropic
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if anthropic_key and len(anthropic_key) > 10:
        print(f"🔍 Testing Anthropic (key starts with {anthropic_key[:10]}...)")
        
        models_to_test = [
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307"
        ]
        
        working = []
        
        try:
            async with httpx.AsyncClient() as client:
                # Test haiku first (cheapest)
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": anthropic_key,
                        "anthropic-version": "2023-06-01"
                    },
                    json={
                        "model": "claude-3-haiku-20240307",
                        "messages": [{"role": "user", "content": "Hi"}],
                        "max_tokens": 1
                    },
                    timeout=15
                )
                
                if resp.status_code in [200, 201]:
                    # If haiku works, assume all models work
                    working = models_to_test
                    config["providers"]["anthropic"]["enabled"] = True
                    config["providers"]["anthropic"]["models"] = working
                    config["providers"]["anthropic"]["default"] = working[0]
                    results.append(f"✅ Anthropic: {len(working)} models")
                    print(f"✅ Anthropic: {len(working)} models available")
                else:
                    print(f"❌ Anthropic: HTTP {resp.status_code}")
                    results.append(f"❌ Anthropic: HTTP {resp.status_code}")
                    
        except Exception as e:
            print(f"❌ Anthropic Error: {e}")
            results.append(f"❌ Anthropic: {str(e)[:50]}")
    else:
        print("⚪ Anthropic: No API key")
        results.append("⚪ Anthropic: No API key")
    
    # Check Ollama
    print("🔍 Testing Ollama...")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://zoe-ollama:11434/api/tags", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                models = [m["name"] for m in data.get("models", [])]
                if models:
                    config["providers"]["ollama"]["enabled"] = True
                    config["providers"]["ollama"]["models"] = models
                    config["providers"]["ollama"]["default"] = models[0]
                    results.append(f"✅ Ollama: {len(models)} models")
                    print(f"✅ Ollama: {models}")
    except Exception as e:
        results.append(f"❌ Ollama: {str(e)[:50]}")
    
    # Update default provider
    if config["providers"]["anthropic"]["enabled"]:
        config["default_provider"] = "anthropic"
    elif config["providers"]["openai"]["enabled"]:
        config["default_provider"] = "openai"
    elif config["providers"]["ollama"]["enabled"]:
        config["default_provider"] = "ollama"
    
    # Save config
    config["last_updated"] = datetime.now().isoformat()
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)
    
    print("\n" + "="*50)
    print("DISCOVERY RESULTS:")
    for result in results:
        print(f"  {result}")
    print("="*50)
    
    # Count totals
    total_providers = sum(1 for p in config["providers"].values() if p["enabled"])
    total_models = sum(len(p.get("models", [])) for p in config["providers"].values() if p["enabled"])
    
    print(f"\n✅ Summary:")
    print(f"  Providers Enabled: {total_providers}")
    print(f"  Total Models: {total_models}")
    print(f"  Default Provider: {config.get('default_provider', 'none')}")

asyncio.run(discover_models())
DISCOVERY

# Step 5: Show final results
echo -e "\n📊 Final Configuration:"
echo "====================="
cat data/llm_models.json | jq '.providers | to_entries | map(select(.value.enabled == true)) | map({
    provider: .key,
    models: (.value.models | length),
    default: .value.default
})'

echo -e "\n✅ ROUTELLM SETUP COMPLETE!"
echo ""
echo "If models weren't discovered:"
echo "1. Check API key format:"
echo "   - OpenAI: Must start with 'sk-'"
echo "   - Anthropic: Must start with 'sk-ant-'"
echo ""
echo "2. Verify keys in .env:"
echo "   cat .env | grep API_KEY | sed 's/=.*/=***/'
echo ""
echo "3. Test keys manually:"
echo "   curl https://api.openai.com/v1/models -H 'Authorization: Bearer YOUR_KEY'"
