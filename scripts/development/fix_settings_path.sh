#!/bin/bash
# FIX_SETTINGS_PATH.sh
# Fix the 404 on settings endpoint

echo "🔧 Fixing Settings Endpoint Path"
echo "================================="
echo ""

cd /home/pi/zoe

# The issue: UI might be calling wrong path
echo "📋 Checking which path works..."
echo "  /api/settings/personalities: $(curl -s http://localhost:8000/api/settings/personalities -o /dev/null -w '%{http_code}')"
echo "  /api/settings-ui/personalities: $(curl -s http://localhost:8000/api/settings-ui/personalities -o /dev/null -w '%{http_code}')"

# Update UI to use correct path
echo -e "\n📝 Updating settings UI to use correct endpoints..."
sed -i 's|/api/settings/|/api/settings-ui/|g' services/zoe-ui/dist/developer/settings.html

echo "✅ Updated settings paths"

# Restart UI
docker compose restart zoe-ui
sleep 5

# Test
echo -e "\n🧪 Testing..."
echo "  Settings endpoint: $(curl -s http://localhost:8000/api/settings-ui/personalities -o /dev/null -w '%{http_code}')"

echo -e "\n✅ Done!"
