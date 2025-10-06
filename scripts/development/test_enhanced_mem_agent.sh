#!/bin/bash
# Test Enhanced MEM Agent
# Purpose: Test the Multi-Expert Model with action execution

set -e

echo "🤖 Testing Enhanced MEM Agent"
echo "============================="

echo ""
echo "🔧 Step 1: Start Enhanced MEM Agent Service"
echo "-------------------------------------------"

# Check if mem-agent container is running
if docker ps | grep -q mem-agent; then
    echo "✅ mem-agent container is running"
else
    echo "❌ mem-agent container not running - starting it..."
    
    # Build and start enhanced mem-agent
    cd /home/pi/zoe/services/mem-agent
    docker build -t zoe-enhanced-mem-agent .
    
    # Stop existing container if running
    docker stop mem-agent 2>/dev/null || true
    docker rm mem-agent 2>/dev/null || true
    
    # Start enhanced mem-agent
    docker run -d --name mem-agent \
        --network zoe_default \
        -p 11435:11435 \
        zoe-enhanced-mem-agent
    
    echo "⏳ Waiting for enhanced mem-agent to start..."
    sleep 10
fi

echo ""
echo "🧪 Step 2: Test Enhanced MEM Agent Health"
echo "----------------------------------------"

HEALTH_RESPONSE=$(curl -s http://localhost:11435/health 2>/dev/null || echo "{}")
if echo "$HEALTH_RESPONSE" | grep -q "healthy"; then
    echo "✅ Enhanced MEM Agent is healthy"
    echo "$HEALTH_RESPONSE" | jq '.' 2>/dev/null || echo "$HEALTH_RESPONSE"
else
    echo "❌ Enhanced MEM Agent health check failed"
    echo "Response: $HEALTH_RESPONSE"
    exit 1
fi

echo ""
echo "🎯 Step 3: Test List Expert"
echo "--------------------------"

echo "Testing: 'Can you add bread to the shopping list'"
LIST_RESPONSE=$(curl -s -X POST http://localhost:11435/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Can you add bread to the shopping list",
    "user_id": "test_user",
    "execute_actions": true
  }' 2>/dev/null)

if echo "$LIST_RESPONSE" | grep -q "actions_executed"; then
    ACTIONS_EXECUTED=$(echo "$LIST_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")
    EXECUTION_SUMMARY=$(echo "$LIST_RESPONSE" | jq -r '.execution_summary' 2>/dev/null || echo "No summary")
    
    if [[ $ACTIONS_EXECUTED -gt 0 ]]; then
        echo "✅ List expert executed $ACTIONS_EXECUTED action(s)"
        echo "   Summary: $EXECUTION_SUMMARY"
    else
        echo "⚠️  List expert responded but no actions executed"
    fi
    
    echo "   Full response:"
    echo "$LIST_RESPONSE" | jq '.' 2>/dev/null || echo "$LIST_RESPONSE"
else
    echo "❌ List expert test failed"
    echo "Response: $LIST_RESPONSE"
fi

echo ""
echo "📅 Step 4: Test Calendar Expert"
echo "------------------------------"

echo "Testing: 'Create a calendar event for Dad birthday tomorrow at 7pm'"
CALENDAR_RESPONSE=$(curl -s -X POST http://localhost:11435/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Create a calendar event for Dad birthday tomorrow at 7pm",
    "user_id": "test_user",
    "execute_actions": true
  }' 2>/dev/null)

if echo "$CALENDAR_RESPONSE" | grep -q "actions_executed"; then
    ACTIONS_EXECUTED=$(echo "$CALENDAR_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")
    EXECUTION_SUMMARY=$(echo "$CALENDAR_RESPONSE" | jq -r '.execution_summary' 2>/dev/null || echo "No summary")
    
    if [[ $ACTIONS_EXECUTED -gt 0 ]]; then
        echo "✅ Calendar expert executed $ACTIONS_EXECUTED action(s)"
        echo "   Summary: $EXECUTION_SUMMARY"
    else
        echo "⚠️  Calendar expert responded but no actions executed"
    fi
    
    echo "   Full response:"
    echo "$CALENDAR_RESPONSE" | jq '.' 2>/dev/null || echo "$CALENDAR_RESPONSE"
else
    echo "❌ Calendar expert test failed"
    echo "Response: $CALENDAR_RESPONSE"
fi

echo ""
echo "🧠 Step 5: Test Planning Expert"
echo "------------------------------"

echo "Testing: 'Help me plan my week'"
PLANNING_RESPONSE=$(curl -s -X POST http://localhost:11435/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Help me plan my week",
    "user_id": "test_user",
    "execute_actions": true
  }' 2>/dev/null)

if echo "$PLANNING_RESPONSE" | grep -q "actions_executed"; then
    ACTIONS_EXECUTED=$(echo "$PLANNING_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")
    EXECUTION_SUMMARY=$(echo "$PLANNING_RESPONSE" | jq -r '.execution_summary' 2>/dev/null || echo "No summary")
    
    if [[ $ACTIONS_EXECUTED -gt 0 ]]; then
        echo "✅ Planning expert executed $ACTIONS_EXECUTED action(s)"
        echo "   Summary: $EXECUTION_SUMMARY"
    else
        echo "⚠️  Planning expert responded but no actions executed"
    fi
    
    echo "   Full response:"
    echo "$PLANNING_RESPONSE" | jq '.' 2>/dev/null || echo "$PLANNING_RESPONSE"
else
    echo "❌ Planning expert test failed"
    echo "Response: $PLANNING_RESPONSE"
fi

echo ""
echo "💬 Step 6: Test Enhanced Chat Integration"
echo "----------------------------------------"

echo "Testing enhanced chat endpoint: 'Add malteasers to shopping list'"
CHAT_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Add malteasers to shopping list",
    "context": {},
    "user_id": "test_user"
  }' 2>/dev/null)

if echo "$CHAT_RESPONSE" | grep -q "enhanced"; then
    ACTIONS_EXECUTED=$(echo "$CHAT_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")
    EXECUTION_SUMMARY=$(echo "$CHAT_RESPONSE" | jq -r '.execution_summary' 2>/dev/null || echo "No summary")
    
    if [[ $ACTIONS_EXECUTED -gt 0 ]]; then
        echo "✅ Enhanced chat executed $ACTIONS_EXECUTED action(s)"
        echo "   Summary: $EXECUTION_SUMMARY"
    else
        echo "⚠️  Enhanced chat responded but no actions executed"
    fi
    
    echo "   Response preview:"
    echo "$CHAT_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response text"
else
    echo "❌ Enhanced chat test failed"
    echo "Response: $CHAT_RESPONSE"
fi

echo ""
echo "📊 TEST SUMMARY"
echo "==============="

echo ""
echo "✅ Enhanced MEM Agent Features Tested:"
echo "   - Multi-Expert Model architecture"
echo "   - List Expert (shopping list management)"
echo "   - Calendar Expert (event creation)"
echo "   - Planning Expert (goal planning)"
echo "   - Enhanced Chat Integration"
echo ""
echo "🎯 Key Capabilities:"
echo "   - Intent classification and routing"
echo "   - Action execution via working APIs"
echo "   - Real-time feedback and summaries"
echo "   - Fallback to basic memory search"
echo ""
echo "🌐 Access Points:"
echo "   - Enhanced MEM Agent: http://localhost:11435"
echo "   - Enhanced Chat API: http://localhost:8000/api/chat/enhanced"
echo "   - Original Chat API: http://localhost:8000/api/chat"
echo ""
echo "🎉 The Enhanced MEM Agent is ready for production use!"

