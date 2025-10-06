#!/bin/bash
# Final Orb Chat Test - Verify Action Execution
# Tests that orb chat now properly executes actions and returns expert messages

set -e

echo "🎯 Final Orb Chat Action Execution Test"
echo "======================================"

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
echo "🎯 Enhanced Chat API Tests"
echo "========================="

# Test 1: List Expert Action
echo ""
echo "📋 Test 1: List Expert - Add Item"
echo "--------------------------------"
LIST_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Add strawberries to shopping list",
    "context": {},
    "user_id": "test_user"
  }')

LIST_RESPONSE_TEXT=$(echo "$LIST_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")
LIST_ACTIONS=$(echo "$LIST_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")

if [[ "$LIST_RESPONSE_TEXT" == *"Added"*"Shopping list"* ]]; then
    echo "✅ List Expert: SUCCESS - $LIST_RESPONSE_TEXT"
else
    echo "❌ List Expert: FAILED - $LIST_RESPONSE_TEXT"
fi

# Test 2: Calendar Expert Action
echo ""
echo "📅 Test 2: Calendar Expert - Create Event"
echo "---------------------------------------"
CALENDAR_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Create calendar event for birthday tomorrow at 7pm",
    "context": {},
    "user_id": "test_user"
  }')

CALENDAR_RESPONSE_TEXT=$(echo "$CALENDAR_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")
CALENDAR_ACTIONS=$(echo "$CALENDAR_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")

if [[ "$CALENDAR_RESPONSE_TEXT" == *"Created event"* ]]; then
    echo "✅ Calendar Expert: SUCCESS - $CALENDAR_RESPONSE_TEXT"
else
    echo "❌ Calendar Expert: FAILED - $CALENDAR_RESPONSE_TEXT"
fi

# Test 3: Conversation (No Action)
echo ""
echo "💬 Test 3: Conversation (No Action)"
echo "----------------------------------"
CONVERSATION_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello, how are you today?",
    "context": {},
    "user_id": "test_user"
  }')

CONVERSATION_TEXT=$(echo "$CONVERSATION_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")
CONVERSATION_ACTIONS=$(echo "$CONVERSATION_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")

if [[ $CONVERSATION_ACTIONS -eq 0 && "$CONVERSATION_TEXT" != *"Added"* && "$CONVERSATION_TEXT" != *"Created"* ]]; then
    echo "✅ Conversation: SUCCESS - Normal LLM response (no actions executed)"
    echo "   Response: ${CONVERSATION_TEXT:0:80}..."
else
    echo "❌ Conversation: FAILED - Unexpected action or response format"
fi

echo ""
echo "🌐 Orb Configuration Verification"
echo "================================"

# Test 4: Verify orb is using enhanced API
echo ""
echo "📡 Test 4: Orb API Configuration"
echo "-------------------------------"
ORB_API_CALENDAR=$(curl -k -s https://localhost/calendar.html | grep -o "api/chat/enhanced")
ORB_API_CHAT=$(curl -k -s https://localhost/chat.html | grep -o "api/chat/enhanced")

if [[ "$ORB_API_CALENDAR" == "api/chat/enhanced" && "$ORB_API_CHAT" == "api/chat/enhanced" ]]; then
    echo "✅ Orb Configuration: Using Enhanced API"
else
    echo "❌ Orb Configuration: Not using Enhanced API"
fi

# Test 5: Verify context format
echo ""
echo "📝 Test 5: Context Format"
echo "------------------------"
CONTEXT_FORMAT=$(curl -k -s https://localhost/calendar.html | grep -o "context: {}")

if [[ "$CONTEXT_FORMAT" == "context: {}" ]]; then
    echo "✅ Context Format: Correct"
else
    echo "❌ Context Format: Incorrect"
fi

echo ""
echo "🎉 FINAL TEST SUMMARY"
echo "===================="

echo ""
echo "✅ Action Execution Status:"
echo "   - List Expert: $([ "$LIST_RESPONSE_TEXT" == *"Added"*"Shopping list"* ] && echo "✅ WORKING" || echo "❌ FAILED")"
echo "   - Calendar Expert: $([ "$CALENDAR_RESPONSE_TEXT" == *"Created event"* ] && echo "✅ WORKING" || echo "❌ FAILED")"
echo "   - Conversation: $([ $CONVERSATION_ACTIONS -eq 0 ] && echo "✅ WORKING" || echo "❌ FAILED")"

echo ""
echo "✅ Orb Configuration:"
echo "   - Enhanced API: $([ "$ORB_API_CALENDAR" == "api/chat/enhanced" ] && echo "✅ CONFIGURED" || echo "❌ NOT CONFIGURED")"
echo "   - Context Format: $([ "$CONTEXT_FORMAT" == "context: {}" ] && echo "✅ CORRECT" || echo "❌ INCORRECT")"

echo ""
if [[ "$LIST_RESPONSE_TEXT" == *"Added"*"Shopping list"* && "$CALENDAR_RESPONSE_TEXT" == *"Created event"* && "$ORB_API_CALENDAR" == "api/chat/enhanced" ]]; then
    echo "🎯 ORB CHAT ACTION EXECUTION IS NOW FULLY WORKING!"
    echo ""
    echo "🌐 How to Use the Fixed Orb Chat:"
    echo "   1. Visit any page: https://localhost/calendar.html"
    echo "   2. Click the floating Zoe orb (bottom-right corner)"
    echo "   3. Type: 'Add strawberries to shopping list'"
    echo "   4. Zoe will respond: '✅ Added strawberries to Shopping list'"
    echo "   5. Type: 'Create calendar event for meeting tomorrow'"
    echo "   6. Zoe will respond: '✅ Created event: Meeting on [date]'"
    echo ""
    echo "✨ The orb chat now executes real actions and provides clear feedback!"
    echo ""
    echo "🎉 SUCCESS: Both chat.html and orb chat are working perfectly!"
else
    echo "⚠️  Some issues detected - check individual test results above"
fi

echo ""
echo "🎉 Final orb chat test completed!"

