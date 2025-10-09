from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import json
import os
from .auth import get_current_user


DB_PATH = "/app/data/zoe.db"
LEGACY_DB_PATH = "/app/data/zoe.db"


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class Task(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    status: str
    priority: str
    assigned_to: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    tags: List[str] = []


def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    os.makedirs("/app/data", exist_ok=True)
    conn = _connect()
    cur = conn.cursor()
    # Create using existing schema expectations (task_id + metadata) if missing
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            type TEXT DEFAULT 'feature',
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'pending',
            assigned_to TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            approved INTEGER DEFAULT 0,
            code_generated TEXT,
            implementation_path TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _migrate_legacy_tasks() -> None:
    if not os.path.exists(LEGACY_DB_PATH):
        return
    try:
        legacy = sqlite3.connect(LEGACY_DB_PATH)
        legacy.row_factory = sqlite3.Row
        lcur = legacy.cursor()

        # Determine if legacy table exists
        lcur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dynamic_tasks'")
        if lcur.fetchone() is None:
            legacy.close()
            return

        lcur.execute(
            """
            SELECT id, title, objective, priority, assigned_to, status, created_at, completed_at
            FROM dynamic_tasks
            """
        )
        rows = lcur.fetchall()
        legacy.close()

        if not rows:
            return

        conn = _connect()
        cur = conn.cursor()

        # Insert if not exists by task_id
        for r in rows:
            task_id = r["id"]
            cur.execute("PRAGMA table_info(tasks)")
            cols = [c[1] for c in cur.fetchall()]
            key_col = "task_id" if "task_id" in cols else ("id" if "id" in cols else None)
            if key_col is None:
                continue
            cur.execute(f"SELECT 1 FROM tasks WHERE {key_col} = ?", (task_id,))
            exists = cur.fetchone() is not None
            if exists:
                continue
            if "task_id" in cols:
                cur.execute(
                    """
                    INSERT INTO tasks (task_id, title, description, priority, status, assigned_to, created_at, completed_at, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r["id"],
                        r["title"],
                        r["objective"],
                        r["priority"] or "medium",
                        r["status"] or "pending",
                        r["assigned_to"],
                        (r["created_at"] if r["created_at"] else datetime.utcnow().isoformat()),
                        r["completed_at"],
                        json.dumps({"tags": []}),
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO tasks (id, title, description, status, priority, assigned_to, created_at, completed_at, tags)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r["id"],
                        r["title"],
                        r["objective"],
                        r["status"] or "pending",
                        r["priority"] or "medium",
                        r["assigned_to"],
                        (r["created_at"] if r["created_at"] else datetime.utcnow().isoformat()),
                        r["completed_at"],
                        json.dumps([]),
                    ),
                )
        conn.commit()
        conn.close()
    except Exception:
        # Best-effort migration; ignore errors
        pass


# Initialize database and attempt migration at import
_init_db()
_migrate_legacy_tasks()


@router.post("/complete/{task_id}")
async def complete_task(task_id: str, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(tasks)")
    cols = [c[1] for c in cur.fetchall()]
    key_col = "task_id" if "task_id" in cols else "id"
    # Ensure user_id column exists then filter by user
    has_user = "user_id" in cols
    if has_user:
        cur.execute(
            f"UPDATE tasks SET status = 'completed', completed_at = ? WHERE {key_col} = ? AND user_id = ?",
            (datetime.utcnow().isoformat(), task_id, user.get("user_id", "default")),
        )
    else:
        cur.execute(
            f"UPDATE tasks SET status = 'completed', completed_at = ? WHERE {key_col} = ?",
            (datetime.utcnow().isoformat(), task_id),
        )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")
    conn.commit()
    conn.close()
    return {"id": task_id, "status": "completed"}


@router.get("/list")
async def list_tasks(status: Optional[str] = None, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(tasks)")
    cols = [c[1] for c in cur.fetchall()]
    uses_metadata = "metadata" in cols
    id_col = "task_id" if "task_id" in cols else "id"
    base = f"SELECT {id_col} as id, title, description, status, priority, assigned_to, created_at, completed_at, " + ("metadata" if uses_metadata else "tags") + " as extras FROM tasks"
    params: List[Any] = []
    has_user = "user_id" in cols
    where_parts: List[str] = []
    if has_user:
        where_parts.append("user_id = ?")
        params.append(user.get("user_id", "default"))
    if status:
        where_parts.append("status = ?")
        params.append(status)
    if where_parts:
        base += " WHERE " + " AND ".join(where_parts)
    base += " ORDER BY datetime(created_at) DESC"
    cur.execute(base, params)
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        if uses_metadata:
            meta = json.loads(r.get("extras") or "{}")
            r["tags"] = meta.get("tags", [])
        else:
            r["tags"] = json.loads(r.get("extras") or "[]")
        r.pop("extras", None)
    conn.close()
    return {"tasks": rows, "count": len(rows)}


@router.get("/next")
async def next_task(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(tasks)")
    cols = [c[1] for c in cur.fetchall()]
    uses_metadata = "metadata" in cols
    id_col = "task_id" if "task_id" in cols else "id"
    if "user_id" in cols:
        cur.execute(
            f"""
            SELECT {id_col} as id, title, description, status, priority, assigned_to, created_at, completed_at, " + ("metadata" if uses_metadata else "tags") + " as extras
            FROM tasks
            WHERE status = 'pending' AND user_id = ?
            ORDER BY 
                CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
                datetime(created_at) ASC
            LIMIT 1
            """,
            (user.get("user_id", "default"),),
        )
    else:
        cur.execute(
            f"""
            SELECT {id_col} as id, title, description, status, priority, assigned_to, created_at, completed_at, " + ("metadata" if uses_metadata else "tags") + " as extras
            FROM tasks
            WHERE status = 'pending'
            ORDER BY 
                CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
                datetime(created_at) ASC
            LIMIT 1
            """
        )
    row = cur.fetchone()
    conn.close()
    if not row:
        return {"message": "No pending tasks"}
    task = dict(row)
    if uses_metadata:
        meta = json.loads(task.get("extras") or "{}")
        task["tags"] = meta.get("tags", [])
    else:
        task["tags"] = json.loads(task.get("extras") or "[]")
    task.pop("extras", None)
    return task


@router.post("/claim/{task_id}")
async def claim_task(task_id: str, assignee: Optional[str] = "zack", user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(tasks)")
    cols = [c[1] for c in cur.fetchall()]
    key_col = "task_id" if "task_id" in cols else "id"
    if "user_id" in cols:
        cur.execute(
            f"UPDATE tasks SET status = 'in_progress', assigned_to = ? WHERE {key_col} = ? AND status IN ('pending','blocked') AND user_id = ?",
            (assignee, task_id, user.get("user_id", "default")),
        )
    else:
        cur.execute(
            f"UPDATE tasks SET status = 'in_progress', assigned_to = ? WHERE {key_col} = ? AND status IN ('pending','blocked')",
            (assignee, task_id),
        )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Task not found or not claimable")
    conn.commit()
    conn.close()
    return {"id": task_id, "status": "in_progress", "assigned_to": assignee}


@router.post("/unclaim/{task_id}")
async def unclaim_task(task_id: str, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(tasks)")
    cols = [c[1] for c in cur.fetchall()]
    key_col = "task_id" if "task_id" in cols else "id"
    if "user_id" in cols:
        cur.execute(
            f"UPDATE tasks SET status = 'pending', assigned_to = NULL WHERE {key_col} = ? AND status = 'in_progress' AND user_id = ?",
            (task_id, user.get("user_id", "default")),
        )
    else:
        cur.execute(
            f"UPDATE tasks SET status = 'pending', assigned_to = NULL WHERE {key_col} = ? AND status = 'in_progress'",
            (task_id,),
        )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Task not found or not in progress")
    conn.commit()
    conn.close()
    return {"id": task_id, "status": "pending"}


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    completed_at: Optional[datetime] = None
    tags: Optional[List[str]] = None


@router.put("/{task_id}")
async def update_task(task_id: str, update: TaskUpdate, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    if update.title is not None:
        fields["title"] = update.title
    if update.description is not None:
        fields["description"] = update.description
    if update.status is not None:
        fields["status"] = update.status
    if update.priority is not None:
        fields["priority"] = update.priority
    if update.assigned_to is not None:
        fields["assigned_to"] = update.assigned_to
    if update.completed_at is not None:
        fields["completed_at"] = update.completed_at.isoformat()
    # Handle tags via metadata or tags column
    cur = None
    conn = _connect()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(tasks)")
    cols = [c[1] for c in cur.fetchall()]
    uses_metadata = "metadata" in cols
    if update.tags is not None:
        if uses_metadata:
            # Fetch existing metadata and merge
            cur.execute("SELECT metadata FROM tasks WHERE " + ("task_id" if "task_id" in cols else "id") + " = ?", (task_id,))
            row = cur.fetchone()
            meta = {}
            if row and row[0]:
                try:
                    meta = json.loads(row[0])
                except Exception:
                    meta = {}
            meta["tags"] = update.tags
            fields["metadata"] = json.dumps(meta)
        else:
            fields["tags"] = json.dumps(update.tags)

    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Build and execute update
    sets = ", ".join([f"{k} = ?" for k in fields.keys()])
    params = list(fields.values()) + [task_id]
    key_col = "task_id" if "task_id" in cols else "id"
    if "user_id" in cols:
        cur.execute(f"UPDATE tasks SET {sets} WHERE {key_col} = ? AND user_id = ?", params + [user.get("user_id", "default")])
    else:
        cur.execute(f"UPDATE tasks SET {sets} WHERE {key_col} = ?", params)
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")
    conn.commit()
    cur.execute("SELECT " + key_col + " as id, title, description, status, priority, assigned_to, created_at, completed_at, " + ("metadata" if uses_metadata else "tags") + " as extras FROM tasks WHERE " + key_col + " = ?", (task_id,))
    row = cur.fetchone()
    conn.close()
    task = dict(row)
    if uses_metadata:
        meta = json.loads(task.get("extras") or "{}")
        task["tags"] = meta.get("tags", [])
    else:
        task["tags"] = json.loads(task.get("extras") or "[]")
    task.pop("extras", None)
    return task


@router.delete("/{task_id}")
async def delete_task(task_id: str, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(tasks)")
    cols = [c[1] for c in cur.fetchall()]
    key_col = "task_id" if "task_id" in cols else "id"
    if "user_id" in cols:
        cur.execute(f"DELETE FROM tasks WHERE {key_col} = ? AND user_id = ?", (task_id, user.get("user_id", "default")))
    else:
        cur.execute(f"DELETE FROM tasks WHERE {key_col} = ?", (task_id,))
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")
    conn.commit()
    conn.close()
    return {"id": task_id, "deleted": True}


@router.get("/stats")
async def task_stats(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()
    # By status
    try:
        cur.execute("PRAGMA table_info(tasks)")
        cols = [c[1] for c in cur.fetchall()]
        if "user_id" in cols:
            cur.execute("SELECT status, COUNT(*) as count FROM tasks WHERE user_id = ? GROUP BY status", (user.get("user_id", "default"),))
        else:
            cur.execute("SELECT status, COUNT(*) as count FROM tasks GROUP BY status")
    except Exception:
        cur.execute("SELECT status, COUNT(*) as count FROM tasks GROUP BY status")
    by_status = {row["status"]: row["count"] for row in cur.fetchall()}
    # By priority
    try:
        if "user_id" in cols:
            cur.execute("SELECT priority, COUNT(*) as count FROM tasks WHERE user_id = ? GROUP BY priority", (user.get("user_id", "default"),))
        else:
            cur.execute("SELECT priority, COUNT(*) as count FROM tasks GROUP BY priority")
    except Exception:
        cur.execute("SELECT priority, COUNT(*) as count FROM tasks GROUP BY priority")
    by_priority = {row["priority"]: row["count"] for row in cur.fetchall()}
    # Totals
    try:
        if "user_id" in cols:
            cur.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user.get("user_id", "default"),))
        else:
            cur.execute("SELECT COUNT(*) FROM tasks")
    except Exception:
        cur.execute("SELECT COUNT(*) FROM tasks")
    total = cur.fetchone()[0]
    conn.close()
    return {
        "total": total,
        "by_status": by_status,
        "by_priority": by_priority,
    }


