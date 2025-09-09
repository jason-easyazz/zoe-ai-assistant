#!/bin/bash
# FIX_INDENTATION_NOW.sh
# Fix the indentation error that's crashing the container

echo "🚨 FIXING CRITICAL INDENTATION ERROR"
echo "====================================="
echo ""

cd /home/pi/zoe

# Since container is crashed, work directly on the file
echo "📝 Fixing main.py indentation directly..."

# Fix the indentation on lines 41-45
sed -i '41s/^from/    from/' services/zoe-core/main.py
sed -i '42s/^app\./    app\./' services/zoe-core/main.py
sed -i '43s/^app\./    app\./' services/zoe-core/main.py
sed -i '44s/^logger/    logger/' services/zoe-core/main.py
sed -i '45s/^logger/    logger/' services/zoe-core/main.py

# Show the fixed section
echo "📋 Fixed section (lines 40-46):"
sed -n '40,46p' services/zoe-core/main.py

# Restart the container
echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core
sleep 10

# Check status
echo -e "\n🧪 Checking container status..."
if docker ps | grep -q "zoe-core.*Up"; then
    echo "✅ Container is running!"
    
    # Test endpoints
    echo -e "\nTesting endpoints:"
    echo "Health: $(curl -s http://localhost:8000/health -o /dev/null -w '%{http_code}')"
    echo "Zoe: $(curl -s -X POST http://localhost:8000/api/chat/ -H 'Content-Type: application/json' -d '{"message":"test"}' -o /dev/null -w '%{http_code}')"
    echo "Zack: $(curl -s -X POST http://localhost:8000/api/developer/chat -H 'Content-Type: application/json' -d '{"message":"test"}' -o /dev/null -w '%{http_code}')"
    echo "Settings UI: $(curl -s http://localhost:8000/api/settings-ui/personalities -o /dev/null -w '%{http_code}')"
else
    echo "❌ Container still not running"
    echo "Last error:"
    docker logs zoe-core --tail 10
fi
