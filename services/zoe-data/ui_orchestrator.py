import json
import uuid
from typing import Any, Dict, Optional

from push import broadcaster

ACTION_STATES = {"queued", "running", "success", "failed", "blocked"}
ALLOWED_ACTION_TYPES = {
    "navigate",
    "open_panel",
    "focus",
    "fill",
    "submit",
    "create_record",
    "update_record",
    "delete_record",
    "highlight",
    "notify",
    "refresh",
    "click",
    # Touch panel control actions (consumed by touch-ui-executor.js on the kiosk)
    "panel_navigate",
    "panel_navigate_fullscreen",
    "panel_clear",
    "panel_browser_frame",
    "panel_show_fullscreen",
    "panel_announce",
    "panel_request_auth",
    "panel_set_mode",
    "panel_show_smart_home",
    "panel_show_media",
}
DANGEROUS_ACTION_TYPES = {"delete_record"}


def _requires_confirmation(action_type: str) -> bool:
    return action_type in DANGEROUS_ACTION_TYPES


async def append_ledger(
    db,
    action_id: str,
    user_id: str,
    event_type: str,
    event_data: Optional[Dict[str, Any]] = None,
    panel_id: Optional[str] = None,
) -> None:
    await db.execute(
        """INSERT INTO ui_action_ledger (id, action_id, user_id, panel_id, event_type, event_data)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            uuid.uuid4().hex[:16],
            action_id,
            user_id,
            panel_id,
            event_type,
            json.dumps(event_data or {}),
        ),
    )


async def enqueue_ui_action(
    db,
    *,
    user_id: str,
    action_type: str,
    payload: Dict[str, Any],
    requested_by: str = "system",
    panel_id: Optional[str] = None,
    chat_session_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    confirmation_token: Optional[str] = None,
) -> Dict[str, Any]:
    if action_type not in ALLOWED_ACTION_TYPES:
        raise ValueError(f"Unsupported action type: {action_type}")

    if not panel_id:
        # No panel specified — find the requester's foreground panel.
        cursor = await db.execute(
            """SELECT panel_id
               FROM ui_panel_sessions
               WHERE user_id = ? AND is_foreground = 1
               ORDER BY updated_at DESC
               LIMIT 1""",
            (user_id,),
        )
        row = await cursor.fetchone()
        panel_id = row["panel_id"] if row else None

    if panel_id:
        # Resolve the user that owns this panel (who will poll for its actions).
        # This ensures actions are stored under the panel's user, not the requester's
        # user — so the kiosk can always find its own actions regardless of who queued them.
        cursor = await db.execute(
            """SELECT user_id FROM ui_panel_sessions WHERE panel_id = ?
               ORDER BY last_seen_at DESC LIMIT 1""",
            (panel_id,),
        )
        row = await cursor.fetchone()
        if row and row["user_id"]:
            user_id = row["user_id"]

    if idempotency_key:
        cursor = await db.execute(
            """SELECT id, status
               FROM ui_actions
               WHERE user_id = ? AND idempotency_key = ?
               LIMIT 1""",
            (user_id, idempotency_key),
        )
        existing = await cursor.fetchone()
        if existing:
            return {
                "id": existing["id"],
                "status": existing["status"],
                "deduped": True,
                "panel_id": panel_id,
            }

    action_id = uuid.uuid4().hex[:16]
    requires_confirmation = 1 if _requires_confirmation(action_type) else 0
    if requires_confirmation and not confirmation_token:
        confirmation_token = uuid.uuid4().hex[:12]

    await db.execute(
        """INSERT INTO ui_actions (
               id, user_id, panel_id, chat_session_id, idempotency_key, action_type, payload,
               status, requires_confirmation, confirmation_token, requested_by
           ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)""",
        (
            action_id,
            user_id,
            panel_id,
            chat_session_id,
            idempotency_key,
            action_type,
            json.dumps(payload or {}),
            requires_confirmation,
            confirmation_token,
            requested_by,
        ),
    )
    await append_ledger(
        db,
        action_id=action_id,
        user_id=user_id,
        panel_id=panel_id,
        event_type="queued",
        event_data={
            "action_type": action_type,
            "payload": payload,
            "requires_confirmation": bool(requires_confirmation),
        },
    )
    await db.commit()

    message = {
        "action_id": action_id,
        "user_id": user_id,
        "panel_id": panel_id,
        "chat_session_id": chat_session_id,
        "action_type": action_type,
        "payload": payload,
        "status": "queued",
        "requires_confirmation": bool(requires_confirmation),
        "confirmation_token": confirmation_token if requires_confirmation else None,
    }
    await broadcaster.broadcast("all", "ui_action", message)
    return message
