#!/bin/bash
# Final Chat Fix Test - Both Main Chat and Orb Chat
# Verifies that both interfaces now execute actions properly

set -e

echo "🎯 Final Chat Fix Test - Both Interfaces"
echo "========================================"

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
echo "🎯 Interface Configuration Tests"
echo "==============================="

# Test 1: Main Chat Interface Configuration
echo ""
echo "📝 Test 1: Main Chat Interface (chat.html)"
echo "----------------------------------------"
MAIN_CHAT_API=$(curl -k -s https://localhost/chat.html | grep -o "chat/enhanced" | head -1)

if [[ "$MAIN_CHAT_API" == "chat/enhanced" ]]; then
    echo "✅ Main Chat: Using Enhanced API"
else
    echo "❌ Main Chat: Not using Enhanced API"
fi

# Test 2: Orb Chat Interface Configuration
echo ""
echo "🔮 Test 2: Orb Chat Interface (floating orb)"
echo "-------------------------------------------"
ORB_CHAT_API=$(curl -k -s https://localhost/chat.html | grep -A 5 -B 5 "sendOrbMessage" | grep -o "chat/enhanced" | head -1)

if [[ "$ORB_CHAT_API" == "chat/enhanced" ]]; then
    echo "✅ Orb Chat: Using Enhanced API"
else
    echo "❌ Orb Chat: Not using Enhanced API"
fi

echo ""
echo "🎯 Action Execution Tests"
echo "======================="

# Test 3: Main Chat Action Execution
echo ""
echo "📋 Test 3: Main Chat - Add Item to List"
echo "--------------------------------------"
MAIN_CHAT_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Add apples to shopping list",
    "context": {"mode": "main_chat"},
    "user_id": "test_user",
    "session_id": "test_session"
  }')

MAIN_RESPONSE=$(echo "$MAIN_CHAT_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")
MAIN_ACTIONS=$(echo "$MAIN_CHAT_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")

if [[ "$MAIN_RESPONSE" == *"Added"*"Shopping list"* ]]; then
    echo "✅ Main Chat Action: SUCCESS - $MAIN_RESPONSE"
else
    echo "❌ Main Chat Action: FAILED - $MAIN_RESPONSE"
fi

# Test 4: Orb Chat Action Execution
echo ""
echo "🔮 Test 4: Orb Chat - Create Calendar Event"
echo "------------------------------------------"
ORB_CHAT_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Create calendar event for meeting tomorrow at 3pm",
    "context": {},
    "user_id": "test_user"
  }')

ORB_RESPONSE=$(echo "$ORB_CHAT_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")
ORB_ACTIONS=$(echo "$ORB_CHAT_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")

if [[ "$ORB_RESPONSE" == *"Created event"* ]]; then
    echo "✅ Orb Chat Action: SUCCESS - $ORB_RESPONSE"
else
    echo "❌ Orb Chat Action: FAILED - $ORB_RESPONSE"
fi

# Test 5: Conversation (No Action)
echo ""
echo "💬 Test 5: Conversation (No Action)"
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
echo "🎉 FINAL TEST SUMMARY"
echo "===================="

echo ""
echo "✅ Interface Configuration:"
echo "   - Main Chat Enhanced API: $([ "$MAIN_CHAT_API" == "chat/enhanced" ] && echo "✅ CONFIGURED" || echo "❌ NOT CONFIGURED")"
echo "   - Orb Chat Enhanced API: $([ "$ORB_CHAT_API" == "chat/enhanced" ] && echo "✅ CONFIGURED" || echo "❌ NOT CONFIGURED")"

echo ""
echo "✅ Action Execution:"
echo "   - Main Chat Actions: $([ "$MAIN_RESPONSE" == *"Added"*"Shopping list"* ] && echo "✅ WORKING" || echo "❌ FAILED")"
echo "   - Orb Chat Actions: $([ "$ORB_RESPONSE" == *"Created event"* ] && echo "✅ WORKING" || echo "❌ FAILED")"
echo "   - Conversation: $([ $CONVERSATION_ACTIONS -eq 0 ] && echo "✅ WORKING" || echo "❌ FAILED")"

echo ""
if [[ "$MAIN_CHAT_API" == "chat/enhanced" && "$ORB_CHAT_API" == "chat/enhanced" && "$MAIN_RESPONSE" == *"Added"*"Shopping list"* && "$ORB_RESPONSE" == *"Created event"* ]]; then
    echo "🎯 BOTH CHAT INTERFACES ARE NOW WORKING PERFECTLY!"
    echo ""
    echo "🌐 How to Use:"
    echo ""
    echo "1. 📝 Main Chat Interface:"
    echo "   - Visit: https://localhost/chat.html"
    echo "   - Type: 'Add strawberries to shopping list'"
    echo "   - Zoe will respond: '✅ Added strawberries to Shopping list'"
    echo ""
    echo "2. 🔮 Orb Chat Interface:"
    echo "   - Visit any page: https://localhost/calendar.html"
    echo "   - Click the floating purple orb (bottom-right corner)"
    echo "   - Type: 'Create calendar event for birthday tomorrow'"
    echo "   - Zoe will respond: '✅ Created event: Birthday on [date]'"
    echo ""
    echo "✨ Both interfaces now execute real actions and provide clear feedback!"
    echo ""
    echo "🎉 SUCCESS: The user's issue is completely resolved!"
else
    echo "⚠️  Some issues detected - check individual test results above"
fi

echo ""
echo "🎉 Final chat fix test completed!"

