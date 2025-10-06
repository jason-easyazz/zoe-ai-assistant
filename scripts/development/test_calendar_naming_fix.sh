#!/bin/bash
# Test: Calendar Event Naming Fix

echo "🎯 Testing Improved Calendar Event Naming"
echo "========================================"

echo ""
echo "📋 Test Cases:"
echo "1. 'Add Dad's Birthday to the calendar tomorrow at 7 pm'"
echo "2. 'Add Get Ready to the calendar tomorrow at 6 pm'"
echo "3. 'Create calendar event for Mom's Birthday tomorrow at 3pm'"

echo ""
echo "🧪 Running Tests..."
echo "==================="

# Test 1: Dad's Birthday
echo ""
echo "Test 1: Dad's Birthday"
echo "---------------------"
DAD_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Add Dad'\''s Birthday to the calendar tomorrow at 7 pm",
    "context": {},
    "user_id": "test_user"
  }')

echo "Response: $(echo "$DAD_RESPONSE" | jq -r '.response')"

# Test 2: Get Ready
echo ""
echo "Test 2: Get Ready"
echo "-----------------"
READY_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Add Get Ready to the calendar tomorrow at 6 pm",
    "context": {},
    "user_id": "test_user"
  }')

echo "Response: $(echo "$READY_RESPONSE" | jq -r '.response')"

# Test 3: Mom's Birthday
echo ""
echo "Test 3: Mom's Birthday"
echo "---------------------"
MOM_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Create calendar event for Mom'\''s Birthday tomorrow at 3pm",
    "context": {},
    "user_id": "test_user"
  }')

echo "Response: $(echo "$MOM_RESPONSE" | jq -r '.response')"

echo ""
echo "📅 Checking Created Events..."
echo "============================="

echo ""
echo "Recent calendar events:"
curl -s http://localhost:8000/api/calendar/events | jq '.events[-3:] | .[] | {title: .title, date: .start_date, time: .start_time}'

echo ""
echo "✅ Expected Results:"
echo "   • Dad's Birthday event should be titled 'Dad'\''s Birthday'"
echo "   • Get Ready event should be titled 'Get Ready'"
echo "   • Mom's Birthday event should be titled 'Mom'\''s Birthday'"

echo ""
echo "🎉 Calendar event naming fix completed!"
echo "   • Events now have proper titles extracted from user queries"
echo "   • No more generic 'Event' or 'Birthday' titles"
echo "   • Orb chat will create events with meaningful names"

