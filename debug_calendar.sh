#!/bin/bash
echo "🔍 Debugging calendar display issue..."

# Check if calendar.html exists and its size
echo "📁 Checking calendar.html file:"
if [ -f "services/zoe-ui/dist/calendar.html" ]; then
    echo "✅ calendar.html exists"
    echo "📏 File size: $(du -h services/zoe-ui/dist/calendar.html | cut -f1)"
    echo "📄 First few lines:"
    head -5 services/zoe-ui/dist/calendar.html
else
    echo "❌ calendar.html not found"
fi

# Check what's actually being served
echo ""
echo "🌐 Testing what's being served at /calendar.html:"
curl -s http://192.168.1.60:8080/calendar.html | head -10

# Check nginx logs for any issues
echo ""
echo "📜 Recent nginx logs:"
docker compose logs --tail=10 zoe-ui

# Check container file system
echo ""
echo "📂 Files in container:"
docker exec zoe-ui ls -la /usr/share/nginx/html/

# Test direct file access
echo ""
echo "🧪 Testing if nginx can find the file:"
docker exec zoe-ui test -f /usr/share/nginx/html/calendar.html && echo "✅ File exists in container" || echo "❌ File missing in container"
