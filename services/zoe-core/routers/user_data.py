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
