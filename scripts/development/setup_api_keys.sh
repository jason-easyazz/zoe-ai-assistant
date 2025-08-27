#!/bin/bash
# SETUP_API_KEYS.sh
# Location: scripts/development/setup_api_keys.sh
# Purpose: Ensure API keys are properly configured for RouteLLM

set -e

echo "🔑 SETTING UP API KEYS FOR ROUTELLM"
echo "===================================="
echo ""

cd /home/pi/zoe

# Step 1: Check current .env file
echo "📄 Checking .env file..."
if [ -f .env ]; then
    echo "✅ .env file exists"
    
    # Check for keys (without showing values)
    if grep -q "OPENAI_API_KEY=" .env; then
        echo "✅ OpenAI key found in .env"
    else
        echo "❌ OpenAI key NOT in .env"
    fi
    
    if grep -q "ANTHROPIC_API_KEY=" .env; then
        echo "✅ Anthropic key found in .env"
    else
        echo "❌ Anthropic key NOT in .env"
    fi
else
    echo "❌ No .env file found!"
    echo "Creating from template..."
    cp .env.example .env
    echo "Please add your API keys to .env file"
    exit 1
fi

# Step 2: Ensure docker-compose.yml passes environment variables
echo -e "\n📝 Checking docker-compose.yml configuration..."

# Check if zoe-core service has env_file directive
if grep -A 10 "zoe-core:" docker-compose.yml | grep -q "env_file:"; then
    echo "✅ env_file directive found"
else
    echo "⚠️  env_file directive missing - adding it..."
    
    # Backup docker-compose.yml
    cp docker-compose.yml docker-compose.yml.backup
    
    # Add env_file to zoe-core service
    python3 << 'PYTHON'
import yaml

with open('docker-compose.yml', 'r') as f:
    config = yaml.safe_load(f)

# Add env_file to zoe-core service
if 'zoe-core' in config['services']:
    if 'env_file' not in config['services']['zoe-core']:
        config['services']['zoe-core']['env_file'] = ['.env']
        print("Added env_file: .env to zoe-core service")

# Save updated config
with open('docker-compose.yml', 'w') as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
print("✅ docker-compose.yml updated")
PYTHON
fi

# Step 3: Restart container to load environment
echo -e "\n🔄 Restarting zoe-core to load API keys..."
docker compose up -d zoe-core
sleep 5

# Step 4: Verify keys are loaded
echo -e "\n✅ Verifying API keys in container..."
docker exec zoe-core python3 << 'VERIFY'
import os

print("\n🔑 API Key Status in Container:")
print("=" * 40)

# Check OpenAI
openai_key = os.getenv("OPENAI_API_KEY", "")
if openai_key and openai_key != "your-key-here":
    print(f"✅ OpenAI Key: Loaded ({openai_key[:7]}...)")
else:
    print("❌ OpenAI Key: Not found or invalid")

# Check Anthropic  
anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
if anthropic_key and anthropic_key != "your-key-here":
    print(f"✅ Anthropic Key: Loaded ({anthropic_key[:10]}...)")
else:
    print("❌ Anthropic Key: Not found or invalid")

# Check other providers
for provider, env_var in [
    ("Google", "GOOGLE_API_KEY"),
    ("Mistral", "MISTRAL_API_KEY"),
    ("Cohere", "COHERE_API_KEY"),
    ("Groq", "GROQ_API_KEY")
]:
    key = os.getenv(env_var, "")
    if key and key != "your-key-here":
        print(f"✅ {provider} Key: Loaded")
    else:
        print(f"⚪ {provider} Key: Not configured")

print("=" * 40)
VERIFY

# Step 5: Run discovery again
echo -e "\n🔍 Running model discovery with loaded keys..."
docker exec zoe-core python3 << 'DISCOVER'
import os
import json
import httpx
import asyncio

async def quick_discover():
    results = {}
    
    # Test OpenAI
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if openai_key and openai_key != "your-key-here":
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {openai_key}"},
                    timeout=10
                )
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["id"] for m in data["data"] if "gpt" in m["id"]]
                    results["openai"] = {"status": "✅", "models": len(models)}
                else:
                    results["openai"] = {"status": "❌", "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            results["openai"] = {"status": "❌", "error": str(e)[:50]}
    else:
        results["openai"] = {"status": "⚪", "error": "No API key"}
    
    # Test Anthropic
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if anthropic_key and anthropic_key != "your-key-here":
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": anthropic_key,
                        "anthropic-version": "2023-06-01"
                    },
                    json={
                        "model": "claude-3-haiku-20240307",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "test"}]
                    },
                    timeout=10
                )
                if resp.status_code in [200, 201]:
                    results["anthropic"] = {"status": "✅", "models": "Working"}
                else:
                    results["anthropic"] = {"status": "❌", "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            results["anthropic"] = {"status": "❌", "error": str(e)[:50]}
    else:
        results["anthropic"] = {"status": "⚪", "error": "No API key"}
    
    print("\n📊 Discovery Test Results:")
    print("=" * 40)
    for provider, result in results.items():
        print(f"{provider}: {result}")
    print("=" * 40)
    
    if any(r["status"] == "✅" for r in results.values()):
        print("\n✅ At least one API provider is working!")
        print("Now run: ./scripts/development/fix_model_discovery.sh")
    else:
        print("\n❌ No API providers working. Check:")
        print("1. API keys are valid")
        print("2. Network connection")
        print("3. API key format (sk-... for OpenAI, sk-ant-... for Anthropic)")

asyncio.run(quick_discover())
DISCOVER

echo -e "\n✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. If API keys loaded successfully, run:"
echo "   ./scripts/development/fix_model_discovery.sh"
echo ""
echo "2. If keys NOT loaded, check your .env file:"
echo "   nano .env"
echo "   # Ensure you have:"
echo "   OPENAI_API_KEY=sk-..."
echo "   ANTHROPIC_API_KEY=sk-ant-..."
