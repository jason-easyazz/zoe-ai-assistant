#!/bin/bash
echo "🔧 Fixing index.html calendar menu link..."

# Check if we're using dist or static directory
if [ -d "services/zoe-ui/dist" ]; then
    UI_DIR="dist"
    echo "📁 Using dist directory"
else
    UI_DIR="static"
    echo "📁 Using static directory"
fi

# Backup current index.html
cp services/zoe-ui/$UI_DIR/index.html services/zoe-ui/$UI_DIR/index.html.backup-$(date +%H%M)

# Fix the calendar link in index.html
echo "🔗 Updating calendar menu link..."
sed -i 's/href="calendar.html" class="nav-item active">Calendar/href="calendar.html" class="nav-item">Calendar/g' services/zoe-ui/$UI_DIR/index.html
sed -i 's/<a href="[^"]*" class="nav-item">Calendar<\/a>/<a href="calendar.html" class="nav-item">Calendar<\/a>/g' services/zoe-ui/$UI_DIR/index.html

# Also fix any other potential calendar links
sed -i 's/onclick=".*Calendar.*"/href="calendar.html"/g' services/zoe-ui/$UI_DIR/index.html

# Copy updated index.html to container
echo "📋 Copying updated index.html to container..."
docker cp services/zoe-ui/$UI_DIR/index.html zoe-ui:/usr/share/nginx/html/index.html

# Also ensure calendar.html is in container
echo "📋 Ensuring calendar.html is in container..."
docker cp services/zoe-ui/$UI_DIR/calendar.html zoe-ui:/usr/share/nginx/html/calendar.html

# Test the links
echo ""
echo "🧪 Testing navigation:"
echo "Index page:"
curl -s http://192.168.1.60:8080/ | grep -o 'href="calendar.html"' | head -1

echo "Calendar page exists:"
if curl -s http://192.168.1.60:8080/calendar.html | grep -q "Zoe AI Calendar"; then
    echo "✅ Calendar page working"
else
    echo "❌ Calendar page issues"
fi

echo ""
echo "🌐 Test the fixed navigation:"
echo "   Main page: http://192.168.1.60:8080/"
echo "   Calendar: http://192.168.1.60:8080/calendar.html"
echo ""
echo "✅ Calendar menu item should now properly link to calendar page!"
