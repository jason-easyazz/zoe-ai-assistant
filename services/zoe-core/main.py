"""
Fixed main.py with proper router inclusion
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import sys
import os

sys.path.append('/app')

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
    from routers import chat, aider
    app.include_router(chat.router)
    print("✅ Chat router loaded")
except Exception as e:
    print(f"❌ Chat router failed: {e}")

try:
    from routers import developer, developer_tasks, aider
    app.include_router(developer.router)
    app.include_router(developer_tasks.router)
    app.include_router(aider.router)
    print("✅ Developer router loaded")
except Exception as e:
    print(f"❌ Developer router failed: {e}")

try:
    from routers import calendar, aider
    app.include_router(calendar.router)
    print("✅ Calendar router loaded")
except:
    pass

try:
    from routers import lists, aider
    app.include_router(lists.router)
    print("✅ Lists router loaded")
except:
    pass

try:
    from routers import memory, aider
    app.include_router(memory.router)
    print("✅ Memory router loaded")
except:
    pass

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
