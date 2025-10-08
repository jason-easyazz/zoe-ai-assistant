"""
Zoe Authentication Service
Main FastAPI application with comprehensive authentication features
"""

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging
import uvicorn
import os
from datetime import datetime

# Import our modules
from api.auth import router as auth_router
from api.admin import router as admin_router  
from api.touch_panel import router as touch_panel_router
from api.sso import router as sso_router
from models.database import auth_db
from core.sessions import session_manager
from core.rbac import rbac_manager
from touch_panel.cache import cache_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    logger.info("Starting Zoe Authentication Service")
    
    # Initialize database and migrate existing users
    try:
        auth_db.create_migration_from_existing()
        logger.info("Database migration completed")
    except Exception as e:
        logger.error(f"Database migration failed: {e}")
    
    # Cleanup expired sessions and cache on startup
    session_manager._cleanup_expired_sessions()
    cache_manager.cleanup_all_caches()
    
    logger.info("Zoe Authentication Service started successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Zoe Authentication Service")
    session_manager.close()

# Create FastAPI app
app = FastAPI(
    title="Zoe Authentication Service",
    description="Comprehensive authentication system with passcode support and RBAC",
    version="1.0.0",
    docs_url="/docs" if os.getenv("ENVIRONMENT") != "production" else None,
    redoc_url="/redoc" if os.getenv("ENVIRONMENT") != "production" else None,
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://zoe-ui:80",
        "http://localhost:3000",  # Development
        "https://*.zoe.local",    # Local domain
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add trusted host middleware for security
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        "localhost",
        "zoe-auth",
        "zoe-core",
        "*.zoe.local",
        "127.0.0.1",
        "192.168.*",
        "10.*",
        "172.16.*",
        "172.17.*",
        "172.18.*",
        "172.19.*",
        "172.20.*",
        "172.21.*",
        "172.22.*",
        "172.23.*",
        "172.24.*",
        "172.25.*",
        "172.26.*",
        "172.27.*",
        "172.28.*",
        "172.29.*",
        "172.30.*",
        "172.31.*"
    ]
)

# Add request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests for audit purposes"""
    start_time = datetime.now()
    
    # Log request
    logger.info(f"Request: {request.method} {request.url} from {request.client.host if request.client else 'unknown'}")
    
    response = await call_next(request)
    
    # Log response
    process_time = (datetime.now() - start_time).total_seconds()
    logger.info(f"Response: {response.status_code} in {process_time:.3f}s")
    
    return response

# Include routers
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(touch_panel_router)
app.include_router(sso_router)

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Test database connection
        with auth_db.get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        
        # Get basic stats
        stats = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": "1.0.0",
            "database": "connected",
            "active_sessions": len(session_manager.active_sessions),
            "cache_devices": len(cache_manager.caches)
        }
        
        return stats
    
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "timestamp": datetime.now().isoformat(),
                "error": str(e)
            }
        )

# Root endpoint
@app.get("/")
async def root():
    """Root endpoint with service information"""
    return {
        "service": "Zoe Authentication Service",
        "version": "1.0.0",
        "description": "Comprehensive authentication with passcode support and RBAC",
        "endpoints": {
            "health": "/health",
            "docs": "/docs",
            "auth": "/api/auth/*",
            "admin": "/api/admin/*",
            "touch_panel": "/api/touch-panel/*"
        },
        "features": [
            "Password authentication",
            "Passcode authentication", 
            "Role-based access control",
            "Session management",
            "Touch panel optimization",
            "Offline support",
            "Audit logging",
            "SSO integration ready"
        ]
    }

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unhandled errors"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "timestamp": datetime.now().isoformat(),
            "path": str(request.url)
        }
    )

if __name__ == "__main__":
    # Development server
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
        log_level="info"
    )
