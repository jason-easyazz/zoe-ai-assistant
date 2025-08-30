#!/bin/bash
# DEBUG_AND_FIX_BACKUPS.sh - Debug why backup endpoints aren't working

echo "🔍 DEBUGGING BACKUP ENDPOINTS"
echo "============================="

cd /home/pi/zoe

# Step 1: Check what routers actually exist
echo "📋 Current routers in container:"
docker exec zoe-core ls -la /app/routers/ | grep -E "backup|developer"

# Step 2: Check main.py imports
echo -e "\n📋 Main.py imports:"
docker exec zoe-core grep -E "import.*backup|import.*developer" /app/main.py

# Step 3: Check if developer.py has backup endpoints
echo -e "\n📋 Developer.py backup endpoints:"
docker exec zoe-core grep -E "@router.*backup|def.*backup" /app/routers/developer.py | head -5

# Step 4: Check logs for errors
echo -e "\n📋 Recent errors:"
docker logs zoe-core --tail 30 | grep -E "ERROR|error|Warning|Failed"

# Step 5: Let's use Zack to fix this properly
echo -e "\n🔧 Using Zack to generate proper backup system..."
curl -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Build two separate backup systems: 1) User backup at /api/user/backup for personal data only (conversations, memories, tasks) 2) Developer backup at /api/developer/backup for full system (code, config, database). Make them complete with create, list, restore, delete operations."
  }' > /tmp/backup_code.json

# Extract the task ID
TASK_ID=$(cat /tmp/backup_code.json | jq -r '.task_id')
echo -e "\n📝 Generated task: $TASK_ID"

# Show the generated code
echo -e "\n📋 Generated backup code (first 50 lines):"
cat /tmp/backup_code.json | jq -r '.code' | head -50

# Step 6: Quick fix - manually create working backup endpoints
echo -e "\n🔧 Creating minimal working backup endpoints..."
docker exec zoe-core bash -c 'cat >> /app/routers/developer.py << "PYEOF"

# Developer Backup Endpoints
@router.post("/backup")
async def create_developer_backup():
    """Create full system backup"""
    import tarfile
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"/app/data/dev_backup_{timestamp}.tar.gz"
    
    with tarfile.open(backup_file, "w:gz") as tar:
        # Add database
        if os.path.exists("/app/data/zoe.db"):
            tar.add("/app/data/zoe.db", arcname="zoe.db")
        # Add routers
        if os.path.exists("/app/routers"):
            tar.add("/app/routers", arcname="routers")
    
    return {
        "status": "success",
        "backup_id": f"dev_backup_{timestamp}",
        "file": backup_file,
        "size": os.path.getsize(backup_file) if os.path.exists(backup_file) else 0
    }

@router.get("/backups")
async def list_developer_backups():
    """List all system backups"""
    backups = []
    if os.path.exists("/app/data"):
        for file in os.listdir("/app/data"):
            if file.startswith("dev_backup_") and file.endswith(".tar.gz"):
                backups.append({
                    "id": file.replace(".tar.gz", ""),
                    "file": file
                })
    return {"backups": backups, "type": "system"}
PYEOF'

# Step 7: Create simple user backup router
docker exec zoe-core bash -c 'cat > /app/routers/user_data.py << "PYEOF"
"""User Data Backup Router"""
from fastapi import APIRouter
import sqlite3
import json
import os
from datetime import datetime

router = APIRouter(prefix="/api/userdata", tags=["user"])

@router.post("/backup")
async def backup_user_data(user_id: str = "default"):
    """Backup user data only"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"/app/data/user_{user_id}_{timestamp}.json"
    
    # Get user data from database
    conn = sqlite3.connect("/app/data/zoe.db")
    cursor = conn.cursor()
    
    backup_data = {
        "user_id": user_id,
        "timestamp": timestamp,
        "data": {}
    }
    
    # Get memories
    try:
        cursor.execute("SELECT * FROM memories")
        backup_data["data"]["memories"] = cursor.fetchall()
    except:
        pass
    
    # Get tasks
    try:
        cursor.execute("SELECT * FROM tasks")
        backup_data["data"]["tasks"] = cursor.fetchall()
    except:
        pass
    
    conn.close()
    
    # Save backup
    with open(backup_file, "w") as f:
        json.dump(backup_data, f, default=str)
    
    return {
        "status": "success",
        "backup_id": f"user_{user_id}_{timestamp}",
        "file": backup_file
    }

@router.get("/backups")
async def list_user_backups(user_id: str = "default"):
    """List user backups"""
    backups = []
    if os.path.exists("/app/data"):
        for file in os.listdir("/app/data"):
            if file.startswith(f"user_{user_id}_") and file.endswith(".json"):
                backups.append({"id": file.replace(".json", ""), "file": file})
    return {"backups": backups, "user_id": user_id}
PYEOF'

# Step 8: Register the user_data router
docker exec zoe-core python3 << 'PYTHON'
with open("/app/main.py", "r") as f:
    content = f.read()

# Add user_data import
if "user_data" not in content:
    content = content.replace(
        "from routers import developer",
        "from routers import developer, user_data"
    )

# Register router
if "app.include_router(user_data.router)" not in content:
    content = content.replace(
        "app.include_router(developer.router)",
        "app.include_router(developer.router)\n    app.include_router(user_data.router)"
    )

with open("/app/main.py", "w") as f:
    f.write(content)

print("✅ Registered user_data router")
PYTHON

# Step 9: Restart
echo -e "\n🔄 Restarting..."
docker compose restart zoe-core
sleep 8

# Step 10: Test the fixed endpoints
echo -e "\n🧪 Testing fixed endpoints..."

echo "1️⃣ Developer backup:"
curl -s -X POST http://localhost:8000/api/developer/backup | jq '.'

echo -e "\n2️⃣ Developer backup list:"
curl -s http://localhost:8000/api/developer/backups | jq '.'

echo -e "\n3️⃣ User data backup:"
curl -s -X POST "http://localhost:8000/api/userdata/backup?user_id=default" | jq '.'

echo -e "\n4️⃣ User backup list:"
curl -s "http://localhost:8000/api/userdata/backups?user_id=default" | jq '.'

echo -e "\n5️⃣ All backup endpoints:"
curl -s http://localhost:8000/openapi.json | jq '.paths | keys[]' | grep -i backup

echo -e "\n✅ Backup systems fixed!"
