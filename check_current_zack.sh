#!/bin/bash
# CHECK_CURRENT_ZACK.sh
# Step 1: Analyze what version of developer.py is currently running

echo "🔍 CHECKING CURRENT ZACK VERSION"
echo "================================="
echo ""

cd /home/pi/zoe

# Check if developer.py has the key functions
echo "📊 Analyzing current developer.py capabilities:"
echo ""

docker exec zoe-core bash -c '
echo "Checking for key functions in /app/routers/developer.py:"
echo "  ✓ execute_command: $(grep -c "def execute_command" /app/routers/developer.py)"
echo "  ✓ analyze_for_optimization: $(grep -c "def analyze_for_optimization" /app/routers/developer.py)"
echo "  ✓ psutil usage: $(grep -c "import psutil" /app/routers/developer.py)"
echo "  ✓ subprocess.run: $(grep -c "subprocess.run" /app/routers/developer.py)"
echo "  ✓ docker client: $(grep -c "docker.from_env" /app/routers/developer.py)"
echo ""
echo "First 30 lines of current file:"
head -30 /app/routers/developer.py
'

echo ""
echo "Testing if Zack gives real or fake data:"
curl -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the current memory usage in GB?"}' | jq -r '.response' | head -20
