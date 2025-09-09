#!/bin/bash
# TEST_ZACK_CAPABILITIES.sh
# Demonstrate Zack's system visibility and autonomous fixing

echo "🔧 TESTING ZACK'S SYSTEM CAPABILITIES"
echo "======================================"
echo ""

cd /home/pi/zoe

# Test 1: System Visibility
echo "TEST 1: System Visibility"
echo "-------------------------"
echo "Query: 'Full system diagnostic report with container health'"
response=$(curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{
        "message": "Generate a full system diagnostic report. Include: container status, memory usage, disk space, running services, and any detected issues.",
        "context": {"mode": "developer", "needs_execution": true}
    }')

echo "Response:"
echo "$response" | jq -r '.response' 2>/dev/null | head -200
echo ""

# Test 2: Detect and Fix Issues
echo -e "\nTEST 2: Autonomous Issue Detection"
echo "-----------------------------------"
echo "Query: 'Detect any system issues and provide fixes'"
response=$(curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{
        "message": "Scan for system issues. Check: high memory usage, stopped containers, full logs, permission problems. Provide executable fixes.",
        "context": {"mode": "developer", "needs_execution": true}
    }')

echo "$response" | jq -r '.response' 2>/dev/null | head -200
echo ""

# Test 3: Script Generation
echo -e "\nTEST 3: Autonomous Script Generation"
echo "------------------------------------"
echo "Query: 'Create a script to optimize system performance'"
response=$(curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{
        "message": "Create an executable bash script to optimize Zoe system performance. Include: clearing caches, removing old logs, optimizing Docker, checking API response times.",
        "context": {"mode": "developer", "generate_script": true}
    }')

echo "$response" | jq -r '.response' 2>/dev/null
echo ""

# Test 4: Real-time Monitoring
echo -e "\nTEST 4: Real-time System Monitoring"
echo "-----------------------------------"
echo "Query: 'Monitor system for 10 seconds and report'"
response=$(curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{
        "message": "Monitor CPU, memory, and container health for the next 10 seconds. Report any anomalies.",
        "context": {"mode": "developer", "real_time": true}
    }')

echo "$response" | jq -r '.response' 2>/dev/null | head -200
