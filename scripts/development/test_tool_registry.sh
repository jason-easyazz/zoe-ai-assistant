#!/bin/bash
# Tool Registry System Test Script
# Purpose: Test the new tool registry and AI-driven invocation system

set -e

echo "🔧 Testing Tool Registry System..."

# Test 1: List available tools
echo "📋 Test 1: Listing available tools..."
TOOLS_RESPONSE=$(curl -s http://localhost:8000/api/tools/available 2>/dev/null)

if echo "$TOOLS_RESPONSE" | grep -q "tool_id"; then
    TOOLS_COUNT=$(echo "$TOOLS_RESPONSE" | jq '.total')
    echo "✅ Found $TOOLS_COUNT tools in registry"
    
    # Show some tools
    echo "$TOOLS_RESPONSE" | jq -r '.tools[0:3][] | "- \(.name) (\(.category))"' 2>/dev/null || echo "   - Tools available"
else
    echo "❌ Failed to list tools"
    echo "Response: $TOOLS_RESPONSE"
    exit 1
fi

# Test 2: Invoke a simple tool (system info)
echo "🔍 Test 2: Invoking system info tool..."
SYSTEM_RESPONSE=$(curl -X POST -s http://localhost:8000/api/tools/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "tool_id": "system_info",
    "parameters": {},
    "user_id": "test_user"
  }' 2>/dev/null)

if echo "$SYSTEM_RESPONSE" | grep -q "execution_id"; then
    EXECUTION_ID=$(echo "$SYSTEM_RESPONSE" | jq -r '.execution.execution_id')
    echo "✅ System info tool executed successfully"
    echo "   Execution ID: $EXECUTION_ID"
    
    # Show result
    if echo "$SYSTEM_RESPONSE" | grep -q "uptime"; then
        UPTIME=$(echo "$SYSTEM_RESPONSE" | jq -r '.execution.result.uptime' 2>/dev/null || echo "N/A")
        echo "   System uptime: $UPTIME"
    fi
else
    echo "❌ Failed to execute system info tool"
    echo "Response: $SYSTEM_RESPONSE"
fi

# Test 3: Invoke a tool that requires confirmation
echo "📝 Test 3: Testing tool requiring confirmation..."
FILE_RESPONSE=$(curl -X POST -s http://localhost:8000/api/tools/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "tool_id": "file_write",
    "parameters": {
      "file_path": "/tmp/test_file.txt",
      "content": "Test content from tool registry"
    },
    "user_id": "test_user"
  }' 2>/dev/null)

if echo "$FILE_RESPONSE" | grep -q "requires_confirmation"; then
    FILE_EXECUTION_ID=$(echo "$FILE_RESPONSE" | jq -r '.execution.execution_id')
    echo "✅ File write tool requires confirmation (as expected)"
    echo "   Execution ID: $FILE_EXECUTION_ID"
    
    # Confirm the execution
    echo "🔐 Confirming file write execution..."
    CONFIRM_RESPONSE=$(curl -X POST -s "http://localhost:8000/api/tools/executions/$FILE_EXECUTION_ID/confirm" 2>/dev/null)
    
    if echo "$CONFIRM_RESPONSE" | grep -q "execution_id"; then
        echo "✅ File write execution confirmed and completed"
    else
        echo "⚠️  Confirmation response: $CONFIRM_RESPONSE"
    fi
else
    echo "❌ File write tool did not require confirmation (unexpected)"
    echo "Response: $FILE_RESPONSE"
fi

# Test 4: AI-driven tool selection
echo "🤖 Test 4: Testing AI-driven tool selection..."
AI_RESPONSE=$(curl -X POST -s http://localhost:8000/api/tools/ai-invoke \
  -H "Content-Type: application/json" \
  -d '{
    "user_request": "Turn on the living room lights and play some jazz music",
    "context": {"room": "living_room", "user_preferences": {"music_genre": "jazz"}},
    "user_id": "test_user",
    "max_tools": 3,
    "require_confirmation": false
  }' 2>/dev/null)

if echo "$AI_RESPONSE" | grep -q "selected_tools"; then
    SELECTED_TOOLS=$(echo "$AI_RESPONSE" | jq -r '.selected_tools | join(", ")')
    INVOCATION_ID=$(echo "$AI_RESPONSE" | jq -r '.invocation_id')
    echo "✅ AI selected tools: $SELECTED_TOOLS"
    echo "   Invocation ID: $INVOCATION_ID"
    
    # Show execution results
    EXECUTIONS_COUNT=$(echo "$AI_RESPONSE" | jq '.executions | length')
    echo "   Executions: $EXECUTIONS_COUNT tools executed"
else
    echo "❌ AI tool selection failed"
    echo "Response: $AI_RESPONSE"
fi

# Test 5: Get tool statistics
echo "📊 Test 5: Getting tool registry statistics..."
STATS_RESPONSE=$(curl -s http://localhost:8000/api/tools/stats 2>/dev/null)

if echo "$STATS_RESPONSE" | grep -q "total_tools"; then
    TOTAL_TOOLS=$(echo "$STATS_RESPONSE" | jq '.total_tools')
    TOTAL_EXECUTIONS=$(echo "$STATS_RESPONSE" | jq '.total_executions')
    AI_INVOCATIONS=$(echo "$STATS_RESPONSE" | jq '.ai_invocations')
    AVG_SUCCESS=$(echo "$STATS_RESPONSE" | jq '.average_success_rate')
    
    echo "✅ Tool registry statistics:"
    echo "   📋 Total tools: $TOTAL_TOOLS"
    echo "   🔧 Total executions: $TOTAL_EXECUTIONS"
    echo "   🤖 AI invocations: $AI_INVOCATIONS"
    echo "   📈 Average success rate: $AVG_SUCCESS"
else
    echo "❌ Failed to get statistics"
    echo "Response: $STATS_RESPONSE"
fi

# Test 6: Test memory search tool
echo "🧠 Test 6: Testing memory search tool..."
MEMORY_RESPONSE=$(curl -X POST -s http://localhost:8000/api/tools/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "tool_id": "memory_search",
    "parameters": {
      "query": "family movie night",
      "limit": 5
    },
    "user_id": "test_user"
  }' 2>/dev/null)

if echo "$MEMORY_RESPONSE" | grep -q "execution_id"; then
    echo "✅ Memory search tool executed successfully"
    
    # Show search results
    RESULTS_COUNT=$(echo "$MEMORY_RESPONSE" | jq '.execution.result | length' 2>/dev/null || echo "0")
    echo "   Found $RESULTS_COUNT memory results"
else
    echo "❌ Failed to execute memory search"
    echo "Response: $MEMORY_RESPONSE"
fi

# Test 7: Test calendar tool
echo "📅 Test 7: Testing calendar event creation..."
CALENDAR_RESPONSE=$(curl -X POST -s http://localhost:8000/api/tools/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "tool_id": "calendar_create_event",
    "parameters": {
      "title": "Test Event from Tool Registry",
      "start_time": "2025-01-15T19:00:00",
      "end_time": "2025-01-15T21:00:00",
      "description": "Testing the tool registry calendar integration"
    },
    "user_id": "test_user"
  }' 2>/dev/null)

if echo "$CALENDAR_RESPONSE" | grep -q "requires_confirmation"; then
    CALENDAR_EXECUTION_ID=$(echo "$CALENDAR_RESPONSE" | jq -r '.execution.execution_id')
    echo "✅ Calendar event creation requires confirmation (as expected)"
    echo "   Execution ID: $CALENDAR_EXECUTION_ID"
    
    # Confirm calendar event
    echo "🔐 Confirming calendar event creation..."
    CALENDAR_CONFIRM=$(curl -X POST -s "http://localhost:8000/api/tools/executions/$CALENDAR_EXECUTION_ID/confirm" 2>/dev/null)
    
    if echo "$CALENDAR_CONFIRM" | grep -q "execution_id"; then
        echo "✅ Calendar event confirmed and created"
    else
        echo "⚠️  Calendar confirmation response: $CALENDAR_CONFIRM"
    fi
else
    echo "❌ Calendar tool did not require confirmation (unexpected)"
    echo "Response: $CALENDAR_RESPONSE"
fi

echo ""
echo "🎉 Tool Registry System Tests Complete!"
echo ""
echo "📋 Available Features:"
echo "   ✅ Tool registry with 9+ default tools"
echo "   ✅ Safe tool execution with permission checks"
echo "   ✅ Confirmation system for destructive operations"
echo "   ✅ AI-driven tool selection and invocation"
echo "   ✅ Execution monitoring and statistics"
echo "   ✅ Database persistence and rollback support"
echo ""
echo "🔧 Tool Categories:"
echo "   📁 File operations (read, write)"
echo "   🗄️  Database queries"
echo "   📅 Calendar events"
echo "   🧠 Memory search"
echo "   🔔 Notifications"
echo "   🏠 HomeAssistant integration"
echo "   💻 System information"
echo ""
echo "🤖 AI Capabilities:"
echo "   🧠 Intelligent tool selection based on user requests"
echo "   🔄 Automatic parameter inference"
echo "   ⚡ Batch tool execution"
echo "   🛡️  Safety confirmation system"
echo ""
echo "📝 Example Usage:"
echo "   # List tools:"
echo "   curl http://localhost:8000/api/tools/available"
echo ""
echo "   # Invoke specific tool:"
echo "   curl -X POST http://localhost:8000/api/tools/invoke \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"tool_id\": \"system_info\", \"parameters\": {}}'"
echo ""
echo "   # AI-driven invocation:"
echo "   curl -X POST http://localhost:8000/api/tools/ai-invoke \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"user_request\": \"Turn on lights and play music\"}'"

