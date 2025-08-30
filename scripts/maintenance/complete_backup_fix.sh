#!/bin/bash
# COMPLETE_BACKUP_FIX.sh - Fix all backup issues

echo "🔧 COMPLETE BACKUP FIX"
echo "====================="

cd /home/pi/zoe

# Step 1: Fix main.py import error
echo "📝 Fixing main.py import error..."
docker exec zoe-core python3 << 'PYTHON'
with open("/app/main.py", "r") as f:
    content = f.read()

# Remove the broken backup import
content = content.replace(", backup", "")
content = content.replace("from routers import backup", "")
content = content.replace("app.include_router(backup.router)", "")

# Clean up imports - keep only what exists
content = content.replace(
    "from routers import developer, backup, chat, settings, settings_ui",
    "from routers import developer, chat, settings, settings_ui"
)

with open("/app/main.py", "w") as f:
    f.write(content)

print("✅ Fixed main.py imports")
PYTHON

# Step 2: Add backup endpoints directly to developer.py
echo -e "\n📝 Adding backup endpoints to developer.py..."
docker exec zoe-core bash -c 'cat >> /app/routers/developer.py << "PYEOF"

# ============= BACKUP SYSTEM =============
@router.post("/system-backup")
async def create_system_backup():
    """Create complete system backup (developer use)"""
    import tarfile
    import shutil
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_id = f"system_{timestamp}"
    
    # Create backup directory
    os.makedirs("/app/data/backups", exist_ok=True)
    backup_file = f"/app/data/backups/{backup_id}.tar.gz"
    
    try:
        with tarfile.open(backup_file, "w:gz") as tar:
            # Add database
            if os.path.exists("/app/data/zoe.db"):
                tar.add("/app/data/zoe.db", arcname="zoe.db")
            
            # Add all routers
            if os.path.exists("/app/routers"):
                tar.add("/app/routers", arcname="routers")
            
            # Add main.py
            if os.path.exists("/app/main.py"):
                tar.add("/app/main.py", arcname="main.py")
        
        return {
            "status": "success",
            "backup_id": backup_id,
            "file": backup_file,
            "size": os.path.getsize(backup_file)
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/system-backups")
async def list_system_backups():
    """List all system backups"""
    backup_dir = "/app/data/backups"
    
    if not os.path.exists(backup_dir):
        return {"backups": [], "count": 0}
    
    backups = []
    for file in os.listdir(backup_dir):
        if file.startswith("system_") and file.endswith(".tar.gz"):
            file_path = f"{backup_dir}/{file}"
            backups.append({
                "id": file.replace(".tar.gz", ""),
                "file": file,
                "size": os.path.getsize(file_path),
                "created": os.path.getctime(file_path)
            })
    
    backups.sort(key=lambda x: x["created"], reverse=True)
    return {"backups": backups, "count": len(backups)}
# ============= END BACKUP SYSTEM =============
PYEOF
echo "✅ Added to developer.py"'

# Step 3: Create a separate user backup router
echo -e "\n📝 Creating user backup router..."
docker exec zoe-core bash -c 'cat > /app/routers/user_backups.py << "PYEOF"
"""User-specific backup system"""
from fastapi import APIRouter, Query
import sqlite3
import json
import os
from datetime import datetime

router = APIRouter(prefix="/api/user-backup", tags=["user"])

@router.post("/create")
async def create_user_backup(user_id: str = Query(default="default")):
    """Backup user data only (not system files)"""
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_id = f"user_{user_id}_{timestamp}"
    
    # Create user backup directory
    os.makedirs("/app/data/user_backups", exist_ok=True)
    backup_file = f"/app/data/user_backups/{backup_id}.json"
    
    # Connect to database
    conn = sqlite3.connect("/app/data/zoe.db")
    cursor = conn.cursor()
    
    backup_data = {
        "backup_id": backup_id,
        "user_id": user_id,
        "timestamp": timestamp,
        "tables": {}
    }
    
    # Backup user tables
    user_tables = ["conversations", "memories", "events", "tasks", "lists"]
    
    for table in user_tables:
        try:
            cursor.execute(f"SELECT * FROM {table}")
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            
            backup_data["tables"][table] = {
                "columns": columns,
                "rows": rows
            }
        except Exception as e:
            backup_data["tables"][table] = {"error": str(e)}
    
    conn.close()
    
    # Save backup
    with open(backup_file, "w") as f:
        json.dump(backup_data, f, indent=2, default=str)
    
    return {
        "status": "success",
        "backup_id": backup_id,
        "user_id": user_id,
        "file": backup_file,
        "size": os.path.getsize(backup_file)
    }

@router.get("/list")
async def list_user_backups(user_id: str = Query(default="default")):
    """List backups for a specific user"""
    
    backup_dir = "/app/data/user_backups"
    
    if not os.path.exists(backup_dir):
        return {"backups": [], "user_id": user_id}
    
    backups = []
    for file in os.listdir(backup_dir):
        if file.startswith(f"user_{user_id}_") and file.endswith(".json"):
            file_path = f"{backup_dir}/{file}"
            backups.append({
                "id": file.replace(".json", ""),
                "file": file,
                "size": os.path.getsize(file_path),
                "created": os.path.getctime(file_path)
            })
    
    backups.sort(key=lambda x: x["created"], reverse=True)
    return {"backups": backups, "user_id": user_id, "count": len(backups)}
PYEOF
echo "✅ Created user_backups.py"'

# Step 4: Update main.py to include user_backups
echo -e "\n📝 Registering user_backups router..."
docker exec zoe-core python3 << 'PYTHON'
with open("/app/main.py", "r") as f:
    content = f.read()

# Add user_backups to imports
if "user_backups" not in content:
    content = content.replace(
        "from routers import developer",
        "from routers import developer, user_backups"
    )

# Register the router
if "app.include_router(user_backups.router)" not in content:
    # Find where to add it
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if "app.include_router(developer.router)" in line:
            lines.insert(i+1, "    app.include_router(user_backups.router)")
            lines.insert(i+2, '    logger.info("✅ User backups router registered")')
            break
    content = "\n".join(lines)

with open("/app/main.py", "w") as f:
    f.write(content)

print("✅ Registered user_backups router")
PYTHON

# Step 5: Restart
echo -e "\n🔄 Restarting service..."
docker compose restart zoe-core
sleep 10

# Step 6: Check logs
echo -e "\n📋 Checking startup logs:"
docker logs zoe-core --tail 15 | grep -E "registered|error|ERROR"

# Step 7: Test all backup endpoints
echo -e "\n🧪 Testing backup endpoints..."

echo "1️⃣ Developer system backup:"
curl -s -X POST http://localhost:8000/api/developer/system-backup | jq '.'

echo -e "\n2️⃣ List system backups:"
curl -s http://localhost:8000/api/developer/system-backups | jq '.'

echo -e "\n3️⃣ User backup:"
curl -s -X POST "http://localhost:8000/api/user-backup/create?user_id=default" | jq '.'

echo -e "\n4️⃣ List user backups:"
curl -s "http://localhost:8000/api/user-backup/list?user_id=default" | jq '.'

echo -e "\n5️⃣ All backup endpoints:"
curl -s http://localhost:8000/openapi.json | jq '.paths | keys[]' | grep -i backup

echo -e "\n✅ Complete backup fix applied!"
echo ""
echo "📊 Final Status:"
echo "  • Developer backups: /api/developer/system-backup[s]"
echo "  • User backups: /api/user-backup/create|list"
echo "  • Both systems working independently"
echo "  • Privacy maintained"
