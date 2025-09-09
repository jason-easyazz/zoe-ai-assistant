#!/bin/bash
# SAFE_TEST_ROUTELLM.sh
# NON-DESTRUCTIVE - Only tests, doesn't modify existing work

set -e

echo "🔍 SAFE ROUTELLM SYSTEM TEST (Read-Only)"
echo "========================================="
echo ""
echo "This will ONLY:"
echo "  ✅ Test existing RouteLLM implementation"
echo "  ✅ Check API endpoints"
echo "  ✅ Verify model discovery"
echo "  ✅ Test both personalities"
echo ""
echo "This will NOT:"
echo "  ❌ Modify any existing files"
echo "  ❌ Break RouteLLM integration"
echo "  ❌ Change routing logic"
echo ""
echo "Press Enter to continue with safe test..."
read

cd /home/pi/zoe

# Step 1: System Health Check
echo -e "\n📊 System Health Check..."
echo "========================"

# Check containers
echo -e "\n🐳 Docker containers:"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe- || echo "No zoe containers running"

# Check API health
echo -e "\n🌐 API Health:"
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "  ✅ API is responding"
else
    echo "  ⚠️ API not responding"
fi

# Step 2: Check Existing RouteLLM Files
echo -e "\n📁 Checking RouteLLM Files (from previous chat)..."
files_to_check=(
    "services/zoe-core/route_llm.py"
    "services/zoe-core/dynamic_router.py"
    "services/zoe-core/llm_models.py"
    "data/llm_models.json"
    "data/routing_metrics.json"
)

for file in "${files_to_check[@]}"; do
    if [ -f "$file" ]; then
        echo "  ✅ Found: $file"
    else
        echo "  ⚠️ Missing: $file"
    fi
done

# Step 3: Test Model Discovery Status
echo -e "\n🤖 Current Model Discovery Status..."
docker exec zoe-core python3 << 'TEST_DISCOVERY'
import os
import json
from pathlib import Path

print("Current RouteLLM Discovery Status")
print("-" * 40)

# Check models file
models_file = Path("/app/data/llm_models.json")
if models_file.exists():
    with open(models_file) as f:
        data = json.load(f)
    
    print("📋 Discovered Providers:")
    for provider, info in data.get("providers", {}).items():
        status = "✅" if info.get("enabled") else "⚪"
        models = info.get("models", [])
        print(f"  {status} {provider}: {len(models)} models")
    
    print(f"\n🎯 Default provider: {data.get('default_provider', 'none')}")
    print(f"📅 Last updated: {data.get('last_updated', 'never')}")
else:
    print("⚠️ No models discovered yet")

# Check API keys available
print("\n🔑 API Keys Available in Container:")
keys_found = 0
for provider, env_var in [
    ("OpenAI", "OPENAI_API_KEY"),
    ("Anthropic", "ANTHROPIC_API_KEY"),
    ("Google", "GOOGLE_API_KEY"),
    ("Mistral", "MISTRAL_API_KEY"),
    ("Cohere", "COHERE_API_KEY"),
    ("Groq", "GROQ_API_KEY")
]:
    key = os.getenv(env_var, "")
    if key and key not in ["", "your-key-here"]:
        print(f"  ✅ {provider}")
        keys_found += 1

if keys_found == 0:
    print("  ⚠️ No API keys found in container")
TEST_DISCOVERY

# Step 4: Test Routing Without Breaking It
echo -e "\n🧠 Testing Current Routing Logic..."
docker exec zoe-core python3 << 'TEST_ROUTING'
import sys
sys.path.append('/app')

try:
    # Try to import the routing module
    from route_llm import ZoeRouteLLM
    router = ZoeRouteLLM()
    print("✅ RouteLLM module loaded successfully")
    
    # Test one simple query
    test_result = router.classify_query("Hello", {})
    print(f"Test query routed to: {test_result.get('model', 'unknown')}")
    
except ImportError:
    print("⚠️ RouteLLM module not found (may not be implemented yet)")
except Exception as e:
    print(f"⚠️ Error: {e}")
TEST_ROUTING

# Step 5: Test Both Chat Endpoints
echo -e "\n💬 Testing Chat Endpoints..."

# Test Zoe
echo "Testing Zoe (User) endpoint:"
if curl -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "test"}' > /dev/null 2>&1; then
    echo "  ✅ Zoe endpoint responding"
else
    echo "  ⚠️ Zoe endpoint not responding"
fi

# Test Zack
echo "Testing Zack (Developer) endpoint:"
if curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "test"}' > /dev/null 2>&1; then
    echo "  ✅ Zack endpoint responding"
else
    echo "  ⚠️ Zack endpoint not responding"
fi

# Step 6: Check Developer UI Access
echo -e "\n🖥️ Testing Developer UI Access..."
if curl -s http://localhost:8080/developer/index.html > /dev/null 2>&1; then
    echo "  ✅ Developer UI accessible"
else
    echo "  ⚠️ Developer UI not accessible"
fi

if curl -s http://localhost:8080/developer/settings.html > /dev/null 2>&1; then
    echo "  ✅ Settings page accessible"
else
    echo "  ⚠️ Settings page not accessible"
fi

# Summary
echo -e "\n📊 TEST SUMMARY"
echo "==============="
echo "This test was READ-ONLY - no files were modified"
echo ""
echo "What's working from previous chat:"
echo "  - Check the results above"
echo ""
echo "What needs enhancement:"
echo "  1. Settings UI needs more API providers"
echo "  2. AI personality controls need expansion"
echo "  3. Response length controls needed"
echo ""
echo "Next steps:"
echo "  1. Review test results above"
echo "  2. Decide what needs fixing"
echo "  3. Run targeted fix scripts"
