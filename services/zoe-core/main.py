from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
import sys
import sqlite3

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
    from routers import developer, developer_tasks, roadmap, health_dashboard, alert_system, auto_recovery, smart_scheduling, n8n_integration, matrix_integration, vector_search, streaming_stt, performance_trending, encrypted_snapshots
    app.include_router(developer.router)
    app.include_router(developer_tasks.router)
    app.include_router(roadmap.router)
    app.include_router(health_dashboard.router)
    app.include_router(alert_system.router)
    app.include_router(auto_recovery.router)
    app.include_router(smart_scheduling.router)
    app.include_router(n8n_integration.router)
    app.include_router(matrix_integration.router)
    app.include_router(vector_search.router)
    app.include_router(streaming_stt.router)
    app.include_router(performance_trending.router)
    app.include_router(encrypted_snapshots.router)
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
    from routers import memories
    app.include_router(memories.router)
    print("✅ Memories router loaded")
except Exception as e:
    print(f"❌ Memories router failed: {e}")

try:
    from routers import reminders
    app.include_router(reminders.router)
    # Initialize reminders database
    reminders.init_reminders_db()
    print("✅ Reminders router loaded")
except Exception as e:
    print(f"❌ Reminders router failed: {e}")

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
    from routers import time_sync
    app.include_router(time_sync.router)
    print("✅ Time sync router loaded")
except Exception as e:
    print(f"❌ Time sync router failed: {e}")

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

try:
    from routers import aider
    app.include_router(aider.router)
    print("✅ Aider router loaded")
except Exception as e:
    print(f"❌ Aider router failed: {e}")

try:
    from routers import simple_creator
    app.include_router(simple_creator.router)
    print("✅ Creator router loaded")
except Exception as e:
    print(f"❌ Creator router failed: {e}")

# Database performance setup (SQLite)
@app.on_event("startup")
async def configure_databases():
    try:
        db_paths = [
            os.getenv("DATABASE_PATH", "/app/data/zoe.db"),
            "/app/data/memory.db",
        ]

        for db_path in db_paths:
            try:
                if not os.path.exists(db_path):
                    # Skip non-existent optional DBs
                    continue
                with sqlite3.connect(db_path) as conn:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA synchronous=NORMAL;")
                    conn.execute("PRAGMA temp_store=MEMORY;")
                    conn.execute("PRAGMA cache_size=-20000;")
                    conn.execute("PRAGMA foreign_keys=ON;")
                    conn.execute("PRAGMA busy_timeout=5000;")

                    # Helpful indexes for common query patterns
                    # Lists: filter by user_id, list_type and order by updated_at
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_lists_user_type_updated ON lists(user_id, list_type, updated_at);"
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_lists_user_type_category ON lists(user_id, list_type, list_category);"
                    )

                    # Events: lookups by user_id and date range already have start_date index; add user_id
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id);"
                    )

                    # Reminders: additional composite to support today queries
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_reminders_user_active_due ON reminders(user_id, is_active, due_date, due_time);"
                    )

                    # Update planner stats
                    conn.execute("ANALYZE;")
                    conn.commit()
            except Exception as inner_e:
                print(f"⚠️ SQLite tuning skipped for {db_path}: {inner_e}")
    except Exception as e:
        print(f"⚠️ Database configuration on startup failed: {e}")

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
