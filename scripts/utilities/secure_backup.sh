#!/bin/bash
# SECURE_BACKUP.sh - Excludes all sensitive files

echo "🔒 Creating secure backup..."

cd /home/pi/zoe

# Create backup excluding sensitive files
backup_file="backups/secure_backup_$(date +%Y%m%d_%H%M%S).tar.gz"

tar -czf "$backup_file" \
    --exclude=".env" \
    --exclude="*.env" \
    --exclude=".env.*" \
    --exclude="api_keys.json" \
    --exclude="*api_key*" \
    --exclude="*secret*" \
    --exclude="*.key" \
    --exclude="data/secure_keys" \
    --exclude="data/api_keys" \
    --exclude=".git" \
    services/ \
    scripts/ \
    docker-compose.yml \
    nginx.conf

echo "✅ Secure backup created: $backup_file"
echo "   (sensitive files excluded)"

# Verify no secrets in backup
echo "🔍 Verifying backup security..."
if tar -tzf "$backup_file" | grep -E "(\.env|api_key|secret)" > /dev/null 2>&1; then
    echo "❌ WARNING: Sensitive files detected in backup!"
else
    echo "✅ Backup verified secure - no sensitive files"
fi
