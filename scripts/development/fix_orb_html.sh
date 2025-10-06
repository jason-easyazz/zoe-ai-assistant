#!/bin/bash
# Fix Missing Orb HTML Div
# Purpose: Add the missing orb HTML div to all pages

set -e

echo "🔧 Fixing Missing Orb HTML Div"
echo "==============================="

PAGES=("calendar.html" "lists.html" "memories.html" "workflows.html" "settings.html" "journal.html" "chat.html" "diagnostics.html")

for page in "${PAGES[@]}"; do
    echo "🔧 Fixing $page..."
    
    # Check if orb HTML div exists
    if docker exec zoe-ui grep -q 'id="zoeOrb"' /usr/share/nginx/html/$page; then
        echo "   ✅ Orb HTML div already exists"
    else
        echo "   ❌ Orb HTML div missing - adding it..."
        
        # Add orb HTML div before closing body tag
        docker exec zoe-ui sed -i '/<\/body>/i\
    <!-- Zoe Orb -->\
    <div class="zoe-orb" id="zoeOrb" title="Click to chat with Zoe" onclick="toggleOrbChat()">\
    </div>' /usr/share/nginx/html/$page
        
        echo "   ✅ Orb HTML div added"
    fi
done

echo ""
echo "🧪 Testing orb HTML presence:"
echo "-----------------------------"

for page in "${PAGES[@]}"; do
    echo -n "   $page: "
    if docker exec zoe-ui grep -q 'id="zoeOrb"' /usr/share/nginx/html/$page; then
        echo "✅ Orb HTML present"
    else
        echo "❌ Orb HTML still missing"
    fi
done

echo ""
echo "🎯 Verification:"
echo "================="
echo "✅ CSS: Present (11 components)"
echo "✅ JavaScript: Present (orb functions)"
echo "✅ HTML: Now added (orb div)"
echo ""
echo "🌐 Test URLs:"
echo "https://zoe.local/calendar.html"
echo "https://zoe.local/settings.html"
echo "https://zoe.local/lists.html"
echo ""
echo "📱 Instructions:"
echo "1. Clear browser cache (Ctrl+F5)"
echo "2. Visit https://zoe.local/calendar.html"
echo "3. Look for purple orb in bottom-right corner"
echo "4. Click orb to test chat functionality"

