#!/bin/bash
# FIX_MODEL_DISCOVERY.sh
# Location: scripts/development/fix_model_discovery.sh
# Purpose: Fix and run model discovery for all providers

set -e

echo "🔧 FIXING MODEL DISCOVERY"
echo "========================="
echo ""

cd /home/pi/zoe

# Run discovery directly in container
docker exec zoe-core python3 << 'PYTHON'
import os
import json
import httpx
import asyncio
from datetime import datetime

print("🔍 Starting Model Discovery...")

config_file = "/app/data/llm_models.json"

# Load existing config
with open(config_file) as f:
    config = json.load(f)

async def discover():
    # Test OpenAI
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key and openai_key != "your-key-here":
        print("\n📡 Discovering OpenAI models...")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {openai_key}"},
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    all_models = [m["id"] for m in data["data"]]
                    
                    # Filter for useful models
                    gpt_models = sorted([m for m in all_models if "gpt" in m])
                    
                    # Prioritize important models
                    priority_models = []
                    for pattern in ["gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"]:
                        matches = [m for m in gpt_models if pattern in m]
                        if matches:
                            priority_models.extend(matches[:2])
                    
                    # Combine priority and others
                    final_models = priority_models + [m for m in gpt_models if m not in priority_models][:5]
                    
                    config["providers"]["openai"]["enabled"] = True
                    config["providers"]["openai"]["models"] = final_models
                    config["providers"]["openai"]["default"] = final_models[0] if final_models else None
                    print(f"  ✅ Found {len(final_models)} models: {final_models[:3]}...")
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    # Test Anthropic
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key and anthropic_key != "your-key-here":
        print("\n📡 Testing Anthropic models...")
        
        # Known Claude models
        claude_models = [
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229", 
            "claude-3-haiku-20240307",
            "claude-2.1",
            "claude-instant-1.2"
        ]
        
        working_models = []
        
        try:
            async with httpx.AsyncClient() as client:
                # Test each model
                for model in claude_models:
                    try:
                        response = await client.post(
                            "https://api.anthropic.com/v1/messages",
                            headers={
                                "x-api-key": anthropic_key,
                                "anthropic-version": "2023-06-01"
                            },
                            json={
                                "model": model,
                                "max_tokens": 1,
                                "messages": [{"role": "user", "content": "Hi"}]
                            },
                            timeout=5
                        )
                        # Model exists if we get 200 or even 400 (bad request but model exists)
                        if response.status_code in [200, 201]:
                            working_models.append(model)
                            print(f"  ✅ {model} - available")
                        elif response.status_code == 400:
                            # Check if it's a model error or other error
                            error_data = response.json()
                            if "model" not in str(error_data.get("error", {}).get("type", "")):
                                working_models.append(model)
                                print(f"  ✅ {model} - available")
                    except:
                        pass
                
                if working_models:
                    config["providers"]["anthropic"]["enabled"] = True
                    config["providers"]["anthropic"]["models"] = working_models
                    config["providers"]["anthropic"]["default"] = working_models[0]
                    print(f"\n  ✅ Total Anthropic models available: {len(working_models)}")
                
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    # Keep Ollama as is
    print("\n📡 Checking Ollama models...")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "http://zoe-ollama:11434/api/tags",
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                models = [m["name"] for m in data.get("models", [])]
                if models:
                    config["providers"]["ollama"]["enabled"] = True
                    config["providers"]["ollama"]["models"] = models
                    config["providers"]["ollama"]["default"] = models[0]
                    print(f"  ✅ Found {len(models)} models: {models}")
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    # Update config
    config["last_updated"] = datetime.now().isoformat()
    
    # Set default provider (prefer Anthropic > OpenAI > Ollama)
    for provider in ["anthropic", "openai", "ollama"]:
        if config["providers"][provider]["enabled"]:
            config["default_provider"] = provider
            break
    
    # Save config
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)
    
    print("\n" + "="*50)
    print("📊 DISCOVERY COMPLETE!")
    print("="*50)
    
    # Summary
    for provider, data in config["providers"].items():
        if data["enabled"]:
            print(f"\n✅ {provider.upper()}:")
            print(f"   Models: {len(data['models'])}")
            print(f"   Default: {data['default']}")
    
    print(f"\n🎯 Default Provider: {config['default_provider']}")

asyncio.run(discover())
PYTHON

echo ""
echo "✅ Discovery complete! Checking results..."
echo ""

# Show results
cat data/llm_models.json | jq '.providers | to_entries | map(select(.value.enabled == true)) | map({provider: .key, models: .value.models | length, default: .value.default})'

echo ""
echo "To test routing with discovered models:"
echo "docker exec zoe-core python3 -c 'from dynamic_router import dynamic_router; print(dynamic_router.get_best_model_for_complexity(\"complex\"))'"
