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
)
from routers.dashboard import router as dashboard_router
from routers.stubs import router as stubs_router
from routers.push import router as push_router
from routers.system import (
    start_openclaw_background_tasks,
    start_memory_digest_background,
    start_memory_consolidation_background,
)
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
_keepwarm_task = None
_memory_capture_health: dict[str, str] = {"status": "unknown", "detail": "startup pending"}

# --- Keep-warm constants --------------------------------------------------
# Session ID used for keep-warm pings. A fixed ID means Hermes reuses the
# same cached AIAgent → Bonsai's 16k-token KV prefix stays hot in VRAM.
_HERMES_WARMUP_SESSION = "zoe-system-keepwarm"
# Ping interval (seconds). Hermes agent pool TTL is now 3600s (1 hour);
# ping every 25 minutes (1500s) to refresh the pool entry well before expiry.
_KEEPWARM_INTERVAL_S = 1500
# Initial delay before first warm-up so the service finishes starting.
_KEEPWARM_INITIAL_DELAY_S = 15


class RequestIdFilter(logging.Filter):
    """Inject request_id into every log record while a request is active."""
    def filter(self, record):
        record.request_id = getattr(_REQUEST_ID_CTX_VAR, "get", lambda d: d)(None) or "-"
        return True


# Apply the filter to root logger so all modules get it.
logging.root.addFilter(RequestIdFilter())
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RequestIdFilter())


async def _keepwarm_loop():
    """
    Periodically ping Hermes so the agent pool never expires and Bonsai's
    16k-token KV prefix (SOUL.md + MCP schemas) stays hot in VRAM.

    - System keepwarm session: always-warm baseline so ANY user's first message
      benefits from a partially warm Bonsai KV cache.
    - Recently-active user sessions: if a session had traffic in the last hour,
      refresh its TTL so returning users get sub-2s responses, not a 45s cold start.
    """
    import httpx
    from database import DB_PATH
    import aiosqlite

    _HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://127.0.0.1:11435")
    _HERMES_FAST_PATH = os.environ.get("HERMES_FAST_PATH", "true").lower() == "true"

    await asyncio.sleep(_KEEPWARM_INITIAL_DELAY_S)

    while True:
        if not _HERMES_FAST_PATH:
            await asyncio.sleep(_KEEPWARM_INTERVAL_S)
            continue

        # --- 1. System keepwarm session (always-on baseline) ---
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post(
                    f"{_HERMES_API_URL}/v1/chat/completions",
                    json={"model": "hermes-agent", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
                    headers={"X-Hermes-Session-Id": _HERMES_WARMUP_SESSION},
                )
                logger.info("Keep-warm system session: HTTP %s", r.status_code)
        except Exception as e:
            logger.debug("Keep-warm system session failed (will retry): %s", e)

        # --- 2. Recently-active user sessions (refresh their agent pool TTL) ---
        # Find sessions with activity in the past hour. Re-ping them so the
        # cached AIAgent doesn't expire before the user returns.
        active_sessions = []
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                rows = await db.execute_fetchall(
                    """SELECT DISTINCT chat_session_id
                       FROM chat_messages
                       WHERE created_at > datetime('now', '-1 hour')
                       AND chat_session_id != ?
                       ORDER BY created_at DESC
                       LIMIT 10""",
                    (_HERMES_WARMUP_SESSION,),
                )
                active_sessions = [r["chat_session_id"] for r in rows]
        except Exception as e:
            logger.debug("Keep-warm: could not query active sessions: %s", e)

        for session_id in active_sessions:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        f"{_HERMES_API_URL}/v1/chat/completions",
                        json={"model": "hermes-agent", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
                        headers={"X-Hermes-Session-Id": session_id},
                    )
                    logger.debug("Keep-warm user session %s: refreshed", session_id)
            except Exception as e:
                logger.debug("Keep-warm session %s failed: %s", session_id, e)

        await asyncio.sleep(_KEEPWARM_INTERVAL_S)


async def _run_memory_capture_startup_probe() -> None:
    """Validate memory capture plumbing at startup.

    This is a non-invasive probe: it verifies extractor import/patterns and
    MemoryService read paths without writing synthetic rows.
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
        await asyncio.wait_for(svc.load_for_prompt("family-admin", limit=1), timeout=3.0)
        await asyncio.wait_for(
            svc.search("startup probe", user_id="family-admin", limit=1, timeout_s=1.5),
            timeout=3.0,
        )
        _memory_capture_health = {"status": "ok", "detail": "extractor+service ready"}
        logger.info("Memory capture startup probe: OK")
    except Exception as exc:
        detail = str(exc)[:240]
        _memory_capture_health = {"status": "degraded", "detail": detail}
        logger.error("Memory capture startup probe FAILED: %s", detail)
        if os.environ.get("ZOE_MEMORY_STARTUP_STRICT", "false").strip().lower() == "true":
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _openclaw_bg_task, _digest_bg_task, _keepwarm_task
    logger.info("Initializing zoe-data database...")
    await init_db()
    logger.info("Database initialized. zoe-data is ready.")
    # One-time MemPalace migration: re-tag legacy records from wing="zoe" to wing="family-admin"
    try:
        from pi_agent import migrate_mempalace_legacy_records
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
    _openclaw_bg_task = start_openclaw_background_tasks()
    _digest_bg_task = start_memory_digest_background()
    _consolidation_bg_task = start_memory_consolidation_background()
    _keepwarm_task = asyncio.create_task(_keepwarm_loop(), name="keepwarm")
    logger.info("Keep-warm task started (Hermes every %ds, session=%s)", _KEEPWARM_INTERVAL_S, _HERMES_WARMUP_SESSION)

    # Pi/Jetson Agent: warm Gemma's KV cache in background so first real query is fast
    # Check env directly to avoid circular import from routers.chat
    _pi_mode = os.environ.get("HERMES_FAST_PATH", "true").lower() != "true"
    _jetson_mode = os.environ.get("JETSON_AGENT_MODE", "false").lower() == "true"
    if _pi_mode or _jetson_mode:
        try:
            from pi_agent import warmup_kv_cache
            asyncio.create_task(warmup_kv_cache(), name="gemma_kv_warmup")
            tier = "Jetson" if _jetson_mode else "Pi"
            logger.info("%s Agent: Gemma KV cache warmup scheduled (fires in 8s)", tier)
        except Exception as _wup_exc:
            logger.warning("Agent KV warmup scheduling failed (non-fatal): %s", _wup_exc)

    # Warm wyoming-piper TTS: one dummy request so Piper's ONNX model is loaded
    # before the first real voice command. Avoids ~1.5s cold-start on first request.
    async def _warmup_piper():
        await asyncio.sleep(10)  # let service fully start first
        try:
            from routers.voice_tts import _synthesize_wyoming_piper
            await _synthesize_wyoming_piper("Zoe is ready.")
            logger.info("wyoming-piper TTS warmup complete")
        except Exception as _exc:
            logger.debug("wyoming-piper warmup failed (non-fatal): %s", _exc)

    asyncio.create_task(_warmup_piper(), name="piper_tts_warmup")

    yield
    for task in (_openclaw_bg_task, _digest_bg_task, _keepwarm_task):
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
app.include_router(panel_auth_router)
app.include_router(capability_matrix_router)

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
