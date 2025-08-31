#!/bin/bash
# Rollback AI client to backup

echo "🔄 Rolling back AI client..."
cd /home/pi/zoe

# Find most recent backup
BACKUP=$(docker exec zoe-core ls -t /app/ai_client.backup* 2>/dev/null | head -1)

if [ ! -z "$BACKUP" ]; then
    docker exec zoe-core cp "$BACKUP" /app/ai_client.py
    docker compose restart zoe-core
    echo "✅ Rolled back to $BACKUP"
else
    echo "❌ No backup found"
fi
