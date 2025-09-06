#!/bin/bash
# CHECK_ZACK_VERSION.sh

echo "🔍 CHECKING ZACK VERSION"
echo "========================"

# Check key functions
docker exec zoe-core bash -c '
echo "Current developer.py capabilities:"
echo "  execute_command: $(grep -c "def execute_command" /app/routers/developer.py)"
echo "  analyze_for_optimization: $(grep -c "def analyze_for_optimization" /app/routers/developer.py)"
echo "  Real psutil: $(grep -c "psutil.cpu_percent" /app/routers/developer.py)"
echo "  Task management: $(grep -c "developer_tasks" /app/routers/developer.py)"
echo ""
echo "File size: $(wc -l /app/routers/developer.py | cut -d" " -f1) lines"
'

# Test real data
echo -e "\nTesting real data response:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "memory"}' | jq -r '.response' | grep -q "REAL" && echo "✅ Using REAL data" || echo "❌ Not using real data"
