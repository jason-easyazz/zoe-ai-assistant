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
    from routers.chat import router as chat_router
    app.include_router(chat_router)
    print("✅ Chat router loaded")
except Exception as e:
    print(f"❌ Chat router failed: {e}")

try:
    from routers.developer import router as developer_router
    from routers.developer_tasks import router as developer_tasks_router
    app.include_router(developer_router)
    app.include_router(developer_tasks_router)
    print("✅ Developer routers loaded")
except Exception as e:
    print(f"❌ Developer routers failed: {e}")

try:
    from routers.developer_enhanced import router as developer_enhanced_router
    app.include_router(developer_enhanced_router)
    print("✅ Developer enhanced router loaded")
except Exception as e:
    print(f"❌ Developer enhanced failed: {e}")

try:
    from routers.calendar import router as calendar_router
    app.include_router(calendar_router)
    print("✅ Calendar router loaded")
except Exception as e:
    print(f"❌ Calendar router failed: {e}")

try:
    from routers.lists import router as lists_router
    app.include_router(lists_router)
    print("✅ Lists router loaded")
except Exception as e:
    print(f"❌ Lists router failed: {e}")

try:
    from routers.memories import router as memories_router
    app.include_router(memories_router)
    print("✅ Memory router loaded")
except Exception as e:
    print(f"❌ Memory router failed: {e}")

try:
    from routers.settings import router as settings_router
    from routers.simple_ai import router as simple_ai_router
    app.include_router(settings_router)
    app.include_router(simple_ai_router)
    print("✅ Settings router loaded")
except Exception as e:
    print(f"❌ Settings router failed: {e}")

try:
    from routers.ai_task_integration import router as ai_task_integration_router
    app.include_router(ai_task_integration_router)
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
