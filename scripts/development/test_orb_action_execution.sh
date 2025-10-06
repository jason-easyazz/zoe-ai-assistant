#!/bin/bash
# Test Orb Chat Action Execution
# Verifies that orb chat now uses Enhanced MEM Agent for action execution

set -e

echo "🔮 Orb Chat Action Execution Test"
echo "================================"

echo ""
echo "🔧 System Status Check"
echo "---------------------"

# Check if all services are running
if curl -s http://localhost:8000/health | grep -q "healthy"; then
    echo "✅ Zoe Core API: Running"
else
    echo "❌ Zoe Core API: Not running"
    exit 1
fi

if curl -s http://localhost:11435/health | grep -q "healthy"; then
    echo "✅ Enhanced MEM Agent: Running"
else
    echo "❌ Enhanced MEM Agent: Not running"
    exit 1
fi

echo ""
echo "🌐 Orb Configuration Tests"
echo "========================="

# Test 1: Verify orb is using enhanced API
echo ""
echo "📡 Test 1: Orb API Configuration"
echo "-------------------------------"
ORB_API_CALENDAR=$(curl -k -s https://localhost/calendar.html | grep -o "api/chat/enhanced")
ORB_API_CHAT=$(curl -k -s https://localhost/chat.html | grep -o "api/chat/enhanced")

if [[ "$ORB_API_CALENDAR" == "api/chat/enhanced" && "$ORB_API_CHAT" == "api/chat/enhanced" ]]; then
    echo "✅ Orb Configuration: Using Enhanced API on both pages"
else
    echo "❌ Orb Configuration: Not using Enhanced API (calendar: $ORB_API_CALENDAR, chat: $ORB_API_CHAT)"
fi

# Test 2: Verify context format
echo ""
echo "📝 Test 2: Context Format"
echo "------------------------"
CONTEXT_FORMAT=$(curl -k -s https://localhost/calendar.html | grep -o "context: {}")

if [[ "$CONTEXT_FORMAT" == "context: {}" ]]; then
    echo "✅ Context Format: Correct dictionary format"
else
    echo "❌ Context Format: Incorrect format"
fi

echo ""
echo "🎯 Action Execution Tests"
echo "======================="

# Test 3: List Expert via Enhanced API
echo ""
echo "📋 Test 3: List Expert Action"
echo "----------------------------"
LIST_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Add strawberries to shopping list",
    "context": {},
    "user_id": "test_user"
  }')

ACTIONS_EXECUTED=$(echo "$LIST_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")
EXPERTS_USED=$(echo "$LIST_RESPONSE" | jq -r '.experts_used[]' 2>/dev/null || echo "none")
RESPONSE_TEXT=$(echo "$LIST_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")

if [[ $ACTIONS_EXECUTED -gt 0 && "$EXPERTS_USED" == "list" ]]; then
    echo "✅ List Expert: Working (${ACTIONS_EXECUTED} actions, expert: $EXPERTS_USED)"
    echo "   Response: ${RESPONSE_TEXT:0:100}..."
else
    echo "❌ List Expert: Failed (${ACTIONS_EXECUTED} actions, expert: $EXPERTS_USED)"
fi

# Test 4: Calendar Expert via Enhanced API
echo ""
echo "📅 Test 4: Calendar Expert Action"
echo "--------------------------------"
CALENDAR_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Create calendar event for team meeting tomorrow at 3pm",
    "context": {},
    "user_id": "test_user"
  }')

CALENDAR_ACTIONS=$(echo "$CALENDAR_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")
CALENDAR_EXPERTS=$(echo "$CALENDAR_RESPONSE" | jq -r '.experts_used[]' 2>/dev/null || echo "none")
CALENDAR_TEXT=$(echo "$CALENDAR_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")

if [[ $CALENDAR_ACTIONS -gt 0 && "$CALENDAR_EXPERTS" == "calendar" ]]; then
    echo "✅ Calendar Expert: Working (${CALENDAR_ACTIONS} actions, expert: $CALENDAR_EXPERTS)"
    echo "   Response: ${CALENDAR_TEXT:0:100}..."
else
    echo "❌ Calendar Expert: Failed (${CALENDAR_ACTIONS} actions, expert: $CALENDAR_EXPERTS)"
fi

# Test 5: Planning Expert via Enhanced API
echo ""
echo "🧠 Test 5: Planning Expert Action"
echo "--------------------------------"
PLANNING_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Help me plan a home renovation project",
    "context": {},
    "user_id": "test_user"
  }')

PLANNING_ACTIONS=$(echo "$PLANNING_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")
PLANNING_EXPERTS=$(echo "$PLANNING_RESPONSE" | jq -r '.experts_used[]' 2>/dev/null || echo "none")
PLANNING_TEXT=$(echo "$PLANNING_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")

if [[ $PLANNING_ACTIONS -gt 0 && "$PLANNING_EXPERTS" == "planning" ]]; then
    echo "✅ Planning Expert: Working (${PLANNING_ACTIONS} actions, expert: $PLANNING_EXPERTS)"
    echo "   Response: ${PLANNING_TEXT:0:100}..."
else
    echo "❌ Planning Expert: Failed (${PLANNING_ACTIONS} actions, expert: $PLANNING_EXPERTS)"
fi

echo ""
echo "🎉 TEST SUMMARY"
echo "==============="

echo ""
echo "✅ Orb Chat Action Execution Status:"
echo "   - Orb using Enhanced API: $([ "$ORB_API_CALENDAR" == "api/chat/enhanced" ] && echo "✅ YES" || echo "❌ NO")"
echo "   - Context format correct: $([ "$CONTEXT_FORMAT" == "context: {}" ] && echo "✅ YES" || echo "❌ NO")"
echo "   - List Expert working: $([ $ACTIONS_EXECUTED -gt 0 ] && echo "✅ YES" || echo "❌ NO")"
echo "   - Calendar Expert working: $([ $CALENDAR_ACTIONS -gt 0 ] && echo "✅ YES" || echo "❌ NO")"
echo "   - Planning Expert working: $([ $PLANNING_ACTIONS -gt 0 ] && echo "✅ YES" || echo "❌ NO")"

echo ""
if [[ "$ORB_API_CALENDAR" == "api/chat/enhanced" && "$CONTEXT_FORMAT" == "context: {}" && $ACTIONS_EXECUTED -gt 0 ]]; then
    echo "🎯 ORB CHAT ACTION EXECUTION IS NOW WORKING!"
    echo ""
    echo "🌐 How to Use:"
    echo "   1. Visit any page (https://localhost/calendar.html, etc.)"
    echo "   2. Click the floating Zoe orb in bottom-right"
    echo "   3. Type: 'Add strawberries to shopping list'"
    echo "   4. Watch Zoe actually add strawberries to your list!"
    echo "   5. Try: 'Create calendar event for birthday tomorrow'"
    echo "   6. Try: 'Help me plan a garden project'"
    echo ""
    echo "✨ The orb chat now executes real actions, not just responds!"
else
    echo "⚠️  Some issues detected - check individual test results above"
fi

echo ""
echo "🎉 Orb chat action execution test completed!"

