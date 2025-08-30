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
                        f"INSERT INTO {table_name} ({,.join(table_data[columns])}) VALUES ({placeholders})",
                        row
                    )
                restored_tables.append(f"{table_name} ({len(table_data[data])} records)")
            
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
