"""
Zoe Core Service - Enhanced with All Enhancement Ideas
=====================================================

Main application with all enhancement systems integrated:
- Temporal & Episodic Memory
- Cross-Agent Collaboration
- User Satisfaction Measurement
- Context Summarization Cache
"""

from fastapi import FastAPI, HTTPException, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
from datetime import datetime

# Import existing routers
from routers import auth, tasks, chat

# Import new enhancement routers
from routers import temporal_memory, cross_agent_collaboration, user_satisfaction, context_cache

# Import missing routers for complete API functionality
from routers import calendar, memories, lists, reminders, developer, homeassistant, weather, developer_tasks, settings, journal, family, enhanced_calendar, event_permissions, system, self_awareness

# Import metrics middleware
from middleware.metrics import MetricsMiddleware, get_metrics

app = FastAPI(
    title="Zoe Core API - Enhanced", 
    description="Core API for Zoe AI Assistant with all enhancement systems",
    version="6.0"
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

# Include enhancement routers
app.include_router(temporal_memory.router)
app.include_router(cross_agent_collaboration.router)
app.include_router(user_satisfaction.router)
app.include_router(context_cache.router)

# Include other routers for complete API functionality
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
    """Health check endpoint with enhancement status"""
    return {
        "status": "healthy",
        "service": "zoe-core-enhanced",
        "version": "6.0",
        "features": [
            "authentication",
            "task_management", 
            "chat_interface",
            "knowledge_management",
            "temporal_memory",  # New enhancement
            "cross_agent_collaboration",  # New enhancement
            "user_satisfaction_measurement",  # New enhancement
            "context_summarization_cache",  # New enhancement
            "calendar_management",
            "memory_system",
            "lists_management",
            "reminders_system",
            "developer_tools",
            "family_groups",
            "self_awareness"
        ],
        "enhancement_systems": {
            "temporal_memory": "active",
            "cross_agent_collaboration": "active", 
            "user_satisfaction": "active",
            "context_cache": "active"
        }
    }

@app.get("/api/health")
async def api_health_check():
    """API health check endpoint (for frontend compatibility)"""
    return {
        "status": "healthy",
        "service": "zoe-core-enhanced",
        "version": "6.0",
        "timestamp": datetime.now().isoformat(),
        "enhancements": "all_active"
    }

@app.get("/api/enhancements/status")
async def get_enhancement_status():
    """Get status of all enhancement systems"""
    try:
        # Import enhancement systems
        from temporal_memory import temporal_memory
        from cross_agent_collaboration import orchestrator
        from user_satisfaction import satisfaction_system
        from context_cache import context_cache
        
        return {
            "temporal_memory": {
                "status": "active",
                "database_initialized": True,
                "episode_timeouts": temporal_memory.episode_timeouts
            },
            "cross_agent_collaboration": {
                "status": "active",
                "expert_count": len(orchestrator.expert_endpoints),
                "active_orchestrations": len(orchestrator.active_orchestrations)
            },
            "user_satisfaction": {
                "status": "active",
                "database_initialized": True,
                "implicit_weights": satisfaction_system.implicit_weights
            },
            "context_cache": {
                "status": "active",
                "database_initialized": True,
                "max_cache_size": context_cache.max_cache_size,
                "performance_threshold_ms": context_cache.performance_threshold_ms
            }
        }
    except Exception as e:
        return {
            "error": str(e),
            "status": "degraded"
        }

@app.get("/api/enhancements/test")
async def run_enhancement_tests():
    """Run enhancement test suite"""
    try:
        import subprocess
        import json
        
        # Run the test suite
        result = subprocess.run([
            "python3", "/workspace/tests/test_enhancement_suite.py"
        ], capture_output=True, text=True, cwd="/workspace")
        
        if result.returncode == 0:
            # Try to load results
            try:
                with open("/workspace/enhancement_test_results.json", "r") as f:
                    test_results = json.load(f)
                return test_results
            except FileNotFoundError:
                return {
                    "status": "completed",
                    "output": result.stdout,
                    "error": "Results file not found"
                }
        else:
            return {
                "status": "failed",
                "error": result.stderr,
                "output": result.stdout
            }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
