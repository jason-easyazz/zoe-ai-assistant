#!/bin/bash
# Debug Orb Issue - Why is orb not executing actions?

echo "🔍 Debug: Orb Action Execution Issue"
echo "==================================="

echo ""
echo "📋 User's Reported Response:"
echo "   'I'm glad you reached out for help with your shopping list. Here's a direct and concise answer:"
echo "   * I can suggest adding bread to your shopping list."
echo "   * You have plans to visit a store on October 10th (Birthday), but no specific time has been mentioned yet."
echo "   * On October 4th, there is a meeting scheduled at 2 PM.'"

echo ""
echo "🎯 Expected Response:"
echo "   '✅ Added 'Bread' to Shopping list'"

echo ""
echo "🧪 Testing API Endpoints"
echo "======================="

echo ""
echo "📝 Test 1: Original Chat API"
echo "----------------------------"
ORIGINAL_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Add bread to the shopping list",
    "context": {},
    "user_id": "test_user"
  }')

ORIGINAL_TEXT=$(echo "$ORIGINAL_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")
echo "Original API Response: ${ORIGINAL_TEXT:0:100}..."

echo ""
echo "🚀 Test 2: Enhanced Chat API"
echo "----------------------------"
ENHANCED_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Add bread to the shopping list",
    "context": {},
    "user_id": "test_user"
  }')

ENHANCED_TEXT=$(echo "$ENHANCED_RESPONSE" | jq -r '.response' 2>/dev/null || echo "No response")
ENHANCED_ACTIONS=$(echo "$ENHANCED_RESPONSE" | jq -r '.actions_executed' 2>/dev/null || echo "0")
echo "Enhanced API Response: ${ENHANCED_TEXT:0:100}..."
echo "Actions Executed: $ENHANCED_ACTIONS"

echo ""
echo "🔍 Analysis"
echo "==========="

echo ""
echo "User's response mentions:"
echo "   - Calendar events (October 10th Birthday, October 4th meeting)"
echo "   - Shopping list suggestions"
echo "   - Conversational tone"

echo ""
echo "This suggests the orb is:"
echo "   - Using the original chat API (not enhanced)"
echo "   - Getting calendar context (which enhanced API also gets)"
echo "   - Not executing actions"

echo ""
echo "🔧 Possible Issues"
echo "=================="

echo ""
echo "1. 🌐 Browser Caching:"
echo "   - User's browser might be caching old JavaScript"
echo "   - Orb might be using cached version with original API"

echo ""
echo "2. 🔄 JavaScript Error:"
echo "   - Orb JavaScript might have an error"
echo "   - Fetch request might be failing silently"

echo ""
echo "3. 🎯 Different Orb Implementation:"
echo "   - Orb might be using a different JavaScript file"
echo "   - There might be multiple orb implementations"

echo ""
echo "4. 📡 API Call Not Being Made:"
echo "   - Orb might not be making the API call at all"
echo "   - Request might be intercepted or redirected"

echo ""
echo "🎯 RECOMMENDED SOLUTIONS"
echo "========================"

echo ""
echo "1. 🔄 Force Browser Refresh:"
echo "   - User should do Ctrl+F5 (hard refresh)"
echo "   - Clear browser cache"
echo "   - Try in incognito/private mode"

echo ""
echo "2. 🔧 Check Browser Console:"
echo "   - Open browser developer tools (F12)"
echo "   - Check Console tab for JavaScript errors"
echo "   - Check Network tab to see which API is being called"

echo ""
echo "3. 🎯 Test Different Pages:"
echo "   - Try orb on different pages (lists.html, journal.html)"
echo "   - See if the issue is page-specific"

echo ""
echo "4. 🔍 Verify Orb HTML:"
echo "   - Check if orb HTML is correct on the page"
echo "   - Verify sendOrbMessage function is present"

echo ""
echo "🎉 Debug completed!"

