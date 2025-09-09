#!/bin/bash
# CHECK_KEY_SECURITY.sh
# Analyze current API key security

echo "🔒 API KEY SECURITY AUDIT"
echo "=========================="
echo ""

cd /home/pi/zoe

# Check 1: .env file permissions
echo "📁 Checking .env file security..."
if [ -f .env ]; then
    permissions=$(ls -l .env | awk '{print $1}')
    owner=$(ls -l .env | awk '{print $3}')
    echo "  Permissions: $permissions"
    echo "  Owner: $owner"
    
    if [[ "$permissions" == *"rw-r--r--"* ]]; then
        echo "  ⚠️ WARNING: .env is readable by other users!"
    elif [[ "$permissions" == *"rw-------"* ]]; then
        echo "  ✅ Good: .env is only readable by owner"
    fi
else
    echo "  ❌ No .env file found"
fi

# Check 2: Git security
echo -e "\n📦 Checking Git security..."
if [ -f .gitignore ]; then
    if grep -q "^.env$" .gitignore; then
        echo "  ✅ .env is in .gitignore"
    else
        echo "  ❌ CRITICAL: .env is NOT in .gitignore!"
    fi
else
    echo "  ⚠️ No .gitignore file!"
fi

# Check 3: Keys in containers
echo -e "\n🐳 Checking container exposure..."
echo "  Testing if keys are visible in container env:"
docker exec zoe-core printenv | grep -c "API_KEY" || echo "  Keys found: 0"

# Check 4: Backup security
echo -e "\n💾 Checking backup security..."
if find backups -name "*.env" 2>/dev/null | grep -q "."; then
    echo "  ⚠️ Found .env files in backups!"
else
    echo "  ✅ No .env files in backups"
fi

# Check 5: Memory/Swap
echo -e "\n🧠 Checking memory security..."
if [ -f /proc/sys/vm/swappiness ]; then
    swappiness=$(cat /proc/sys/vm/swappiness)
    echo "  Swappiness: $swappiness"
    if [ "$swappiness" -gt 10 ]; then
        echo "  ⚠️ High swappiness could write keys to disk"
    fi
fi

echo -e "\n🔍 Security Summary:"
echo "================================"
