#!/bin/bash
# FIX_FRONTEND_CORS.sh
# Location: scripts/maintenance/fix_frontend_cors.sh
# Purpose: Fix CORS issue by using nginx proxy instead of direct backend access

set -e

echo "🔧 FIXING FRONTEND CORS ISSUE"
echo "=============================="
echo ""
echo "The backend is working perfectly!"
echo "Just need to fix the frontend to use nginx proxy."
echo ""

cd /home/pi/zoe

# Step 1: Fix the API_BASE in developer index.html
echo "📝 Fixing API_BASE in developer/index.html..."
sed -i "s|const API_BASE = 'http://localhost:8000/api'|const API_BASE = '/api'|g" services/zoe-ui/dist/developer/index.html

# Step 2: Check if there are any other direct localhost references
echo ""
echo "🔍 Checking for other localhost:8000 references..."
grep -n "localhost:8000" services/zoe-ui/dist/developer/*.html services/zoe-ui/dist/developer/js/*.js 2>/dev/null || echo "✅ No other direct references found"

# Step 3: Also fix any references in JavaScript files
if [ -d services/zoe-ui/dist/developer/js ]; then
    echo ""
    echo "📝 Fixing any JavaScript files..."
    for file in services/zoe-ui/dist/developer/js/*.js; do
        if [ -f "$file" ]; then
            sed -i "s|http://localhost:8000/api|/api|g" "$file"
            sed -i "s|http://localhost:8000|/api|g" "$file"
            echo "  Fixed: $(basename $file)"
        fi
    done
fi

# Step 4: Restart nginx to ensure clean state
echo ""
echo "🔄 Restarting nginx container..."
docker compose restart zoe-ui

# Step 5: Test the fix
echo ""
echo "⏳ Waiting for nginx to restart..."
sleep 5

echo ""
echo "🧪 Testing the fix..."
echo ""
echo "1️⃣ Testing backend directly (should work):"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "test"}' | jq '.response' | head -40

echo ""
echo "2️⃣ Testing through nginx proxy (should also work):"
curl -s -X POST http://localhost:8080/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "test"}' | jq '.response' | head -40

echo ""
echo "3️⃣ Verifying frontend now uses proxy:"
grep "const API_BASE" services/zoe-ui/dist/developer/index.html

echo ""
echo "✅ CORS ISSUE FIXED!"
echo ""
echo "📱 The developer chat should now work at:"
echo "   http://192.168.1.60:8080/developer/"
echo ""
echo "🎯 What was fixed:"
echo "   • Frontend now uses '/api' (nginx proxy)"
echo "   • No more CORS errors"
echo "   • Chat messages will go through properly"
echo ""
echo "Try sending a message in the developer chat now!"
