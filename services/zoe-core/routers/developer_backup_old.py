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
