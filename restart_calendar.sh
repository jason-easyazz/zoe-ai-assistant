#!/bin/bash
echo "🔄 Restarting UI service to load updated calendar..."

# Restart the UI service
docker compose restart zoe-ui

# Wait for service to start
echo "⏳ Waiting for service to start..."
sleep 5

# Check service status
echo "📊 Checking service status:"
docker compose ps zoe-ui

# Test calendar accessibility
echo ""
echo "🧪 Testing calendar access:"
if curl -s -f http://192.168.1.60:8080/calendar.html > /dev/null; then
   echo "✅ Calendar is accessible"
else
   echo "❌ Calendar not accessible"
fi

# Test API connection
echo ""
echo "🔗 Testing API connection:"
if curl -s -f http://192.168.1.60:8000/health > /dev/null; then
   echo "✅ Backend API is healthy"
else
   echo "❌ Backend API not responding"
fi

# Get event count
echo ""
echo "📅 Checking events:"
EVENT_COUNT=$(curl -s http://192.168.1.60:8000/api/events | jq -r '.events | length' 2>/dev/null || echo "unknown")
echo "Events available: $EVENT_COUNT"

echo ""
echo "🌐 Calendar URL: http://192.168.1.60:8080/calendar.html"
echo "🎉 Ready to test your updated calendar!"
