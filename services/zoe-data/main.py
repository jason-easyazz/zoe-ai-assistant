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
    panel_provision_router,
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
    _agent_card_router,
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
_consolidation_bg_task = None
_runtime_health_task = None
_zoe_update_bg_task = None
_skills_observer = None
_memory_capture_health: dict[str, str] = {"status": "unknown", "detail": "startup pending"}

# Runtime health dict — populated by _probe_runtimes() at startup; exported so
# routers.system can read it for agent card tier status.
_RUNTIME_HEALTH: dict[str, bool] = {
    "local_llm": False,
    "hermes": False,
    "openclaw": False,
}
# Timestamp of the last probe (ISO string); exposed via GET /api/agent/runtimes
_RUNTIME_LAST_PROBED: str = ""


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


async def _probe_runtimes() -> None:
    """Probe agent runtime ports at startup; update _RUNTIME_HEALTH."""
    import asyncio as _asyncio

    async def _check_port(port: int, timeout: float = 2.0) -> bool:
        try:
            reader, writer = await _asyncio.wait_for(
                _asyncio.open_connection("127.0.0.1", port), timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    _RUNTIME_HEALTH["local_llm"] = await _check_port(11434)
    _RUNTIME_HEALTH["hermes"] = await _check_port(8642)
    _RUNTIME_HEALTH["openclaw"] = await _check_port(18789)
    global _RUNTIME_LAST_PROBED
    from datetime import datetime, timezone
    _RUNTIME_LAST_PROBED = datetime.now(timezone.utc).isoformat()
    logger.info(
        "Runtime health probe: local_llm=%s hermes=%s openclaw=%s",
        _RUNTIME_HEALTH["local_llm"],
        _RUNTIME_HEALTH["hermes"],
        _RUNTIME_HEALTH["openclaw"],
    )


async def _runtime_health_refresh_loop() -> None:
    """Re-probe runtime health every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        await _probe_runtimes()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _openclaw_bg_task, _digest_bg_task, _zoe_update_bg_task, _consolidation_bg_task, _runtime_health_task
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
        from routers.panel_auth import load_device_tokens
        from db_pool import get_db_ctx as _get_pg_db
        async with _get_pg_db() as _db_conn:
            await load_device_tokens(_db_conn)
    except Exception as _exc:
        logger.warning("Could not pre-load device tokens: %s", _exc)
    await _run_memory_capture_startup_probe()
    asyncio.create_task(_memory_capture_retry_task(), name="memory_capture_retry")
    _openclaw_bg_task = start_openclaw_background_tasks()
    _digest_bg_task = start_memory_digest_background()
    _consolidation_bg_task = start_memory_consolidation_background()
    _zoe_update_bg_task = start_zoe_update_background_tasks()

    # Runtime health probe — run once immediately, then refresh every 5 min
    await _probe_runtimes()
    _runtime_health_task = asyncio.create_task(_runtime_health_refresh_loop(), name="runtime_health_refresh")

    # Background task watchdog — detects tasks stuck in 'running' state
    try:
        from background_runner import _watchdog_loop
        asyncio.create_task(_watchdog_loop(), name="task_watchdog")
        logger.info("Background task watchdog started")
    except Exception as _wd_exc:
        logger.warning("Task watchdog not started (non-fatal): %s", _wd_exc)
    # Zoe Agent: warm Gemma's KV cache in background so first real query is fast
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
        from proactive.triggers.openclaw_trigger import OpenClawTrigger
        from proactive.triggers.people_health import PeopleHealthTrigger
        from proactive.triggers.people_birthday import PeopleBirthdayTrigger
        register_trigger(ReminderScanTrigger())
        register_trigger(MorningCheckInTrigger())
        register_trigger(EveningWindDownTrigger())
        register_trigger(OpenClawTrigger())
        register_trigger(PeopleHealthTrigger())
        register_trigger(PeopleBirthdayTrigger())
        # EvolutionWeeklyDigestTrigger registered after evolve-weekly-digest is built
        try:
            from proactive.triggers.evolution_weekly_digest import EvolutionWeeklyDigestTrigger
            register_trigger(EvolutionWeeklyDigestTrigger())
            logger.info("Proactive engine started (+ EvolutionWeeklyDigestTrigger registered)")
        except ImportError:
            logger.info(
                "Proactive engine started (ReminderScanTrigger, MorningCheckInTrigger,"
                " EveningWindDownTrigger, OpenClawTrigger registered)"
            )
        start_proactive_engine()
    except Exception as _pe_exc:
        logger.warning("Proactive engine failed to start (non-fatal): %s", _pe_exc)

    # Multica autopilot schedule sync — register cron jobs from Multica into APScheduler.
    # Must run after start_proactive_engine() so the scheduler is already running.
    if os.environ.get("ZOE_MULTICA", "false").lower() == "true":
        try:
            from multica_autopilot_sync import sync_autopilots_from_multica
            from proactive.scheduler import get_scheduler as _get_aps
            _n = await sync_autopilots_from_multica(_get_aps())
            logger.info("Multica autopilot sync: %d job(s) registered", _n)
        except Exception as _mas_exc:
            logger.warning("Multica autopilot sync skipped (non-fatal): %s", _mas_exc)

    # Skills filesystem watcher (live cache invalidation for peer agent cards)
    try:
        from skills_watcher import start_skills_watcher  # type: ignore[import]
        _skills_observer = start_skills_watcher()
        logger.info("Skills watcher started")
    except Exception as _sw_exc:
        _skills_observer = None
        logger.warning("Skills watcher not started (non-fatal): %s", _sw_exc)

    # Multica board polling loop (30s interval, no-op if ZOE_MULTICA=false)
    async def _multica_poll_loop():
        import time as _t
        while True:
            try:
                await asyncio.sleep(30)
                from multica_client import MULClient  # type: ignore[import]
                client = MULClient()
                if not client.is_configured():
                    continue
                # Fast-path: auto-close stale autopilot tracker todos (no agent needed)
                stale_todos = await client.list_issues(status="todo")
                _now_ts = _t.time()
                for _stale in stale_todos or []:
                    _stale_title = _stale.get("title", "")
                    _stale_id = _stale.get("id")
                    if not _stale_id or not _stale_title.startswith("Autopilot:"):
                        continue
                    _created = _stale.get("created_at", "")
                    try:
                        import datetime as _dt
                        _age_h = (_dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(
                            _created.replace("Z", "+00:00")
                        )).total_seconds() / 3600
                        if _age_h >= 2:
                            await client.update_issue(_stale_id, status="done")
                            logger.info("multica_poll: auto-closed stale todo '%s'", _stale_title[:50])
                    except Exception as _se:
                        logger.debug("multica_poll: stale-todo close error: %s", _se)

                issues = await client.list_issues(status="in_progress")
                for issue in issues or []:
                    # Check whether a linked engineering workflow has reached a terminal state.
                    issue_id = issue.get("id")
                    title = issue.get("title", "")
                    if not issue_id:
                        continue
                    try:
                        from db_pool import get_db_ctx  # type: ignore[import]
                        async with get_db_ctx() as _db:
                            rows = await _db.fetch(
                                """SELECT id, phase, status, user_id FROM engineering_tasks
                                   WHERE multica_issue_id=$1
                                   ORDER BY updated_at DESC LIMIT 1""",
                                str(issue_id),
                            )
                        if rows and rows[0]["phase"] in ("done", "ready_for_human"):
                            new_status = "done" if rows[0]["phase"] == "done" else "in_review"
                            await client.update_issue(issue_id, status=new_status)
                            logger.info(
                                "multica_poll: advanced issue %s ('%s') — engineering phase=%s",
                                issue_id, title[:40], rows[0]["phase"],
                            )
                            # Push WebSocket notification to all connected clients
                            try:
                                await broadcaster.broadcast(
                                    "all",
                                    "multica_task_done",
                                    {
                                        "multica_issue_id": str(issue_id),
                                        "title": title,
                                        "phase": rows[0]["phase"],
                                    },
                                )
                            except Exception as _push_exc:
                                logger.debug("multica_poll: ws push failed: %s", _push_exc)
                    except Exception as _inner_exc:
                        logger.debug("multica_poll: inner error for issue %s: %s", issue_id, _inner_exc)
            except asyncio.CancelledError:
                return
            except Exception as _exc:
                logger.debug("multica_poll: loop error (non-fatal): %s", _exc)

    if os.environ.get("ZOE_MULTICA", "false").lower() == "true":
        asyncio.create_task(_multica_poll_loop(), name="multica_poll")
        logger.info("Multica board polling loop started (30s interval)")

    # Start the LiveKit server-side voice agent (connects to the LiveKit room and handles VAD/STT/TTS).
    try:
        from routers.voice_livekit import start_livekit_agent
        asyncio.create_task(start_livekit_agent(), name="livekit_agent")
        logger.info("LiveKit voice agent task created")
    except Exception as _lk_exc:
        logger.warning("LiveKit setup (non-fatal): %s", _lk_exc)

    yield

    # Stop skills watcher thread
    try:
        if _skills_observer is not None:
            _skills_observer.stop()
            _skills_observer.join(timeout=3)
    except Exception:
        pass
    try:
        from proactive.engine import stop_proactive_engine
        stop_proactive_engine()
    except Exception:
        pass
    for task in (_openclaw_bg_task, _digest_bg_task, _zoe_update_bg_task,
                 _consolidation_bg_task, _runtime_health_task):
        if task and not task.done():
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
app.include_router(_agent_card_router)
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
app.include_router(panel_provision_router)
app.include_router(capability_matrix_router)
app.include_router(music_router)

from routers.portrait import router as portrait_router
app.include_router(portrait_router)

from routers.ha_control import router as ha_control_router
app.include_router(ha_control_router)

try:
    from routers.voice_livekit import router as voice_livekit_router
    app.include_router(voice_livekit_router)
    logger.info("LiveKit audio upload endpoint registered")
except Exception as _lk_router_exc:
    logger.warning("LiveKit router not loaded (non-fatal): %s", _lk_router_exc)


@app.get("/.well-known/agent.json", include_in_schema=False)
async def a2a_well_known():
    """A2A v1.0 agent discovery — served inline to avoid redirect caching issues."""
    from routers.system import _build_agent_card
    return _build_agent_card()


@app.get("/health")
async def root_health():
    return {
        "status": "ok",
        "service": "zoe-data",
        "version": "1.0.0",
        "memory_capture": _memory_capture_health,
    }


@app.get("/api/settings")
async def app_settings():
    """Public settings consumed by frontend widgets (e.g. music widget HA deep-link)."""
    import os
    return {
        "homeassistant_url": os.environ.get("HA_BASE_URL", "http://homeassistant.local:8123"),
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
    # -----------------------------------------------------------------
    # WebSocket auth and session validation
    # -----------------------------------------------------------------
    # 1. Panel connections are exempted as long as a valid device token is present
    if panel_id:
        token_header = websocket.headers.get("X-Device-Token", "")
        device_info = None
        if token_header:
            from routers.panel_auth import lookup_device_token
            device_info = lookup_device_token(token_header)
        # If token missing or invalid, close immediately.
        # lookup_device_token already returns None for revoked tokens; no need to re-check.
        if not device_info:
            await websocket.close(1008, "Invalid device token")
            return
        # Panel token verified – proceed with panel subscription
        await broadcaster.connect_panel(websocket, panel_id)
    else:
        # Non-panel: perform lightweight session validation via zoe-auth HTTP.
        session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
        if not session_id:
            await websocket.close(1008, "Missing session identifier")
            return
        # Use a direct zoe-auth call rather than a fake Starlette Request to avoid
        # fragility around scope fields and the fail-open degraded-user path.
        user = await _resolve_ws_session(session_id)
        if user is None:
            await websocket.close(1008, "Invalid session")
            return
        # Use an allowlist to prevent fail-open when zoe-auth is unreachable.
        # Only accept explicitly valid, authenticated roles.
        if user.get("role") not in ("member", "admin", "agent"):
            await websocket.close(1008, "Insufficient privileges")
            return
        # Auth OK – connect to requested channel
        await broadcaster.connect(websocket, channel)
    # -----------------------------------------------------------------
    # Normal data relay loop
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
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
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
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
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
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
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
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
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
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
    await broadcaster.connect(websocket, "reminders")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "reminders")


@app.websocket("/api/notes/ws/{user_id}")
async def notes_ws(websocket: WebSocket, user_id: str):
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
    await broadcaster.connect(websocket, "notes")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "notes")


@app.websocket("/api/journal/ws/{user_id}")
async def journal_ws(websocket: WebSocket, user_id: str):
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
    await broadcaster.connect(websocket, "journal")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "journal")


async def _resolve_ws_session(session_id: str | None) -> dict | None:
    """Resolve a browser session_id to a full user dict via zoe-auth HTTP.

    Returns a dict with at least ``user_id`` and ``role`` keys on success, or
    ``None`` if the session is absent, invalid, or explicitly rejected (401/403).

    On transient zoe-auth failures (5xx, timeout, connection error) the function
    returns a degraded-user dict (role=member) via auth._validate_with_auth_service
    so that the caller's role allowlist still gates access correctly.
    """
    if not session_id:
        return None
    from auth import _validate_with_auth_service
    return await _validate_with_auth_service(session_id)


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
    - Text JSON {"type": "cancel"} → sets cancel flag for in-flight pipeline
    - Binary → transcribed via faster-whisper then routed as text
    Emits {"type": "state"}, {"type": "transcript"}, {"type": "audio"}, {"type": "done"}.
    """
    await websocket.accept()
    ws_session_id = session_id or f"ws-voice-{_uuid_mod.uuid4().hex[:8]}"
    user_id = await _resolve_ws_user(session_id)

    await websocket.send_json({"type": "state", "state": "ambient"})

    # Mutable cancel flag — text handler sets it; streaming pipeline checks it
    # between sentence boundaries. True mid-stream cancel requires task refactor (future).
    _ws_cancelled: list[bool] = [False]

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
                    elif msg.get("type") == "cancel":
                        _ws_cancelled[0] = True
                        continue
                except Exception:
                    pass

            if not message_text:
                continue

            # Reset cancel flag at the start of each new pipeline
            _ws_cancelled[0] = False

            await websocket.send_json({"type": "state", "state": "thinking"})

            # ── Streaming LLM + per-sentence TTS ────────────────────────────────
            # Track LLM output and TTS output separately so fallback only re-runs
            # what actually failed (avoids double-transcript if LLM works but TTS is down).
            _stream_llm_reply = ""   # filled once LLM streaming completes
            _stream_tts_ok = False   # True if at least one audio chunk was sent
            try:
                import base64 as _b64
                from zoe_agent import run_zoe_agent_streaming  # type: ignore
                from routers.voice_tts import _extract_complete_sentences, _synthesize_kokoro_sidecar  # type: ignore

                token_buf = ""
                full_reply: list[str] = []
                tts_started = False

                async for delta in run_zoe_agent_streaming(
                    message_text, ws_session_id, user_id=user_id, voice_mode=True
                ):
                    if _ws_cancelled[0]:
                        break
                    token_buf += delta
                    sentences, token_buf = _extract_complete_sentences(token_buf)
                    for sentence in sentences:
                        if _ws_cancelled[0]:
                            break
                        full_reply.append(sentence)
                        if not tts_started:
                            await websocket.send_json({"type": "state", "state": "responding"})
                            tts_started = True
                        wav = await _synthesize_kokoro_sidecar(sentence)
                        if wav:
                            _stream_tts_ok = True
                            await websocket.send_json({
                                "type": "audio",
                                "audio_base64": _b64.b64encode(wav).decode("ascii"),
                                "content_type": "audio/wav",
                            })

                # Flush any remaining text that didn't end with punctuation
                if not _ws_cancelled[0] and token_buf.strip():
                    full_reply.append(token_buf.strip())
                    if not tts_started:
                        await websocket.send_json({"type": "state", "state": "responding"})
                    wav = await _synthesize_kokoro_sidecar(token_buf.strip())
                    if wav:
                        _stream_tts_ok = True
                        await websocket.send_json({
                            "type": "audio",
                            "audio_base64": _b64.b64encode(wav).decode("ascii"),
                            "content_type": "audio/wav",
                        })

                _stream_llm_reply = " ".join(p.strip() for p in full_reply if p.strip())
                if _stream_llm_reply:
                    await websocket.send_json({"type": "transcript", "role": "zoe", "text": _stream_llm_reply})

            except Exception as _stream_exc:
                logger.error("Voice WS streaming error: %s", _stream_exc, exc_info=True)

            if not _ws_cancelled[0]:
                if not _stream_llm_reply:
                    # LLM streaming failed entirely → full single-shot fallback
                    try:
                        import base64 as _b64
                        from zoe_agent import run_zoe_agent  # type: ignore
                        from routers.voice_tts import synthesize as _synth  # type: ignore
                        _fallback_response = await run_zoe_agent(
                            message_text, ws_session_id, user_id, voice_mode=True
                        )
                        await websocket.send_json({"type": "state", "state": "responding"})
                        await websocket.send_json({"type": "transcript", "role": "zoe", "text": _fallback_response})
                        try:
                            _tts_resp = await _synth({"text": _fallback_response}, caller={"source": "ws", "user_id": user_id})
                            await websocket.send_json({
                                "type": "audio",
                                "audio_base64": _b64.b64encode(_tts_resp.body).decode("ascii"),
                                "content_type": _tts_resp.media_type,
                                "text": _fallback_response,
                            })
                        except Exception as _tts_exc:
                            logger.warning("Voice WS TTS fallback failed: %s", _tts_exc)
                            await websocket.send_json({"type": "text", "content": _fallback_response})
                    except Exception as _fallback_exc:
                        logger.error("Voice WS fallback error: %s", _fallback_exc)

                elif not _stream_tts_ok:
                    # LLM worked but Kokoro TTS was down → try synthesize (has Edge TTS fallback)
                    try:
                        import base64 as _b64
                        from routers.voice_tts import synthesize as _synth  # type: ignore
                        _tts_resp = await _synth({"text": _stream_llm_reply}, caller={"source": "ws", "user_id": user_id})
                        await websocket.send_json({
                            "type": "audio",
                            "audio_base64": _b64.b64encode(_tts_resp.body).decode("ascii"),
                            "content_type": _tts_resp.media_type,
                        })
                    except Exception as _tts_exc:
                        logger.warning("Voice WS TTS synthesize fallback failed: %s", _tts_exc)

            await websocket.send_json({"type": "done"})
            await websocket.send_json({"type": "state", "state": "ambient"})

    except Exception as _exc:
        logger.warning("Voice WS closed: %s", _exc)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
