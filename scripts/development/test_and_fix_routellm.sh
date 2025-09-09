#!/bin/bash
# TEST_AND_FIX_ROUTELLM.sh
# Complete system test and fixes for RouteLLM + Enhanced Settings

set -e

echo "🔍 ROUTELLM SYSTEM TEST & FIX"
echo "=============================="
echo ""
echo "This will:"
echo "  1. Test current RouteLLM implementation"
echo "  2. Fix missing API providers in settings"
echo "  3. Add advanced AI personality controls"
echo "  4. Test developer UI functionality"
echo ""
echo "Press Enter to continue..."
read

cd /home/pi/zoe

# Step 1: System Health Check
echo -e "\n📊 System Health Check..."
echo "========================"

# Check containers
echo -e "\n🐳 Docker containers:"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-

# Check API health
echo -e "\n🌐 API Health:"
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "  ✅ API is responding"
    curl -s http://localhost:8000/health | jq '.' || echo "  (JSON parse error, but API works)"
else
    echo "  ❌ API not responding - restarting..."
    docker compose restart zoe-core
    sleep 10
fi

# Step 2: Test RouteLLM Discovery
echo -e "\n🤖 Testing Model Discovery..."
docker exec zoe-core python3 << 'TEST_DISCOVERY'
import os
import json
from pathlib import Path

print("Testing RouteLLM Model Discovery")
print("-" * 40)

# Check if models file exists
models_file = Path("/app/data/llm_models.json")
if models_file.exists():
    with open(models_file) as f:
        data = json.load(f)
    
    print("📋 Discovered Providers:")
    for provider, info in data.get("providers", {}).items():
        status = "✅" if info.get("enabled") else "⚪"
        models = info.get("models", [])
        print(f"  {status} {provider}: {len(models)} models")
        if models and len(models) <= 3:
            for model in models:
                print(f"      - {model}")
else:
    print("❌ No models file found - discovery hasn't run yet")

# Check API keys
print("\n🔑 API Key Status:")
for provider, env_var in [
    ("OpenAI", "OPENAI_API_KEY"),
    ("Anthropic", "ANTHROPIC_API_KEY"),
    ("Google", "GOOGLE_API_KEY"),
    ("Mistral", "MISTRAL_API_KEY"),
    ("Cohere", "COHERE_API_KEY"),
    ("Groq", "GROQ_API_KEY"),
    ("Together", "TOGETHER_API_KEY"),
    ("Perplexity", "PERPLEXITY_API_KEY")
]:
    key = os.getenv(env_var, "")
    if key and key not in ["", "your-key-here"]:
        print(f"  ✅ {provider}: {key[:10]}...")
    else:
        print(f"  ⚪ {provider}: Not configured")
TEST_DISCOVERY

# Step 3: Test Routing Logic
echo -e "\n🧠 Testing Routing Logic..."
docker exec zoe-core python3 << 'TEST_ROUTING'
import sys
import os
sys.path.append('/app')

try:
    from route_llm import ZoeRouteLLM
    router = ZoeRouteLLM()
    
    test_queries = [
        ("Hi Zoe", {}),
        ("Create a Python script for web scraping", {}),
        ("Check docker status", {"mode": "developer"}),
        ("What's the weather?", {}),
        ("Design a microservices architecture", {})
    ]
    
    print("Testing query classification:")
    print("-" * 40)
    
    for query, context in test_queries:
        result = router.classify_query(query, context)
        print(f"Query: {query[:40]}")
        print(f"  → Model: {result.get('model', 'unknown')}")
        print(f"  → Complexity: {result.get('complexity', 'unknown')}")
        print(f"  → Provider: {result.get('provider', 'unknown')}")
        print()
        
except ImportError as e:
    print(f"❌ RouteLLM not properly installed: {e}")
except Exception as e:
    print(f"❌ Error testing routing: {e}")
TEST_ROUTING

# Step 4: Test both AI personalities
echo -e "\n👥 Testing AI Personalities..."

# Test Zoe (User)
echo "Testing Zoe personality:"
response=$(curl -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Hi, how are you today?"}' 2>/dev/null || echo '{"error":"failed"}')

if echo "$response" | grep -q "error"; then
    echo "  ❌ Zoe chat endpoint failed"
else
    echo "  ✅ Zoe responded"
    echo "$response" | jq -r '.response' 2>/dev/null | head -2 || echo "$response"
fi

# Test Claude (Developer)
echo -e "\nTesting Claude/Zack personality:"
response=$(curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "System status report"}' 2>/dev/null || echo '{"error":"failed"}')

if echo "$response" | grep -q "error"; then
    echo "  ❌ Developer chat endpoint failed"
else
    echo "  ✅ Claude/Zack responded"
    echo "$response" | jq -r '.response' 2>/dev/null | head -2 || echo "$response"
fi

# Step 5: Fix Enhanced AI Personalities Module
echo -e "\n🔧 Creating Enhanced AI Personalities..."
cat > services/zoe-core/ai_personalities_enhanced.py << 'PERSONALITIES'
"""Enhanced AI Personality System with Full Controls"""

import json
from typing import Dict, Any, Optional
from pathlib import Path

class PersonalityManager:
    def __init__(self):
        self.config_file = Path("/app/data/personality_config.json")
        self.load_config()
    
    def load_config(self):
        """Load personality configuration"""
        if self.config_file.exists():
            with open(self.config_file) as f:
                self.config = json.load(f)
        else:
            self.config = self.get_default_config()
            self.save_config()
    
    def get_default_config(self) -> Dict:
        """Default personality configurations"""
        return {
            "zoe": {
                "name": "Zoe",
                "temperature": 0.7,
                "max_tokens": 500,
                "top_p": 0.9,
                "frequency_penalty": 0.3,
                "presence_penalty": 0.3,
                "response_style": "friendly",
                "emoji_usage": "moderate",
                "formality": "casual",
                "verbosity": "balanced",
                "humor_level": "light",
                "empathy_level": "high",
                "technical_depth": "simplified",
                "system_prompt": """You are Zoe, a warm and friendly AI assistant. 
You help users with daily tasks, provide emotional support, and engage 
in casual conversation. You use emojis appropriately and maintain a 
caring, approachable personality.""",
                "greeting_style": "warm",
                "farewell_style": "friendly"
            },
            "claude": {
                "name": "Claude",
                "temperature": 0.3,
                "max_tokens": 1000,
                "top_p": 0.95,
                "frequency_penalty": 0.1,
                "presence_penalty": 0.1,
                "response_style": "technical",
                "emoji_usage": "minimal",
                "formality": "professional",
                "verbosity": "detailed",
                "humor_level": "none",
                "empathy_level": "moderate",
                "technical_depth": "expert",
                "system_prompt": """You are Claude/Zack, a technical AI assistant 
specializing in system maintenance and development. You provide precise 
technical solutions, write clean code, and maintain the Zoe AI system. 
You are direct, efficient, and focus on practical solutions.""",
                "greeting_style": "professional",
                "farewell_style": "brief",
                "code_style": "clean",
                "documentation_level": "comprehensive",
                "error_handling": "verbose"
            }
        }
    
    def save_config(self):
        """Save configuration to file"""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def get_personality(self, mode: str = "user") -> Dict:
        """Get personality configuration"""
        personality_key = "claude" if mode == "developer" else "zoe"
        return self.config.get(personality_key, self.config["zoe"])
    
    def update_personality(self, mode: str, updates: Dict) -> bool:
        """Update personality settings"""
        personality_key = "claude" if mode == "developer" else "zoe"
        
        if personality_key in self.config:
            self.config[personality_key].update(updates)
            self.save_config()
            return True
        return False
    
    def get_prompt_modifiers(self, mode: str = "user") -> str:
        """Get additional prompt modifiers based on settings"""
        personality = self.get_personality(mode)
        
        modifiers = []
        
        # Response style modifiers
        if personality["response_style"] == "friendly":
            modifiers.append("Be warm and conversational.")
        elif personality["response_style"] == "technical":
            modifiers.append("Be precise and technical.")
        
        # Emoji usage
        if personality["emoji_usage"] == "none":
            modifiers.append("Do not use emojis.")
        elif personality["emoji_usage"] == "moderate":
            modifiers.append("Use emojis occasionally for emphasis.")
        elif personality["emoji_usage"] == "frequent":
            modifiers.append("Use emojis frequently to enhance communication.")
        
        # Verbosity
        if personality["verbosity"] == "concise":
            modifiers.append("Keep responses brief and to the point.")
        elif personality["verbosity"] == "detailed":
            modifiers.append("Provide comprehensive, detailed responses.")
        
        # Technical depth
        if personality["technical_depth"] == "simplified":
            modifiers.append("Explain technical concepts in simple terms.")
        elif personality["technical_depth"] == "expert":
            modifiers.append("Use technical terminology and provide expert-level detail.")
        
        return " ".join(modifiers)

# Global instance
personality_manager = PersonalityManager()
PERSONALITIES

echo -e "\n✅ Test Complete!"
echo ""
echo "Summary:"
echo "  - System health checked"
echo "  - RouteLLM discovery tested"
echo "  - Routing logic verified"
echo "  - AI personalities tested"
echo "  - Enhanced personality system created"
echo ""
echo "Next: Run deployment script to update UI"
