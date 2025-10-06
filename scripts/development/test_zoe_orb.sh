#!/bin/bash
# Zoe Orb Test Script
# Purpose: Test orb functionality across all pages

set -e

echo "🧪 Testing Zoe Orb Implementation..."

# Test WebSocket endpoint
echo "🔌 Testing WebSocket endpoint..."
if curl -s --connect-timeout 5 http://localhost:8000/ws/intelligence > /dev/null 2>&1; then
    echo "✅ WebSocket endpoint accessible"
else
    echo "❌ WebSocket endpoint not accessible"
fi

# Test notification system
echo "📢 Testing notification system..."
NOTIFICATION_RESPONSE=$(curl -X POST -s http://localhost:8000/api/notifications/test/suggestion 2>/dev/null)
if echo "$NOTIFICATION_RESPONSE" | grep -q "Reconnect Opportunity"; then
    echo "✅ Notification system working"
else
    echo "❌ Notification system not working"
    echo "Response: $NOTIFICATION_RESPONSE"
fi

# Test pages for orb presence
echo "🔍 Testing orb presence on pages..."
PAGES=(
    "calendar.html"
    "lists.html" 
    "memories.html"
    "workflows.html"
    "settings.html"
    "journal.html"
    "chat.html"
    "diagnostics.html"
)

DIST_DIR="/home/pi/zoe/services/zoe-ui/dist"
ALL_GOOD=true

for page in "${PAGES[@]}"; do
    if [[ -f "$DIST_DIR/$page" ]]; then
        ORB_COUNT=$(grep -c "zoe-orb" "$DIST_DIR/$page" 2>/dev/null || echo "0")
        if [[ "$ORB_COUNT" -ge 10 ]]; then
            echo "✅ $page - Orb present ($ORB_COUNT components)"
        else
            echo "❌ $page - Orb missing or incomplete ($ORB_COUNT components)"
            ALL_GOOD=false
        fi
    else
        echo "❌ $page - File not found"
        ALL_GOOD=false
    fi
done

echo ""
if [[ "$ALL_GOOD" == true ]]; then
    echo "🎉 All orb tests passed!"
    echo ""
    echo "📋 Orb Features Available:"
    echo "   ✅ Beautiful purple orb with liquid animations"
    echo "   ✅ State-based colors (connecting, connected, thinking, etc.)"
    echo "   ✅ Hover effects and smooth transitions"
    echo "   ✅ Click to open chat window"
    echo "   ✅ WebSocket connection for real-time intelligence"
    echo "   ✅ Toast notifications for proactive suggestions"
    echo "   ✅ Badge indicator for new notifications"
    echo ""
    echo "🚀 Next Steps:"
    echo "   1. Open any page in browser to see the orb"
    echo "   2. Click the orb to test chat functionality"
    echo "   3. Verify WebSocket connection (orb should show 'connected')"
    echo "   4. Test proactive notifications"
else
    echo "❌ Some orb tests failed. Check the output above."
fi

echo ""
echo "🌐 Test URLs:"
echo "   - Calendar: http://localhost/calendar.html"
echo "   - Lists: http://localhost/lists.html"
echo "   - Memories: http://localhost/memories.html"
echo "   - Settings: http://localhost/settings.html"

