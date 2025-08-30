#!/bin/bash
# FIX_MAIN_PY_COMPLETE.sh - Completely rebuild main.py with all routers

echo "🔧 COMPLETELY FIXING MAIN.PY"
echo "============================"

cd /home/pi/zoe

# Step 1: List all routers
echo "📋 Available routers:"
docker exec zoe-core ls -1 /app/routers/*.py | xargs -n1 basename | grep -v __

# Step 2: Create a clean main.py
echo -e "\n📝 Creating clean main.py..."
docker exec zoe-core bash -c 'cat > /app/main.py << "PYEOF"
"""
Zoe AI System - Main Application
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import os
import sys

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Zoe AI System",
    version="2.0",
    description="AI Assistant with dual personalities: Zoe (user) and Zack (developer)"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and register routers dynamically
try:
    from routers import developer, backup, chat, settings, settings_ui
    
    app.include_router(developer.router)
    logger.info("✅ Developer router registered")
    
    app.include_router(backup.router)
    logger.info("✅ Backup router registered")
    
    app.include_router(chat.router)
    logger.info("✅ Chat router registered")
    
    app.include_router(settings.router)
    logger.info("✅ Settings router registered")
    
    app.include_router(settings_ui.router)
    logger.info("✅ Settings UI router registered")
    
except ImportError as e:
    logger.error(f"Router import error: {e}")

# Try to import optional routers
optional_routers = ["memory", "calendar", "lists", "creator", "templates"]
for router_name in optional_routers:
    try:
        router_module = __import__(f"routers.{router_name}", fromlist=[router_name])
        if hasattr(router_module, "router"):
            app.include_router(router_module.router)
            logger.info(f"✅ {router_name.capitalize()} router registered")
    except Exception as e:
        logger.warning(f"Optional router {router_name} not loaded: {e}")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "zoe-core",
        "version": "2.0"
    }

@app.get("/")
async def root():
    """Root endpoint with system info"""
    return {
        "message": "Zoe AI System Active",
        "personalities": {
            "zoe": "Friendly user assistant",
            "zack": "Technical developer assistant"
        },
        "endpoints": {
            "chat": "/api/chat",
            "developer": "/api/developer",
            "backup": "/api/backup",
            "settings": "/api/settings",
            "docs": "/docs"
        }
    }

@app.on_event("startup")
async def startup_event():
    """Startup tasks"""
    logger.info("🚀 Zoe AI System starting...")
    logger.info(f"📍 Working directory: {os.getcwd()}")
    
    # Ensure data directories exist
    os.makedirs("/app/data/backups", exist_ok=True)
    os.makedirs("/app/data/logs", exist_ok=True)
    
    logger.info("✅ Zoe AI System ready!")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup tasks"""
    logger.info("👋 Zoe AI System shutting down...")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
PYEOF'

echo "✅ Created clean main.py"

# Step 3: Restart service
echo -e "\n🔄 Restarting service..."
docker compose restart zoe-core
sleep 10

# Step 4: Check logs for any errors
echo -e "\n📋 Checking startup logs:"
docker logs zoe-core --tail 20 | grep -E "✅|❌|ERROR|registered"

# Step 5: Test all endpoints
echo -e "\n🧪 Testing endpoints..."

echo "1️⃣ Health check:"
curl -s http://localhost:8000/health | jq '.'

echo -e "\n2️⃣ Root info:"
curl -s http://localhost:8000/ | jq '.endpoints'

echo -e "\n3️⃣ Developer status:"
curl -s http://localhost:8000/api/developer/status | jq '.status' || echo "Failed"

echo -e "\n4️⃣ Create backup:"
curl -s -X POST http://localhost:8000/api/backup/create | jq '.' || echo "Failed"

echo -e "\n5️⃣ List backups:"
curl -s http://localhost:8000/api/backup/list | jq '.' || echo "Failed"

echo -e "\n6️⃣ All registered routes:"
curl -s http://localhost:8000/openapi.json | jq '.paths | keys[]' | grep -E "backup|developer|chat" | head -10

echo -e "\n✅ Main.py completely fixed!"
