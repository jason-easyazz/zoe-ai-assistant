from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel
from enum import Enum
from typing import Optional, Dict, Any, List, Set
from datetime import datetime
import sqlite3
import asyncio
from fastapi.responses import StreamingResponse


class NotificationPriority(str, Enum):
    AMBIENT = "ambient"
    IMPORTANT = "important"
    CRITICAL = "critical"


class NotificationCreate(BaseModel):
    title: str
    message: str
    priority: NotificationPriority
    action_url: Optional[str] = None
    dismissible: bool = True
    metadata: Optional[Dict[str, Any]] = None


class Notification(BaseModel):
    id: int
    title: str
    message: str
    priority: NotificationPriority
    action_url: Optional[str]
    dismissible: bool
    created_at: str
    read: bool
    metadata: Optional[Dict[str, Any]]


DB_PATH = "/app/data/zoe.db"


def init_notifications_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Create table if not exists (new schema)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            message TEXT,
            priority TEXT,
            action_url TEXT,
            dismissible INTEGER,
            created_at TEXT,
            read INTEGER,
            metadata TEXT
        )
        """
    )
    # Migrate existing table to ensure columns exist
    try:
        cursor.execute("PRAGMA table_info(notifications)")
        cols = [r[1] for r in cursor.fetchall()]
        required_cols = [
            ("title", "TEXT"),
            ("message", "TEXT"),
            ("priority", "TEXT"),
            ("action_url", "TEXT"),
            ("dismissible", "INTEGER"),
            ("created_at", "TEXT"),
            ("read", "INTEGER"),
            ("metadata", "TEXT"),
        ]
        for name, ctype in required_cols:
            if name not in cols:
                cursor.execute(f"ALTER TABLE notifications ADD COLUMN {name} {ctype}")
    except Exception:
        pass
    conn.commit()
    conn.close()


init_notifications_db()


class IntelligenceStreamManager:
    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self.lock:
            self.active_connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self.lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        # Copy to avoid iteration issues during disconnects
        async with self.lock:
            connections = list(self.active_connections)
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception:
                await self.disconnect(ws)


stream_manager = IntelligenceStreamManager()


router = APIRouter(prefix="/api/notifications", tags=["notifications"])


async def send_notification(
    title: str,
    message: str,
    priority: NotificationPriority,
    action_url: Optional[str] = None,
    dismissible: bool = True,
    metadata: Optional[Dict[str, Any]] = None
) -> Notification:
    created_at = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Build insert dynamically to satisfy existing schema
    cursor.execute("PRAGMA table_info(notifications)")
    cols_info = cursor.fetchall()
    col_names = [c[1] for c in cols_info]
    values_map = {
        "title": title,
        "message": message,
        "priority": priority.value,
        "action_url": action_url,
        "dismissible": 1 if dismissible else 0,
        "created_at": created_at,
        "read": 0,
        "metadata": (metadata and __import__("json").dumps(metadata)) or None,
        # Common legacy aliases
        "notification_time": created_at,
    }
    insert_cols = [name for name in [
        "title","message","priority","action_url","dismissible","created_at","read","metadata","notification_time"
    ] if name in col_names]
    placeholders = ", ".join(["?" for _ in insert_cols])
    sql = f"INSERT INTO notifications ({', '.join(insert_cols)}) VALUES ({placeholders})"
    cursor.execute(sql, tuple(values_map.get(c) for c in insert_cols))
    notif_id = cursor.lastrowid
    conn.commit()
    conn.close()

    notif = Notification(
        id=notif_id,
        title=title,
        message=message,
        priority=priority,
        action_url=action_url,
        dismissible=dismissible,
        created_at=created_at,
        read=False,
        metadata=metadata,
    )

    await broadcast_intelligence({
        "type": "proactive_suggestion" if priority != NotificationPriority.AMBIENT else "ambient_notification",
        "data": notif.model_dump(),
    })

    return notif


@router.post("/")
async def create_notification(body: NotificationCreate):
    try:
        notif = await send_notification(
            title=body.title,
            message=body.message,
            priority=body.priority,
            action_url=body.action_url,
            dismissible=body.dismissible,
            metadata=body.metadata,
        )
        return {"notification": notif.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create notification: {str(e)}")


@router.get("/")
async def list_notifications(limit: int = Query(50, ge=1, le=200), unread_only: bool = Query(False)):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = "SELECT * FROM notifications"
        params: List[Any] = []
        if unread_only:
            query += " WHERE read = 0"
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        results: List[Notification] = []
        import json as json_module
        for r in rows:
            raw_priority = (r["priority"] or "ambient") if "priority" in r.keys() else "ambient"
            parsed_priority = raw_priority if raw_priority in {"ambient","important","critical"} else "ambient"
            safe_title = r["title"] if ("title" in r.keys() and r["title"] is not None) else ""
            safe_message = r["message"] if ("message" in r.keys() and r["message"] is not None) else ""
            created = r["created_at"] if ("created_at" in r.keys() and r["created_at"] is not None) else r.get("notification_time", datetime.now().isoformat())
            results.append(
                Notification(
                    id=r["id"],
                    title=safe_title,
                    message=safe_message,
                    priority=NotificationPriority(parsed_priority),
                    action_url=r["action_url"] if "action_url" in r.keys() else None,
                    dismissible=bool(r["dismissible"]) if "dismissible" in r.keys() else True,
                    created_at=created,
                    read=bool(r["read"]) if "read" in r.keys() else False,
                    metadata=(r["metadata"] and json_module.loads(r["metadata"])) if ("metadata" in r.keys() and r["metadata"]) else None,
                )
            )
        return {"notifications": [n.model_dump() for n in results]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list notifications: {str(e)}")


@router.post("/{notification_id}/read")
async def mark_read(notification_id: int):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,))
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Notification not found")
        conn.commit()
        conn.close()
        return {"message": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to mark read: {str(e)}")


# Suggestion interaction tracking (accept / dismiss / never)
@router.post("/{notification_id}/interaction")
async def track_interaction(notification_id: int, action: str):
    try:
        # Persist in lightweight table
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id INTEGER,
                action TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            "INSERT INTO notification_interactions (notification_id, action, created_at) VALUES (?, ?, ?)",
            (notification_id, action, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

        # Broadcast acceptance to allow learning loops later
        await broadcast_intelligence({
            "type": "suggestion_feedback",
            "data": {"notification_id": notification_id, "action": action}
        })
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to track interaction: {str(e)}")


ws_router = APIRouter()


@ws_router.websocket("/ws/intelligence")
async def intelligence_stream(websocket: WebSocket):
    await stream_manager.connect(websocket)
    try:
        # Send a hello event upon connection
        await websocket.send_json({"type": "status", "data": {"state": "connected"}})
        while True:
            # Keep the connection alive; we do not expect client messages
            await asyncio.sleep(30)
            try:
                await websocket.send_json({"type": "heartbeat", "data": {"ts": datetime.now().isoformat()}})
            except RuntimeError:
                break
    except WebSocketDisconnect:
        await stream_manager.disconnect(websocket)
    except Exception:
        await stream_manager.disconnect(websocket)


# Provide an alternative path under /api for reverse proxies that scope API under /api
@ws_router.websocket("/api/ws/intelligence")
async def intelligence_stream_api(websocket: WebSocket):
    await intelligence_stream(websocket)


# -------------------- SSE fallback (for proxies that block WS) --------------------
class IntelligenceSSEManager:
    def __init__(self) -> None:
        self.queues: Set[asyncio.Queue] = set()
        self.lock = asyncio.Lock()

    async def register(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self.lock:
            self.queues.add(q)
        return q

    async def unregister(self, q: asyncio.Queue) -> None:
        async with self.lock:
            if q in self.queues:
                self.queues.remove(q)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        async with self.lock:
            targets = list(self.queues)
        for q in targets:
            try:
                await q.put(message)
            except Exception:
                pass


sse_manager = IntelligenceSSEManager()


async def broadcast_intelligence(message: Dict[str, Any]) -> None:
    # Fan out to WS and SSE
    await stream_manager.broadcast(message)
    await sse_manager.broadcast(message)


async def sse_event_generator(q: asyncio.Queue):
    try:
        yield "data: {\"type\": \"status\", \"data\": {\"state\": \"connected\"}}\n\n"
        last_ping = asyncio.get_event_loop().time()
        while True:
            # Send heartbeat every 15s even if no events
            now = asyncio.get_event_loop().time()
            if now - last_ping >= 15:
                yield ": keep-alive\n\n"
                last_ping = now
            try:
                msg = await asyncio.wait_for(q.get(), timeout=5.0)
                import json as json_module
                payload = json_module.dumps(msg)
                yield f"data: {payload}\n\n"
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        return


@ws_router.get("/api/intelligence/stream")
async def intelligence_sse():
    q = await sse_manager.register()
    async def _gen():
        try:
            async for chunk in sse_event_generator(q):
                yield chunk
        finally:
            await sse_manager.unregister(q)
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_gen(), media_type="text/event-stream", headers=headers)


# Convenience signal endpoints (useful for testing UI wires)
@router.post("/test/suggestion")
async def test_suggestion():
    return {
        "notification": (
            await send_notification(
                title="Reconnect Opportunity",
                message="You haven't talked to Sarah in 3 weeks. Add a reminder?",
                priority=NotificationPriority.IMPORTANT,
                metadata={"suggestion_type": "relationship_check"},
            )
        ).model_dump()
    }


