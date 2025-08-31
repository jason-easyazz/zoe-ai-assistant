#!/bin/bash
# DIAGNOSE_CURRENT_STATE.sh
# Location: scripts/maintenance/diagnose_current_state.sh
# Purpose: Diagnose exactly what's wrong with developer chat and tasks

set -e

echo "🔍 DEVELOPER SYSTEM DIAGNOSIS"
echo "=============================="
echo ""
echo "Checking what's working and what's broken..."
echo ""

cd /home/pi/zoe

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "═══════════════════════════════════════"
echo "1️⃣  CHECKING CURRENT DEVELOPER.PY"
echo "═══════════════════════════════════════"
echo "Current implementation:"
docker exec zoe-core head -50 /app/routers/developer.py | grep -E "(class|def|@router|import)" || echo "Unable to read"

echo ""
echo "═══════════════════════════════════════"
echo "2️⃣  CHECKING AVAILABLE ENDPOINTS"
echo "═══════════════════════════════════════"
curl -s http://localhost:8000/openapi.json | jq '.paths | to_entries[] | select(.key | contains("/developer")) | .key' 2>/dev/null || echo "Can't get endpoints"

echo ""
echo "═══════════════════════════════════════"
echo "3️⃣  TESTING DEVELOPER CHAT CAPABILITIES"
echo "═══════════════════════════════════════"

echo -e "\n${YELLOW}Test 1: Can Zack see Docker containers?${NC}"
echo "Sending: 'show me all docker containers'"
response=$(curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "show me all docker containers"}' 2>/dev/null)

echo "Response preview:"
echo "$response" | jq -r '.response' 2>/dev/null | head -10 || echo "$response"

if echo "$response" | grep -q "zoe-core\|zoe-ui\|zoe-redis"; then
    echo -e "${GREEN}✅ WORKING: Can see actual containers${NC}"
else
    echo -e "${RED}❌ BROKEN: Cannot see real containers${NC}"
fi

echo -e "\n${YELLOW}Test 2: Can Zack check system resources?${NC}"
echo "Sending: 'check memory usage'"
response=$(curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "check memory usage"}' 2>/dev/null)

echo "Response preview:"
echo "$response" | jq -r '.response' 2>/dev/null | head -10 || echo "$response"

if echo "$response" | grep -qE "[0-9]+.*GB\|[0-9]+.*MB\|Mem:"; then
    echo -e "${GREEN}✅ WORKING: Can see actual memory${NC}"
else
    echo -e "${RED}❌ BROKEN: Cannot see real memory${NC}"
fi

echo -e "\n${YELLOW}Test 3: Can Zack see logs?${NC}"
echo "Sending: 'show recent logs'"
response=$(curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "show recent logs from zoe-core"}' 2>/dev/null)

echo "Response preview:"
echo "$response" | jq -r '.response' 2>/dev/null | head -10 || echo "$response"

echo ""
echo "═══════════════════════════════════════"
echo "4️⃣  TESTING TASK MANAGEMENT"
echo "═══════════════════════════════════════"

echo -e "\n${YELLOW}Test 4: Can we create tasks?${NC}"
task_response=$(curl -s -X POST http://localhost:8000/api/developer/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "Test Task", "description": "Testing task system"}' 2>/dev/null)

echo "Task creation response:"
echo "$task_response" | jq '.' 2>/dev/null || echo "$task_response"

echo -e "\n${YELLOW}Test 5: Can we list tasks?${NC}"
list_response=$(curl -s http://localhost:8000/api/developer/tasks 2>/dev/null)
echo "Task list response:"
echo "$list_response" | jq '.' 2>/dev/null || echo "$list_response"

echo ""
echo "═══════════════════════════════════════"
echo "5️⃣  CHECKING AI INTEGRATION"
echo "═══════════════════════════════════════"

echo "Checking if ai_client is imported:"
docker exec zoe-core grep -n "ai_client\|generate_response" /app/routers/developer.py 2>/dev/null || echo "No AI client found"

echo ""
echo "═══════════════════════════════════════"
echo "6️⃣  CHECKING SUBPROCESS/EXECUTION"
echo "═══════════════════════════════════════"

echo "Checking for command execution capability:"
docker exec zoe-core grep -n "subprocess\|safe_execute\|run(" /app/routers/developer.py 2>/dev/null || echo "No subprocess execution found"

echo ""
echo "═══════════════════════════════════════"
echo "7️⃣  CHECKING BACKUP FILES"
echo "═══════════════════════════════════════"
echo "Available developer.py versions:"
docker exec zoe-core ls -la /app/routers/developer*.py 2>/dev/null || echo "No backup files"

echo ""
echo "═══════════════════════════════════════"
echo "8️⃣  ACTUAL SYSTEM STATE (REAL DATA)"
echo "═══════════════════════════════════════"

echo -e "\n${YELLOW}Real Docker Containers:${NC}"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-

echo -e "\n${YELLOW}Real Memory Usage:${NC}"
free -h | head -3

echo -e "\n${YELLOW}Real Disk Usage:${NC}"
df -h / | head -2

echo ""
echo "═══════════════════════════════════════"
echo "📊 DIAGNOSIS SUMMARY"
echo "═══════════════════════════════════════"

echo -e "\n${YELLOW}What Zack SHOULD be able to see:${NC}"
echo "• All Docker containers (zoe-core, zoe-ui, etc.)"
echo "• Memory usage (X.X GB used of 7.9 GB)"
echo "• Disk usage (XX GB used of 117 GB)"
echo "• Container logs"
echo "• Database tables"
echo "• CPU usage"

echo -e "\n${YELLOW}What Zack ACTUALLY sees:${NC}"
echo "Check the responses above - if they're generic or don't match real data, the system is broken."

echo ""
echo "═══════════════════════════════════════"
echo "✅ DIAGNOSIS COMPLETE"
echo "═══════════════════════════════════════"
echo ""
echo "Please share the output above, especially:"
echo "1. What endpoints are available"
echo "2. Whether Zack can see real container names"
echo "3. Whether task management works"
echo "4. What error messages appear"
echo ""
echo "Based on this diagnosis, I'll create the fix script!"
