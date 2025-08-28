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
