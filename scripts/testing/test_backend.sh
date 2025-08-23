#!/bin/bash
# Backend Test Suite

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

test_endpoint() {
    local name=$1
    local method=$2
    local url=$3
    local data=$4
    local expected=$5
    
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    echo -n "Testing $name... "
    
    if [ "$method" = "GET" ]; then
        response=$(curl -s -w "\n%{http_code}" $url)
    else
        response=$(curl -s -w "\n%{http_code}" -X $method -H "Content-Type: application/json" -d "$data" $url)
    fi
    
    http_code=$(echo "$response" | tail -1)
    body=$(echo "$response" | head -n -1)
    
    if [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
        echo -e "${GREEN}✅ PASS${NC} (HTTP $http_code)"
        PASSED_TESTS=$((PASSED_TESTS + 1))
        
        # Show response preview
        echo "  Response: $(echo $body | jq -r '.' | head -3)"
    else
        echo -e "${RED}❌ FAIL${NC} (HTTP $http_code)"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        echo "  Error: $body"
    fi
    echo ""
}

echo "================================"
echo "ZOE BACKEND TEST SUITE"
echo "================================"
echo ""

# Core Health Tests
echo "🏥 HEALTH CHECKS"
echo "----------------"
test_endpoint "Core Health" "GET" "http://localhost:8000/health" "" "healthy"
test_endpoint "Root Endpoint" "GET" "http://localhost:8000/" "" "operational"

# Developer Endpoints
echo "👨‍💻 DEVELOPER ENDPOINTS"
echo "----------------------"
test_endpoint "Developer Status" "GET" "http://localhost:8000/api/developer/status" "" "operational"
test_endpoint "Recent Tasks" "GET" "http://localhost:8000/api/developer/tasks/recent" "" "tasks"
test_endpoint "System Metrics" "GET" "http://localhost:8000/api/developer/metrics" "" "cpu"
test_endpoint "Create Task" "POST" "http://localhost:8000/api/developer/tasks" \
    '{"title":"Test Task","priority":"high","category":"testing"}' "created"

# Lists Endpoints
echo "📋 LISTS ENDPOINTS"
echo "------------------"
test_endpoint "List Types" "GET" "http://localhost:8000/api/lists/types" "" "types"
test_endpoint "Get Shopping Lists" "GET" "http://localhost:8000/api/lists/shopping" "" "lists"
test_endpoint "Create Shopping List" "POST" "http://localhost:8000/api/lists/shopping" \
    '{"list_type":"shopping","name":"Groceries","category":"personal","items":[{"text":"Milk","completed":false}]}' "created"

# Chat Endpoints
echo "💬 CHAT ENDPOINTS"
echo "-----------------"
test_endpoint "User Chat" "POST" "http://localhost:8000/api/chat" \
    '{"message":"Hello Zoe","mode":"user"}' "response"
test_endpoint "Developer Chat" "POST" "http://localhost:8000/api/developer/chat" \
    '{"message":"System status","mode":"developer"}' "response"

# Summary
echo "================================"
echo "TEST SUMMARY"
echo "================================"
echo -e "Total Tests: $TOTAL_TESTS"
echo -e "Passed: ${GREEN}$PASSED_TESTS${NC}"
echo -e "Failed: ${RED}$FAILED_TESTS${NC}"

if [ $FAILED_TESTS -eq 0 ]; then
    echo -e "\n${GREEN}🎉 ALL TESTS PASSED!${NC}"
    exit 0
else
    echo -e "\n${RED}⚠️ SOME TESTS FAILED${NC}"
    exit 1
fi
