import asyncio
import os
import time
import uuid as _uuid_mod
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from database import init_db
from push import broadcaster
from routers import (
    calendar_router,
    lists_router,
    people_router,
    memories_router,
    reminders_router,
    notes_router,
    journal_router,
    transactions_router,
    weather_router,
    system_router,
    notifications_router,
    chat_router,
    ui_router,
    voice_tts_router,
    user_profile_router,
    panel_auth_router,
    capability_matrix_router,
    music_router,
)
from routers.dashboard import router as dashboard_router
from routers.stubs import router as stubs_router
from routers.push import router as push_router
from routers.proactive import router as proactive_router
from routers.system import (
    start_openclaw_background_tasks,
    start_memory_digest_background,
    start_memory_consolidation_background,
)
from system_updates import start_zoe_update_background_tasks
from routers.openclaw import router as openclaw_router
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s rid=%(request_id)s %(message)s",
)
logger = logging.getLogger(__name__)

_REQUEST_ID_CTX_VAR = None  # set after app creation to avoid import cycles
_openclaw_bg_task = None
_digest_bg_task = None
_zoe_update_bg_task = None
_memory_capture_health: dict[str, str] = {"status": "unknown", "detail": "startup pending"}


class RequestIdFilter(logging.Filter):
    """Inject request_id into every log record while a request is active."""
    def filter(self, record):
        record.request_id = getattr(_REQUEST_ID_CTX_VAR, "get", lambda d: d)(None) or "-"
        return True


# Apply the filter to root logger so all modules get it.
logging.root.addFilter(RequestIdFilter())
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RequestIdFilter())


async def _run_memory_capture_startup_probe() -> None:
    """Validate memory capture plumbing at startup.

    This is a non-invasive probe: it verifies extractor import/patterns and
    MemoryService read paths without writing synthetic rows.

    Timeouts are generous (10 s) because at cold boot the vector index and
    ONNX runtime are both initialising concurrently with DB setup.  A
    background retry fires 45 s later so transient cold-boot failures
    self-heal without requiring a service restart.
    """
    global _memory_capture_health
    try:
        from memory_extractor import extract_candidates
        from memory_service import get_memory_service

        probe = "remember that i met startup-probe person"
        candidates = extract_candidates(probe, "")
        if not candidates:
            raise RuntimeError("memory_extractor produced no candidates for probe")

        svc = get_memory_service()
        await asyncio.wait_for(svc.load_for_prompt("family-admin", limit=1), timeout=10.0)
        await asyncio.wait_for(
            svc.search("startup probe", user_id="family-admin", limit=1, timeout_s=3.0),
            timeout=10.0,
        )
        _memory_capture_health = {"status": "ok", "detail": "extractor+service ready"}
        logger.info("Memory capture startup probe: OK")
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        _memory_capture_health = {"status": "degraded", "detail": detail[:240]}
        logger.error("Memory capture startup probe FAILED: %s", detail)
        if os.environ.get("ZOE_MEMORY_STARTUP_STRICT", "false").strip().lower() == "true":
            raise


async def _memory_capture_retry_task() -> None:
    """Background retry: re-run the startup probe once, 45 s after boot.

    Clears false-degraded status caused by cold-boot timeouts without
    requiring a full service restart.
    """
    await asyncio.sleep(45)
    if _memory_capture_health.get("status") == "ok":
        return
    logger.info("Memory capture retry probe starting...")
    await _run_memory_capture_startup_probe()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _openclaw_bg_task, _digest_bg_task, _zoe_update_bg_task
    logger.info("Initializing zoe-data database...")
    await init_db()
    logger.info("Database initialized. zoe-data is ready.")
    # One-time MemPalace migration: re-tag legacy records from wing="zoe" to wing="family-admin"
    try:
        from zoe_agent import migrate_mempalace_legacy_records
        await asyncio.get_event_loop().run_in_executor(None, migrate_mempalace_legacy_records)
    except Exception as _mig_exc:
        logger.warning("MemPalace migration (non-fatal): %s", _mig_exc)
    # Load device tokens into memory so voice daemons can authenticate.
    try:
        import aiosqlite
        from database import DB_PATH
        from routers.panel_auth import load_device_tokens
        async with aiosqlite.connect(DB_PATH) as _db:
            _db.row_factory = aiosqlite.Row
            await load_device_tokens(_db)
    except Exception as _exc:
        logger.warning("Could not pre-load device tokens: %s", _exc)
    await _run_memory_capture_startup_probe()
    asyncio.create_task(_memory_capture_retry_task(), name="memory_capture_retry")
    _openclaw_bg_task = start_openclaw_background_tasks()
    _digest_bg_task = start_memory_digest_background()
    _consolidation_bg_task = start_memory_consolidation_background()
    _zoe_update_bg_task = start_zoe_update_background_tasks()
    # Pi/Jetson Agent: warm Gemma's KV cache in background so first real query is fast
    # Check env directly to avoid circular import from routers.chat
    _pi_mode = os.environ.get("HERMES_FAST_PATH", "true").lower() != "true"
    _jetson_mode = os.environ.get("JETSON_AGENT_MODE", "false").lower() == "true"
    if _pi_mode or _jetson_mode:
        try:
            from zoe_agent import warmup_kv_cache
            asyncio.create_task(warmup_kv_cache(), name="gemma_kv_warmup")
            tier = "Jetson" if _jetson_mode else "Pi"
            logger.info("%s Agent: Gemma KV cache warmup scheduled (fires in 8s)", tier)
        except Exception as _wup_exc:
            logger.warning("Agent KV warmup scheduling failed (non-fatal): %s", _wup_exc)

    # Proactive engine: APScheduler (Tier 1) + slow-loop (Tier 2).
    try:
        from proactive.engine import start_proactive_engine, register_trigger
        from proactive.triggers.reminder_scan import ReminderScanTrigger
        from proactive.triggers.morning_checkin import MorningCheckInTrigger
        from proactive.triggers.evening_windown import EveningWindDownTrigger
        register_trigger(ReminderScanTrigger())
        register_trigger(MorningCheckInTrigger())
        register_trigger(EveningWindDownTrigger())
        start_proactive_engine()
        logger.info(
            "Proactive engine started (ReminderScanTrigger, MorningCheckInTrigger,"
            " EveningWindDownTrigger registered)"
        )
    except Exception as _pe_exc:
        logger.warning("Proactive engine failed to start (non-fatal): %s", _pe_exc)

    # LiveKit voice agent: joins "zoe-voice" room as server-side participant.
    # Only starts if LIVEKIT_API_KEY is configured; safe to skip if LiveKit is not running.
    _livekit_agent_task = None
    try:
        from routers.voice_livekit import start_livekit_agent
        _livekit_agent_task = asyncio.create_task(start_livekit_agent(), name="livekit_agent")
        logger.info("LiveKit voice agent task started")
    except Exception as _lk_exc:
        logger.warning("LiveKit agent failed to start (non-fatal): %s", _lk_exc)

    yield

    if _livekit_agent_task and not _livekit_agent_task.done():
        _livekit_agent_task.cancel()
        try:
            await _livekit_agent_task
        except asyncio.CancelledError:
            pass

    try:
        from proactive.engine import stop_proactive_engine
        stop_proactive_engine()
    except Exception:
        pass
    for task in (_openclaw_bg_task, _digest_bg_task, _zoe_update_bg_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    logger.info("zoe-data shutting down.")


app = FastAPI(
    title="zoe-data",
    description="Structured data backend for Zoe family assistant",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Correlation / request-id middleware ──────────────────────────────────────
import contextvars as _cvars

_request_id_ctx: _cvars.ContextVar[str | None] = _cvars.ContextVar("request_id", default=None)
_REQUEST_ID_CTX_VAR = _request_id_ctx  # expose to filter above


class _CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = (
            request.headers.get("X-Request-ID")
            or request.headers.get("X-Correlation-ID")
            or _uuid_mod.uuid4().hex[:12]
        )
        token = _request_id_ctx.set(rid)
        t0 = time.monotonic()
        try:
            response = await call_next(request)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            response.headers["X-Request-ID"] = rid
            logger.info(
                "%s %s → %s (%dms)",
                request.method, request.url.path, response.status_code, elapsed_ms,
                extra={"request_id": rid},
            )
            return response
        finally:
            _request_id_ctx.reset(token)


app.add_middleware(_CorrelationMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://zoe.local",
        "https://zoe.the411.life",
        "http://zoe.local",
        "http://localhost",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(calendar_router)
app.include_router(lists_router)
app.include_router(people_router)
app.include_router(memories_router)
app.include_router(reminders_router)
app.include_router(notes_router)
app.include_router(journal_router)
app.include_router(transactions_router)
app.include_router(weather_router)
app.include_router(system_router)
app.include_router(notifications_router)
app.include_router(chat_router)
app.include_router(ui_router)
app.include_router(openclaw_router)
app.include_router(voice_tts_router)
app.include_router(user_profile_router)
app.include_router(dashboard_router)
app.include_router(stubs_router)
app.include_router(push_router)
app.include_router(proactive_router)
app.include_router(panel_auth_router)
app.include_router(capability_matrix_router)
app.include_router(music_router)

from routers.ha_control import router as ha_control_router
app.include_router(ha_control_router)


@app.get("/health")
async def root_health():
    return {
        "status": "ok",
        "service": "zoe-data",
        "version": "1.0.0",
        "memory_capture": _memory_capture_health,
    }


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus scrape endpoint.

    Exposes counters/gauges from `memory_metrics.REGISTRY` (MemPalace ingest,
    search latency, PII rejects, feedback, training, routing). Refreshes the
    per-user collection-size gauge inline so dashboards always see current
    values. Uses `prometheus_client.generate_latest` with our dedicated
    registry to avoid leaking default Python/process metrics.
    """
    from fastapi.responses import Response
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from memory_metrics import REGISTRY, snapshot_collection_sizes

    snapshot_collection_sizes()
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.post("/api/internal/broadcast")
async def internal_broadcast(payload: dict):
    """Internal endpoint for MCP server to trigger WebSocket broadcasts."""
    channel = payload.get("channel", "all")
    event_type = payload.get("event_type", "update")
    data = payload.get("data", {})
    await broadcaster.broadcast(channel, event_type, data)
    return {"ok": True}


@app.websocket("/ws/push")
async def websocket_push(
    websocket: WebSocket,
    channel: str = Query("all"),
    panel_id: str = Query(default=""),
):
    """WebSocket push endpoint.

    Clients may connect with ?panel_id=<id> to subscribe to their dedicated
    panel channel (receives both global 'all' events AND panel-specific events).
    Without panel_id, falls back to the plain channel subscription.
    """
    if panel_id:
        await broadcaster.connect_panel(websocket, panel_id)
    else:
        await broadcaster.connect(websocket, channel)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
            elif data.startswith("catchup:"):
                seq = int(data.split(":")[1])
                await broadcaster.catchup(websocket, seq)
    except WebSocketDisconnect:
        if panel_id:
            broadcaster.disconnect(websocket, f"panel_{panel_id}")
        else:
            broadcaster.disconnect(websocket, channel)


@app.websocket("/api/calendar/ws/{user_id}")
async def calendar_ws(websocket: WebSocket, user_id: str):
    await broadcaster.connect(websocket, "calendar")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "calendar")


@app.websocket("/api/lists/ws/{user_id}")
async def lists_ws_with_user(websocket: WebSocket, user_id: str):
    await broadcaster.connect(websocket, "lists")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "lists")


@app.websocket("/api/lists/ws")
async def lists_ws(websocket: WebSocket):
    await broadcaster.connect(websocket, "lists")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "lists")


@app.websocket("/api/people/ws/{user_id}")
async def people_ws(websocket: WebSocket, user_id: str):
    await broadcaster.connect(websocket, "all")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "all")


@app.websocket("/api/reminders/ws/{user_id}")
async def reminders_ws(websocket: WebSocket, user_id: str):
    await broadcaster.connect(websocket, "all")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "all")


@app.websocket("/api/notes/ws/{user_id}")
async def notes_ws(websocket: WebSocket, user_id: str):
    await broadcaster.connect(websocket, "all")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "all")


@app.websocket("/api/journal/ws/{user_id}")
async def journal_ws(websocket: WebSocket, user_id: str):
    await broadcaster.connect(websocket, "all")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "all")


async def _resolve_ws_user(session_id: str) -> str:
    """Resolve a browser session_id to a user_id via zoe-auth HTTP.

    Calls the same /api/auth/user endpoint that get_current_user uses in auth.py.
    Falls back to 'family-admin' on any auth failure or timeout.
    """
    if not session_id:
        return "family-admin"
    try:
        import httpx
        auth_url = os.getenv("ZOE_AUTH_URL", "http://localhost:8002").rstrip("/")
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(
                f"{auth_url}/api/auth/user",
                headers={"X-Session-ID": session_id},
            )
        if r.status_code == 200:
            data = r.json()
            uid = data.get("user_id") or data.get("id") or ""
            return uid or "family-admin"
    except Exception:
        pass
    return "family-admin"


@app.websocket("/ws/voice/")
async def websocket_voice(websocket: WebSocket, session_id: str = Query("")):
    """Local voice session WebSocket for voice.html.

    Accepts text and binary (audio) messages:
    - Text JSON {"type": "text", "message": "..."} → routed through zoe_agent
    - Binary → transcribed via faster-whisper then routed as text
    Emits {"type": "state", "state": "..."} and {"type": "transcript", ...}
    and {"type": "done"} events.
    """
    await websocket.accept()
    ws_session_id = session_id or f"ws-voice-{_uuid_mod.uuid4().hex[:8]}"
    user_id = await _resolve_ws_user(session_id)

    await websocket.send_json({"type": "state", "state": "ambient"})

    try:
        while True:
            message_text: str | None = None
            try:
                raw = await websocket.receive()
            except WebSocketDisconnect:
                break

            if raw.get("type") == "websocket.disconnect":
                break

            if raw.get("bytes"):
                # Binary audio chunk: detect format from magic bytes, write temp file, transcribe.
                # Browser MediaRecorder always sends WebM/opus (magic: \x1a\x45\xdf\xa3).
                # Saving as .wav when the content is WebM causes whisper.cpp to fail.
                audio_bytes: bytes = raw["bytes"]
                await websocket.send_json({"type": "state", "state": "thinking"})
                try:
                    import tempfile, os as _os
                    from routers.voice_tts import _transcribe_audio
                    _magic = audio_bytes[:4] if len(audio_bytes) >= 4 else b""
                    _suffix = ".wav" if _magic == b"RIFF" else ".webm"
                    with tempfile.NamedTemporaryFile(suffix=_suffix, delete=False) as tf:
                        tf.write(audio_bytes)
                        tmp_path = tf.name
                    try:
                        message_text = await _transcribe_audio(tmp_path)
                    finally:
                        try:
                            _os.unlink(tmp_path)
                        except OSError:
                            pass
                    if message_text:
                        await websocket.send_json({"type": "transcript", "role": "user", "text": message_text})
                except Exception as _exc:
                    logger.warning("Voice WS audio transcription failed: %s", _exc)
                    await websocket.send_json({"type": "state", "state": "ambient"})
                    continue

            elif raw.get("text"):
                text_data: str = raw["text"]
                if text_data == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                try:
                    msg = __import__("json").loads(text_data)
                    if msg.get("type") == "text":
                        message_text = msg.get("message", "").strip()
                except Exception:
                    pass

            if not message_text:
                continue

            await websocket.send_json({"type": "state", "state": "thinking"})
            response = "Sorry, I had trouble with that."
            try:
                from zoe_agent import run_zoe_agent
                response = await run_zoe_agent(
                    message_text,
                    ws_session_id,
                    user_id,
                    voice_mode=True,
                )
            except Exception as _exc:
                logger.error("Voice WS agent error: %s", _exc)
            await websocket.send_json({"type": "state", "state": "responding"})
            await websocket.send_json({"type": "transcript", "role": "zoe", "text": response})
            # Synthesize TTS and send audio so the client can play without an extra HTTP call
            try:
                import base64 as _b64
                from routers.voice_tts import synthesize as _synth
                _tts_resp = await _synth({"text": response}, caller={"source": "ws", "user_id": user_id})
                await websocket.send_json({
                    "type": "audio",
                    "audio_base64": _b64.b64encode(_tts_resp.body).decode("ascii"),
                    "content_type": _tts_resp.media_type,
                    "text": response,
                })
            except Exception as _tts_exc:
                logger.warning("Voice WS TTS failed: %s", _tts_exc)
                await websocket.send_json({"type": "text", "content": response})

            await websocket.send_json({"type": "done"})
            await websocket.send_json({"type": "state", "state": "ambient"})

    except Exception as _exc:
        logger.warning("Voice WS closed: %s", _exc)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
