"""
Zoe Core Service - Main Application
===================================

Enhanced to include touch panel configuration management.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
from datetime import datetime

# Import existing routers
from routers import auth, tasks, chat

# Import new touch panel router
from routers import touch_panel_config

# Import missing routers for complete API functionality
from routers import calendar, memories, lists, reminders, developer, homeassistant, weather, developer_tasks, settings, journal, family, enhanced_calendar, event_permissions, system, self_awareness

app = FastAPI(
    title="Zoe Core API", 
    description="Core API for Zoe AI Assistant with Touch Panel Management",
    version="5.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(tasks.router)
app.include_router(chat.router)
app.include_router(touch_panel_config.router)  # New touch panel router

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

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "zoe-core",
        "version": "5.0",
        "features": [
            "authentication",
            "task_management", 
            "chat_interface",
            "knowledge_management",
            "touch_panel_configuration",  # New feature
            "calendar_management",
            "memory_system",
            "lists_management",
            "reminders_system",
            "developer_tools",
            "family_groups",  # New family/group feature
            "self_awareness"  # New self-awareness feature
        ]
    }

@app.get("/api/health")
async def api_health_check():
    """API health check endpoint (for frontend compatibility)"""
    return {
        "status": "healthy",
        "service": "zoe-core",
        "version": "5.0",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Zoe Core API",
        "version": "5.0",
        "documentation": "/docs"
    }

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0", 
        port=8000,
        reload=True
    )