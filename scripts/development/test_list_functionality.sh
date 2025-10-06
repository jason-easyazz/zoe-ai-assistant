#!/bin/bash
# Test List Functionality
# Purpose: Test adding items to lists via existing APIs

echo "🛒 Testing List Functionality"
echo "============================="

echo ""
echo "📋 Test 1: Check existing lists API"
echo "-----------------------------------"

LISTS_RESPONSE=$(curl -s http://localhost:8000/api/lists/tasks)
if echo "$LISTS_RESPONSE" | grep -q "lists"; then
    echo "✅ Lists API is working"
    echo "$LISTS_RESPONSE" | jq '.lists[0] | {name, items: (.items | length)}' 2>/dev/null || echo "   Lists available"
else
    echo "❌ Lists API not working"
fi

echo ""
echo "📝 Test 2: Add item via lists API"
echo "--------------------------------"

# Try to add bread to shopping list
ADD_RESPONSE=$(curl -X POST -s http://localhost:8000/api/lists/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Bread",
    "list_name": "Shopping",
    "list_category": "shopping",
    "priority": "medium"
  }' 2>/dev/null)

if echo "$ADD_RESPONSE" | grep -q "success\|id"; then
    echo "✅ Successfully added 'Bread' to shopping list"
    echo "Response: $ADD_RESPONSE"
else
    echo "❌ Failed to add item"
    echo "Response: $ADD_RESPONSE"
fi

echo ""
echo "🍫 Test 3: Add another item"
echo "---------------------------"

ADD_RESPONSE2=$(curl -X POST -s http://localhost:8000/api/lists/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Malteasers",
    "list_name": "Shopping", 
    "list_category": "shopping",
    "priority": "high"
  }' 2>/dev/null)

if echo "$ADD_RESPONSE2" | grep -q "success\|id"; then
    echo "✅ Successfully added 'Malteasers' to shopping list"
else
    echo "❌ Failed to add Malteasers"
    echo "Response: $ADD_RESPONSE2"
fi

echo ""
echo "📅 Test 4: Test calendar event creation"
echo "--------------------------------------"

CALENDAR_RESPONSE=$(curl -X POST -s http://localhost:8000/api/calendar/events \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Dad'\''s Birthday",
    "start_time": "2025-01-04T19:00:00",
    "end_time": "2025-01-04T21:00:00",
    "description": "Dad'\''s birthday celebration"
  }' 2>/dev/null)

if echo "$CALENDAR_RESPONSE" | grep -q "success\|id"; then
    echo "✅ Successfully created calendar event for Dad'\''s birthday"
else
    echo "❌ Failed to create calendar event"
    echo "Response: $CALENDAR_RESPONSE"
fi

echo ""
echo "📊 SUMMARY"
echo "=========="
echo ""
echo "The issue is that the chat API is not connected to the actual"
echo "list and calendar APIs. The agent system exists but needs to be"
echo "integrated with the chat interface."
echo ""
echo "🔧 Solutions:"
echo "1. Use direct API calls (shown above)"
echo "2. Integrate chat API with tool registry"
echo "3. Connect orb chat to existing APIs"
echo ""
echo "🌐 Direct API Usage:"
echo "   curl -X POST http://localhost:8000/api/lists/tasks \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"text\": \"Bread\", \"list_name\": \"Shopping\"}'"
echo ""
echo "   curl -X POST http://localhost:8000/api/calendar/events \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"title\": \"Event\", \"start_time\": \"2025-01-04T19:00:00\"}'"
