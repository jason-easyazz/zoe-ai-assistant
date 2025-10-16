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

# Import existing routers
from routers import auth, tasks, chat

# Enhanced chat functionality now integrated into existing chat router

# Import new touch panel router
from routers import touch_panel_config

# Import widget builder router
from routers import widget_builder

# Import missing routers for complete API functionality
from routers import calendar, memories, lists, reminders, developer, homeassistant, weather, developer_tasks, settings, journal, family, enhanced_calendar, event_permissions, system, self_awareness, birthday_calendar, birthday_memories, test_memories, public_memories
from routers import vector_search, notifications
from routers import proactive_insights, agent_planner, tool_registry, onboarding, chat_sessions
from routers import location, media, journeys  # Journal enhancement routers
from routers import tts  # NeuTTS Air voice synthesis

# Optional LiveKit voice agent (requires livekit package)
try:
    from routers import voice_agent  # LiveKit real-time voice
    VOICE_AGENT_AVAILABLE = True
except ImportError:
    print("⚠️  LiveKit voice agent not available (livekit package not installed)")
    VOICE_AGENT_AVAILABLE = False

# Import metrics middleware
from middleware.metrics import MetricsMiddleware, get_metrics

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Zoe Core API - Enhanced", 
    description="Core API for Zoe AI Assistant with Enhanced Multi-Expert Model",
    version="5.1"
)

# CORS middleware - enable for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (can restrict to specific domains in production)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Metrics middleware
app.add_middleware(MetricsMiddleware)

from auth_integration import validate_session

# Include routers
app.include_router(auth.router)
app.include_router(tasks.router)
app.include_router(chat.router)  # Original chat
# Enhanced chat functionality integrated into existing chat router above
app.include_router(touch_panel_config.router)
app.include_router(widget_builder.router)  # Widget system
app.include_router(widget_builder.user_layout_router)  # User layout endpoints (/api/user/layout)

# Include missing routers for complete API functionality
app.include_router(calendar.router)
app.include_router(memories.router)
app.include_router(lists.router)
app.include_router(reminders.router)
app.include_router(developer.router)
app.include_router(homeassistant.router)
app.include_router(weather.router)
app.include_router(developer_tasks.router)
app.include_router(settings.router)
app.include_router(journal.router)
app.include_router(family.router)
app.include_router(enhanced_calendar.router)
app.include_router(event_permissions.router)
app.include_router(system.router)
app.include_router(self_awareness.router)
app.include_router(birthday_calendar.router)
app.include_router(birthday_memories.router)
app.include_router(test_memories.router)
app.include_router(public_memories.router)
app.include_router(vector_search.router)
app.include_router(notifications.router)

# Mount websocket router for intelligence stream
app.include_router(notifications.ws_router)
app.include_router(proactive_insights.router)
app.include_router(agent_planner.router)
app.include_router(tool_registry.router)
app.include_router(onboarding.router)
app.include_router(chat_sessions.router)

# Journal enhancement routers
app.include_router(location.router)
app.include_router(media.router)
app.include_router(journeys.router)

# TTS router (NeuTTS Air voice synthesis)
app.include_router(tts.router)

# Voice agent router (LiveKit real-time voice)
if VOICE_AGENT_AVAILABLE:
    app.include_router(voice_agent.router)

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
