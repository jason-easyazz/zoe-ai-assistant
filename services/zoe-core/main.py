from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
import sys

# Create FastAPI app
app = FastAPI(title="Zoe AI Core", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import routers
try:
    from routers import chat
    app.include_router(chat.router)
    print("✅ Chat router loaded")
except Exception as e:
    print(f"❌ Chat router failed: {e}")

try:
    from routers import developer, developer_tasks
    app.include_router(developer.router)
    app.include_router(developer_tasks.router)
    print("✅ Developer routers loaded")
except Exception as e:
    print(f"❌ Developer routers failed: {e}")

try:
    from routers import developer_enhanced
    app.include_router(developer_enhanced.router)
    print("✅ Developer enhanced router loaded")
except Exception as e:
    print(f"❌ Developer enhanced failed: {e}")

try:
    from routers import tasks
    app.include_router(tasks.router)
    print("✅ Tasks router loaded")
except Exception as e:
    print(f"❌ Tasks router failed: {e}")

try:
    from routers import auth
    app.include_router(auth.router)
    print("✅ Auth router loaded")
except Exception as e:
    print(f"❌ Auth router failed: {e}")

try:
    from routers import setup_multiuser
    app.include_router(setup_multiuser.router)
    print("✅ Setup multiuser router loaded")
except Exception as e:
    print(f"❌ Setup multiuser router failed: {e}")

try:
    from routers import calendar
    app.include_router(calendar.router)
    print("✅ Calendar router loaded")
except Exception as e:
    print(f"❌ Calendar router failed: {e}")

try:
    from routers import lists
    app.include_router(lists.router)
    print("✅ Lists router loaded")
except Exception as e:
    print(f"❌ Lists router failed: {e}")

try:
    from routers import memory
    app.include_router(memory.router)
    print("✅ Memory router loaded")
except Exception as e:
    print(f"❌ Memory router failed: {e}")

try:
    from routers import memories
    app.include_router(memories.router)
    print("✅ Memories router loaded")
except Exception as e:
    print(f"❌ Memories router failed: {e}")

try:
    from routers import journal
    app.include_router(journal.router)
    print("✅ Journal router loaded")
except Exception as e:
    print(f"❌ Journal router failed: {e}")

try:
    from routers import workflows
    app.include_router(workflows.router)
    print("✅ Workflows router loaded")
except Exception as e:
    print(f"❌ Workflows router failed: {e}")

try:
    from routers import homeassistant
    app.include_router(homeassistant.router)
    print("✅ Home Assistant router loaded")
except Exception as e:
    print(f"❌ Home Assistant router failed: {e}")

try:
    from routers import weather
    app.include_router(weather.router)
    print("✅ Weather router loaded")
except Exception as e:
    print(f"❌ Weather router failed: {e}")

try:
    from routers import system
    app.include_router(system.router)
    print("✅ System router loaded")
except Exception as e:
    print(f"❌ System router failed: {e}")

try:
    from routers import settings
    app.include_router(settings.router)
    print("✅ Settings router loaded")
except Exception as e:
    print(f"❌ Settings router failed: {e}")

try:
    from routers import simple_ai
    app.include_router(simple_ai.router)
    print("✅ Simple AI router loaded")
except Exception as e:
    print(f"❌ Simple AI router failed: {e}")

try:
    from routers import backup
    app.include_router(backup.router)
    print("✅ Backup router loaded")
except Exception as e:
    print(f"❌ Backup router failed: {e}")

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/api/health")
async def api_health():
    return {"status": "healthy"}

@app.get("/")
async def root():
    return {"message": "Zoe AI Core API", "version": "1.0.0"}

# Error handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "type": type(exc).__name__}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
