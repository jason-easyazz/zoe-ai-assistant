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

# Import enhanced chat router
from routers import chat_enhanced

# Import new touch panel router
from routers import touch_panel_config

# Import missing routers for complete API functionality
from routers import calendar, memories, lists, reminders, developer, homeassistant, weather, developer_tasks, settings, journal, family, enhanced_calendar, event_permissions, system, self_awareness
from routers import vector_search, notifications
from routers import proactive_insights, agent_planner, tool_registry

# Import metrics middleware
from middleware.metrics import MetricsMiddleware, get_metrics

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Zoe Core API - Enhanced", 
    description="Core API for Zoe AI Assistant with Enhanced Multi-Expert Model",
    version="5.1"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
app.include_router(chat_enhanced.router)  # Enhanced chat with Multi-Expert Model
app.include_router(touch_panel_config.router)

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
app.include_router(vector_search.router)
app.include_router(notifications.router)

# Mount websocket router for intelligence stream
app.include_router(notifications.ws_router)
app.include_router(proactive_insights.router)
app.include_router(agent_planner.router)
app.include_router(tool_registry.router)

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
            "self_awareness"
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
            "original_chat": "/api/chat",
            "enhanced_chat": "/api/chat/enhanced",
            "multi_expert_model": True,
            "action_execution": True
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

