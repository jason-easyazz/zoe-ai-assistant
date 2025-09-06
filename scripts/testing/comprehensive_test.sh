#!/bin/bash
# COMPREHENSIVE TEST SUITE
set -e

echo "🧪 COMPREHENSIVE ZOE SYSTEM TEST"
echo "================================="

FAILURES=0
TESTS=0

# Test function
run_test() {
    TESTS=$((TESTS + 1))
    echo -n "Testing $1... "
    if eval "$2" > /dev/null 2>&1; then
        echo "✅ PASS"
    else
        echo "❌ FAIL"
        FAILURES=$((FAILURES + 1))
    fi
}

# 1. Container Tests
echo -e "\n📦 Container Tests:"
run_test "zoe-core running" "docker ps | grep -q zoe-core"
run_test "zoe-ui running" "docker ps | grep -q zoe-ui"
run_test "zoe-ollama running" "docker ps | grep -q zoe-ollama"
run_test "zoe-redis running" "docker ps | grep -q zoe-redis"

# 2. API Tests
echo -e "\n🌐 API Tests:"
run_test "API health" "curl -s http://localhost:8000/health"
run_test "Developer status" "curl -s http://localhost:8000/api/developer/status"
run_test "Developer metrics" "curl -s http://localhost:8000/api/developer/metrics"

# 3. Function Tests
echo -e "\n⚙️ Function Tests:"
run_test "execute_command exists" "docker exec zoe-core grep -q 'def execute_command' /app/routers/developer.py"
run_test "analyze_for_optimization exists" "docker exec zoe-core grep -q 'def analyze_for_optimization' /app/routers/developer.py"
run_test "Task endpoints exist" "docker exec zoe-core grep -q '/tasks' /app/routers/developer.py"

# 4. Database Tests
echo -e "\n💾 Database Tests:"
run_test "SQLite3 available" "docker exec zoe-core which sqlite3"
run_test "Database accessible" "docker exec zoe-core sqlite3 /app/data/zoe.db '.tables' 2>/dev/null"

# 5. AI Tests
echo -e "\n🤖 AI Tests:"
run_test "AI client exists" "docker exec zoe-core test -f /app/ai_client.py"
run_test "RouteLLM exists" "docker exec zoe-core test -f /app/llm_models.py"
run_test "AI imports work" "docker exec zoe-core python3 -c 'from ai_client import get_ai_response'"
run_test "RouteLLM loads" "docker exec zoe-core python3 -c 'from llm_models import LLMModelManager; m = LLMModelManager()'"

# 6. RouteLLM Dynamic Tests
echo -e "\n🧠 RouteLLM Dynamic Tests:"
run_test "Models discovered" "docker exec zoe-core test -f /app/data/llm_models.json"
run_test "No hardcoded models" "! docker exec zoe-core grep -q 'claude-3-opus-20240229' /app/llm_models.py"
run_test "Dynamic routing works" "docker exec zoe-core python3 -c 'from llm_models import manager; p, m = manager.get_model_for_request(\"test\")'"

# 7. Chat Test
echo -e "\n💬 Chat Test:"
run_test "Developer chat" 'curl -s -X POST http://localhost:8000/api/developer/chat -H "Content-Type: application/json" -d "{\"message\": \"test\"}"'

# Summary
echo -e "\n📊 TEST SUMMARY:"
echo "================================="
echo "Total Tests: $TESTS"
echo "Passed: $((TESTS - FAILURES))"
echo "Failed: $FAILURES"

if [ $FAILURES -eq 0 ]; then
    echo -e "\n🎉 ALL TESTS PASSED!"
else
    echo -e "\n⚠️ Some tests failed. Check the output above."
fi
