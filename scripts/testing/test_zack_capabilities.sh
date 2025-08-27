#!/bin/bash
# TEST_ZACK_CAPABILITIES.sh
# Location: scripts/testing/test_zack_capabilities.sh
# Purpose: Test what Zack can actually DO, not just how he talks

set -e

echo "🔧 TESTING ZACK'S TECHNICAL CAPABILITIES"
echo "========================================"
echo ""
echo "Testing practical developer tasks..."
echo ""

cd /home/pi/zoe

# Function to test and display response
test_capability() {
    local QUESTION=$1
    local TEST_NAME=$2
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "TEST: $TEST_NAME"
    echo "QUESTION: $QUESTION"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/chat \
        -H "Content-Type: application/json" \
        -d "{\"message\": \"$QUESTION\"}" 2>/dev/null | jq -r '.response' || echo "Error: No response")
    
    echo "$RESPONSE"
    echo ""
    echo "Press Enter for next test..."
    read
}

# Test 1: System diagnostics
test_capability \
    "Check docker containers status" \
    "1. Container Status Check"

# Test 2: Resource monitoring
test_capability \
    "How much RAM and CPU is being used?" \
    "2. Resource Monitoring"

# Test 3: Error diagnosis
test_capability \
    "Check for any errors in the system logs" \
    "3. Error Detection"

# Test 4: Script generation
test_capability \
    "Create a backup script for the database" \
    "4. Script Generation"

# Test 5: Technical solution
test_capability \
    "How would you fix a container that keeps restarting?" \
    "5. Problem Solving"

# Test 6: System information
test_capability \
    "What version of Python and what OS are we running?" \
    "6. System Information"

# Test 7: Performance analysis
test_capability \
    "Which container is using the most resources?" \
    "7. Performance Analysis"

# Test 8: Security check
test_capability \
    "Check if all services are properly secured" \
    "8. Security Assessment"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ CAPABILITY TESTS COMPLETE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Based on these responses, we can see:"
echo "1. What Zack can actually do"
echo "2. What information he has access to"
echo "3. How technical his responses are"
echo "4. What needs improvement"
echo ""
echo "Share the results and we'll adjust the prompts (not hardcode) to improve!"
