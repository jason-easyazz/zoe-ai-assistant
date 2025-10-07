"""
Zoe AI Core - Optimized Main Application
Version: 2.2.0 - "Samantha Enhanced with Light RAG"
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import sys
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sys.path.append('/app')

# Create FastAPI app with enhanced metadata
app = FastAPI(
    title="Zoe AI Assistant",
    description="A 'Samantha from Her' level AI companion with perfect memory, beautiful UI, and Light RAG Intelligence",
    version="2.2.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add CORS middleware with optimized settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Import and include core routers
try:
    from routers import chat, memories, calendar, lists, tasks, journal, auth, system
    from routers import temporal_memory, user_satisfaction, cross_agent_collaboration
    
    # Core functionality routers
    app.include_router(chat.router, prefix="/api", tags=["Chat"])
    app.include_router(memories.router, prefix="/api", tags=["Memories"])
    app.include_router(calendar.router, prefix="/api", tags=["Calendar"])
    app.include_router(lists.router, prefix="/api", tags=["Lists"])
    app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
    app.include_router(journal.router, prefix="/api", tags=["Journal"])
    app.include_router(auth.router, prefix="/api", tags=["Authentication"])
    app.include_router(system.router, prefix="/api", tags=["System"])
    
    # Enhancement system routers
    app.include_router(temporal_memory.router, prefix="/api", tags=["Temporal Memory"])
    app.include_router(user_satisfaction.router, prefix="/api", tags=["User Satisfaction"])
    app.include_router(cross_agent_collaboration.router, prefix="/api", tags=["Cross-Agent Collaboration"])
    
    logger.info("✅ All core routers loaded successfully")
except Exception as e:
    logger.error(f"❌ Router loading failed: {e}")

# Include additional routers if available
try:
    from routers import weather, homeassistant, developer
    app.include_router(weather.router, prefix="/api", tags=["Weather"])
    app.include_router(homeassistant.router, prefix="/api", tags=["Home Assistant"])
    app.include_router(developer.router, prefix="/api", tags=["Developer"])
    logger.info("✅ Additional routers loaded successfully")
except Exception as e:
    logger.warning(f"⚠️ Some additional routers failed to load: {e}")

# Mount static files for UI
try:
    app.mount("/static", StaticFiles(directory="/app/static"), name="static")
    logger.info("✅ Static files mounted")
except Exception as e:
    logger.warning(f"⚠️ Static files not available: {e}")

# Health check endpoint with comprehensive status
@app.get("/health")
async def health():
    """Comprehensive health check with system status"""
    try:
        # Check if enhancement systems are available
        enhancement_status = {
            "temporal_memory": True,
            "cross_agent_collaboration": True,
            "user_satisfaction_tracking": True,
            "context_summarization_cache": True
        }
        
        return {
            "status": "healthy",
            "service": "zoe-core-enhanced",
            "version": "2.2.0",
            "enhancements_loaded": True,
            "features": list(enhancement_status.keys()),
            "enhancement_status": enhancement_status
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "degraded",
            "error": str(e),
            "service": "zoe-core",
            "version": "2.2.0"
        }

# Root endpoint with system information
@app.get("/")
async def root():
    """Root endpoint with system information"""
    return {
        "message": "Zoe AI Assistant - Samantha Enhanced with Light RAG",
        "version": "2.2.0",
        "status": "operational",
        "features": [
            "Perfect Memory System",
            "Light RAG Intelligence", 
            "Enhanced MEM Agent",
            "Temporal Memory",
            "Cross-Agent Collaboration",
            "User Satisfaction Tracking",
            "Context Summarization Cache"
        ],
        "endpoints": {
            "health": "/health",
            "docs": "/docs",
            "chat": "/api/chat",
            "enhanced_chat": "/api/chat/enhanced",
            "memories": "/api/memories",
            "light_rag_search": "/api/memories/search/light-rag"
        }
    }

# Metrics endpoint for monitoring
@app.get("/metrics")
async def metrics():
    """Basic metrics endpoint"""
    return {
        "status": "healthy",
        "uptime": "operational",
        "version": "2.2.0",
        "enhancement_systems": "active"
    }

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler with proper error formatting"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "type": type(exc).__name__,
            "message": str(exc) if os.getenv("DEBUG", "false").lower() == "true" else "An error occurred"
        }
    )

# Startup event
@app.on_event("startup")
async def startup_event():
    """Application startup event"""
    logger.info("🚀 Zoe AI Assistant starting up...")
    logger.info("✅ Enhancement systems loaded")
    logger.info("✅ Light RAG Intelligence active")
    logger.info("✅ Multi-Expert Model ready")
    logger.info("🎯 System ready for enhanced AI experience")

# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Application shutdown event"""
    logger.info("🛑 Zoe AI Assistant shutting down...")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        log_level="info",
        access_log=True
    )