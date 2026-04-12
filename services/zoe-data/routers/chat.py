"""
Chat proxy router: bridges the Zoe UI (REST+SSE) to the active agent backend.

Pi branch hybrid architecture:
- Tier 0: Intent router — regex-matched commands (lists, calendar, HA control)
  handled directly in <1s without any LLM.
- Tier 1: Pi Agent — BitNet b1.58 (:11434) for short/conversational turns,
  Gemma 4 E2B (:11435) for complex/personality turns. Both run host-native
  with MemPalace memory, HA control, and Bash self-extension tools.
  Activated when HERMES_FAST_PATH=false (set in Pi systemd unit).
- Jetson path (HERMES_FAST_PATH=true): Bonsai fast path + OpenClaw agent.
"""
import asyncio
import json
import logging
import re
import time
import uuid
import os
from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse
from intent_router import detect_intent, execute_intent, openclaw_user_message

# Intent → touch panel navigation map (page + optional form to open).
_INTENT_PANEL_NAV = {
    "calendar_create":   ("/touch/calendar.html", "new_event"),
    "note_create":       ("/touch/notes.html",    "new_note"),
    "journal_create":    ("/touch/journal.html",  "new_journal"),
    "weather":           ("/touch/weather.html",  None),
    "list_add":          ("/touch/lists.html",    "new_list_item"),
    "timer_create":      ("/touch/cooking.html",  "new_timer"),
    "recipe_search":     ("/touch/cooking.html",  "recipe_search"),
    "reminder_create":   (None,                   None),  # handled as toast
}

async def _broadcast_intent_nav(intent, panel_id: str | None = None) -> None:
    """Instantly broadcast panel_navigate + panel_open_form when intent is detected."""
    nav = _INTENT_PANEL_NAV.get(intent.name)
    if not nav:
        return
    page, form = nav
    try:
        from push import broadcaster
        if page:
            await broadcaster.broadcast("all", "ui_action", {
                "action": {
                    "id": f"intent_nav_{intent.name}",
                    "action_type": "panel_navigate",
                    "payload": {"url": page, "label": f"Opening {page.split('/')[-1]}"},
                }
            })
        if form:
            await asyncio.sleep(0.4)  # Brief delay so page loads before form opens.
            await broadcaster.broadcast("all", "ui_action", {
                "action": {
                    "id": f"intent_form_{intent.name}",
                    "action_type": "panel_open_form",
                    "payload": {"form": form, "prefill": intent.slots},
                }
            })
    except Exception as exc:
        logger.debug("_broadcast_intent_nav failed (non-fatal): %s", exc)
from openclaw_ws import openclaw_cli, chat_inject, discover_openclaw_capabilities
from pi_agent import run_pi_agent, run_pi_agent_streaming
from auth import get_current_user
from database import get_db
from ui_orchestrator import enqueue_ui_action
from risk_policy import classify_request, is_whatsapp_connect_request
from chat_session_title import derive_session_title, title_is_weak
from ag_ui_stream import AgRunRecorder, iter_openclaw_text_chunks, iter_text_message_chunks, new_run_ids
from ag_ui.core import (
    CustomEvent,
    EventType,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])
_MEMORY_AUTO_INGEST = os.environ.get("MEMORY_AUTO_INGEST", "false").lower() == "true"
_GUARDED_AUTO = os.environ.get("OPENCLAW_GUARDED_AUTO", "true").lower() == "true"
_ALL_TOOLS_ENABLED = os.environ.get("OPENCLAW_ALL_TOOLS_ENABLED", "true").lower() == "true"
_WHATSAPP_FLOW_ENABLED = os.environ.get("WHATSAPP_FLOW_ENABLED", "true").lower() == "true"

# Bonsai-8B fast path: enabled by default, bypasses OpenClaw for conversational turns.
# Set BONSAI_FAST_PATH=false to disable and always use OpenClaw.
_BONSAI_FAST_PATH = os.environ.get("BONSAI_FAST_PATH", "true").lower() == "true"
_BONSAI_URL = os.environ.get("BONSAI_URL", "http://127.0.0.1:11435")

# Pi Agent mode: activated when HERMES_FAST_PATH=false (set in Pi systemd unit).
# Replaces both Bonsai and OpenClaw with local BitNet + Gemma + MemPalace loop.
_PI_AGENT_MODE = os.environ.get("HERMES_FAST_PATH", "true").lower() != "true"

# Keywords in Bonsai's response that signal it needs OpenClaw for this task.
# Bonsai is instructed to say ESCALATE_TO_OPENCLAW when it can't handle something.
_ESCALATION_MARKERS = {"ESCALATE_TO_OPENCLAW", "[ESCALATE]", "[CLOUD_REQUEST:"}

# System prompt for Bonsai in the web chat context (no tool execution — conversational only).
# Bonsai handles conversation and signals ESCALATE_TO_OPENCLAW for complex tasks.
_BONSAI_CHAT_SYSTEM_PROMPT = """You are Zoe, a warm, capable home assistant. Be concise and helpful.

You handle CONVERSATIONAL responses only in this channel — calendar/list/reminder tool calls are handled automatically by the system before reaching you.

For any of these, respond with EXACTLY "ESCALATE_TO_OPENCLAW" (nothing else):
- Multi-step planning or project breakdown
- Code generation, debugging, or technical analysis
- Research or web lookup requests
- Anything requiring specialist skills or sub-agents
- Tasks where you are genuinely uncertain

For everything else — questions, advice, explanations, opinions, general chat — respond directly and concisely.

User preferences (timezone, language, units) are stored in memory. Adapt your tone to what you know about the user."""

# Per-session concurrency guard: only one OpenClaw turn runs per session at a time.
# If a second request arrives for the same session while one is running, it waits
# up to _SESSION_LOCK_TIMEOUT_S before being rejected to avoid duplicate responses.
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}
_SESSION_LOCK_TIMEOUT_S = float(os.environ.get("ZOE_SESSION_LOCK_TIMEOUT_S", "5"))


def _get_session_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _SESSION_LOCKS:
        _SESSION_LOCKS[session_id] = asyncio.Lock()
    return _SESSION_LOCKS[session_id]

async def _persist_ag_ui_run(session_id: str, run_id: str, events: list) -> None:
    """Best-effort persistence of the wire-format event list for debugging / future resume."""
    if not events:
        return
    try:
        async for db in get_db():
            await db.execute(
                """INSERT INTO chat_ag_ui_runs (id, session_id, run_id, events)
                   VALUES (?, ?, ?, ?)""",
                (uuid.uuid4().hex[:16], session_id, run_id, json.dumps(events)),
            )
            await db.commit()
            break
    except Exception as e:
        logger.warning("chat_ag_ui_runs persist failed (non-fatal): %s", e)


def _extract_memory_candidates(user_message: str, assistant_response: str):
    """
    Small, conservative extraction pass for future review queue.
    Produces low-confidence proposals unless the signal is explicit.
    """
    candidates = []
    text = f"{user_message}\n{assistant_response}".lower()
    patterns = [
        ("preference", r"\bi (like|love|prefer)\b"),
        ("dislike", r"\bi (don't like|dislike|hate)\b"),
        ("profile", r"\bmy birthday is\b"),
        ("relationship", r"\bmy (mom|dad|wife|husband|partner|friend)\b"),
    ]
    for memory_type, pattern in patterns:
        if re.search(pattern, text):
            confidence = 0.92 if memory_type == "preference" and _MEMORY_AUTO_INGEST else 0.64
            candidates.append(
                {
                    "memory_type": memory_type,
                    "title": f"Learned {memory_type}",
                    "content": user_message.strip()[:500],
                    "entity_type": "self",
                    "entity_id": None,
                    "confidence": confidence,
                    "source_type": "chat",
                    "source_excerpt": user_message.strip()[:280],
                    "visibility": "personal",
                    "provenance": {"session_id": None},
                }
            )
    return candidates


async def _persist_memory_candidates(user_id: str, session_id: str, user_message: str, assistant_response: str):
    candidates = _extract_memory_candidates(user_message, assistant_response)
    if not candidates:
        return
    status = "approved" if _MEMORY_AUTO_INGEST else "pending_review"
    try:
        async for db in get_db():
            for item in candidates:
                item["provenance"] = {"session_id": session_id}
                await db.execute(
                    """INSERT INTO memory_items
                       (id, user_id, memory_type, title, content, entity_type, entity_id, confidence,
                        source_type, source_id, source_excerpt, provenance_json, visibility, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        uuid.uuid4().hex,
                        user_id,
                        item["memory_type"],
                        item["title"],
                        item["content"],
                        item["entity_type"],
                        item["entity_id"],
                        item["confidence"],
                        "chat",
                        session_id,
                        item["source_excerpt"],
                        json.dumps(item["provenance"]),
                        item["visibility"],
                        status,
                    ),
                )
            await db.commit()
            break
    except Exception as e:
        logger.warning("Memory candidate persistence failed: %s", e)


async def _ensure_user_and_chat_session(session_id: str, user_id: str) -> None:
    """Create users row and chat_sessions row if missing (UI sends client session ids before POST /sessions/)."""
    async for db in get_db():
        await db.execute(
            "INSERT OR IGNORE INTO users (id, name, role) VALUES (?, ?, ?)",
            (user_id, user_id, "member"),
        )
        await db.execute(
            "INSERT OR IGNORE INTO chat_sessions (id, user_id, title) VALUES (?, ?, ?)",
            (session_id, user_id, "New Chat"),
        )
        await db.commit()
        break


INTENT_LABELS = {
    "list_add": "Shopping List",
    "list_show": "Shopping List",
    "list_remove": "Shopping List",
    "calendar_create": "Calendar",
    "calendar_show": "Calendar",
    "reminder_create": "Reminders",
    "reminder_list": "Reminders",
    "people_create": "Contacts",
    "people_search": "Contacts",
    "note_create": "Notes",
    "note_search": "Notes",
    "weather": "Weather",
    "journal_create": "Journal",
    "journal_streak": "Journal",
    "journal_prompt": "Journal",
    "transaction_create": "Transactions",
    "transaction_summary": "Transactions",
    "daily_briefing": "Daily Briefing",
    "ha_full_setup": "Home Assistant setup",
}


async def run_openclaw_agent(
    message: str,
    session_id: str,
    user_id: str = "family-admin",
    *,
    user_role: str | None = None,
    username: str | None = None,
) -> str:
    """Route through OpenClaw for full memory, personality, and tool access."""
    return await openclaw_cli(
        message,
        session_id,
        user_id,
        user_role=user_role,
        username=username,
    )


async def _bonsai_slot_free() -> bool:
    """Return True if Bonsai has a free inference slot (no queued requests)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            r = await client.get(f"{_BONSAI_URL}/slots")
            if r.status_code == 200:
                slots = r.json()
                return all(not s.get("is_processing", False) for s in slots)
    except Exception:
        pass
    return True  # Assume free if check fails


async def run_bonsai_agent(
    message: str,
    session_id: str,
    user_id: str = "family-admin",
) -> tuple[str, bool]:
    """
    Call Bonsai-8B for a fast conversational response.

    Returns (response_text, needs_escalation).
    needs_escalation=True means Bonsai is busy or signalled the task requires OpenClaw.
    """
    import httpx

    # Skip if Bonsai is already busy — another request (e.g. Hermes API) is running.
    # This avoids queuing behind a 45s MCP-heavy request from another platform.
    if not await _bonsai_slot_free():
        logger.info("Bonsai busy — routing directly to OpenClaw for session %s", session_id)
        return "", True

    payload = {
        "model": "bonsai-8b",
        "messages": [
            {"role": "system", "content": _BONSAI_CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ],
        "max_tokens": 512,
        "temperature": 0.5,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            t0 = time.monotonic()
            r = await client.post(f"{_BONSAI_URL}/v1/chat/completions", json=payload)
            elapsed = time.monotonic() - t0

            if r.status_code != 200:
                logger.warning("Bonsai returned %s — falling back to OpenClaw", r.status_code)
                return "", True

            data = r.json()
            text = data["choices"][0]["message"]["content"].strip()
            logger.info("Bonsai responded in %.1fs: %s...", elapsed, text[:60])

            # Check if Bonsai is signalling escalation
            needs_escalation = any(marker in text for marker in _ESCALATION_MARKERS)
            if needs_escalation:
                logger.info("Bonsai signalled escalation for session %s", session_id)
                return "", True

            return text, False

    except Exception as e:
        logger.warning("Bonsai fast path failed (%s) — falling back to OpenClaw", e)
        return "", True


async def _iter_openclaw_heartbeats(emit, task: asyncio.Task, *, phase_label: str = "OpenClaw"):
    """Emit run_log + STATE_SNAPSHOT every ~4s while the OpenClaw subprocess runs."""
    t0 = time.monotonic()
    while not task.done():
        await asyncio.wait({task}, timeout=4.0)
        if task.done():
            break
        elapsed = int(time.monotonic() - t0)
        yield emit(
            CustomEvent(
                name="zoe.run_log",
                value={
                    "level": "info",
                    "message": f"{phase_label} still working… {elapsed}s elapsed (browser and tools can take several minutes).",
                },
            )
        )
        yield emit(
            StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT,
                snapshot={
                    "status": "generating",
                    "phase": "openclaw",
                    "model": phase_label,
                    "detail": f"Running agent… {elapsed}s",
                },
            )
        )


async def chat_inject_background(user_message: str, assistant_response: str, intent_name: str, user_id: str = "family-admin", session_id: str = "web"):
    """Fire-and-forget: inject a summary into the correct user's OpenClaw session."""
    try:
        summary = f"[Intent: {intent_name}] User: {user_message} | Result: {assistant_response}"
        await chat_inject(summary, user_id, session_id)
        logger.info(f"chat.inject sent for intent {intent_name}")
    except Exception as e:
        logger.warning(f"chat.inject background failed (non-fatal): {e}")


_UI_MARKER_RE = re.compile(r":::zoe-ui\s*\n(.*?)\n:::", re.DOTALL)
_APPROVE_RE = re.compile(r"^/approve\s+([a-zA-Z0-9_-]{8,})\s*(.*)$")


def _extract_approval_token(message: str):
    m = _APPROVE_RE.match((message or "").strip())
    if not m:
        return None, message
    token = m.group(1)
    rest = (m.group(2) or "").strip()
    return token, rest or message


def _extract_ui_actions(text: str):
    """Extract :::zoe-ui JSON blocks from response text. Returns (clean_text, actions)."""
    actions = []
    for match in _UI_MARKER_RE.finditer(text):
        try:
            payload = json.loads(match.group(1).strip())
            actions.append(payload)
        except json.JSONDecodeError:
            pass
    clean = _UI_MARKER_RE.sub("", text).strip()
    return clean, actions


def _map_ui_payload_to_action(action: dict):
    if "command" in action:
        command = action.get("command")
        params = action.get("params", {})
        if command == "navigate":
            return "navigate", params
        if command == "notify":
            return "notify", params
        if command == "refresh_data":
            return "refresh", params
        if command in {"add_widget", "remove_widget"}:
            return "update_record", {"command": command, **params}
        return "highlight", {"command": command, **params}
    if "component" in action:
        return "open_panel", action
    return None, {}


async def _queue_ui_actions_background(actions: list, user_id: str, session_id: str):
    if not actions:
        return
    try:
        async for db in get_db():
            for i, action in enumerate(actions):
                action_type, payload = _map_ui_payload_to_action(action)
                if not action_type:
                    continue
                await enqueue_ui_action(
                    db,
                    user_id=user_id,
                    action_type=action_type,
                    payload=payload,
                    requested_by="chat",
                    chat_session_id=session_id,
                    idempotency_key=f"{session_id}:{action_type}:{i}",
                )
            break
    except Exception as e:
        logger.warning(f"Failed to enqueue UI actions (non-fatal): {e}")


async def _create_pending_approval(user_id: str, session_id: str, message: str, risk_level: str, reason: str, normalized_action: str) -> str:
    approval_id = uuid.uuid4().hex[:16]
    async for db in get_db():
        await db.execute(
            """INSERT INTO openclaw_approvals
               (id, session_id, user_id, request_text, normalized_action, risk_level, status, reason)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (approval_id, session_id, user_id, message, normalized_action, risk_level, reason),
        )
        await db.commit()
        break
    return approval_id


async def _resolve_approval(user_id: str, approval_id: str) -> dict | None:
    async for db in get_db():
        rows = await db.execute_fetchall(
            """SELECT * FROM openclaw_approvals
               WHERE id = ? AND user_id = ? AND status = 'pending'
               ORDER BY created_at DESC LIMIT 1""",
            (approval_id, user_id),
        )
        if not rows:
            return None
        row = dict(rows[0])
        await db.execute(
            "UPDATE openclaw_approvals SET status='approved', resolved_at=datetime('now') WHERE id = ?",
            (approval_id,),
        )
        await db.commit()
        return row
    return None


async def _record_run_state(run_id: str, session_id: str, user_id: str, mode: str, status: str, request_text: str, response_text: str | None = None, metadata: dict | None = None):
    async for db in get_db():
        await db.execute(
            """INSERT INTO openclaw_run_state
               (id, session_id, user_id, mode, status, request_text, response_text, metadata, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? IN ('completed','error','cancelled') THEN datetime('now') ELSE NULL END)
               ON CONFLICT(id) DO UPDATE SET
                 status=excluded.status,
                 response_text=COALESCE(excluded.response_text, response_text),
                 metadata=COALESCE(excluded.metadata, metadata),
                 finished_at=CASE WHEN excluded.status IN ('completed','error','cancelled') THEN datetime('now') ELSE finished_at END""",
            (
                run_id,
                session_id,
                user_id,
                mode,
                status,
                request_text,
                response_text,
                json.dumps(metadata) if metadata else None,
                status,
            ),
        )
        await db.commit()
        break


async def _stream_openclaw_assistant_ag(
    enc: EventEncoder,
    recorder: AgRunRecorder,
    assistant_message_id: str,
    response_text: str,
):
    """TEXT_MESSAGE_* for assistant reply, then CUSTOM zoe.ui_* for generative UI."""
    clean_text, actions = _extract_ui_actions(response_text)
    yield recorder.emit(
        enc,
        CustomEvent(
            name="zoe.run_log",
            value={"level": "info", "message": "OpenClaw response received", "chars": len(clean_text)},
        ),
    )
    yield recorder.emit(
        enc,
        TextMessageStartEvent(
            type=EventType.TEXT_MESSAGE_START,
            message_id=assistant_message_id,
            role="assistant",
        ),
    )
    async for line in iter_openclaw_text_chunks(enc, recorder, assistant_message_id, clean_text):
        yield line
    yield recorder.emit(
        enc,
        TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_message_id),
    )
    for action in actions:
        if "component" in action:
            yield recorder.emit(enc, CustomEvent(name="zoe.ui_component", value=action))
        elif "command" in action:
            yield recorder.emit(enc, CustomEvent(name="zoe.ui_command", value=action))


async def chat_stream_generator(
    message: str,
    session_id: str,
    user: dict,
    *,
    force_openclaw: bool = False,
):
    user_id = user["user_id"]
    user_role = user.get("role")
    username = user.get("username")
    await _ensure_user_and_chat_session(session_id, user_id)
    enc = EventEncoder()
    recorder = AgRunRecorder()
    run_id, assistant_message_id = new_run_ids()

    def emit(ev):
        return recorder.emit(enc, ev)

    try:
        yield emit(RunStartedEvent(type=EventType.RUN_STARTED, thread_id=session_id, run_id=run_id))
        yield emit(
            CustomEvent(
                name="zoe.run_meta",
                value={"runId": run_id, "sessionId": session_id, "mode": "chat", "forceOpenClaw": force_openclaw},
            )
        )
        yield emit(
            CustomEvent(
                name="zoe.session",
                value={"sessionId": session_id, "messageId": assistant_message_id},
            )
        )
        await _record_run_state(run_id, session_id, user_id, mode="chat", status="running", request_text=message)

        approval_token, message_for_processing = _extract_approval_token(message)
        if approval_token:
            approved = await _resolve_approval(user_id, approval_token)
            if not approved:
                yield emit(
                    RunErrorEvent(
                        type=EventType.RUN_ERROR,
                        message="Approval token is invalid or already used.",
                        code="approval_invalid",
                    )
                )
                await _record_run_state(run_id, session_id, user_id, mode="chat", status="error", request_text=message)
                return
            message_for_processing = approved.get("request_text") or message_for_processing
            yield emit(
                CustomEvent(
                    name="zoe.run_log",
                    value={"level": "info", "message": "Approval accepted. Executing requested action."},
                )
            )

        if _GUARDED_AUTO:
            risk = classify_request(message_for_processing)
            if risk.requires_confirmation and not approval_token:
                approval_id = await _create_pending_approval(
                    user_id=user_id,
                    session_id=session_id,
                    message=message_for_processing,
                    risk_level=risk.level,
                    reason=risk.reason,
                    normalized_action=risk.normalized_action,
                )
                yield emit(
                    CustomEvent(
                        name="zoe.ui_component",
                        value={
                            "component": "confirmation",
                            "props": {
                                "title": "Approval Required",
                                "description": f"{risk.reason}. Approve to continue.",
                                "yes_text": "Approve",
                                "no_text": "Cancel",
                                "yes_action": f"/approve {approval_id}",
                            },
                        },
                    )
                )
                yield emit(
                    CustomEvent(
                        name="zoe.run_log",
                        value={"level": "warn", "message": f"Action gated ({risk.level} risk). Pending approval id: {approval_id}"},
                    )
                )
                yield emit(
                    RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id)
                )
                await _record_run_state(
                    run_id,
                    session_id,
                    user_id,
                    mode="chat",
                    status="completed",
                    request_text=message,
                    response_text="Approval required",
                    metadata={"approval_id": approval_id, "risk": risk.level},
                )
                return

        lc = message_for_processing.lower().strip()
        if "what can you do right now" in lc or lc in {"/capabilities", "capabilities", "tools"}:
            caps = await discover_openclaw_capabilities()
            caps_text = json.dumps(caps.get("payload", caps), indent=2)[:12000]
            yield emit(
                TextMessageStartEvent(
                    type=EventType.TEXT_MESSAGE_START,
                    message_id=assistant_message_id,
                    role="assistant",
                )
            )
            async for line in iter_text_message_chunks(enc, recorder, assistant_message_id, f"OpenClaw capabilities:\n```json\n{caps_text}\n```"):
                yield line
            yield emit(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_message_id))
            yield emit(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id))
            await _record_run_state(run_id, session_id, user_id, mode="chat", status="completed", request_text=message, response_text="capabilities")
            return

        if _WHATSAPP_FLOW_ENABLED and is_whatsapp_connect_request(message_for_processing):
            yield emit(
                CustomEvent(
                    name="zoe.run_log",
                    value={"level": "info", "message": "Starting WhatsApp connect flow preflight..."},
                )
            )
            flow_text = (
                "Starting WhatsApp connection workflow.\n"
                "1) Preflight checks\n"
                "2) Credential/session validation\n"
                "3) QR/session handshake\n"
                "4) Webhook and test message validation\n"
                "I will now run this through OpenClaw with guarded confirmations."
            )
            yield emit(TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=assistant_message_id, role="assistant"))
            async for line in iter_text_message_chunks(enc, recorder, assistant_message_id, flow_text):
                yield line
            yield emit(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_message_id))
            message_for_processing = (
                "Connect WhatsApp integration for user with full guided flow: "
                "preflight checks, credential/session validation, qr/session setup, webhook test, "
                "and remediation steps. Use guarded execution and ask for confirmation before any write/auth step."
            )

        use_intent_fast_path = (not force_openclaw) and _ALL_TOOLS_ENABLED
        if message_for_processing.startswith("/openclaw "):
            message_for_processing = message_for_processing[len("/openclaw ") :].strip()
            use_intent_fast_path = False

        intent = detect_intent(message_for_processing) if use_intent_fast_path else None
        if intent:
            logger.info("Intent matched: %s slots=%s", intent.name, getattr(intent, "slots", None))
            # Immediately navigate the touch panel before executing the intent.
            asyncio.ensure_future(_broadcast_intent_nav(intent))

            label = INTENT_LABELS.get(intent.name, intent.name)
            tool_call_id = uuid.uuid4().hex[:12]
            tool_name = f"zoe-data.{intent.name}"

            yield emit(StepStartedEvent(type=EventType.STEP_STARTED, step_name=label))
            yield emit(
                ToolCallStartEvent(
                    type=EventType.TOOL_CALL_START,
                    tool_call_id=tool_call_id,
                    tool_call_name=tool_name,
                    parent_message_id=assistant_message_id,
                )
            )
            slots = getattr(intent, "slots", None) or {}
            yield emit(
                ToolCallArgsEvent(
                    type=EventType.TOOL_CALL_ARGS,
                    tool_call_id=tool_call_id,
                    delta=json.dumps(slots),
                )
            )
            yield emit(ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tool_call_id))

            result = await execute_intent(intent, user_id)

            if result:
                body = result if len(result) <= 12000 else result[:12000] + "…"
                yield emit(
                    ToolCallResultEvent(
                        type=EventType.TOOL_CALL_RESULT,
                        message_id=assistant_message_id,
                        tool_call_id=tool_call_id,
                        content=body,
                        role="tool",
                    )
                )
                yield emit(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=label))
                yield emit(
                    TextMessageStartEvent(
                        type=EventType.TEXT_MESSAGE_START,
                        message_id=assistant_message_id,
                        role="assistant",
                    )
                )
                async for line in iter_text_message_chunks(enc, recorder, assistant_message_id, result):
                    yield line
                yield emit(
                    TextMessageEndEvent(
                        type=EventType.TEXT_MESSAGE_END,
                        message_id=assistant_message_id,
                    )
                )
                asyncio.ensure_future(chat_inject_background(message_for_processing, result, intent.name, user_id, session_id))
                asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, result))
            else:
                logger.warning("Intent %s execution failed, falling back to LLM", intent.name)
                yield emit(
                    ToolCallResultEvent(
                        type=EventType.TOOL_CALL_RESULT,
                        message_id=assistant_message_id,
                        tool_call_id=tool_call_id,
                        content=json.dumps({"status": "failed", "tool": tool_name}),
                        role="tool",
                    )
                )
                yield emit(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=label))
                if _PI_AGENT_MODE:
                    yield emit(
                        StateSnapshotEvent(
                            type=EventType.STATE_SNAPSHOT,
                            snapshot={
                                "status": "generating",
                                "phase": "pi_agent",
                                "model": "Zoe (Pi Agent fallback)",
                                "detail": "Thinking…",
                            },
                        )
                    )
                    task = asyncio.create_task(
                        run_pi_agent(message_for_processing, session_id, user_id)
                    )
                    async for hb in _iter_openclaw_heartbeats(emit, task, phase_label="Pi Agent"):
                        yield hb
                    response_text = await task
                else:
                    yield emit(
                        StateSnapshotEvent(
                            type=EventType.STATE_SNAPSHOT,
                            snapshot={
                                "status": "generating",
                                "phase": "openclaw",
                                "model": "Zoe (LLM fallback)",
                                "detail": "Handing off to OpenClaw…",
                            },
                        )
                    )
                    oc_message = openclaw_user_message(intent, message_for_processing)
                    yield emit(
                        CustomEvent(
                            name="zoe.run_log",
                            value={
                                "level": "info",
                                "message": "Starting OpenClaw agent (browser and tools can take 30s to several minutes).",
                            },
                        )
                    )
                    task = asyncio.create_task(
                        run_openclaw_agent(
                            oc_message,
                            session_id,
                            user_id,
                            user_role=user_role,
                            username=username,
                        )
                    )
                    async for hb in _iter_openclaw_heartbeats(emit, task):
                        yield hb
                    response_text = await task
                _, actions = _extract_ui_actions(response_text)
                if actions:
                    asyncio.ensure_future(_queue_ui_actions_background(actions, user_id, session_id))
                asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, response_text))
                async for line in _stream_openclaw_assistant_ag(
                    enc, recorder, assistant_message_id, response_text
                ):
                    yield line
        else:
            if _PI_AGENT_MODE:
                # ── Pi Agent: BitNet b1.58 or Gemma 4 E2B with MemPalace + tools ──
                from pi_agent import _route_to_model
                model_label = "BitNet" if _route_to_model(message_for_processing) == "bitnet" else "Gemma"
                yield emit(
                    StateSnapshotEvent(
                        type=EventType.STATE_SNAPSHOT,
                        snapshot={
                            "status": "generating",
                            "phase": "pi_agent",
                            "model": f"Zoe ({model_label})",
                            "detail": "Thinking…",
                        },
                    )
                )
                yield emit(
                    CustomEvent(
                        name="zoe.run_log",
                        value={"level": "info", "message": f"Pi Agent running ({model_label})…"},
                    )
                )
                task = asyncio.create_task(
                    run_pi_agent(message_for_processing, session_id, user_id)
                )
                async for hb in _iter_openclaw_heartbeats(emit, task, phase_label=f"Pi Agent ({model_label})"):
                    yield hb
                response_text = await task
                asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, response_text))
                async for line in _stream_openclaw_assistant_ag(
                    enc, recorder, assistant_message_id, response_text
                ):
                    yield line

            else:
                # ── Jetson: Tier 2 Bonsai-8B fast path ──
                bonsai_response = ""
                used_bonsai = False
                if _BONSAI_FAST_PATH and not force_openclaw:
                    yield emit(
                        StateSnapshotEvent(
                            type=EventType.STATE_SNAPSHOT,
                            snapshot={
                                "status": "generating",
                                "phase": "bonsai",
                                "model": "Zoe",
                                "detail": "Thinking…",
                            },
                        )
                    )
                    bonsai_response, needs_escalation = await run_bonsai_agent(
                        message_for_processing, session_id, user_id
                    )
                    if not needs_escalation and bonsai_response:
                        used_bonsai = True

                if used_bonsai:
                    asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, bonsai_response))
                    async for line in _stream_openclaw_assistant_ag(
                        enc, recorder, assistant_message_id, bonsai_response
                    ):
                        yield line
                else:
                    # ── Jetson: Tier 3 OpenClaw ──
                    yield emit(
                        StateSnapshotEvent(
                            type=EventType.STATE_SNAPSHOT,
                            snapshot={
                                "status": "generating",
                                "phase": "openclaw",
                                "model": "Zoe",
                                "detail": "Starting OpenClaw agent…",
                            },
                        )
                    )
                    oc_message = openclaw_user_message(intent, message_for_processing)
                    yield emit(
                        CustomEvent(
                            name="zoe.run_log",
                            value={
                                "level": "info",
                                "message": "Starting OpenClaw agent (browser and tools can take 30s to several minutes).",
                            },
                        )
                    )
                    task = asyncio.create_task(
                        run_openclaw_agent(
                            oc_message,
                            session_id,
                            user_id,
                            user_role=user_role,
                            username=username,
                        )
                    )
                    async for hb in _iter_openclaw_heartbeats(emit, task):
                        yield hb
                    response_text = await task
                    _, actions = _extract_ui_actions(response_text)
                    if actions:
                        asyncio.ensure_future(_queue_ui_actions_background(actions, user_id, session_id))
                    asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, response_text))
                    async for line in _stream_openclaw_assistant_ag(
                        enc, recorder, assistant_message_id, response_text
                    ):
                        yield line

        yield emit(
            RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id)
        )
        await _record_run_state(run_id, session_id, user_id, mode="chat", status="completed", request_text=message)
    except Exception as e:
        logger.exception("Error in chat stream: %s", e)
        yield emit(
            RunErrorEvent(
                type=EventType.RUN_ERROR,
                message="Something went wrong. Please try again.",
                code="internal_error",
            )
        )
        await _record_run_state(run_id, session_id, user_id, mode="chat", status="error", request_text=message, response_text=str(e))
    finally:
        await _persist_ag_ui_run(session_id, run_id, recorder.events)


@router.post("/")
async def chat(request: Request, user: dict = Depends(get_current_user), stream: bool = True):
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", f"web_{uuid.uuid4().hex[:8]}")
    user_id = user["user_id"]
    force_openclaw = bool(body.get("force_openclaw", False))

    if not message:
        return {"error": "No message provided"}

    await _ensure_user_and_chat_session(session_id, user_id)

    if stream:
        # Wrap the generator with a per-session lock so parallel SSE connections
        # for the same session don't interleave OpenClaw calls.
        async def _locked_stream():
            lock = _get_session_lock(session_id)
            try:
                acquired = await asyncio.wait_for(lock.acquire(), timeout=_SESSION_LOCK_TIMEOUT_S)
            except asyncio.TimeoutError:
                acquired = False
            if not acquired:
                logger.warning("session %s concurrency timeout — rejecting duplicate request", session_id)
                enc = EventEncoder()
                err_ev = enc.encode(RunErrorEvent(type=EventType.RUN_ERROR, message="Another request is already in progress for this session. Please wait.", code="session_busy"))
                yield err_ev
                return
            try:
                async for chunk in chat_stream_generator(message, session_id, user, force_openclaw=force_openclaw):
                    yield chunk
            finally:
                lock.release()

        return StreamingResponse(
            _locked_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        approval_token, message_for_processing = _extract_approval_token(message)
        if approval_token:
            approved = await _resolve_approval(user_id, approval_token)
            if not approved:
                return {"error": "Invalid approval token", "session_id": session_id}
            message_for_processing = approved.get("request_text") or message_for_processing

        if _GUARDED_AUTO and not approval_token:
            risk = classify_request(message_for_processing)
            if risk.requires_confirmation:
                approval_id = await _create_pending_approval(
                    user_id=user_id,
                    session_id=session_id,
                    message=message_for_processing,
                    risk_level=risk.level,
                    reason=risk.reason,
                    normalized_action=risk.normalized_action,
                )
                return {
                    "response": "Approval required before executing this action.",
                    "session_id": session_id,
                    "ui_components": [
                        {
                            "component": "confirmation",
                            "props": {
                                "description": f"{risk.reason}. Approve to continue.",
                                "yes_text": "Approve",
                                "no_text": "Cancel",
                                "yes_action": f"/approve {approval_id}",
                            },
                        }
                    ],
                }

        lc = message_for_processing.lower().strip()
        if "what can you do right now" in lc or lc in {"/capabilities", "capabilities", "tools"}:
            caps = await discover_openclaw_capabilities()
            return {"response": "OpenClaw capabilities:\n" + json.dumps(caps.get("payload", caps), indent=2), "session_id": session_id}

        use_intent_fast_path = (not force_openclaw) and _ALL_TOOLS_ENABLED
        if message_for_processing.startswith("/openclaw "):
            message_for_processing = message_for_processing[len("/openclaw ") :].strip()
            use_intent_fast_path = False

        intent = detect_intent(message_for_processing) if use_intent_fast_path else None
        if intent:
            result = await execute_intent(intent, user_id)
            if result:
                asyncio.ensure_future(
                    chat_inject_background(message_for_processing, result, intent.name, user_id, session_id)
                )
                asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, result))
                return {"response": result, "session_id": session_id}
        if _WHATSAPP_FLOW_ENABLED and is_whatsapp_connect_request(message_for_processing):
            message_for_processing = (
                "Connect WhatsApp integration for user with full guided flow: preflight checks, "
                "credential/session validation, qr/session setup, webhook test, remediation."
            )

        if _PI_AGENT_MODE:
            response_text = await run_pi_agent(message_for_processing, session_id, user_id)
        else:
            # Try Bonsai fast path first for conversational messages
            if _BONSAI_FAST_PATH and not force_openclaw:
                bonsai_text, needs_escalation = await run_bonsai_agent(message_for_processing, session_id, user_id)
                if not needs_escalation and bonsai_text:
                    asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, bonsai_text))
                    return {"response": bonsai_text, "session_id": session_id}

            oc_message = openclaw_user_message(intent, message_for_processing)
            response_text = await run_openclaw_agent(
                oc_message,
                session_id,
                user_id,
                user_role=user.get("role"),
                username=user.get("username"),
            )
        clean_text, actions = _extract_ui_actions(response_text)
        resp = {"response": clean_text, "session_id": session_id}
        ui_commands = [a for a in actions if "command" in a]
        ui_components = [a for a in actions if "component" in a]
        if ui_commands:
            resp["ui_commands"] = ui_commands
        if ui_components:
            resp["ui_components"] = ui_components
        if actions:
            asyncio.ensure_future(_queue_ui_actions_background(actions, user_id, session_id))
        asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, response_text))
        return resp


@router.get("/sessions/")
async def list_sessions(user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    async for db in get_db():
        rows = await db.execute_fetchall(
            """SELECT s.id, s.title, s.created_at, s.updated_at,
                      (SELECT COUNT(*) FROM chat_messages WHERE session_id = s.id) AS message_count
               FROM chat_sessions s WHERE s.user_id = ? ORDER BY s.updated_at DESC LIMIT 50""",
            (user_id,),
        )
        sessions = [dict(r) for r in rows]
        return {"sessions": sessions, "count": len(sessions)}


@router.post("/sessions/")
async def create_session(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    session_id = f"web_{uuid.uuid4().hex[:8]}"
    user_id = user["user_id"]
    title = body.get("title", "New Chat")
    async for db in get_db():
        await db.execute(
            "INSERT INTO chat_sessions (id, user_id, title) VALUES (?, ?, ?)",
            (session_id, user_id, title),
        )
        await db.commit()
    return {"session_id": session_id, "title": title, "created_at": "now"}


@router.get("/sessions/{session_id}/messages/")
async def get_session_messages(session_id: str, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    async for db in get_db():
        owner = await db.execute_fetchall(
            "SELECT id FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
        )
        if not owner:
            return {"messages": [], "count": 0}
        rows = await db.execute_fetchall(
            "SELECT id, role, content, metadata, created_at FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        )
        messages = [dict(r) for r in rows]
        return {"messages": messages, "count": len(messages)}


@router.post("/sessions/{session_id}/messages/")
async def save_message(session_id: str, request: Request, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    body = await request.json()
    msg_id = uuid.uuid4().hex[:12]
    role = body.get("role", "user")
    content = body.get("content", "")
    metadata = json.dumps(body.get("metadata")) if body.get("metadata") else None
    async for db in get_db():
        await db.execute(
            "INSERT OR IGNORE INTO users (id, name, role) VALUES (?, ?, ?)",
            (user_id, user_id, "member"),
        )
        await db.execute(
            "INSERT OR IGNORE INTO chat_sessions (id, user_id, title) VALUES (?, ?, ?)",
            (session_id, user_id, "New Chat"),
        )
        owner = await db.execute_fetchall(
            "SELECT id FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
        )
        if not owner:
            await db.commit()
            return {"status": "error", "message": "Session not found"}
        title_row = await db.execute_fetchall(
            "SELECT title FROM chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        current_title = dict(title_row[0])["title"] if title_row else "New Chat"

        await db.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES (?, ?, ?, ?, ?)",
            (msg_id, session_id, role, content, metadata),
        )

        new_title = None
        if role == "user" and content.strip():
            candidate = derive_session_title(content)
            if title_is_weak(current_title):
                new_title = candidate
        elif role == "assistant" and content.strip():
            if title_is_weak(current_title):
                new_title = derive_session_title(content)

        if new_title:
            await db.execute(
                """UPDATE chat_sessions SET updated_at = datetime('now'), title = ?
                   WHERE id = ? AND user_id = ?""",
                (new_title, session_id, user_id),
            )
        else:
            await db.execute(
                "UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
        await db.commit()
    return {"status": "ok", "id": msg_id}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    async for db in get_db():
        owner = await db.execute_fetchall(
            "SELECT id FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
        )
        if not owner:
            return {"status": "error", "message": "Session not found"}
        await db.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
        await db.commit()
    return {"status": "ok"}


@router.get("/capabilities")
async def chat_capabilities(user: dict = Depends(get_current_user)):
    caps = await discover_openclaw_capabilities()
    return {"ok": caps.get("ok", False), "capabilities": caps.get("payload", caps), "source_method": caps.get("source_method")}


@router.post("/whatsapp/connect")
async def whatsapp_connect(request: Request, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    body = await request.json()
    session_id = body.get("session_id", f"web_{uuid.uuid4().hex[:8]}")
    approved = bool(body.get("approved", False))
    if not approved:
        approval_id = await _create_pending_approval(
            user_id=user_id,
            session_id=session_id,
            message="Connect to WhatsApp",
            risk_level="high",
            reason="External account linking requires confirmation",
            normalized_action="connect to whatsapp",
        )
        return {
            "status": "approval_required",
            "approval_id": approval_id,
            "prompt": f"/approve {approval_id}",
        }
    guidance = (
        "Connect WhatsApp integration for user with full guided flow: "
        "preflight checks, credential/session validation, qr/session setup, "
        "webhook test message validation, and remediation on failures."
    )
    response_text = await run_openclaw_agent(
        guidance,
        session_id,
        user_id,
        user_role=user.get("role"),
        username=user.get("username"),
    )
    return {"status": "ok", "response": response_text}


@router.get("/approvals/pending")
async def pending_approvals(limit: int = 20, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    async for db in get_db():
        rows = await db.execute_fetchall(
            """SELECT id, session_id, request_text, risk_level, reason, created_at
               FROM openclaw_approvals
               WHERE user_id = ? AND status = 'pending'
               ORDER BY created_at DESC
               LIMIT ?""",
            (user_id, limit),
        )
        return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get("/runs/{session_id}/latest")
async def latest_run(session_id: str, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    async for db in get_db():
        rows = await db.execute_fetchall(
            """SELECT id, status, request_text, response_text, metadata, started_at, finished_at
               FROM openclaw_run_state
               WHERE session_id = ? AND user_id = ?
               ORDER BY started_at DESC LIMIT 1""",
            (session_id, user_id),
        )
        if not rows:
            return {"run": None}
        row = dict(rows[0])
        row["metadata"] = json.loads(row["metadata"] or "{}")
        return {"run": row}


@router.post("/runs/{session_id}/resume")
async def resume_run(session_id: str, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    async for db in get_db():
        rows = await db.execute_fetchall(
            """SELECT request_text FROM openclaw_run_state
               WHERE session_id = ? AND user_id = ?
               ORDER BY started_at DESC LIMIT 1""",
            (session_id, user_id),
        )
        if not rows:
            return {"status": "error", "message": "No run to resume"}
        req = dict(rows[0]).get("request_text")
        return {"status": "ok", "resume_prompt": req}


@router.post("/runs/{session_id}/cancel")
async def cancel_latest_run(session_id: str, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    async for db in get_db():
        await db.execute(
            """UPDATE openclaw_run_state
               SET status='cancelled', finished_at=datetime('now')
               WHERE id = (
                 SELECT id FROM openclaw_run_state
                 WHERE session_id = ? AND user_id = ?
                 ORDER BY started_at DESC LIMIT 1
               )""",
            (session_id, user_id),
        )
        await db.commit()
        return {"status": "ok"}


@router.post("/feedback/{interaction_id}")
async def submit_feedback(interaction_id: str, request: Request, feedback_type: str = "thumbs_up", user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    corrected = body.get("corrected_response")
    async for db in get_db():
        await db.execute(
            """INSERT INTO chat_feedback (id, interaction_id, user_id, feedback_type, corrected_response)
               VALUES (?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex[:12], interaction_id, user_id, feedback_type, corrected),
        )
        await db.commit()
    logger.info(f"Feedback {feedback_type} from {user_id} on {interaction_id}")
    messages = {
        "thumbs_up": "Thanks — glad that helped.",
        "thumbs_down": "Thanks — we'll use that to improve.",
        "correction": "Thanks — noted for learning.",
    }
    return {
        "status": "ok",
        "feedback_type": feedback_type,
        "message": messages.get(feedback_type, "Feedback saved."),
    }
