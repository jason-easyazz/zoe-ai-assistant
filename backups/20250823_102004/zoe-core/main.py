from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe AI Core API", version="5.0")

# CORS configuration - CRITICAL for browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Import routers
try:
    from routers import developer, chat
    app.include_router(developer.router)
    app.include_router(chat.router)
    logger.info("All routers loaded successfully")
except ImportError as e:
    logger.error(f"Router import error: {e}")
    from routers import developer
    app.include_router(developer.router)

@app.get("/")
async def root():
    return {"message": "Zoe AI Core API", "status": "running"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "5.0",
        "services": {
            "core": "running",
            "memory": "available",
            "developer": "active"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
