from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe AI Core", version="6.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import working routers
try:
    from routers import developer
    app.include_router(developer.router)
    logger.info("✅ Developer router loaded")
except Exception as e:
    logger.error(f"Developer router failed: {e}")

try:
    from routers import simple_creator
    app.include_router(simple_creator.router)
    logger.info("✅ Creator router loaded")
except Exception as e:
    logger.error(f"Creator router failed: {e}")

try:
    from routers import chat
    app.include_router(chat.router)
    logger.info("✅ Chat router loaded")
except:
    pass

try:
    from routers import settings, settings_ui
    app.include_router(settings.router)
    app.include_router(settings_ui.router)
    logger.info("✅ Settings UI router loaded")
    logger.info("✅ Settings router loaded")
except:
    pass

@app.get("/")
async def root():
    return {"service": "Zoe AI Core", "version": "6.3", "creator": "enabled"}

@app.get("/health")
async def health():
    return {"status": "healthy", "creator": "active"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
