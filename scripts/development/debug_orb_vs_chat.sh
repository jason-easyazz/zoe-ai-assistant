#!/bin/bash
# Debug Orb vs Chat Interface
# Determines which interface the user is actually using

echo "🔍 Debug: Orb vs Chat Interface"
echo "==============================="

echo ""
echo "📋 Interface Analysis"
echo "--------------------"

echo ""
echo "🎯 Main Chat Interface (chat.html):"
echo "   - Uses sendMessage() function"
echo "   - Calls /api/chat (original API)"
echo "   - Returns conversational responses"
echo "   - No action execution"

echo ""
echo "🔮 Orb Chat Interface (floating orb):"
echo "   - Uses sendOrbMessage() function" 
echo "   - Calls /api/chat/enhanced (enhanced API)"
echo "   - Returns action execution responses"
echo "   - Executes real actions"

echo ""
echo "🧪 Test Both Interfaces"
echo "======================="

echo ""
echo "📝 Test 1: Main Chat Interface"
echo "-----------------------------"
MAIN_CHAT_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Add strawberries to shopping list",
    "context": {},
    "user_id": "test_user"
  }')

MAIN_RESPONSE=$(echo "$MAIN_CHAT_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")
echo "Main Chat Response: ${MAIN_RESPONSE:0:100}..."

echo ""
echo "🚀 Test 2: Enhanced Chat Interface (Orb)"
echo "---------------------------------------"
ENHANCED_CHAT_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Add strawberries to shopping list",
    "context": {},
    "user_id": "test_user"
  }')

ENHANCED_RESPONSE=$(echo "$ENHANCED_CHAT_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")
ENHANCED_ACTIONS=$(echo "$ENHANCED_CHAT_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")
echo "Enhanced Chat Response: ${ENHANCED_RESPONSE:0:100}..."
echo "Actions Executed: $ENHANCED_ACTIONS"

echo ""
echo "🎯 DIAGNOSIS"
echo "============"

echo ""
echo "Based on the user's reported responses:"
echo "   'I need a bit more context. Could you please specify...'"
echo "   'Hi there! I'm Zoe, your personal AI assistant...'"

echo ""
if [[ "$MAIN_RESPONSE" == *"need a bit more context"* || "$MAIN_RESPONSE" == *"personal AI assistant"* ]]; then
    echo "✅ DIAGNOSIS: User is using the MAIN CHAT INTERFACE (chat.html)"
    echo "   - This explains why actions are not being executed"
    echo "   - The main chat uses the original /api/chat endpoint"
    echo "   - The orb chat uses the enhanced /api/chat/enhanced endpoint"
else
    echo "❓ DIAGNOSIS: Unable to determine interface from response patterns"
fi

echo ""
echo "🔧 SOLUTION"
echo "==========="

echo ""
echo "To fix the action execution issue:"
echo ""
echo "1. 🎯 Use the ORB CHAT (floating orb):"
echo "   - Visit any page: https://localhost/calendar.html"
echo "   - Click the floating purple orb in bottom-right corner"
echo "   - Type: 'Add strawberries to shopping list'"
echo "   - This will execute the action and show: '✅ Added strawberries to Shopping list'"
echo ""
echo "2. 🔧 OR fix the main chat interface:"
echo "   - Update chat.html to use /api/chat/enhanced instead of /api/chat"
echo "   - This would make the main chat also execute actions"
echo ""

echo "🎉 Debug completed!"

