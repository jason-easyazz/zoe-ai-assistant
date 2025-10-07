"""
Zoe Core Service - Enhanced Main Application with Enhancement Systems
====================================================================

Enhanced version with all four enhancement systems integrated.
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

# Import new touch panel router
from routers import touch_panel_config

# Import missing routers for complete API functionality
from routers import calendar, memories, lists, reminders, developer, homeassistant, weather, developer_tasks, settings, journal, family, enhanced_calendar, event_permissions, system, self_awareness

# Import enhancement routers
try:
    from routers import temporal_memory, cross_agent_collaboration, user_satisfaction
    ENHANCEMENT_ROUTERS_AVAILABLE = True
    print("✅ Enhancement routers loaded successfully")
except ImportError as e:
    print(f"⚠️ Enhancement routers not available: {e}")
    ENHANCEMENT_ROUTERS_AVAILABLE = False

# Import metrics middleware
from middleware.metrics import MetricsMiddleware, get_metrics

app = FastAPI(
    title="Zoe Core API Enhanced", 
    description="Core API for Zoe AI Assistant with Enhancement Systems",
    version="5.2"
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

# Include existing routers
app.include_router(auth.router)
app.include_router(tasks.router)
app.include_router(chat.router)
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

# Include enhancement routers if available
if ENHANCEMENT_ROUTERS_AVAILABLE:
    try:
        app.include_router(temporal_memory.router)
        app.include_router(cross_agent_collaboration.router)
        app.include_router(user_satisfaction.router)
        print("✅ Enhancement routers included successfully")
    except Exception as e:
        print(f"⚠️ Failed to include enhancement routers: {e}")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    features = [
        "authentication",
        "task_management", 
        "chat_interface",
        "enhanced_chat_with_actions",
        "multi_expert_model",
        "action_execution",
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
    
    # Add enhancement features if available
    if ENHANCEMENT_ROUTERS_AVAILABLE:
        features.extend([
            "temporal_memory",
            "cross_agent_collaboration", 
            "user_satisfaction_tracking",
            "context_summarization_cache"
        ])
    
    return {
        "status": "healthy",
        "service": "zoe-core-enhanced",
        "version": "5.2",
        "features": features,
        "enhancements_loaded": ENHANCEMENT_ROUTERS_AVAILABLE
    }

@app.get("/api/health")
async def api_health_check():
    """API health check endpoint (for frontend compatibility)"""
    return {
        "status": "healthy",
        "service": "zoe-core-enhanced",
        "version": "5.2",
        "timestamp": datetime.now().isoformat(),
        "enhancements_active": ENHANCEMENT_ROUTERS_AVAILABLE
    }

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return Response(content=get_metrics(), media_type="text/plain")

# Exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body}
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)


