#!/bin/bash
# SIMPLE_ZACK_TEST.sh
# Location: scripts/testing/simple_zack_test.sh
# Purpose: Basic test to see if Zack (Developer AI) is working

set -e

echo "🧪 SIMPLE ZACK TEST"
echo "==================="
echo ""

cd /home/pi/zoe

# 1. Check if containers are running
echo "Step 1: Checking Docker containers..."
echo "--------------------------------------"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe- || echo "No zoe containers found!"
echo ""

# 2. Test basic API health
echo "Step 2: Testing API health..."
echo "------------------------------"
HEALTH=$(curl -s http://localhost:8000/health 2>/dev/null || echo "API not responding")
echo "Response: $HEALTH"
echo ""

# 3. Test Zoe (User) personality
echo "Step 3: Testing Zoe (User) personality..."
echo "------------------------------------------"
echo "Sending: 'Hi Zoe, how are you?'"
curl -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Hi Zoe, how are you?"}' 2>/dev/null | jq -r '.response' | head -5 || echo "Zoe not responding"
echo ""

# 4. Test Zack (Developer) personality
echo "Step 4: Testing Zack (Developer) personality..."
echo "------------------------------------------------"
echo "Sending: 'System status'"
curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "System status"}' 2>/dev/null | jq -r '.response' | head -5 || echo "Zack not responding"
echo ""

# 5. Check if developer dashboard exists
echo "Step 5: Checking Developer Dashboard..."
echo "----------------------------------------"
if [ -f "services/zoe-ui/dist/developer/index.html" ]; then
    echo "✅ Developer dashboard files exist"
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/developer/)
    echo "HTTP Status: $STATUS"
    if [ "$STATUS" = "200" ]; then
        echo "✅ Dashboard accessible at: http://192.168.1.60:8080/developer/"
    else
        echo "⚠️  Dashboard files exist but not accessible via web"
    fi
else
    echo "❌ Developer dashboard files not found"
fi

echo ""
echo "Test complete! Please share the results above."
