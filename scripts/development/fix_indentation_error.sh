#!/bin/bash
# FIX_INDENTATION_ERROR.sh
# Fix the critical indentation error in main.py

echo "🚨 FIXING CRITICAL INDENTATION ERROR"
echo "====================================="
echo ""

cd /home/pi/zoe

# The container is crashing due to indentation error
echo "📝 Fixing indentation in main.py..."
docker exec zoe-core python3 << 'FIX_INDENT' 2>/dev/null || docker run --rm -v $(pwd)/services/zoe-core:/app python:3.11 python3 << 'FIX_INDENT'
import re

# Read the file
with open('/app/main.py', 'r') as f:
    lines = f.readlines()

# Fix indentation issues
fixed_lines = []
in_try_block = False
for i, line in enumerate(lines):
    # Detect try blocks
    if line.strip().startswith('try:'):
        in_try_block = True
        fixed_lines.append(line)
    elif line.strip().startswith('except'):
        in_try_block = False
        fixed_lines.append(line)
    elif in_try_block and line.strip() and not line.startswith('    '):
        # This line should be indented but isn't
        fixed_lines.append('    ' + line)
        print(f"Fixed indentation at line {i+1}: {line.strip()}")
    else:
        fixed_lines.append(line)

# Write back
with open('/app/main.py', 'w') as f:
    f.writelines(fixed_lines)

print("✅ Fixed indentation issues")
FIX_INDENT

# Alternative: Use a simpler approach - fix the specific line
echo -e "\n📝 Ensuring proper indentation for line 41..."
docker exec zoe-core bash -c "sed -i '41s/^from/    from/' /app/main.py" 2>/dev/null || \
  docker run --rm -v $(pwd)/services/zoe-core:/app alpine sed -i '41s/^from/    from/' /app/main.py

docker exec zoe-core bash -c "sed -i '42s/^app\./    app\./' /app/main.py" 2>/dev/null || \
  docker run --rm -v $(pwd)/services/zoe-core:/app alpine sed -i '42s/^app\./    app\./' /app/main.py

docker exec zoe-core bash -c "sed -i '43s/^app\./    app\./' /app/main.py" 2>/dev/null || \
  docker run --rm -v $(pwd)/services/zoe-core:/app alpine sed -i '43s/^app\./    app\./' /app/main.py

docker exec zoe-core bash -c "sed -i '44s/^logger/    logger/' /app/main.py" 2>/dev/null || \
  docker run --rm -v $(pwd)/services/zoe-core:/app alpine sed -i '44s/^logger/    logger/' /app/main.py

docker exec zoe-core bash -c "sed -i '45s/^logger/    logger/' /app/main.py" 2>/dev/null || \
  docker run --rm -v $(pwd)/services/zoe-core:/app alpine sed -i '45s/^logger/    logger/' /app/main.py

# Show the fixed section
echo -e "\n📋 Fixed section (lines 40-46):"
docker exec zoe-core sed -n '40,46p' /app/main.py 2>/dev/null || \
  docker run --rm -v $(pwd)/services/zoe-core:/app alpine sed -n '40,46p' /app/main.py

# Restart the container
echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core
sleep 10

# Check if it started properly
echo -e "\n🧪 Testing if container started..."
if docker ps | grep -q "zoe-core.*Up"; then
    echo "✅ Container is running!"
    
    # Test endpoints
    echo -e "\n📊 Testing endpoints:"
    echo "Zoe: $(curl -s -X POST http://localhost:8000/api/chat/ -H 'Content-Type: application/json' -d '{"message":"test"}' -o /dev/null -w '%{http_code}')"
    echo "Zack: $(curl -s -X POST http://localhost:8000/api/developer/chat -H 'Content-Type: application/json' -d '{"message":"test"}' -o /dev/null -w '%{http_code}')"
    echo "Settings UI: $(curl -s http://localhost:8000/api/settings-ui/personalities -o /dev/null -w '%{http_code}')"
    
    # Check logs
    echo -e "\nRouter loading status:"
    docker logs zoe-core --tail 20 | grep "router loaded" || echo "No router messages"
else
    echo "❌ Container failed to start!"
    echo "Checking error:"
    docker logs zoe-core --tail 30
fi
