#!/bin/bash
# RESTORE_ZACK.sh - Quickly restore working Zack

echo "🔄 RESTORING ZACK TO WORKING VERSION"
echo "===================================="

cd /home/pi/zoe

# Find the most recent GOLDEN backup
LATEST_BACKUP=$(ls -t scripts/permanent/developer_v5_GOLDEN_*.py 2>/dev/null | head -1)

if [ -z "$LATEST_BACKUP" ]; then
    echo "❌ No GOLDEN backup found!"
    exit 1
fi

echo "📦 Restoring from: $LATEST_BACKUP"

# Copy to container
docker cp "$LATEST_BACKUP" zoe-core:/app/routers/developer.py

# Restart
docker compose restart zoe-core

echo "✅ Zack restored to working version!"
