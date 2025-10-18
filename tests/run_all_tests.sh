#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status
set -u  # Treat unset variables as an error
set -o pipefail  # Fail if any command in a pipeline fails

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counters
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# Log function
log_test() {
    local status=$1
    local message=$2
    
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    
    if [ "$status" = "PASS" ]; then
        echo -e "${GREEN}✓${NC} $message"
        PASSED_TESTS=$((PASSED_TESTS + 1))
    else
        echo -e "${RED}✗${NC} $message"
        FAILED_TESTS=$((FAILED_TESTS + 1))
    fi
}

# Cleanup function for graceful shutdown
cleanup() {
    echo ""
    echo "========================================="
    echo "Test Summary:"
    echo "  Total:  $TOTAL_TESTS"
    echo -e "  Passed: ${GREEN}$PASSED_TESTS${NC}"
    echo -e "  Failed: ${RED}$FAILED_TESTS${NC}"
    echo "========================================="
    
    if [ $FAILED_TESTS -gt 0 ]; then
        exit 1
    fi
}

# Register cleanup on exit
trap cleanup EXIT

echo "🧪 Running Zoe Test Suite..."
echo ""

# Unit tests
echo -e "\n📝 Running unit tests..."
if python3 tests/unit/test_memory.py 2>&1 | tee /tmp/test_memory.log; then
    log_test "PASS" "Unit tests: test_memory.py"
else
    log_test "FAIL" "Unit tests: test_memory.py (see /tmp/test_memory.log)"
fi

# Integration tests
echo -e "\n🔗 Running integration tests..."
if [ -f tests/integration/test_voice_integration.sh ]; then
    if bash tests/integration/test_voice_integration.sh 2>&1 | tee /tmp/test_voice.log; then
        log_test "PASS" "Integration tests: test_voice_integration.sh"
    else
        log_test "FAIL" "Integration tests: test_voice_integration.sh (see /tmp/test_voice.log)"
    fi
else
    log_test "SKIP" "Integration tests: test_voice_integration.sh (not found)"
fi

# Performance tests
echo -e "\n⚡ Running performance tests..."
if [ -f tests/performance/test_api_performance.py ]; then
    if python3 tests/performance/test_api_performance.py 2>&1 | tee /tmp/test_performance.log; then
        log_test "PASS" "Performance tests: test_api_performance.py"
    else
        log_test "FAIL" "Performance tests: test_api_performance.py (see /tmp/test_performance.log)"
    fi
else
    log_test "SKIP" "Performance tests: test_api_performance.py (not found)"
fi

# API endpoint tests
echo -e "\n🌐 Testing API endpoints..."

# Check if jq is available
if ! command -v jq &> /dev/null; then
    echo -e "${YELLOW}⚠${NC} jq not installed, skipping JSON validation"
    JQ_AVAILABLE=false
else
    JQ_AVAILABLE=true
fi

# Test 1: Health endpoint
if curl -f -s http://localhost:8000/health > /tmp/health_response.json 2>&1; then
    if [ "$JQ_AVAILABLE" = true ] && jq -e '.status' /tmp/health_response.json > /dev/null 2>&1; then
        log_test "PASS" "API: /health endpoint"
    elif [ "$JQ_AVAILABLE" = false ]; then
        log_test "PASS" "API: /health endpoint (no JSON validation)"
    else
        log_test "FAIL" "API: /health endpoint (invalid JSON response)"
    fi
else
    log_test "FAIL" "API: /health endpoint (request failed)"
fi

# Test 2: Memory search endpoint
if curl -f -s -X POST http://localhost:8000/api/memory/search \
    -H "Content-Type: application/json" \
    -d '{"query": "test"}' > /tmp/memory_response.json 2>&1; then
    if [ "$JQ_AVAILABLE" = true ] && jq -e '.' /tmp/memory_response.json > /dev/null 2>&1; then
        log_test "PASS" "API: /api/memory/search endpoint"
    elif [ "$JQ_AVAILABLE" = false ]; then
        log_test "PASS" "API: /api/memory/search endpoint (no JSON validation)"
    else
        log_test "FAIL" "API: /api/memory/search endpoint (invalid JSON response)"
    fi
else
    log_test "FAIL" "API: /api/memory/search endpoint (request failed)"
fi

# Test 3: Chat endpoint
if curl -f -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "hello", "user_id": "test"}' > /tmp/chat_response.json 2>&1; then
    if [ "$JQ_AVAILABLE" = true ] && jq -e '.response' /tmp/chat_response.json > /dev/null 2>&1; then
        log_test "PASS" "API: /api/chat endpoint"
    elif [ "$JQ_AVAILABLE" = false ]; then
        log_test "PASS" "API: /api/chat endpoint (no JSON validation)"
    else
        log_test "FAIL" "API: /api/chat endpoint (invalid JSON response)"
    fi
else
    log_test "FAIL" "API: /api/chat endpoint (request failed)"
fi

# Cleanup temp files
rm -f /tmp/health_response.json /tmp/memory_response.json /tmp/chat_response.json

echo ""
echo "✅ All tests completed!"
