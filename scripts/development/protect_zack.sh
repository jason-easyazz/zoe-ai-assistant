#!/bin/bash
# PROTECT_ZACK.sh
# Creates multiple backups of the working Zack version

set -e

echo "🔒 PROTECTING ZACK'S WORKING VERSION"
echo "===================================="
echo ""

cd /home/pi/zoe

# Step 1: Create container backup
echo "📦 Step 1: Creating container backup..."
docker exec zoe-core bash -c '
cp /app/routers/developer.py /app/routers/developer_GOLDEN_$(date +%Y%m%d_%H%M%S).py
echo "✅ Container backup created"
ls -la /app/routers/developer_GOLDEN_*.py | tail -1
'

# Step 2: Extract to host
echo -e "\n💾 Step 2: Extracting to host..."
docker cp zoe-core:/app/routers/developer.py /tmp/developer_working.py

# Step 3: Create multiple backup locations
echo -e "\n🗂️ Step 3: Creating multiple backups..."

# Permanent scripts folder
mkdir -p scripts/permanent
cp /tmp/developer_working.py scripts/permanent/developer_v5_GOLDEN_$(date +%Y%m%d).py
echo "✅ Saved to scripts/permanent/"

# Backup folder
mkdir -p backups/developer
cp /tmp/developer_working.py backups/developer/developer_$(date +%Y%m%d_%H%M%S).py
echo "✅ Saved to backups/developer/"

# Documentation folder
mkdir -p documentation/working_versions
cp /tmp/developer_working.py documentation/working_versions/developer_v5_working.py
echo "✅ Saved to documentation/working_versions/"

# Step 4: Create recovery script
echo -e "\n📝 Step 4: Creating recovery script..."
cat > scripts/utilities/restore_zack.sh << 'RECOVERY'
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
RECOVERY

chmod +x scripts/utilities/restore_zack.sh
echo "✅ Recovery script created"

# Step 5: Create version check script
echo -e "\n🔍 Step 5: Creating version check script..."
cat > scripts/utilities/check_zack_version.sh << 'CHECK'
#!/bin/bash
# CHECK_ZACK_VERSION.sh

echo "🔍 CHECKING ZACK VERSION"
echo "========================"

# Check key functions
docker exec zoe-core bash -c '
echo "Current developer.py capabilities:"
echo "  execute_command: $(grep -c "def execute_command" /app/routers/developer.py)"
echo "  analyze_for_optimization: $(grep -c "def analyze_for_optimization" /app/routers/developer.py)"
echo "  Real psutil: $(grep -c "psutil.cpu_percent" /app/routers/developer.py)"
echo "  Task management: $(grep -c "developer_tasks" /app/routers/developer.py)"
echo ""
echo "File size: $(wc -l /app/routers/developer.py | cut -d" " -f1) lines"
'

# Test real data
echo -e "\nTesting real data response:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "memory"}' | jq -r '.response' | grep -q "REAL" && echo "✅ Using REAL data" || echo "❌ Not using real data"
CHECK

chmod +x scripts/utilities/check_zack_version.sh
echo "✅ Version check script created"

# Step 6: Document the protection
echo -e "\n📋 Step 6: Documenting protection..."
cat >> ZOE_CURRENT_STATE.md << EOF

## 🔒 PROTECTED VERSION - $(date)
- Working Zack v5 backed up to multiple locations
- Recovery script: scripts/utilities/restore_zack.sh
- Version check: scripts/utilities/check_zack_version.sh
- Backups in: scripts/permanent/, backups/developer/, documentation/working_versions/
EOF

# Step 7: Show summary
echo -e "\n✅ PROTECTION COMPLETE!"
echo "======================="
echo ""
echo "📂 Backups created in:"
ls -la scripts/permanent/developer_v5_GOLDEN_*.py 2>/dev/null | tail -1
ls -la backups/developer/developer_*.py 2>/dev/null | tail -1
ls -la documentation/working_versions/developer_v5_working.py 2>/dev/null

echo ""
echo "🛠️ Utility scripts:"
echo "  • Restore: ./scripts/utilities/restore_zack.sh"
echo "  • Check: ./scripts/utilities/check_zack_version.sh"
echo ""
echo "💡 If Zack ever breaks, run:"
echo "  ./scripts/utilities/restore_zack.sh"
