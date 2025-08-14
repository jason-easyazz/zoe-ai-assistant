#!/bin/bash
echo "🔧 Fixing calendar menu link in index.html..."

# First, let's see what the current calendar link looks like
echo "🔍 Current calendar link in index.html:"
docker exec zoe-ui grep -n "Calendar" /usr/share/nginx/html/index.html

# Copy the current index.html from container to see what we're working with
docker cp zoe-ui:/usr/share/nginx/html/index.html ./current_index.html

# Show the nav menu section
echo ""
echo "📋 Current navigation menu:"
grep -A 5 -B 5 "Calendar" ./current_index.html

# Create a fixed version
echo ""
echo "🔧 Creating fixed version..."

# Method 1: Use sed to fix the calendar link
sed 's/class="nav-item">Calendar/href="calendar.html" class="nav-item">Calendar/g' ./current_index.html > ./fixed_index.html
sed -i 's/onclick="[^"]*calendar[^"]*"/href="calendar.html"/gi' ./fixed_index.html

# Copy the fixed version back to container
docker cp ./fixed_index.html zoe-ui:/usr/share/nginx/html/index.html

# Test the fix
echo ""
echo "✅ Testing the fix:"
curl -s http://192.168.1.60:8080/ | grep -o 'href="calendar.html"' && echo "✅ Calendar link found" || echo "❌ Calendar link missing"

# Clean up
rm ./current_index.html ./fixed_index.html

echo ""
echo "🌐 Test now: http://192.168.1.60:8080/"
echo "   Click Calendar in menu - should go to calendar page"
