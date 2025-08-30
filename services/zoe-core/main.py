"""Zoe AI System - Main Application"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create app
app = FastAPI(title="Zoe AI System", version="2.0")

# Add CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import existing routers safely
try:
    from routers import developer
    app.include_router(developer.router)
    logger.info("✅ Developer router registered")
except Exception as e:
    logger.error(f"Failed to load developer: {e}")

try:
    from routers import chat
    app.include_router(chat.router)
    logger.info("✅ Chat router registered")
except Exception as e:
    logger.error(f"Failed to load chat: {e}")

try:
    from routers import settings
    app.include_router(settings.router)
    logger.info("✅ Settings router registered")
except Exception as e:
    logger.error(f"Failed to load settings: {e}")

try:
    from routers import settings_ui
    app.include_router(settings_ui.router)
    logger.info("✅ Settings UI router registered")
except Exception as e:
    logger.error(f"Failed to load settings_ui: {e}")

try:
    from routers import user_backups
    app.include_router(user_backups.router)
    logger.info("✅ User backups router registered")
except Exception as e:
    logger.error(f"Failed to load user_backups: {e}")

# Try optional routers
optional = ["memory", "lists", "templates", "tasks"]
for name in optional:
    try:
        module = __import__(f"routers.{name}", fromlist=[name])
        if hasattr(module, "router"):
            app.include_router(module.router)
            logger.info(f"✅ {name.capitalize()} router registered")
    except Exception as e:
        logger.warning(f"Optional {name}: {e}")

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/")
async def root():
    return {
        "service": "Zoe AI System",
        "endpoints": {
            "developer": "/api/developer",
            "chat": "/api/chat",
            "settings": "/api/settings",
            "user_backup": "/api/user-backup",
            "docs": "/docs"
        }
    }

@app.on_event("startup")
async def startup():
    logger.info("🚀 Starting Zoe AI System...")
    os.makedirs("/app/data/backups", exist_ok=True)
    os.makedirs("/app/data/user_backups", exist_ok=True)
    logger.info("✅ Ready!")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
