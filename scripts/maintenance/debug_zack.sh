#!/bin/bash
# DEBUG_ZACK.sh - Find why Zack isn't generating code

echo "🔍 DEBUGGING ZACK CODE GENERATION"
echo "================================="

cd /home/pi/zoe

# 1. Check if files were created
echo -e "\n1️⃣ Checking if enhanced AI client exists:"
docker exec zoe-core ls -la /app/ai_client_enhanced.py 2>&1 || echo "❌ File not found"

# 2. Check if developer router is loaded
echo -e "\n2️⃣ Checking developer router:"
docker exec zoe-core ls -la /app/routers/developer.py 2>&1 || echo "❌ Router not found"

# 3. Check for import errors
echo -e "\n3️⃣ Checking for Python errors:"
docker exec zoe-core python3 -c "
try:
    import sys
    sys.path.append('/app')
    from ai_client_enhanced import ai_client
    print('✅ Enhanced AI client imports successfully')
except Exception as e:
    print(f'❌ Import error: {e}')
"

# 4. Check logs for errors
echo -e "\n4️⃣ Recent error logs:"
docker logs zoe-core --tail 20 2>&1 | grep -E "ERROR|error|Error" || echo "No errors found"

# 5. Check if developer endpoint is registered
echo -e "\n5️⃣ Checking API routes:"
curl -s http://localhost:8000/openapi.json | jq '.paths | keys' | grep developer || echo "❌ Developer routes not found"

# 6. Test basic developer status
echo -e "\n6️⃣ Testing developer status endpoint:"
curl -s http://localhost:8000/api/developer/status | jq '.'

# 7. Check main.py imports
echo -e "\n7️⃣ Checking main.py imports:"
docker exec zoe-core grep -E "ai_client|developer" /app/main.py || echo "❌ Imports not found"

echo -e "\n✅ Debug complete"
