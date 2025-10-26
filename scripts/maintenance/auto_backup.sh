#!/bin/bash
# Automatic Database Backup Script
# Runs every 6 hours to protect against data loss
# Created: 2025-10-26 after user data loss incident

set -e

BACKUP_DIR="/home/pi/zoe/data/backups"
DATA_DIR="/home/pi/zoe/data"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
MAX_BACKUPS=48  # Keep 12 days of 6-hourly backups

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

echo "🔄 Starting automatic backup at $(date)"

# Backup all databases
for db in zoe.db memory.db training.db; do
    if [ -f "$DATA_DIR/$db" ]; then
        cp "$DATA_DIR/$db" "$BACKUP_DIR/${db%.db}_$TIMESTAMP.db"
        echo "✅ Backed up $db"
    else
        echo "⚠️  Warning: $db not found"
    fi
done

# Check user count in main database
USER_COUNT=$(sqlite3 "$DATA_DIR/zoe.db" "SELECT COUNT(*) FROM users WHERE user_id != 'system';" 2>/dev/null || echo "0")
echo "📊 Current user count: $USER_COUNT"

if [ "$USER_COUNT" -lt 5 ]; then
    echo "🚨 ALERT: User count below 5! Expected: jason, andrew, teneeka, asya, user"
    echo "$(date): CRITICAL - User count is $USER_COUNT" >> "$DATA_DIR/alerts.log"
fi

# Clean up old backups (keep last MAX_BACKUPS)
cd "$BACKUP_DIR"
for db_prefix in zoe memory training; do
    ls -t ${db_prefix}_*.db 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | xargs -r rm
done

echo "✅ Backup complete at $(date)"
echo "---"

