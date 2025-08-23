#!/bin/bash
echo "🔍 Verifying Relative URL Implementation"
echo "========================================"

# Check for absolute URLs in frontend
echo -e "\n1. Checking for absolute URLs in frontend..."
ABSOLUTE_URLS=$(grep -r "http://.*:8000" services/zoe-ui/dist/ 2>/dev/null | wc -l)
if [ "$ABSOLUTE_URLS" -eq 0 ]; then
    echo "  ✅ No absolute URLs found"
else
    echo "  ❌ Found $ABSOLUTE_URLS absolute URLs:"
    grep -r "http://.*:8000" services/zoe-ui/dist/ | head -3
fi

# Check for localhost references
echo -e "\n2. Checking for localhost references..."
LOCALHOST_REFS=$(grep -r "localhost:8000" services/zoe-ui/dist/ 2>/dev/null | wc -l)
if [ "$LOCALHOST_REFS" -eq 0 ]; then
    echo "  ✅ No localhost references"
else
    echo "  ❌ Found $LOCALHOST_REFS localhost references"
fi

# Check nginx configuration
echo -e "\n3. Checking nginx proxy configuration..."
if grep -q "location /api/" services/zoe-ui/nginx.conf; then
    echo "  ✅ API proxy configured"
else
    echo "  ❌ API proxy not configured"
fi

# Test if services are running
echo -e "\n4. Testing services (if running)..."
if curl -s http://localhost:8080/api/health > /dev/null 2>&1; then
    echo "  ✅ API accessible through nginx proxy"
else
    echo "  ⚠️  Services not running or proxy not working"
fi

echo -e "\n✅ Verification complete!"
