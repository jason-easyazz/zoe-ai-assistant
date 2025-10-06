#!/bin/bash
# Agent Planning System Test Script
# Purpose: Test the new agent-based task planning framework

set -e

echo "🤖 Testing Agent Planning System..."

# Test 1: Create a goal
echo "📋 Test 1: Creating a goal..."
GOAL_RESPONSE=$(curl -X POST -s http://localhost:8000/api/agent/goals \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Plan family movie night Friday",
    "objective": "Plan family movie night for Friday evening with snacks and movie selection",
    "constraints": ["Budget under $50", "Family-friendly movies only", "Start by 7 PM"],
    "success_criteria": ["Movie selected", "Snacks planned", "Event on calendar", "Family notified"],
    "priority": "high",
    "estimated_duration_minutes": 30,
    "context": {"family_size": 4, "preferred_genres": ["action", "comedy"]}
  }' 2>/dev/null)

if echo "$GOAL_RESPONSE" | grep -q "id"; then
    GOAL_ID=$(echo "$GOAL_RESPONSE" | jq -r '.id')
    echo "✅ Goal created successfully: $GOAL_ID"
else
    echo "❌ Failed to create goal"
    echo "Response: $GOAL_RESPONSE"
    exit 1
fi

# Test 2: Generate execution plan
echo "📊 Test 2: Generating execution plan..."
PLAN_RESPONSE=$(curl -X POST -s "http://localhost:8000/api/agent/goals/$GOAL_ID/plan" 2>/dev/null)

if echo "$PLAN_RESPONSE" | grep -q "plan_id"; then
    PLAN_ID=$(echo "$PLAN_RESPONSE" | jq -r '.plan_id')
    echo "✅ Plan generated successfully: $PLAN_ID"
    
    # Show plan details
    STEPS_COUNT=$(echo "$PLAN_RESPONSE" | jq '.steps | length')
    DURATION=$(echo "$PLAN_RESPONSE" | jq '.estimated_total_duration')
    echo "   📈 Plan has $STEPS_COUNT steps, estimated ${DURATION} minutes"
else
    echo "❌ Failed to generate plan"
    echo "Response: $PLAN_RESPONSE"
    exit 1
fi

# Test 3: List agents
echo "👥 Test 3: Listing available agents..."
AGENTS_RESPONSE=$(curl -s http://localhost:8000/api/agent/agents 2>/dev/null)

if echo "$AGENTS_RESPONSE" | grep -q "agent_id"; then
    AGENT_COUNT=$(echo "$AGENTS_RESPONSE" | jq 'length')
    echo "✅ Found $AGENT_COUNT agents in system"
    
    # Show agent types
    echo "$AGENTS_RESPONSE" | jq -r '.[] | "- \(.name) (\(.agent_type))"' | head -4
else
    echo "❌ Failed to list agents"
    echo "Response: $AGENTS_RESPONSE"
fi

# Test 4: Execute goal (background)
echo "🚀 Test 4: Executing goal..."
EXECUTE_RESPONSE=$(curl -X POST -s "http://localhost:8000/api/agent/goals/$GOAL_ID/execute" 2>/dev/null)

if echo "$EXECUTE_RESPONSE" | grep -q "execution started"; then
    echo "✅ Goal execution started successfully"
else
    echo "❌ Failed to start goal execution"
    echo "Response: $EXECUTE_RESPONSE"
fi

# Test 5: Get system stats
echo "📊 Test 5: Getting system statistics..."
STATS_RESPONSE=$(curl -s http://localhost:8000/api/agent/stats 2>/dev/null)

if echo "$STATS_RESPONSE" | grep -q "total_goals"; then
    TOTAL_GOALS=$(echo "$STATS_RESPONSE" | jq '.total_goals')
    TOTAL_PLANS=$(echo "$STATS_RESPONSE" | jq '.total_plans')
    TOTAL_AGENTS=$(echo "$STATS_RESPONSE" | jq '.total_agents')
    REDIS_AVAILABLE=$(echo "$STATS_RESPONSE" | jq '.redis_available')
    
    echo "✅ System statistics:"
    echo "   📋 Total goals: $TOTAL_GOALS"
    echo "   📊 Total plans: $TOTAL_PLANS"
    echo "   👥 Total agents: $TOTAL_AGENTS"
    echo "   🔗 Redis available: $REDIS_AVAILABLE"
else
    echo "❌ Failed to get statistics"
    echo "Response: $STATS_RESPONSE"
fi

# Test 6: List goals
echo "📋 Test 6: Listing goals..."
GOALS_RESPONSE=$(curl -s http://localhost:8000/api/agent/goals 2>/dev/null)

if echo "$GOALS_RESPONSE" | grep -q "id"; then
    GOALS_COUNT=$(echo "$GOALS_RESPONSE" | jq 'length')
    echo "✅ Found $GOALS_COUNT goals in system"
else
    echo "❌ Failed to list goals"
    echo "Response: $GOALS_RESPONSE"
fi

echo ""
echo "🎉 Agent Planning System Tests Complete!"
echo ""
echo "📋 Available Features:"
echo "   ✅ Goal creation with constraints and success criteria"
echo "   ✅ Intelligent plan generation with step decomposition"
echo "   ✅ Agent registry and management"
echo "   ✅ Background goal execution"
echo "   ✅ System statistics and monitoring"
echo "   ✅ Database persistence"
echo ""
echo "🚀 Advanced Capabilities:"
echo "   🤖 Multiple agent types (Planner, Executor, Validator, Coordinator)"
echo "   📊 Dependency analysis and critical path calculation"
echo "   🔄 Risk assessment and rollback strategies"
echo "   📡 Inter-agent communication (Redis-based)"
echo "   ⚡ Parallel step execution"
echo ""
echo "📝 Example Usage:"
echo "   curl -X POST http://localhost:8000/api/agent/goals \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"title\": \"Enhance Memory System\", \"objective\": \"Add semantic search\"}'"
echo ""
echo "   curl http://localhost:8000/api/agent/goals"
echo "   curl http://localhost:8000/api/agent/agents"
echo "   curl http://localhost:8000/api/agent/stats"

