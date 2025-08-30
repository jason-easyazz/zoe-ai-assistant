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
