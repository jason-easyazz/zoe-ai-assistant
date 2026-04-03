import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
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
)
from routers.dashboard import router as dashboard_router
from routers.stubs import router as stubs_router
from routers.push import router as push_router
from routers.system import start_openclaw_background_tasks
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_openclaw_bg_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _openclaw_bg_task
    logger.info("Initializing zoe-data database...")
    await init_db()
    logger.info("Database initialized. zoe-data is ready.")
    _openclaw_bg_task = start_openclaw_background_tasks()
    yield
    if _openclaw_bg_task:
        _openclaw_bg_task.cancel()
        try:
            await _openclaw_bg_task
        except asyncio.CancelledError:
            pass
    logger.info("zoe-data shutting down.")


app = FastAPI(
    title="zoe-data",
    description="Structured data backend for Zoe family assistant",
    version="1.0.0",
    lifespan=lifespan,
)

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
app.include_router(voice_tts_router)
app.include_router(user_profile_router)
app.include_router(dashboard_router)
app.include_router(stubs_router)
app.include_router(push_router)


@app.get("/health")
async def root_health():
    return {"status": "ok", "service": "zoe-data", "version": "1.0.0"}


@app.post("/api/internal/broadcast")
async def internal_broadcast(payload: dict):
    """Internal endpoint for MCP server to trigger WebSocket broadcasts."""
    channel = payload.get("channel", "all")
    event_type = payload.get("event_type", "update")
    data = payload.get("data", {})
    await broadcaster.broadcast(channel, event_type, data)
    return {"ok": True}


@app.websocket("/ws/push")
async def websocket_push(websocket: WebSocket, channel: str = Query("all")):
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
