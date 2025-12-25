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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
async def proxy_homeassistant_entities(session = Depends(validate_session)):
    """Proxy Home Assistant entities request - Requires authentication"""
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
async def proxy_homeassistant_services(session = Depends(validate_session)):
    """Proxy Home Assistant services request - Requires authentication"""
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

# ===== STARTUP AND SHUTDOWN EVENTS =====

async def proactive_suggestion_loop():
    """Background task for proactive assistance suggestions"""
    import asyncio
    import sqlite3
    from datetime import datetime
    
    logger.info("✨ Proactive suggestion loop started")
    
    while True:
        try:
            await asyncio.sleep(3600)  # Check every hour
            
            current_hour = datetime.now().hour
            current_day = datetime.now().strftime("%A")
            
            # Get active users (users who've done something in last 24 hours)
            conn = sqlite3.connect("/app/data/zoe.db")
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT user_id
                FROM action_logs
                WHERE timestamp > datetime('now', '-24 hours')
            """)
            active_users = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            logger.info(f"🔍 Checking proactive opportunities for {len(active_users)} active users")
            
            for user_id in active_users:
                try:
                    from predictive_intelligence import predictive_intelligence
                    
                    # Sunday evening - suggest weekly planning
                    if current_day == "Sunday" and 18 <= current_hour <= 21:
                        logger.info(f"💡 Proactive: Suggesting weekly planning to {user_id}")
                        # TODO: Send notification via notification system
                    
                    # Morning - check today's schedule
                    elif 6 <= current_hour <= 9:
                        logger.info(f"💡 Proactive: Suggesting schedule check to {user_id}")
                        # TODO: Send notification via notification system
                    
                    # Check for shopping list threshold
                    conn = sqlite3.connect("/app/data/zoe.db")
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT COUNT(*) FROM list_items li
                        JOIN lists l ON li.list_id = l.id
                        WHERE l.user_id = ? AND l.list_type = 'shopping'
                          AND li.completed = 0
                    """, (user_id,))
                    count = cursor.fetchone()[0]
                    conn.close()
                    
                    if count >= 8:
                        logger.info(f"💡 Proactive: User {user_id} has {count} shopping items - suggest trip")
                        # TODO: Send notification via notification system
                
                except Exception as e:
                    logger.error(f"Error in proactive check for {user_id}: {e}")
        
        except Exception as e:
            logger.error(f"Error in proactive suggestion loop: {e}")
            await asyncio.sleep(60)  # Wait 1 minute before retrying on error

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    logger.info("🚀 Starting Zoe Core services...")
    
    try:
        # Start calendar reminder service
        from services.calendar_reminder_service import start_reminder_service
        await start_reminder_service()
        logger.info("✅ Calendar reminder service started")
    except Exception as e:
        logger.error(f"❌ Failed to start calendar reminders: {e}")
    
    try:
        # Start proactive suggestion loop
        import asyncio
        asyncio.create_task(proactive_suggestion_loop())
        logger.info("✅ Proactive suggestion loop started")
    except Exception as e:
        logger.error(f"❌ Failed to start proactive suggestions: {e}")
    
    try:
        # Start task reminder service
        from services.task_reminder_service import start_task_reminder_service
        await start_task_reminder_service()
        logger.info("✅ Task reminder service started")
    except Exception as e:
        logger.error(f"❌ Failed to start task reminders: {e}")
    
    # ✅ PHASE 1.3: Pre-warm models for faster response times
    try:
        from model_prewarm import prewarm_background
        logger.info("🔥 Starting model pre-warming in background...")
        await prewarm_background()
        logger.info("✅ Model pre-warming task started")
    except Exception as e:
        logger.warning(f"⚠️ Model pre-warming failed (non-critical): {e}")
    
    # ⏱️ Start timer background service
    try:
        from intent_system.handlers.timer_service import start_timer_service
        start_timer_service(check_interval=1.0)
        logger.info("✅ Timer service started")
    except Exception as e:
        logger.error(f"❌ Failed to start timer service: {e}")
    
    logger.info("🎉 All services started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("🛑 Shutting down Zoe Core services...")
    
    try:
        from services.calendar_reminder_service import stop_reminder_service
        await stop_reminder_service()
        logger.info("✅ Calendar reminder service stopped")
    except Exception as e:
        logger.error(f"❌ Error stopping calendar reminders: {e}")
    
    try:
        from services.task_reminder_service import stop_task_reminder_service
        await stop_task_reminder_service()
        logger.info("✅ Task reminder service stopped")
    except Exception as e:
        logger.error(f"❌ Error stopping task reminders: {e}")
    
    # ⏱️ Stop timer service
    try:
        from intent_system.handlers.timer_service import stop_timer_service
        stop_timer_service()
        logger.info("✅ Timer service stopped")
    except Exception as e:
        logger.error(f"❌ Error stopping timer service: {e}")
    
    logger.info("👋 Shutdown complete")

if __name__ == "__main__":
    uvicorn.run(
        "main_enhanced:app",
        host="0.0.0.0", 
        port=8000,
        reload=True
    )
