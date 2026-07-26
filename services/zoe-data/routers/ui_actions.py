import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth import get_current_user
from database import get_db
from guest_policy import can_use_ui_action, is_guest_user, require_feature_access
from push import broadcaster
from ui_orchestrator import (
    ACTION_STATES,
    ALLOWED_ACTION_TYPES,
    enqueue_ui_action,
    append_ledger,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui", tags=["ui"])

# A guest kiosk may RECLAIM a panel whose registered non-guest owner has gone
# silent. If that owner's ``ui_panel_sessions.last_seen_at`` is older than this
# many seconds, the session is treated as abandoned (e.g. the user signed out /
# their session dropped, so their executor stopped refreshing the row). A live
# panel binds/syncs every ~5s while a real owner is present, so this 300s window
# is ~60× that cadence — an actively-present owner is never falsely reclaimed,
# and a brief network blip cannot hand a live owner's panel to a guest. Used by
# BOTH the read-side gate (``_authorize_panel``) and the atomic upsert guard
# (``_guest_conflict_guard``) so the check and the write agree on "stale".
_PANEL_RECLAIM_STALE_AFTER_S = 300

# SQL predicate (Postgres): the panel's registered owner has gone stale.
# ``last_seen_at`` is stored as TEXT (``NOW()::TEXT`` ISO string, see the
# 0001 schema), so it MUST be cast to timestamptz before arithmetic — a bare
# ``NOW() - last_seen_at`` raises "timestamp - text". Mirrors the existing
# ``created_at::timestamptz < CURRENT_TIMESTAMP - INTERVAL '1 hour'`` idiom in
# this module. The interpolated value is an int constant, never user input.
_OWNER_STALE_SQL = (
    "ui_panel_sessions.last_seen_at::timestamptz"
    f" < CURRENT_TIMESTAMP - INTERVAL '{int(_PANEL_RECLAIM_STALE_AFTER_S)} seconds'"
)


def _guest_conflict_guard(user: dict) -> str:
    """SQL fragment appended to the ``ui_panel_sessions`` upsert for guests.

    ``_authorize_panel`` verifies (via a SELECT) that a guest is acting on an
    unclaimed or already guest-owned panel, but that check and the ON CONFLICT
    upsert are separate statements — a real user could bind the same panel_id in
    between, and the guest's upsert would then overwrite that real user's
    ``user_id`` (the panel-hijack class this gate guards against). Guarding the
    ON CONFLICT DO UPDATE with ``WHERE ui_panel_sessions.user_id = 'guest'``
    closes the race structurally: in Postgres a conflicting real-user row simply
    fails the WHERE and is left untouched (no error, no overwrite). Empty for
    non-guest callers (device-token / bound-user), whose upserts are unrestricted.

    A guest may ALSO reclaim a panel whose non-guest owner has gone stale
    (``_OWNER_STALE_SQL``) — the same condition ``_authorize_panel`` allows on the
    read side. This stays TOCTOU-safe: the WHERE is evaluated against the row's
    CURRENT state at upsert time, so if a fresh non-guest owner races in between
    the authorize check and the upsert, its just-written ``last_seen_at`` is not
    stale and ``user_id != 'guest'``, the WHERE is false, and the live owner's
    row is left untouched.
    """
    if not is_guest_user(user):
        return ""
    return f" WHERE ui_panel_sessions.user_id = 'guest' OR {_OWNER_STALE_SQL}"


async def _authorize_panel(db, user: dict, panel_id: str) -> None:
    """Ensure the caller is allowed to act on ``panel_id``.

    Only two callers are legitimate for panel-scoped writes/polls:

      * A panel daemon authenticated with that panel's **device token** — its
        resolved user dict carries ``panel_id`` (set in ``auth.get_current_user``
        / ``_resolve_device_token_user``). It may act ONLY on its own panel.
      * A **human session user explicitly bound to the panel** via
        ``panel_user_bindings`` (``binding_type`` 'default' or 'allowed').
      * The **kiosk auto-guest** — but ONLY for a panel that is unclaimed,
        already guest-owned, or owned by a non-guest whose session has gone
        stale (see the guest branch below).

    Any other authenticated user is rejected with 403. Without this gate a
    normal session could bind/sync an arbitrary ``panel_id`` (the
    ``ON CONFLICT(panel_id)`` upsert rewrites ``ui_panel_sessions.user_id``) and
    then poll ``/actions/pending`` to drain another panel/user's queued actions
    — the panel-hijack vulnerability (P1).
    """
    token_panel = user.get("panel_id")
    if token_panel:
        # Device-token caller: scoped to exactly one panel.
        if str(token_panel) == str(panel_id):
            return
        raise HTTPException(status_code=403, detail="Device token is not valid for this panel")

    user_id = user.get("user_id")
    if user_id and user_id != "guest":
        row = await (
            await db.execute(
                "SELECT 1 FROM panel_user_bindings WHERE panel_id = ? AND user_id = ? LIMIT 1",
                (panel_id, user_id),
            )
        ).fetchone()
        if row:
            return
        # A non-guest user without a binding is never allowed to act on this
        # panel — fall through to 403 (do NOT reach the guest branch below).
        raise HTTPException(status_code=403, detail="Not authorized for this panel")

    # Kiosk auto-guest. The touch panel deliberately runs UI-action bind/sync/poll
    # as a bare guest (no device token / session) — see touch-ui-executor's
    # getDataApiSession(), which sends no X-Session-ID for guest so Data treats it
    # as guest. A guest may act on a panel when that panel is unclaimed (no session
    # row yet), already guest-owned, OR owned by a non-guest whose session has gone
    # stale (last_seen_at older than _PANEL_RECLAIM_STALE_AFTER_S — the owner
    # signed out / dropped and stopped refreshing the row, so the kiosk reclaims
    # it). This lets the kiosk RECEIVE actions pushed to its OWN panel_id while
    # still blocking the panel-hijack class this gate guards against: a guest
    # cannot bind/sync/drain a panel a real (non-guest) user is ACTIVELY on (fresh
    # last_seen_at), because the upsert would rewrite that panel's user_id and the
    # poll would return the real user's queued actions.
    # Action-TYPE limits are enforced separately by can_use_ui_action, so a guest
    # still cannot ENQUEUE sensitive_ui actions via POST /actions.
    if is_guest_user(user):
        session_row = await (
            await db.execute(
                f"""SELECT user_id, ({_OWNER_STALE_SQL}) AS owner_stale
                    FROM ui_panel_sessions WHERE panel_id = ? LIMIT 1""",
                (panel_id,),
            )
        ).fetchone()
        if session_row is None or str(session_row["user_id"]) == "guest":
            return
        # Non-guest owner: allow reclaim only if their session has gone stale.
        if session_row["owner_stale"]:
            return
        raise HTTPException(status_code=403, detail="Panel is owned by another user")

    raise HTTPException(status_code=403, detail="Not authorized for this panel")


@router.post("/panel/bind")
async def bind_panel(
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="ui_actions", action="bind")
    user_id = user["user_id"]
    panel_id = payload.get("panel_id")
    if not panel_id:
        raise HTTPException(status_code=400, detail="panel_id is required")
    await _authorize_panel(db, user, panel_id)

    chat_session_id = payload.get("session_id")
    page = payload.get("page")
    is_foreground = 1 if payload.get("is_foreground", True) else 0
    ui_context = json.dumps(payload.get("ui_context", {}))

    if is_foreground:
        await db.execute(
            "UPDATE ui_panel_sessions SET is_foreground = 0, updated_at = NOW() WHERE user_id = ?",
            (user_id,),
        )

    await db.execute(
        f"""INSERT INTO ui_panel_sessions (panel_id, user_id, chat_session_id, page, ui_context, is_foreground)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(panel_id) DO UPDATE SET
             user_id=excluded.user_id,
             chat_session_id=excluded.chat_session_id,
             page=excluded.page,
             ui_context=excluded.ui_context,
             is_foreground=excluded.is_foreground,
             last_seen_at=NOW(),
             updated_at=NOW(){_guest_conflict_guard(user)}""",
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
    await require_feature_access(db, user, feature="ui_actions", action="create")
    user_id = user["user_id"]
    action_type = payload.get("action_type")
    if action_type not in ALLOWED_ACTION_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported action_type")
    if not await can_use_ui_action(db, user, action_type):
        raise HTTPException(status_code=403, detail="Role cannot enqueue this action type")
    # A caller-supplied panel_id targets another surface's queue: enqueue_ui_action
    # rewrites the stored user_id to that panel's owner, so the action is delivered
    # when the panel polls /actions/pending. Without this gate a guest or unbound
    # session could inject actions into an arbitrary active panel (same panel-hijack
    # class as bind/poll/sync). Authorize panel ownership/device-token, matching the
    # other panel-scoped routes. No panel_id → enqueue_ui_action resolves the
    # caller's OWN foreground panel, which needs no cross-panel check.
    target_panel_id = payload.get("panel_id")
    if target_panel_id:
        await _authorize_panel(db, user, target_panel_id)
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
    await require_feature_access(db, user, feature="ui_actions", action="read")
    await _authorize_panel(db, user, panel_id)
    # Resolve the panel's registered user_id from ui_panel_sessions.
    # Actions are stored under the panel's user (who polls for them), which may differ
    # from the caller's user_id when actions are queued by OpenClaw/API on behalf of the panel.
    panel_cursor = await db.execute(
        "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
        (panel_id,),
    )
    panel_row = await panel_cursor.fetchone()
    # Fall back to the caller's user_id if the panel has no registered session yet.
    panel_user_id = panel_row["user_id"] if panel_row else user["user_id"]

    # A guest may reach here for a panel whose non-guest owner is stale but not yet
    # reclaimed (bind normally reclaims first, but a direct poll can precede it). A
    # guest must never READ a real user's queued actions, so serve only the guest
    # queue for that panel — the owner's queue is off-limits until a bind reclaim
    # rewrites the row to guest-owned. Post-reclaim this is a no-op (owner=guest).
    if is_guest_user(user) and panel_user_id != "guest":
        panel_user_id = "guest"

    # Auto-expire queued actions older than 1 hour — these are stale (e.g. ack
    # was lost during page navigation) and should not be re-delivered on boot.
    # Use CURRENT_TIMESTAMP (not NOW()) because _adapt_params rewrites NOW() →
    # NOW()::text, which makes "NOW()::text - INTERVAL '1 hour'" invalid SQL.
    # Cast created_at to timestamptz for comparison (column may be TEXT).
    await db.execute(
        """UPDATE ui_actions
           SET status = 'failed', error_code = 'stale',
               error_message = 'Auto-expired: queued >1 hour without ack',
               updated_at = NOW()
           WHERE user_id = ? AND panel_id = ? AND status = 'queued'
             AND created_at::timestamptz < CURRENT_TIMESTAMP - INTERVAL '1 hour'""",
        (panel_user_id, panel_id),
    )
    await db.commit()

    # Skybridge voice cards are state replacement, not a backlog. If a page
    # reload or lost ack leaves older cards queued, skip everything except the
    # newest card for this panel so the surface cannot visually rewind.
    await db.execute(
        """UPDATE ui_actions
           SET status = 'skipped', error_code = 'superseded',
               error_message = 'Superseded by newer Skybridge voice card',
               updated_at = NOW()
           WHERE user_id = ? AND panel_id = ?
             AND requested_by = 'voice'
             AND action_type = 'show_card'
             AND status IN ('queued', 'running')
             AND payload::jsonb->>'source' = 'voice:skybridge'
             AND created_at::timestamptz < (
                 SELECT MAX(created_at::timestamptz)
                 FROM ui_actions
                 WHERE user_id = ? AND panel_id = ?
                   AND requested_by = 'voice'
                   AND action_type = 'show_card'
                   AND status IN ('queued', 'running')
                   AND payload::jsonb->>'source' = 'voice:skybridge'
             )""",
        (panel_user_id, panel_id, panel_user_id, panel_id),
    )
    await db.commit()

    cursor = await db.execute(
        """SELECT id, panel_id, chat_session_id, action_type, payload, status, requires_confirmation,
                  confirmation_token, retry_count, max_retries, created_at, updated_at
           FROM ui_actions
           WHERE user_id = ? AND panel_id = ? AND status IN ('queued', 'running')
           ORDER BY created_at ASC
           LIMIT ?""",
        (panel_user_id, panel_id, limit),
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
    await require_feature_access(db, user, feature="ui_actions", action="ack")
    user_id = user["user_id"]
    status = payload.get("status")
    if not status or status not in ACTION_STATES:
        # Unknown or missing status — treat as a no-op ack to be idempotent.
        return {"status": "already_acked", "action_id": action_id}

    panel_id = str((payload or {}).get("panel_id") or "").strip()

    # Accept ack by DB uuid (id) OR by idempotency_key. Kiosk pages can reload
    # between polling and acking, but panel-targeted cross-user acks are only
    # accepted from a user currently registered on that panel.
    cursor = await db.execute(
        """SELECT id, user_id, panel_id, status FROM ui_actions
           WHERE (id = ? OR idempotency_key = ?)
             AND (
               user_id = ?
               OR (
                 ? != '' AND panel_id = ?
                 AND EXISTS (
                   SELECT 1 FROM ui_panel_sessions
                   WHERE panel_id = ? AND user_id = ?
                 )
               )
             )""",
        (action_id, action_id, user_id, panel_id, panel_id, panel_id, user_id),
    )
    existing = await cursor.fetchone()
    if not existing:
        # Action doesn't exist (stale/already-processed) — return 200 to be idempotent.
        # The SSE client may retry acks for actions that were already cleaned up.
        return {"status": "already_acked", "action_id": action_id}

    # Use the real DB id for the update (in case we matched on idempotency_key)
    real_id = existing["id"]
    action_user_id = existing["user_id"]
    error_code = payload.get("error_code")
    error_message = payload.get("error_message")
    retries = payload.get("retry_count")
    should_set_acked_at = status in {"success", "failed", "blocked"}

    await db.execute(
        """UPDATE ui_actions
           SET status = ?, error_code = ?, error_message = ?, retry_count = COALESCE(?, retry_count),
               updated_at = NOW(),
               acked_at = CASE WHEN CAST(? AS boolean) THEN NOW() ELSE acked_at END
           WHERE id = ? AND user_id = ?""",
        (status, error_code, error_message, retries, should_set_acked_at, real_id, action_user_id),
    )
    # Handler-reported real outcome (additive, P-W2.3): e.g. panel_announce's
    # honest TTS result ({"tts": "played"|"http_401"|...}). Bounded so a rogue
    # panel can't bloat the ledger; non-dict / oversized payloads are dropped.
    handler_event = payload.get("event_data")
    if not isinstance(handler_event, dict) or len(json.dumps(handler_event, default=str)) > 2000:
        handler_event = None
    await append_ledger(
        db,
        action_id=real_id,
        user_id=action_user_id,
        panel_id=existing["panel_id"],
        event_type=f"ack:{status}",
        event_data={
            "ui_context": payload.get("ui_context", {}),
            "error_code": error_code,
            "error_message": error_message,
            **({"result": handler_event} if handler_event else {}),
        },
    )
    await db.commit()
    await broadcaster.broadcast(
        "all",
        "ui_action_status",
        {
            "action_id": real_id,
            "panel_id": existing["panel_id"],
            "status": status,
            "error_code": error_code,
            "error_message": error_message,
        },
    )
    return {"status": "ok", "action_id": real_id, "state": status}


@router.post("/state/sync")
async def sync_ui_state(
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="ui_actions", action="sync")
    user_id = user["user_id"]
    panel_id = payload.get("panel_id")
    if not panel_id:
        raise HTTPException(status_code=400, detail="panel_id is required")
    await _authorize_panel(db, user, panel_id)

    page = payload.get("page")
    chat_session_id = payload.get("session_id")
    ui_context = json.dumps(payload.get("ui_context", {}))
    is_foreground = 1 if payload.get("is_foreground", True) else 0
    await db.execute(
        f"""INSERT INTO ui_panel_sessions (panel_id, user_id, chat_session_id, page, ui_context, is_foreground)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(panel_id) DO UPDATE SET
             user_id=excluded.user_id,
             chat_session_id=excluded.chat_session_id,
             page=excluded.page,
             ui_context=excluded.ui_context,
             is_foreground=excluded.is_foreground,
             last_seen_at=NOW(),
             updated_at=NOW(){_guest_conflict_guard(user)}""",
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
    await require_feature_access(db, user, feature="ui_actions", action="read")
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
    await require_feature_access(db, user, feature="ui_actions", action="read")
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
    await require_feature_access(db, user, feature="ui_actions", action="retry")
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
               updated_at = NOW()
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
    await require_feature_access(db, user, feature="ui_actions", action="requeue")
    user_id = user["user_id"]
    cursor = await db.execute(
        """SELECT id, panel_id, retry_count, max_retries
           FROM ui_actions
           WHERE user_id = ?
             AND status = 'running'
             AND updated_at::timestamptz < CURRENT_TIMESTAMP - (? * INTERVAL '1 second')
             AND retry_count < max_retries""",
        (user_id, timeout_seconds),
    )
    rows = await cursor.fetchall()
    action_ids = [r["id"] for r in rows]
    for r in rows:
        await db.execute(
            """UPDATE ui_actions
               SET status='queued', retry_count=retry_count+1, updated_at=NOW()
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


# ── Action Form Lifecycle ─────────────────────────────────────────────────────

@router.post("/panel/form/open")
async def open_action_form(
    request: Request,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Called by the touch panel when a full-screen action-form overlay opens.

    Registers the panel as having an active form so that subsequent voice
    commands are routed to field-filling rather than the main chat pipeline.

    Body: {"panel_id": "...", "panel_type": "calendar_event"|"shopping_list", "data": {...}}
    """
    body = await request.json()
    panel_id = str(body.get("panel_id") or "").strip() or None
    panel_type = str(body.get("panel_type") or "").strip()
    data = body.get("data") or {}

    if panel_id and panel_type:
        try:
            from panel_form_state import set_active_form
            set_active_form(panel_id, panel_type, data)
        except Exception as exc:
            logger.debug("panel/form/open state update failed (non-fatal): %s", exc)

    logger.info("panel/form/open panel=%s type=%s", panel_id, panel_type)
    return {"status": "ok", "panel_id": panel_id, "panel_type": panel_type}


@router.post("/panel/form/close")
async def close_action_form(
    request: Request,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Called by the touch panel when the action-form overlay is dismissed (cancel/close).

    Clears the active form state so voice commands return to the main pipeline.

    Body: {"panel_id": "..."}
    """
    body = await request.json()
    panel_id = str(body.get("panel_id") or "").strip() or None

    if panel_id:
        try:
            from panel_form_state import clear_active_form
            clear_active_form(panel_id)
        except Exception as exc:
            logger.debug("panel/form/close state clear failed (non-fatal): %s", exc)

    logger.info("panel/form/close panel=%s", panel_id)
    return {"status": "ok", "panel_id": panel_id}


# ── Action Form Confirm ───────────────────────────────────────────────────────

@router.post("/panel/form/confirm")
async def confirm_action_form(
    request: Request,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Handle a Confirm submission from a full-screen action-form overlay.

    The touch panel posts the filled-in form data here. This endpoint routes
    the submission to the appropriate domain handler (calendar, list, etc.) and
    broadcasts a panel_close_action_form event so the overlay dismisses.

    Body:
        panel_type  – "calendar_event" | "shopping_list" | ...
        action_id   – optional, correlates back to the originating intent run
        session_id  – chat session that triggered the intent
        panel_id    – touch panel identifier
        data        – dict with filled-in field values
    """
    body = await request.json()
    panel_type = str(body.get("panel_type") or "").strip()
    panel_id = str(body.get("panel_id") or "").strip() or None
    session_id = str(body.get("session_id") or "").strip() or None
    data = body.get("data") or {}
    user_id = user["user_id"]

    if not panel_type:
        raise HTTPException(status_code=400, detail="panel_type is required")

    result_text = ""
    try:
        if panel_type == "calendar_event":
            try:
                from intent_router import execute_intent, detect_and_extract_intent
                from dataclasses import dataclass

                @dataclass
                class _SynthIntent:
                    name: str
                    slots: dict

                synth = _SynthIntent(
                    name="calendar_create",
                    slots={
                        "title": data.get("title", ""),
                        "date": data.get("date", ""),
                        "time": data.get("time", ""),
                        "duration": data.get("duration", ""),
                        "category": data.get("category", "general"),
                        "location": data.get("location", ""),
                        "notes": data.get("notes", ""),
                    },
                )
                result_text = await execute_intent(synth, user_id) or "Calendar event created."
            except Exception as exc:
                logger.warning("form/confirm calendar_create failed: %s", exc)
                result_text = f"Could not save event: {exc}"

        elif panel_type == "shopping_list":
            try:
                from intent_router import execute_intent
                from dataclasses import dataclass

                # If a specific new_item is flagged (panel pre-filled with existing items),
                # only submit that item to avoid duplicating items already in the list.
                new_item = data.get("new_item") or ""
                if new_item:
                    items_to_add = [new_item]
                else:
                    items_to_add = data.get("items") or []

                @dataclass
                class _SynthIntent:
                    name: str
                    slots: dict

                results = []
                list_name = data.get("list_name", "shopping")
                for item in items_to_add:
                    item_str = str(item).strip()
                    if not item_str:
                        continue
                    synth = _SynthIntent(
                        name="list_add",
                        slots={"item": item_str, "list_name": list_name, "list_type": list_name},
                    )
                    r = await execute_intent(synth, user_id)
                    if r:
                        results.append(r)
                result_text = "; ".join(results) if results else "Shopping list saved."
            except Exception as exc:
                logger.warning("form/confirm shopping_list failed: %s", exc)
                result_text = f"Could not save list: {exc}"

        else:
            result_text = f"Form confirmed (panel_type={panel_type!r}). No specific handler registered."

    except Exception as exc:
        logger.exception("form/confirm unhandled error: %s", exc)
        raise HTTPException(status_code=500, detail="Form submission failed")

    # Broadcast panel_close_action_form so the overlay dismisses on the panel.
    try:
        close_payload: dict = {}
        if panel_id:
            close_payload["panel_id"] = panel_id
        await broadcaster.broadcast("all", "ui_action", {
            "action": {
                "id": f"form_close_{uuid.uuid4().hex[:8]}",
                "action_type": "panel_close_action_form",
                "payload": close_payload,
            }
        })
    except Exception as exc:
        logger.debug("form/confirm close broadcast failed (non-fatal): %s", exc)

    # Clear the active form state so voice returns to the main pipeline.
    if panel_id:
        try:
            from panel_form_state import clear_active_form
            clear_active_form(panel_id)
        except Exception as exc:
            logger.debug("form/confirm state clear failed (non-fatal): %s", exc)

    logger.info("form/confirm panel_type=%s user=%s result=%s", panel_type, user_id, result_text[:120])
    return {"status": "ok", "panel_type": panel_type, "result": result_text}
