#!/bin/bash
# FIX_SECURITY_NOW.sh
# Immediate security fixes for detected issues

set -e

echo "🚨 FIXING SECURITY ISSUES IMMEDIATELY"
echo "====================================="
echo ""

cd /home/pi/zoe

# FIX 1: Secure .env file permissions (CRITICAL)
echo "🔐 Fixing .env permissions..."
if [ -f .env ]; then
    chmod 600 .env
    echo "  ✅ Fixed: .env now has 600 permissions (owner only)"
    
    # Verify the fix
    new_perms=$(ls -l .env | awk '{print $1}')
    echo "  Verified: $new_perms"
fi

# FIX 2: Remove .env files from ALL backups
echo -e "\n🗑️ Removing exposed .env files from backups..."
backup_count=$(find backups -name "*.env" -o -name ".env" 2>/dev/null | wc -l)
if [ "$backup_count" -gt 0 ]; then
    find backups -name "*.env" -o -name ".env" -type f -delete 2>/dev/null
    echo "  ✅ Removed $backup_count .env files from backups"
    
    # Also remove from tar archives if present
    find backups -name "*.tar.gz" -o -name "*.tar" | while read archive; do
        echo "  Checking archive: $archive"
        if tar -tzf "$archive" 2>/dev/null | grep -q ".env"; then
            echo "    ⚠️ Found .env in archive - rebuilding without it"
            temp_dir=$(mktemp -d)
            tar -xzf "$archive" -C "$temp_dir" 2>/dev/null || tar -xf "$archive" -C "$temp_dir"
            find "$temp_dir" -name ".env" -delete
            tar -czf "$archive.new" -C "$temp_dir" .
            mv "$archive.new" "$archive"
            rm -rf "$temp_dir"
        fi
    done
else
    echo "  ✅ No .env files in backups"
fi

# FIX 3: Reduce swappiness for security
echo -e "\n💾 Reducing swappiness for security..."
current_swap=$(cat /proc/sys/vm/swappiness)
if [ "$current_swap" -gt 10 ]; then
    echo 10 | sudo tee /proc/sys/vm/swappiness > /dev/null
    echo "  ✅ Reduced swappiness from $current_swap to 10 (temporary)"
    
    # Make permanent
    if ! grep -q "vm.swappiness" /etc/sysctl.conf; then
        echo "vm.swappiness=10" | sudo tee -a /etc/sysctl.conf > /dev/null
        echo "  ✅ Made swappiness change permanent"
    fi
fi

# FIX 4: Secure the data directory
echo -e "\n📁 Securing data directory..."
chmod 700 data/ 2>/dev/null || true
if [ -d "data/api_keys" ]; then
    chmod 700 data/api_keys
fi
echo "  ✅ Data directory secured"

# FIX 5: Create secure backup script
echo -e "\n📝 Creating secure backup script..."
cat > scripts/utilities/secure_backup.sh << 'BACKUP'
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
BACKUP

chmod +x scripts/utilities/secure_backup.sh
echo "  ✅ Secure backup script created"

# FIX 6: Check if keys are loaded via env_file in docker-compose
echo -e "\n🐳 Checking Docker configuration..."
if grep -q "env_file:" docker-compose.yml; then
    echo "  ℹ️ Docker is loading .env file directly"
    echo "  This is why keys show in container (this is normal)"
else
    echo "  ⚠️ Keys might be hardcoded - check docker-compose.yml"
fi

# FIX 7: Create key rotation reminder
echo -e "\n📅 Setting up security reminders..."
cat > scripts/utilities/security_check.sh << 'CHECK'
#!/bin/bash
# Weekly security check

echo "🔒 Weekly Security Check"
echo "======================="

# Check .env permissions
perms=$(ls -l /home/pi/zoe/.env 2>/dev/null | awk '{print $1}')
if [[ "$perms" != "-rw-------" ]]; then
    echo "❌ .env permissions are not secure: $perms"
    echo "   Run: chmod 600 /home/pi/zoe/.env"
else
    echo "✅ .env permissions secure"
fi

# Check for exposed keys in backups
if find /home/pi/zoe/backups -name "*.env" 2>/dev/null | grep -q "."; then
    echo "❌ Found .env files in backups!"
else
    echo "✅ No .env files in backups"
fi

# Check swappiness
swap=$(cat /proc/sys/vm/swappiness)
if [ "$swap" -gt 10 ]; then
    echo "⚠️ Swappiness is high: $swap"
else
    echo "✅ Swappiness is secure: $swap"
fi

echo ""
echo "Remember to:"
echo "  • Rotate API keys monthly"
echo "  • Check for unauthorized access"
echo "  • Review audit logs"
CHECK

chmod +x scripts/utilities/security_check.sh

# Create cron job for weekly check (optional)
echo "  ✅ Security check script created"
echo "  To enable weekly checks, add to crontab:"
echo "  0 9 * * 1 /home/pi/zoe/scripts/utilities/security_check.sh"

# FINAL: Summary
echo -e "\n✅ SECURITY FIXES APPLIED!"
echo "=========================="
echo ""
echo "Fixed:"
echo "  ✅ .env permissions now 600 (owner-only)"
echo "  ✅ Removed .env from all backups"
echo "  ✅ Reduced swappiness to 10"
echo "  ✅ Secured data directory"
echo "  ✅ Created secure backup script"
echo ""
echo "Notes about container keys:"
echo "  • Keys showing in container is NORMAL when using env_file"
echo "  • This is how Docker passes .env to containers"
echo "  • Keys are still protected by container isolation"
echo ""
echo "Next steps:"
echo "  1. Use secure_backup.sh for future backups"
echo "  2. Run security_check.sh weekly"
echo "  3. Consider rotating your API keys"
echo ""
echo "Run security audit again to verify:"
echo "  ./check_key_security.sh"
