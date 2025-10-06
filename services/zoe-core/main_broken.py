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
    from routers import settings
from routers import simple_ai
    app.include_router(settings.router)
app.include_router(simple_ai.router)
    print("✅ Settings router loaded")
except Exception as e:
    print(f"❌ Settings router failed: {e}")

try:
    from routers import ai_task_integration
    app.include_router(ai_task_integration.router)
    print("✅ AI Task Integration loaded")
except Exception as e:
    print(f"❌ AI Task Integration failed: {e}")

@app.get("/health")
async def health():
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
