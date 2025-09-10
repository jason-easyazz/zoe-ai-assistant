from fastapi import APIRouter, Depends
from datetime import datetime
import os
import sqlite3
import tarfile
import json
from .auth import get_current_user

router = APIRouter(prefix="/api/backup", tags=["backup"])

DB_PATH = "/app/data/zoe.db"


@router.post("/user/create")
async def create_user_backup(user = Depends(get_current_user)):
    """Create backup for current user's data only"""
    user_id = user.get("user_id", "default")
    backup_dir = f"/app/data/users/{user_id}/backups"
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{backup_dir}/backup_{timestamp}.tar.gz"

    # Collect user-scoped data from DB
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    export = {"user_id": user_id, "timestamp": timestamp, "tables": {}}
    tables = ["events", "lists", "memories", "tasks", "conversations"]
    for table in tables:
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            cols = [c[1] for c in cursor.fetchall()]
            if not cols:
                continue
            if "user_id" in cols:
                cursor.execute(f"SELECT * FROM {table} WHERE user_id = ?", (user_id,))
            else:
                cursor.execute(f"SELECT * FROM {table}")
            rows = cursor.fetchall()
            export["tables"][table] = {
                "columns": cols,
                "rows": [dict(r) for r in rows],
                "count": len(rows),
            }
        except Exception as e:
            export["tables"][table] = {"error": str(e)}

    conn.close()

    # Write export JSON to a temp file in user's backup dir
    json_path = f"{backup_dir}/backup_{timestamp}.json"
    with open(json_path, "w") as f:
        json.dump(export, f, indent=2, default=str)

    # Create tar.gz containing the JSON
    with tarfile.open(backup_file, "w:gz") as tar:
        tar.add(json_path, arcname=os.path.basename(json_path))

    # Remove the loose JSON after archiving
    try:
        os.remove(json_path)
    except Exception:
        pass

    return {"backup_id": timestamp, "path": backup_file, "size": os.path.getsize(backup_file)}


@router.get("/user/list")
async def list_user_backups(user = Depends(get_current_user)):
    """List backups for current user only"""
    user_id = user.get("user_id", "default")
    backup_dir = f"/app/data/users/{user_id}/backups"
    if not os.path.exists(backup_dir):
        return {"backups": [], "count": 0}

    backups = []
    for name in os.listdir(backup_dir):
        if name.startswith("backup_") and name.endswith(".tar.gz"):
            full = os.path.join(backup_dir, name)
            backups.append({
                "file": name,
                "path": full,
                "size": os.path.getsize(full),
                "created": os.path.getctime(full),
            })
    backups.sort(key=lambda x: x["created"], reverse=True)
    return {"backups": backups, "count": len(backups)}






