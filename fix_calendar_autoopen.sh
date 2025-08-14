#!/bin/bash
echo "🔧 Making calendar auto-open to interface view..."

# Backup first
cp services/zoe-ui/dist/calendar.html services/zoe-ui/dist/calendar.html.backup-$(date +%H%M)

# Add auto-enter interface after DOM loads
sed -i '/document.addEventListener.*DOMContentLoaded.*initializeApp/a\        \
        // Auto-enter calendar interface\
        setTimeout(() => {\
            enterInterface();\
        }, 100);' services/zoe-ui/dist/calendar.html

echo "✅ Calendar will now auto-open to calendar interface"

# Restart UI to apply changes
docker compose restart zoe-ui
sleep 2

echo "🌐 Test: http://192.168.1.60:8080/calendar.html"
