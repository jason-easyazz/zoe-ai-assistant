#!/bin/bash
echo "🔧 Fixing calendar deployment issue..."

# Check if we're using dist or static directory
if [ -d "services/zoe-ui/dist" ]; then
    UI_DIR="dist"
    echo "📁 Using dist directory"
else
    UI_DIR="static"
    echo "📁 Using static directory"
fi

# Copy calendar.html to container
echo "📋 Copying calendar.html to container..."
docker cp services/zoe-ui/$UI_DIR/calendar.html zoe-ui:/usr/share/nginx/html/calendar.html

# Verify it was copied
echo "✅ Verifying file in container:"
docker exec zoe-ui ls -la /usr/share/nginx/html/calendar.html

# Test the calendar now works
echo ""
echo "🧪 Testing calendar access:"
sleep 2
if curl -s http://192.168.1.60:8080/calendar.html | grep -q "Zoe AI Calendar"; then
    echo "✅ Calendar is now working!"
else
    echo "❌ Still showing wrong content"
fi

echo ""
echo "🌐 Try now: http://192.168.1.60:8080/calendar.html"
