"""
Zoe Core Service - Enhanced Main Application
===========================================

Enhanced version with Multi-Expert Model MEM Agent integration
for both memory search AND action execution capabilities.
"""

from fastapi import FastAPI, HTTPException, Depends, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import uvicorn
import os
import logging
from datetime import datetime

# Auto-discover and import routers
from router_loader import RouterLoader

# Initialize router loader
router_loader = RouterLoader()

# Optional LiveKit voice agent (requires livekit package)
VOICE_AGENT_AVAILABLE = False
try:
    from routers import voice_agent  # LiveKit real-time voice
    VOICE_AGENT_AVAILABLE = True
except ImportError:
    logger.warning("⚠️  LiveKit voice agent not available (livekit package not installed)")

# Import metrics middleware
from middleware.metrics import MetricsMiddleware, get_metrics

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Zoe Core API - Enhanced", 
    description="Core API for Zoe AI Assistant with Enhanced Multi-Expert Model",
    version="5.1"
)

# CORS middleware - environment-based configuration
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS", 
    "http://localhost:3000,http://localhost:8080,http://localhost:5000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,  # Restrict to configured origins only
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

# Metrics middleware
app.add_middleware(MetricsMiddleware)

from auth_integration import validate_session

# Auto-discover and include all routers
discovered_routers = router_loader.discover_routers()
logger.info(f"📦 Discovered {len(discovered_routers)} routers")

for router_name, router_instance in discovered_routers:
    try:
        app.include_router(router_instance)
        logger.info(f"✅ Registered router: {router_name}")
    except Exception as e:
        logger.error(f"❌ Failed to register router {router_name}: {e}")

# Voice agent router (LiveKit real-time voice) - special handling
if VOICE_AGENT_AVAILABLE:
    try:
        app.include_router(voice_agent.router)
        logger.info("✅ Registered voice agent router (LiveKit)")
    except Exception as e:
        logger.error(f"❌ Failed to register voice agent router: {e}")

# Mount static files for uploads
import pathlib
uploads_dir = pathlib.Path(os.getenv("UPLOAD_DIR", "/app/data/uploads"))
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

# Home Assistant proxy endpoints
import httpx

@app.get("/api/homeassistant/entities")
async def proxy_homeassistant_entities():
    """Proxy Home Assistant entities request"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://homeassistant-mcp-bridge:8007/entities")
            data = response.json()
            # If the bridge service returns an error, return it as-is
            if "error" in data:
                return data
            return data
    except Exception as e:
        return {"entities": [], "count": 0, "error": str(e), "status": 500}

@app.get("/api/homeassistant/services")
async def proxy_homeassistant_services():
    """Proxy Home Assistant services request"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://homeassistant-mcp-bridge:8007/services")
            data = response.json()
            # If the bridge service returns an error, return it as-is
            if "error" in data:
                return data
            return data
    except Exception as e:
        return {"services": {}, "error": str(e), "status": 500}

@app.get("/health")
async def health_check():
    """Enhanced health check endpoint"""
    return {
        "status": "healthy",
        "service": "zoe-core-enhanced",
        "version": "5.1",
        "features": [
            "authentication",
            "task_management", 
            "chat_interface",
            "enhanced_chat_with_actions",  # New enhanced feature
            "multi_expert_model",  # New MEM Agent feature
            "action_execution",  # New action capability
            "knowledge_management",
            "touch_panel_configuration",
            "calendar_management",
            "memory_system",
            "lists_management",
            "reminders_system",
            "developer_tools",
            "family_groups",
            "self_awareness",
            "journal_with_journeys",
            "photo_uploads_heic",
            "location_services",
            "journal_prompts"
        ]
    }

@app.get("/api/health")
async def api_health_check():
    """API health check endpoint (for frontend compatibility)"""
    return {
        "status": "healthy",
        "service": "zoe-core-enhanced",
        "version": "5.1",
        "timestamp": datetime.now().isoformat(),
        "enhanced": True
    }

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Zoe Core API - Enhanced",
        "version": "5.1",
        "enhanced": True,
        "documentation": "/docs",
        "features": {
            "intelligent_chat": "/api/chat",
            "quality_monitoring": "/api/models/performance",
            "quality_metrics": "/api/models/quality",
            "multi_expert_model": True,
            "action_execution": True,
            "intelligent_model_selection": True,
            "real_time_quality_analysis": True
        }
    }

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return Response(content=get_metrics(), media_type="text/plain")

if __name__ == "__main__":
    uvicorn.run(
        "main_enhanced:app",
        host="0.0.0.0", 
        port=8000,
        reload=True
    )
