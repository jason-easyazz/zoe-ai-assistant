#!/bin/bash
# POPULATE_COMPLETE_ROADMAP.sh
# Location: /home/pi/zoe/scripts/development/populate_complete_roadmap.sh
# Purpose: Add ALL planned tasks to the Dynamic Context-Aware Task System

set -e

echo "🚀 POPULATING COMPLETE TASK ROADMAP"
echo "===================================="
echo ""
echo "This will add all planned tasks for Cursor + Zack collaboration"
echo "Press Enter to continue..."
read

# Counter for tasks
COUNT=0
FAILED=0

# Function to create task
create_task() {
    local TITLE="$1"
    local OBJECTIVE="$2"
    local REQUIREMENTS="$3"
    local CONSTRAINTS="$4"
    local CRITERIA="$5"
    local PRIORITY="$6"
    
    echo "Creating: $TITLE"
    
    RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/tasks/create \
      -H "Content-Type: application/json" \
      -d "{
        \"title\": \"$TITLE\",
        \"objective\": \"$OBJECTIVE\",
        \"requirements\": $REQUIREMENTS,
        \"constraints\": $CONSTRAINTS,
        \"acceptance_criteria\": $CRITERIA,
        \"priority\": \"$PRIORITY\"
      }")
    
    if echo "$RESPONSE" | grep -q "task_id"; then
        echo "✅ Created successfully"
        COUNT=$((COUNT + 1))
    else
        echo "❌ Failed: $RESPONSE"
        FAILED=$((FAILED + 1))
    fi
    echo ""
}

echo "📋 PHASE 1: Documentation & Context Setup"
echo "========================================="

# We already created Cursor docs manually, but let's add the API documentation task
create_task \
    "Create API Documentation Generator" \
    "Auto-generate API docs from code" \
    "[\"Parse all router files\", \"Extract endpoint definitions\", \"Generate OpenAPI spec\", \"Create markdown docs\"]" \
    "[\"Must update automatically\", \"Include request/response examples\"]" \
    "[\"Docs accessible at /docs\", \"All endpoints documented\", \"Examples work\"]" \
    "medium"

create_task \
    "Enhance Task System API" \
    "Add missing endpoints for Cursor integration" \
    "[\"Add GET /api/developer/tasks/next endpoint\", \"Add claim mechanism\", \"Add WebSocket for real-time updates\"]" \
    "[\"Compatible with existing schema\", \"Non-breaking changes only\"]" \
    "[\"Cursor can query next task\", \"Task claiming works\", \"WebSocket broadcasts updates\"]" \
    "high"

echo "📋 PHASE 2: Developer System Completion"
echo "======================================="

# Note: We already have "Fix Zack Code Generation" task
create_task \
    "Implement Code Review System" \
    "Zack reviews code before execution" \
    "[\"Add static analysis step\", \"Check for dangerous operations\", \"Validate against .cursorrules\", \"Generate improvement suggestions\"]" \
    "[\"Must be fast (<2 seconds)\", \"Clear explanations for rejections\"]" \
    "[\"Dangerous code is blocked\", \"Safe code passes\", \"Suggestions are helpful\"]" \
    "high"

create_task \
    "Create Self-Test Suite" \
    "Automated testing after changes" \
    "[\"Add /api/developer/self-test endpoint\", \"Test all critical paths\", \"Auto-rollback on failure\", \"Generate test reports\"]" \
    "[\"Tests complete in <30 seconds\", \"No destructive tests\"]" \
    "[\"All tests pass on healthy system\", \"Failures are clearly reported\"]" \
    "medium"

echo "📋 PHASE 3: Self-Development Capabilities"
echo "========================================="

create_task \
    "Add Learning System" \
    "System learns from successful/failed tasks" \
    "[\"Track success patterns\", \"Update prompts based on failures\", \"Build knowledge base\", \"Share learnings between Cursor and Zack\"]" \
    "[\"Store in SQLite database\", \"No memory bloat\"]" \
    "[\"System improves over time\", \"Failures decrease\", \"Knowledge is searchable\"]" \
    "medium"

create_task \
    "Implement Feature Request Pipeline" \
    "Convert user requests to tasks automatically" \
    "[\"Parse natural language requests\", \"Generate task requirements\", \"Estimate complexity\", \"Add to queue with priority\"]" \
    "[\"Require confirmation before creating\", \"No duplicate tasks\"]" \
    "[\"Natural language creates valid tasks\", \"Requirements are complete\"]" \
    "low"

echo "📋 PHASE 4: Tool Integration"
echo "============================="

create_task \
    "Deploy LiteLLM Proxy" \
    "Enable access to multiple LLM providers" \
    "[\"Deploy LiteLLM container\", \"Configure providers\", \"Set up API keys\", \"Implement fallbacks\"]" \
    "[\"Must work offline with Ollama\", \"<40ms overhead\", \"Secure key storage\"]" \
    "[\"All configured providers accessible\", \"Fallback to Ollama works\", \"Keys secure\"]" \
    "high"

create_task \
    "Integrate Aider for Code Generation" \
    "AI pair programming capability" \
    "[\"Install Aider via pip\", \"Configure with Ollama\", \"Set up repository mapping\", \"Create helper scripts\"]" \
    "[\"ARM64 compatible\", \"<400MB memory usage\"]" \
    "[\"Aider generates working code\", \"Repository mapping works\", \"Integration with tasks\"]" \
    "medium"

create_task \
    "Add Guardrails Validation" \
    "Content and code safety validation" \
    "[\"Install Guardrails library\", \"Add PII detection\", \"Code safety checks\", \"Re-prompting for corrections\"]" \
    "[\"Lightweight validators only\", \"<100ms overhead\"]" \
    "[\"PII is detected\", \"Dangerous code blocked\", \"Re-prompting works\"]" \
    "low"

echo "📋 PHASE 5: Production & Optimization"
echo "======================================"

create_task \
    "Implement Backup System" \
    "Automatic backups before changes" \
    "[\"Create pre-task snapshots\", \"3-2-1 backup strategy\", \"Quick restore function\", \"Test restore process\"]" \
    "[\"Backups under 1GB\", \"Complete in <10 seconds\"]" \
    "[\"Backup and restore works\", \"No data loss\", \"Quick recovery\"]" \
    "high"

create_task \
    "Add Resource Monitoring" \
    "Prevent resource exhaustion" \
    "[\"Monitor CPU/RAM/Disk\", \"Pause if thresholds exceeded\", \"Alert on issues\", \"Cleanup routines\"]" \
    "[\"<5% overhead\", \"Clear thresholds\"]" \
    "[\"High resource tasks throttled\", \"Alerts work\", \"System stays healthy\"]" \
    "medium"

create_task \
    "Create Development Metrics Dashboard" \
    "Track development velocity" \
    "[\"Task completion rates\", \"Code quality metrics\", \"System improvements\", \"Weekly reports\"]" \
    "[\"Lightweight metrics only\", \"Privacy preserving\"]" \
    "[\"Metrics are accurate\", \"Reports generated\", \"Trends visible\"]" \
    "low"

create_task \
    "Optimize Task Scheduling" \
    "Intelligent task ordering" \
    "[\"Analyze dependencies\", \"Parallelize where possible\", \"Priority-based scheduling\", \"Resource-aware execution\"]" \
    "[\"No breaking existing queue\", \"Transparent scheduling\"]" \
    "[\"Tasks execute optimally\", \"Dependencies respected\", \"Parallel execution works\"]" \
    "low"

echo "📋 PHASE 6: Multi-User Preparation"
echo "==================================="

create_task \
    "Add User Context to Database" \
    "Prepare for multi-user support" \
    "[\"Add user_id columns\", \"Create user table\", \"Update API endpoints\", \"Add authentication hooks\"]" \
    "[\"Default user for backward compatibility\", \"Non-breaking changes\"]" \
    "[\"User context works\", \"Single user unaffected\", \"Ready for auth\"]" \
    "low"

create_task \
    "Create Session Management" \
    "User session handling" \
    "[\"Session tokens\", \"Session storage\", \"Timeout handling\", \"Concurrent session support\"]" \
    "[\"Stateless where possible\", \"Secure tokens\"]" \
    "[\"Sessions persist appropriately\", \"Timeouts work\", \"Multiple sessions supported\"]" \
    "low"

echo "📋 PHASE 7: Voice & Proactive Features"
echo "======================================="

create_task \
    "Fix TTS Audio Quality" \
    "Resolve text-to-speech quality issues" \
    "[\"Debug audio generation\", \"Optimize settings\", \"Test different models\", \"Implement caching\"]" \
    "[\"Must work on Pi audio\", \"Low latency required\"]" \
    "[\"Clear audio output\", \"Whisper understands TTS\", \"<1 second latency\"]" \
    "high"

create_task \
    "Implement Wake Word Detection" \
    "Hey Zoe activation" \
    "[\"Deploy openWakeWord\", \"Configure for 'Hey Zoe'\", \"Integrate with Whisper\", \"Add activation feedback\"]" \
    "[\"Low false positives\", \"<500ms response\"]" \
    "[\"Wake word detected reliably\", \"System activates\", \"Feedback provided\"]" \
    "medium"

create_task \
    "Add Proactive Suggestions" \
    "Context-aware suggestions" \
    "[\"Pattern learning\", \"Time-based triggers\", \"Context analysis\", \"Suggestion generation\"]" \
    "[\"Not annoying\", \"Respect quiet hours\", \"User controllable\"]" \
    "[\"Suggestions are relevant\", \"Timing appropriate\", \"Can be disabled\"]" \
    "low"

echo "📋 PHASE 8: Advanced Features"
echo "=============================="

create_task \
    "N8N Workflow Integration" \
    "Connect automation workflows" \
    "[\"Configure N8N endpoints\", \"Create workflow templates\", \"API integration\", \"Event triggers\"]" \
    "[\"Secure communication\", \"Rate limiting\"]" \
    "[\"Workflows trigger correctly\", \"Events flow through\", \"Templates work\"]" \
    "medium"

create_task \
    "Home Assistant Integration" \
    "Smart home control" \
    "[\"HA API connection\", \"Device discovery\", \"Command mapping\", \"Status reporting\"]" \
    "[\"Local only\", \"No cloud dependencies\"]" \
    "[\"Devices controllable\", \"Status accurate\", \"Commands work\"]" \
    "low"

create_task \
    "Mobile Companion App Design" \
    "Design mobile interface" \
    "[\"API requirements\", \"UI mockups\", \"Security model\", \"Sync strategy\"]" \
    "[\"Privacy first\", \"Offline capable\"]" \
    "[\"Design complete\", \"API defined\", \"Security model approved\"]" \
    "low"

# Summary
echo ""
echo "========================================"
echo "📊 TASK CREATION SUMMARY"
echo "========================================"
echo "✅ Successfully created: $COUNT tasks"
if [ $FAILED -gt 0 ]; then
    echo "❌ Failed to create: $FAILED tasks"
fi

# Show current task count
TOTAL=$(curl -s http://localhost:8000/api/developer/tasks/list | jq '.count')
echo "📋 Total tasks in system: $TOTAL"
echo ""
echo "🎯 Next steps:"
echo "  1. Run: ./scripts/development/cursor_task_helper.sh list"
echo "  2. Start with high priority tasks"
echo "  3. Use Cursor + Zack to complete them!"
echo ""
echo "✨ Your complete roadmap is now loaded!"
