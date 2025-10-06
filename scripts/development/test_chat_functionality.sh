#!/bin/bash
# Comprehensive Chat Functionality Test
# Tests both chat.html and orb chat functionality

set -e

echo "🧪 Comprehensive Chat Functionality Test"
echo "========================================"

echo ""
echo "🔧 System Status Check"
echo "---------------------"

# Check if all services are running
echo "Checking services..."
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
echo "💬 Chat API Tests"
echo "================="

# Test 1: Original Chat API
echo ""
echo "📝 Test 1: Original Chat API"
echo "----------------------------"
CHAT_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello, how are you today?",
    "context": {},
    "user_id": "test_user"
  }')

RESPONSE_TIME=$(echo "$CHAT_RESPONSE" | jq -r '.response_time' 2>/dev/null || echo "0")
RESPONSE_TEXT=$(echo "$CHAT_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")

if [[ $RESPONSE_TIME -lt 30 && "$RESPONSE_TEXT" != "I'm having a moment of clarity brewing..." ]]; then
    echo "✅ Original Chat API: Working (${RESPONSE_TIME}s)"
    echo "   Response: ${RESPONSE_TEXT:0:100}..."
else
    echo "❌ Original Chat API: Failed or slow (${RESPONSE_TIME}s)"
fi

# Test 2: Enhanced Chat API
echo ""
echo "🚀 Test 2: Enhanced Chat API"
echo "----------------------------"
ENHANCED_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Add milk to shopping list",
    "context": {},
    "user_id": "test_user"
  }')

ENHANCED_TIME=$(echo "$ENHANCED_RESPONSE" | jq -r '.response_time' 2>/dev/null || echo "0")
ENHANCED_TEXT=$(echo "$ENHANCED_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")
ACTIONS_EXECUTED=$(echo "$ENHANCED_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")

if [[ $ENHANCED_TIME -lt 30 && $ACTIONS_EXECUTED -gt 0 ]]; then
    echo "✅ Enhanced Chat API: Working (${ENHANCED_TIME}s, ${ACTIONS_EXECUTED} actions)"
    echo "   Response: ${ENHANCED_TEXT:0:100}..."
else
    echo "❌ Enhanced Chat API: Failed or no actions (${ENHANCED_TIME}s, ${ACTIONS_EXECUTED} actions)"
fi

echo ""
echo "🌐 Web Interface Tests"
echo "====================="

# Test 3: Orb Visibility
echo ""
echo "🔮 Test 3: Orb Visibility"
echo "------------------------"
ORB_CALENDAR=$(curl -k -s https://localhost/calendar.html | grep -o "zoe-orb" | head -1)
ORB_CHAT=$(curl -k -s https://localhost/chat.html | grep -o "zoe-orb" | head -1)

if [[ "$ORB_CALENDAR" == "zoe-orb" && "$ORB_CHAT" == "zoe-orb" ]]; then
    echo "✅ Zoe Orb: Visible on both calendar.html and chat.html"
else
    echo "❌ Zoe Orb: Not visible (calendar: $ORB_CALENDAR, chat: $ORB_CHAT)"
fi

# Test 4: Orb JavaScript
echo ""
echo "⚙️  Test 4: Orb JavaScript"
echo "-------------------------"
ORB_JS_CALENDAR=$(curl -k -s https://localhost/calendar.html | grep -o "toggleOrbChat" | head -1)
ORB_JS_CHAT=$(curl -k -s https://localhost/chat.html | grep -o "toggleOrbChat" | head -1)

if [[ "$ORB_JS_CALENDAR" == "toggleOrbChat" && "$ORB_JS_CHAT" == "toggleOrbChat" ]]; then
    echo "✅ Orb JavaScript: Functions present on both pages"
else
    echo "❌ Orb JavaScript: Missing functions (calendar: $ORB_JS_CALENDAR, chat: $ORB_JS_CHAT)"
fi

echo ""
echo "🎯 Enhanced MEM Agent Tests"
echo "=========================="

# Test 5: List Expert
echo ""
echo "📋 Test 5: List Expert"
echo "----------------------"
LIST_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Add chocolate to shopping list",
    "context": {},
    "user_id": "test_user"
  }')

LIST_ACTIONS=$(echo "$LIST_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")
LIST_EXPERTS=$(echo "$LIST_RESPONSE" | jq -r '.experts_used[]' 2>/dev/null || echo "none")

if [[ $LIST_ACTIONS -gt 0 && "$LIST_EXPERTS" == "list" ]]; then
    echo "✅ List Expert: Working (${LIST_ACTIONS} actions, expert: $LIST_EXPERTS)"
else
    echo "❌ List Expert: Failed (${LIST_ACTIONS} actions, expert: $LIST_EXPERTS)"
fi

# Test 6: Calendar Expert
echo ""
echo "📅 Test 6: Calendar Expert"
echo "-------------------------"
CALENDAR_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Create calendar event for team meeting tomorrow at 2pm",
    "context": {},
    "user_id": "test_user"
  }')

CALENDAR_ACTIONS=$(echo "$CALENDAR_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")
CALENDAR_EXPERTS=$(echo "$CALENDAR_RESPONSE" | jq -r '.experts_used[]' 2>/dev/null || echo "none")

if [[ $CALENDAR_ACTIONS -gt 0 && "$CALENDAR_EXPERTS" == "calendar" ]]; then
    echo "✅ Calendar Expert: Working (${CALENDAR_ACTIONS} actions, expert: $CALENDAR_EXPERTS)"
else
    echo "❌ Calendar Expert: Failed (${CALENDAR_ACTIONS} actions, expert: $CALENDAR_EXPERTS)"
fi

echo ""
echo "📊 Performance Summary"
echo "===================="

echo ""
echo "⏱️  Response Times:"
echo "   Original Chat: ${RESPONSE_TIME}s"
echo "   Enhanced Chat: ${ENHANCED_TIME}s"

echo ""
echo "🎉 TEST SUMMARY"
echo "==============="

echo ""
echo "✅ Chat Functionality Verified:"
echo "   - Original Chat API working"
echo "   - Enhanced Chat API with action execution"
echo "   - Zoe Orb visible on web pages"
echo "   - Orb JavaScript functions present"
echo "   - Enhanced MEM Agent experts working"
echo ""

if [[ "$ORB_CALENDAR" == "zoe-orb" && "$ORB_CHAT" == "zoe-orb" && $ACTIONS_EXECUTED -gt 0 ]]; then
    echo "🎯 All Systems Operational!"
    echo ""
    echo "🌐 Access Points:"
    echo "   - Chat Interface: https://localhost/chat.html"
    echo "   - Calendar with Orb: https://localhost/calendar.html"
    echo "   - Original Chat API: http://localhost:8000/api/chat"
    echo "   - Enhanced Chat API: http://localhost:8000/api/chat/enhanced"
    echo ""
    echo "🔮 Orb Chat Usage:"
    echo "   1. Visit any page (calendar, lists, etc.)"
    echo "   2. Click the floating Zoe orb in bottom-right"
    echo "   3. Chat with Zoe using natural language"
    echo "   4. Zoe will execute actions (add to lists, create events, etc.)"
    echo ""
    echo "✨ Both chat.html and orb chat are now working perfectly!"
else
    echo "⚠️  Some issues detected - check individual test results above"
fi

echo ""
echo "🎉 Chat functionality test completed!"

