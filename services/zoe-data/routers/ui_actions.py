import json

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_db
from push import broadcaster
from ui_orchestrator import (
    ACTION_STATES,
    ALLOWED_ACTION_TYPES,
    enqueue_ui_action,
    append_ledger,
)

router = APIRouter(prefix="/api/ui", tags=["ui"])


@router.post("/panel/bind")
async def bind_panel(
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    user_id = user["user_id"]
    panel_id = payload.get("panel_id")
    if not panel_id:
        raise HTTPException(status_code=400, detail="panel_id is required")

    chat_session_id = payload.get("session_id")
    page = payload.get("page")
    is_foreground = 1 if payload.get("is_foreground", True) else 0
    ui_context = json.dumps(payload.get("ui_context", {}))

    if is_foreground:
        await db.execute(
            "UPDATE ui_panel_sessions SET is_foreground = 0, updated_at = datetime('now') WHERE user_id = ?",
            (user_id,),
        )

    await db.execute(
        """INSERT INTO ui_panel_sessions (panel_id, user_id, chat_session_id, page, ui_context, is_foreground)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(panel_id) DO UPDATE SET
             user_id=excluded.user_id,
             chat_session_id=excluded.chat_session_id,
             page=excluded.page,
             ui_context=excluded.ui_context,
             is_foreground=excluded.is_foreground,
             last_seen_at=datetime('now'),
             updated_at=datetime('now')""",
        (panel_id, user_id, chat_session_id, page, ui_context, is_foreground),
    )
    await db.commit()
    return {"status": "ok", "panel_id": panel_id, "is_foreground": bool(is_foreground)}


@router.post("/actions")
async def create_ui_action(
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    user_id = user["user_id"]
    action_type = payload.get("action_type")
    if action_type not in ALLOWED_ACTION_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported action_type")
    data = payload.get("payload", {})
    result = await enqueue_ui_action(
        db,
        user_id=user_id,
        action_type=action_type,
        payload=data,
        requested_by=payload.get("requested_by", "api"),
        panel_id=payload.get("panel_id"),
        chat_session_id=payload.get("session_id"),
        idempotency_key=payload.get("idempotency_key"),
        confirmation_token=payload.get("confirmation_token"),
    )
    return {"status": "ok", "action": result}


@router.get("/actions/pending")
async def get_pending_ui_actions(
    panel_id: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    user_id = user["user_id"]
    cursor = await db.execute(
        """SELECT id, panel_id, chat_session_id, action_type, payload, status, requires_confirmation,
                  confirmation_token, retry_count, max_retries, created_at, updated_at
           FROM ui_actions
           WHERE user_id = ? AND panel_id = ? AND status IN ('queued', 'running')
           ORDER BY created_at ASC
           LIMIT ?""",
        (user_id, panel_id, limit),
    )
    rows = await cursor.fetchall()
    actions = []
    for r in rows:
        item = dict(r)
        item["payload"] = json.loads(item["payload"] or "{}")
        item["requires_confirmation"] = bool(item.get("requires_confirmation", 0))
        actions.append(item)
    return {"actions": actions, "count": len(actions)}


@router.post("/actions/{action_id}/ack")
async def ack_ui_action(
    action_id: str,
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    user_id = user["user_id"]
    status = payload.get("status")
    if status not in ACTION_STATES:
        raise HTTPException(status_code=400, detail="Invalid action status")

    cursor = await db.execute(
        "SELECT id, panel_id, status FROM ui_actions WHERE id = ? AND user_id = ?",
        (action_id, user_id),
    )
    existing = await cursor.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Action not found")

    error_code = payload.get("error_code")
    error_message = payload.get("error_message")
    retries = payload.get("retry_count")
    now_sql = "datetime('now')"
    acked_at = now_sql if status in {"success", "failed", "blocked"} else None

    await db.execute(
        """UPDATE ui_actions
           SET status = ?, error_code = ?, error_message = ?, retry_count = COALESCE(?, retry_count),
               updated_at = datetime('now'),
               acked_at = CASE WHEN ? IS NOT NULL THEN datetime('now') ELSE acked_at END
           WHERE id = ? AND user_id = ?""",
        (status, error_code, error_message, retries, acked_at, action_id, user_id),
    )
    await append_ledger(
        db,
        action_id=action_id,
        user_id=user_id,
        panel_id=existing["panel_id"],
        event_type=f"ack:{status}",
        event_data={
            "ui_context": payload.get("ui_context", {}),
            "error_code": error_code,
            "error_message": error_message,
        },
    )
    await db.commit()
    await broadcaster.broadcast(
        "all",
        "ui_action_status",
        {
            "action_id": action_id,
            "panel_id": existing["panel_id"],
            "status": status,
            "error_code": error_code,
            "error_message": error_message,
        },
    )
    return {"status": "ok", "action_id": action_id, "state": status}


@router.post("/state/sync")
async def sync_ui_state(
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    user_id = user["user_id"]
    panel_id = payload.get("panel_id")
    if not panel_id:
        raise HTTPException(status_code=400, detail="panel_id is required")

    page = payload.get("page")
    chat_session_id = payload.get("session_id")
    ui_context = json.dumps(payload.get("ui_context", {}))
    is_foreground = 1 if payload.get("is_foreground", True) else 0
    await db.execute(
        """INSERT INTO ui_panel_sessions (panel_id, user_id, chat_session_id, page, ui_context, is_foreground)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(panel_id) DO UPDATE SET
             user_id=excluded.user_id,
             chat_session_id=excluded.chat_session_id,
             page=excluded.page,
             ui_context=excluded.ui_context,
             is_foreground=excluded.is_foreground,
             last_seen_at=datetime('now'),
             updated_at=datetime('now')""",
        (panel_id, user_id, chat_session_id, page, ui_context, is_foreground),
    )
    await db.commit()
    return {"status": "ok", "panel_id": panel_id}


@router.get("/session/{session_id}/context")
async def get_session_context(
    session_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    user_id = user["user_id"]
    cursor = await db.execute(
        """SELECT panel_id, page, ui_context, is_foreground, last_seen_at
           FROM ui_panel_sessions
           WHERE user_id = ? AND chat_session_id = ?
           ORDER BY updated_at DESC
           LIMIT 1""",
        (user_id, session_id),
    )
    row = await cursor.fetchone()
    if not row:
        return {"session_id": session_id, "context": None}
    data = dict(row)
    data["is_foreground"] = bool(data.get("is_foreground", 0))
    data["ui_context"] = json.loads(data.get("ui_context") or "{}")
    return {"session_id": session_id, "context": data}


@router.get("/actions/{action_id}/ledger")
async def get_action_ledger(
    action_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    user_id = user["user_id"]
    cursor = await db.execute(
        "SELECT id FROM ui_actions WHERE id = ? AND user_id = ?",
        (action_id, user_id),
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Action not found")
    cursor = await db.execute(
        """SELECT event_type, event_data, created_at
           FROM ui_action_ledger
           WHERE action_id = ?
           ORDER BY created_at ASC""",
        (action_id,),
    )
    rows = await cursor.fetchall()
    items = []
    for row in rows:
        r = dict(row)
        r["event_data"] = json.loads(r.get("event_data") or "{}")
        items.append(r)
    return {"action_id": action_id, "events": items}


@router.post("/actions/{action_id}/retry")
async def retry_ui_action(
    action_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    user_id = user["user_id"]
    cursor = await db.execute(
        """SELECT id, panel_id, status, retry_count, max_retries
           FROM ui_actions
           WHERE id = ? AND user_id = ?""",
        (action_id, user_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Action not found")
    item = dict(row)
    if item["retry_count"] >= item["max_retries"]:
        raise HTTPException(status_code=409, detail="Retry limit reached")

    await db.execute(
        """UPDATE ui_actions
           SET status='queued',
               retry_count = retry_count + 1,
               error_code = NULL,
               error_message = NULL,
               updated_at = datetime('now')
           WHERE id = ?""",
        (action_id,),
    )
    await append_ledger(
        db,
        action_id=action_id,
        user_id=user_id,
        panel_id=item["panel_id"],
        event_type="retry_queued",
        event_data={"retry_count_next": item["retry_count"] + 1},
    )
    await db.commit()
    await broadcaster.broadcast(
        "all",
        "ui_action",
        {
            "action_id": action_id,
            "panel_id": item["panel_id"],
            "status": "queued",
            "retry_count": item["retry_count"] + 1,
        },
    )
    return {"status": "ok", "action_id": action_id, "retry_count": item["retry_count"] + 1}


@router.post("/actions/requeue-stale")
async def requeue_stale_actions(
    timeout_seconds: int = Query(30, ge=5, le=600),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    user_id = user["user_id"]
    cursor = await db.execute(
        """SELECT id, panel_id, retry_count, max_retries
           FROM ui_actions
           WHERE user_id = ?
             AND status = 'running'
             AND updated_at < datetime('now', ?)
             AND retry_count < max_retries""",
        (user_id, f"-{timeout_seconds} seconds"),
    )
    rows = await cursor.fetchall()
    action_ids = [r["id"] for r in rows]
    for r in rows:
        await db.execute(
            """UPDATE ui_actions
               SET status='queued', retry_count=retry_count+1, updated_at=datetime('now')
               WHERE id=?""",
            (r["id"],),
        )
        await append_ledger(
            db,
            action_id=r["id"],
            user_id=user_id,
            panel_id=r["panel_id"],
            event_type="requeue_stale",
            event_data={"timeout_seconds": timeout_seconds},
        )
        await broadcaster.broadcast(
            "all",
            "ui_action",
            {
                "action_id": r["id"],
                "panel_id": r["panel_id"],
                "status": "queued",
                "reason": "stale_timeout",
            },
        )
    await db.commit()
    return {"status": "ok", "requeued": len(action_ids), "action_ids": action_ids}
