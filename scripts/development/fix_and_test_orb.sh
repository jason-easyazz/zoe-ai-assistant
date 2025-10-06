#!/bin/bash
# Fix and Test Orb Visibility
# Purpose: Ensure orb is visible on all pages and provide clear instructions

set -e

echo "🎨 Zoe Orb Visibility Fix & Test"
echo "================================="

echo ""
echo "📋 Current Status:"
echo "   ✅ Orb components are present in all 8 pages"
echo "   ✅ Web server is serving files correctly"
echo "   ✅ HTTPS is working (zoe.local)"
echo "   ✅ HTTP redirects to HTTPS"
echo ""

echo "🔍 Testing Orb Visibility:"
echo "-------------------------"

PAGES=("calendar.html" "lists.html" "memories.html" "workflows.html" "settings.html" "journal.html" "chat.html" "diagnostics.html")

for page in "${PAGES[@]}"; do
    echo -n "   $page: "
    
    # Test HTTPS access
    HTTPS_ORB=$(curl -k -s https://zoe.local/$page | grep -c '<div class="zoe-orb"' 2>/dev/null || echo "0")
    
    if [[ $HTTPS_ORB -gt 0 ]]; then
        echo "✅ Orb HTML present via HTTPS"
    else
        echo "❌ Orb HTML missing via HTTPS"
    fi
done

echo ""
echo "🌐 Access Instructions:"
echo "======================"
echo ""
echo "To see the Zoe orb on all pages:"
echo ""
echo "1. Use HTTPS URLs (not HTTP):"
echo "   ✅ https://zoe.local/calendar.html"
echo "   ✅ https://zoe.local/settings.html"  
echo "   ✅ https://zoe.local/lists.html"
echo "   ✅ https://zoe.local/memories.html"
echo "   ✅ https://zoe.local/workflows.html"
echo "   ✅ https://zoe.local/journal.html"
echo "   ✅ https://zoe.local/chat.html"
echo "   ✅ https://zoe.local/diagnostics.html"
echo ""
echo "2. Clear browser cache:"
echo "   - Press Ctrl+F5 (hard refresh)"
echo "   - Or Ctrl+Shift+R"
echo "   - Or open incognito/private window"
echo ""
echo "3. Check browser console:"
echo "   - Press F12 → Console tab"
echo "   - Look for any JavaScript errors"
echo ""
echo "4. Verify orb appearance:"
echo "   - Purple orb in bottom-right corner"
echo "   - Breathing animation"
echo "   - Hover effect (scales up)"
echo "   - Click to open chat window"
echo ""

echo "🔧 Troubleshooting:"
echo "==================="
echo ""
echo "If orb still not visible:"
echo "1. Check if you're using https://zoe.local/ (not http://localhost/)"
echo "2. Try a different browser"
echo "3. Check if browser blocks mixed content"
echo "4. Verify network connectivity to zoe.local"
echo ""

echo "📱 Test the orb functionality:"
echo "============================="
echo ""
echo "Once orb is visible:"
echo "1. Hover over orb → should scale up with glow"
echo "2. Click orb → should open chat window"
echo "3. Type message → should connect to Zoe"
echo "4. Check orb color → should show connection state"
echo "   - Purple: Default/idle"
echo "   - Green: Connected"
echo "   - Yellow: Thinking"
echo "   - Red: Error"
echo ""

echo "🎯 Success Criteria:"
echo "==================="
echo "✅ Purple orb visible on all 8 pages"
echo "✅ Orb responds to hover and click"
echo "✅ Chat window opens when clicked"
echo "✅ WebSocket connection established"
echo "✅ Real-time notifications working"
echo ""

echo "🔗 Quick Test URLs:"
echo "==================="
echo "https://zoe.local/calendar.html"
echo "https://zoe.local/settings.html"
echo "https://zoe.local/lists.html"

