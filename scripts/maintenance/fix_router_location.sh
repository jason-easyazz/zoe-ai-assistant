#!/bin/bash
# FIX_ROUTER_LOCATION.sh - Find and fix router locations

echo "🔍 FINDING ACTUAL ROUTER LOCATION"
echo "================================="

cd /home/pi/zoe

# Step 1: Find where routers actually are
echo "📋 Searching for router files..."
docker exec zoe-core find /app -name "*.py" -path "*/router*" -type f 2>/dev/null | head -10

echo -e "\n📋 Checking for developer.py location:"
docker exec zoe-core find /app -name "developer.py" 2>/dev/null

echo -e "\n📋 Checking directory structure:"
docker exec zoe-core ls -la /app/ | grep -E "router|Router"

echo -e "\n📋 Looking at Python imports in main.py:"
docker exec zoe-core grep -E "from|import" /app/main.py | head -10

# Step 2: Create backup router in the CORRECT location
echo -e "\n🔧 Creating backup router in correct location..."

# First, let's check if it's a flat structure (all in /app)
docker exec zoe-core bash -c '
# Check where developer.py actually is
DEV_FILE=$(find /app -name "developer.py" -type f | head -1)
if [ -n "$DEV_FILE" ]; then
    ROUTER_DIR=$(dirname "$DEV_FILE")
    echo "Found router directory: $ROUTER_DIR"
    
    # Create backup.py in the same directory
    cat > "$ROUTER_DIR/backup.py" << "PYEOF"
"""Backup System Router"""
from fastapi import APIRouter, HTTPException
import shutil
import os
import tarfile
from datetime import datetime

router = APIRouter(prefix="/api/backup", tags=["backup"])

@router.post("/create")
async def create_backup():
    """Create a backup of the database"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = "/app/data/backups"
        os.makedirs(backup_dir, exist_ok=True)
        
        # Create backup file
        backup_file = f"{backup_dir}/backup_{timestamp}.tar.gz"
        
        # Create a temporary directory for files to backup
        temp_dir = f"/tmp/backup_{timestamp}"
        os.makedirs(temp_dir, exist_ok=True)
        
        # Copy database
        if os.path.exists("/app/data/zoe.db"):
            shutil.copy2("/app/data/zoe.db", f"{temp_dir}/zoe.db")
        
        # Create tarball
        with tarfile.open(backup_file, "w:gz") as tar:
            tar.add(temp_dir, arcname=f"backup_{timestamp}")
        
        # Cleanup temp directory
        shutil.rmtree(temp_dir)
        
        return {
            "status": "success",
            "backup_id": f"backup_{timestamp}",
            "file": backup_file,
            "size": os.path.getsize(backup_file)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/list")
async def list_backups():
    """List all available backups"""
    backup_dir = "/app/data/backups"
    
    if not os.path.exists(backup_dir):
        return {"backups": [], "count": 0}
    
    backups = []
    for file in os.listdir(backup_dir):
        if file.endswith(".tar.gz"):
            file_path = f"{backup_dir}/{file}"
            backups.append({
                "id": file.replace(".tar.gz", ""),
                "file": file,
                "size": os.path.getsize(file_path),
                "created": os.path.getctime(file_path)
            })
    
    return {
        "backups": sorted(backups, key=lambda x: x["created"], reverse=True),
        "count": len(backups)
    }

@router.delete("/{backup_id}")
async def delete_backup(backup_id: str):
    """Delete a specific backup"""
    backup_dir = "/app/data/backups"
    backup_file = f"{backup_dir}/{backup_id}.tar.gz"
    
    if not os.path.exists(backup_file):
        raise HTTPException(status_code=404, detail="Backup not found")
    
    os.remove(backup_file)
    return {"status": "deleted", "backup_id": backup_id}
PYEOF
    
    echo "✅ Created backup.py in $ROUTER_DIR"
    ls -la "$ROUTER_DIR/backup.py"
else
    echo "❌ Could not find router directory"
fi
'

# Step 3: Update main.py to import backup
echo -e "\n📝 Updating main.py to include backup router..."
docker exec zoe-core python3 << 'PYTHON'
import os
import re

# Read main.py
with open("/app/main.py", "r") as f:
    content = f.read()

# Check current import style
if "from routers import" in content:
    # Module-based import
    if "backup" not in content:
        content = re.sub(
            r"(from routers import [^)]+)",
            r"\1, backup",
            content
        )
    
    # Add router registration
    if "app.include_router(backup.router)" not in content:
        # Find last include_router and add after it
        lines = content.split("\n")
        for i in range(len(lines)-1, -1, -1):
            if "app.include_router" in lines[i]:
                lines.insert(i+1, "app.include_router(backup.router)")
                break
        content = "\n".join(lines)
        
elif "import " in content and "developer" in content:
    # Direct imports (files in /app)
    if "import backup" not in content:
        # Add after developer import
        content = content.replace(
            "import developer",
            "import developer\nimport backup"
        )
    
    # Add router registration
    if "app.include_router(backup.router)" not in content:
        content = content.replace(
            "app.include_router(developer.router)",
            "app.include_router(developer.router)\napp.include_router(backup.router)"
        )

# Write back
with open("/app/main.py", "w") as f:
    f.write(content)

print("✅ Updated main.py")

# Show imports and registrations
print("\n📋 Current configuration:")
with open("/app/main.py", "r") as f:
    for line in f:
        if "import" in line and ("backup" in line or "developer" in line):
            print(f"  Import: {line.strip()}")
        if "include_router" in line and ("backup" in line or "developer" in line):
            print(f"  Router: {line.strip()}")
PYTHON

# Step 4: Restart
echo -e "\n🔄 Restarting service..."
docker compose restart zoe-core
sleep 8

# Step 5: Test
echo -e "\n🧪 Testing backup endpoints..."
echo "1️⃣ Create backup:"
curl -s -X POST http://localhost:8000/api/backup/create | jq '.'

echo -e "\n2️⃣ List backups:"
curl -s http://localhost:8000/api/backup/list | jq '.'

echo -e "\n3️⃣ Check all routes:"
curl -s http://localhost:8000/openapi.json | jq '.paths | keys[]' | grep backup || echo "Still not found"

echo -e "\n✅ Router location fixed!"
