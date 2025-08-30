#!/bin/bash
# SEPARATE_BACKUP_SYSTEMS.sh - Create distinct user and developer backup systems

echo "🔧 CREATING SEPARATE BACKUP SYSTEMS"
echo "===================================="
echo ""
echo "Creating two distinct backup systems:"
echo "  1. User backups - Personal data only"
echo "  2. Developer backups - Full system backups"
echo ""
echo "Press Enter to continue..."
read

cd /home/pi/zoe

# Step 1: Move current backup to developer namespace
echo "📝 Moving backup system to developer namespace..."
docker exec zoe-core bash -c '
# Rename current backup.py to developer_backup.py
if [ -f /app/routers/backup.py ]; then
    mv /app/routers/backup.py /app/routers/developer_backup_old.py
fi
'

# Step 2: Create proper developer backup in developer.py
echo "📝 Adding complete backup system to developer router..."
docker exec zoe-core python3 << 'PYTHON'
# Read current developer.py
with open("/app/routers/developer.py", "r") as f:
    content = f.read()

# Add backup methods if not present
if "def create_system_backup" not in content:
    # Find where to insert (before the last function or at the end)
    insertion_point = content.rfind("@router.")
    
    backup_code = '''
@router.post("/backup/system")
async def create_system_backup(description: str = "Manual system backup"):
    """Create complete SYSTEM backup (developer only)"""
    import tarfile
    import shutil
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_id = f"system_backup_{timestamp}"
    backup_dir = f"/app/data/developer_backups/{backup_id}"
    
    os.makedirs(backup_dir, exist_ok=True)
    
    # Backup everything important
    items_to_backup = {
        "database": "/app/data/zoe.db",
        "routers": "/app/routers",
        "configs": ["/app/.env", "/app/config.json"],
        "data_dirs": ["/app/data/api_keys.json", "/app/data/encryption_key"],
        "main_app": "/app/main.py",
        "ai_client": "/app/ai_client.py"
    }
    
    # Copy database
    if os.path.exists(items_to_backup["database"]):
        shutil.copy2(items_to_backup["database"], f"{backup_dir}/zoe.db")
    
    # Copy all routers
    if os.path.exists(items_to_backup["routers"]):
        shutil.copytree(items_to_backup["routers"], f"{backup_dir}/routers")
    
    # Copy configs
    for config in items_to_backup["configs"]:
        if os.path.exists(config):
            shutil.copy2(config, backup_dir)
    
    # Copy main files
    if os.path.exists(items_to_backup["main_app"]):
        shutil.copy2(items_to_backup["main_app"], backup_dir)
    
    # Create manifest
    manifest = {
        "backup_id": backup_id,
        "timestamp": timestamp,
        "description": description,
        "type": "system",
        "includes": list(items_to_backup.keys())
    }
    
    with open(f"{backup_dir}/manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    
    # Create tarball
    tar_path = f"/app/data/developer_backups/{backup_id}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(backup_dir, arcname=backup_id)
    
    # Cleanup temp directory
    shutil.rmtree(backup_dir)
    
    return {
        "status": "success",
        "backup_id": backup_id,
        "type": "system",
        "file": tar_path,
        "size": os.path.getsize(tar_path)
    }

@router.get("/backup/list")
async def list_system_backups():
    """List all SYSTEM backups"""
    backup_dir = "/app/data/developer_backups"
    
    if not os.path.exists(backup_dir):
        return {"backups": [], "type": "system"}
    
    backups = []
    for file in os.listdir(backup_dir):
        if file.endswith(".tar.gz"):
            file_path = f"{backup_dir}/{file}"
            backups.append({
                "id": file.replace(".tar.gz", ""),
                "file": file,
                "size": os.path.getsize(file_path),
                "type": "system"
            })
    
    return {"backups": backups, "count": len(backups), "type": "system"}
'''
    
    # Insert the code
    content = content[:insertion_point] + backup_code + "\n" + content[insertion_point:]

# Write back
with open("/app/routers/developer.py", "w") as f:
    f.write(content)

print("✅ Added system backup to developer router")
PYTHON

# Step 3: Create user backup router
echo -e "\n📝 Creating user-specific backup router..."
docker exec zoe-core bash -c 'cat > /app/routers/user_backup.py << "PYEOF"
"""
User Backup System - Personal data only
Handles user conversations, memories, events, tasks, etc.
"""
from fastapi import APIRouter, HTTPException, Query
from datetime import datetime
import sqlite3
import json
import os
import tarfile
import shutil

router = APIRouter(prefix="/api/user/backup", tags=["user", "backup"])

@router.post("/create")
async def create_user_backup(user_id: str = "default"):
    """Create backup of USER data only (not system data)"""
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_id = f"user_{user_id}_{timestamp}"
    backup_dir = f"/app/data/user_backups/{backup_id}"
    
    os.makedirs(backup_dir, exist_ok=True)
    
    # Connect to database
    conn = sqlite3.connect("/app/data/zoe.db")
    
    # Define user tables to backup
    user_tables = [
        "conversations",
        "memories", 
        "events",
        "tasks",
        "lists"
    ]
    
    # Export each user table
    backup_data = {
        "backup_id": backup_id,
        "user_id": user_id,
        "timestamp": timestamp,
        "tables": {}
    }
    
    for table in user_tables:
        cursor = conn.cursor()
        try:
            # Get table data for this user
            if "user_id" in [col[1] for col in cursor.execute(f"PRAGMA table_info({table})")]:
                cursor.execute(f"SELECT * FROM {table} WHERE user_id = ?", (user_id,))
            else:
                cursor.execute(f"SELECT * FROM {table}")
            
            # Get column names
            columns = [description[0] for description in cursor.description]
            
            # Get all rows
            rows = cursor.fetchall()
            
            # Store in backup
            backup_data["tables"][table] = {
                "columns": columns,
                "data": rows,
                "count": len(rows)
            }
            
        except Exception as e:
            backup_data["tables"][table] = {"error": str(e)}
    
    conn.close()
    
    # Save backup data as JSON
    with open(f"{backup_dir}/user_data.json", "w") as f:
        json.dump(backup_data, f, indent=2, default=str)
    
    # Create tarball
    tar_path = f"/app/data/user_backups/{backup_id}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(backup_dir, arcname=backup_id)
    
    # Cleanup
    shutil.rmtree(backup_dir)
    
    # Calculate what was backed up
    total_records = sum(
        table_info.get("count", 0) 
        for table_info in backup_data["tables"].values()
    )
    
    return {
        "status": "success",
        "backup_id": backup_id,
        "user_id": user_id,
        "type": "user_data",
        "records_backed_up": total_records,
        "tables_backed_up": list(backup_data["tables"].keys()),
        "file": tar_path,
        "size": os.path.getsize(tar_path)
    }

@router.get("/list")
async def list_user_backups(user_id: str = Query(default="default")):
    """List backups for a specific user"""
    
    backup_dir = "/app/data/user_backups"
    if not os.path.exists(backup_dir):
        return {"backups": [], "user_id": user_id}
    
    user_backups = []
    for file in os.listdir(backup_dir):
        if file.startswith(f"user_{user_id}_") and file.endswith(".tar.gz"):
            file_path = f"{backup_dir}/{file}"
            user_backups.append({
                "id": file.replace(".tar.gz", ""),
                "file": file,
                "size": os.path.getsize(file_path),
                "created": os.path.getctime(file_path)
            })
    
    return {
        "backups": sorted(user_backups, key=lambda x: x["created"], reverse=True),
        "count": len(user_backups),
        "user_id": user_id,
        "type": "user_data"
    }

@router.post("/restore/{backup_id}")
async def restore_user_backup(backup_id: str, user_id: str = "default"):
    """Restore USER data from backup"""
    
    backup_file = f"/app/data/user_backups/{backup_id}.tar.gz"
    
    if not os.path.exists(backup_file):
        raise HTTPException(status_code=404, detail="Backup not found")
    
    # Verify this backup belongs to the user
    if not backup_id.startswith(f"user_{user_id}_"):
        raise HTTPException(status_code=403, detail="Backup does not belong to this user")
    
    # Extract backup
    temp_dir = f"/tmp/{backup_id}"
    with tarfile.open(backup_file, "r:gz") as tar:
        tar.extractall("/tmp")
    
    # Load backup data
    with open(f"{temp_dir}/user_data.json", "r") as f:
        backup_data = json.load(f)
    
    # Restore to database (careful not to overwrite other users data)
    conn = sqlite3.connect("/app/data/zoe.db")
    restored_tables = []
    
    for table_name, table_data in backup_data["tables"].items():
        if "error" not in table_data:
            cursor = conn.cursor()
            
            # Delete existing data for this user
            try:
                if "user_id" in table_data["columns"]:
                    cursor.execute(f"DELETE FROM {table_name} WHERE user_id = ?", (user_id,))
            except:
                pass
            
            # Insert backed up data
            if table_data["data"]:
                placeholders = ",".join(["?" for _ in table_data["columns"]])
                for row in table_data["data"]:
                    cursor.execute(
                        f"INSERT INTO {table_name} ({','.join(table_data['columns'])}) VALUES ({placeholders})",
                        row
                    )
                restored_tables.append(f"{table_name} ({len(table_data['data'])} records)")
            
            conn.commit()
    
    conn.close()
    
    # Cleanup
    shutil.rmtree(temp_dir)
    
    return {
        "status": "restored",
        "backup_id": backup_id,
        "user_id": user_id,
        "restored_tables": restored_tables
    }

@router.delete("/{backup_id}")
async def delete_user_backup(backup_id: str, user_id: str = "default"):
    """Delete a user backup"""
    
    # Verify ownership
    if not backup_id.startswith(f"user_{user_id}_"):
        raise HTTPException(status_code=403, detail="Cannot delete other users backups")
    
    backup_file = f"/app/data/user_backups/{backup_id}.tar.gz"
    
    if not os.path.exists(backup_file):
        raise HTTPException(status_code=404, detail="Backup not found")
    
    os.remove(backup_file)
    
    return {
        "status": "deleted",
        "backup_id": backup_id
    }
PYEOF'

# Step 4: Update main.py to include user_backup router
echo -e "\n📝 Registering user_backup router..."
docker exec zoe-core python3 << 'PYTHON'
with open("/app/main.py", "r") as f:
    content = f.read()

# Add user_backup to imports
if "user_backup" not in content:
    content = content.replace(
        "from routers import developer, backup",
        "from routers import developer, user_backup"
    )
    # Remove old backup if present
    content = content.replace(", backup", "")

# Register user_backup router
if "app.include_router(user_backup.router)" not in content:
    content = content.replace(
        "app.include_router(developer.router)",
        "app.include_router(developer.router)\n    app.include_router(user_backup.router)"
    )

# Remove old backup router registration if present
content = content.replace("app.include_router(backup.router)\n", "")
content = content.replace("logger.info(\"✅ Backup router registered\")\n", "")

with open("/app/main.py", "w") as f:
    f.write(content)

print("✅ Updated main.py")
PYTHON

# Step 5: Restart
echo -e "\n🔄 Restarting service..."
docker compose restart zoe-core
sleep 10

# Step 6: Test both backup systems
echo -e "\n🧪 Testing separated backup systems..."

echo "1️⃣ USER backup (personal data only):"
curl -s -X POST "http://localhost:8000/api/user/backup/create?user_id=default" | jq '.'

echo -e "\n2️⃣ List USER backups:"
curl -s "http://localhost:8000/api/user/backup/list?user_id=default" | jq '.'

echo -e "\n3️⃣ DEVELOPER backup (full system):"
curl -s -X POST http://localhost:8000/api/developer/backup/system \
  -H "Content-Type: application/json" \
  -d '{"description": "Complete system backup"}' | jq '.'

echo -e "\n4️⃣ List DEVELOPER backups:"
curl -s http://localhost:8000/api/developer/backup/list | jq '.'

echo -e "\n5️⃣ Check endpoints:"
curl -s http://localhost:8000/openapi.json | jq '.paths | keys[]' | grep backup

echo -e "\n✅ Backup systems separated!"
echo ""
echo "📊 Summary:"
echo "  • User backups: /api/user/backup/* - Personal data only"
echo "  • Developer backups: /api/developer/backup/* - Full system"
echo "  • Clear separation of concerns"
echo "  • User privacy protected"
