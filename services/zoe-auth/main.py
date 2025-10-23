"""
Zoe Authentication Service - Hybrid Version
Combines simple_main.py stability with full RBAC features
"""

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging
import uvicorn
import os
import sqlite3
from datetime import datetime

# Configure logging BEFORE imports
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Monkey-patch the database connection to use simple approach
# This prevents WAL locking issues while keeping full RBAC features
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Override database connection before importing modules
class SimpleAuthDatabase:
    """Simple database wrapper that avoids locking issues"""
    
    def __init__(self):
        self.db_path = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
        self._connection_pool = []
        logger.info(f"Using database: {self.db_path}")
    
    def get_connection(self):
        """Simple connection without WAL or FK constraints"""
        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        # CRITICAL: Don't enable FK constraints or WAL mode
        # isolation_level=None puts in autocommit mode which prevents locking
        return conn
    
    def __enter__(self):
        """Context manager support"""
        self._conn = self.get_connection()
        return self._conn
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager support"""
        if hasattr(self, '_conn'):
            self._conn.close()
        return False
    
    def init_database(self):
        """Minimal init - tables already exist"""
        pass
    
    def create_migration_from_existing(self):
        """Skip migration - database already compatible"""
        logger.info("Using existing database schema")

# Replace the auth_db before any imports use it
import models.database as db_module
db_module.auth_db = SimpleAuthDatabase()

# Now import our modules (they'll use the patched auth_db)
from api.auth import router as auth_router
from api.admin import router as admin_router  
from api.touch_panel import router as touch_panel_router
from api.sso import router as sso_router
from core.sessions import session_manager
from core.rbac import rbac_manager
from touch_panel.cache import cache_manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    logger.info("Starting Zoe Authentication Service (Hybrid Version)")
    
    # Skip complex migration - just log
    try:
        logger.info("Database ready - using existing schema")
    except Exception as e:
        logger.warning(f"Startup warning: {e}")
    
    # Cleanup expired sessions and cache on startup
    try:
        session_manager._cleanup_expired_sessions()
        cache_manager.cleanup_all_caches()
    except Exception as e:
        logger.warning(f"Cleanup warning: {e}")
    
    logger.info("Zoe Authentication Service started successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Zoe Authentication Service")
    try:
        session_manager.close()
    except:
        pass

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
    allow_origins=["*"],  # Simplified for local network
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request logging middleware
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
        # Test database connection using our simple approach
        conn = sqlite3.connect(db_module.auth_db.db_path, timeout=5.0)
        conn.execute("SELECT 1").fetchone()
        conn.close()
        
        # Get basic stats
        stats = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": "1.0.0-hybrid",
            "database": "connected",
            "active_sessions": len(session_manager.active_sessions) if hasattr(session_manager, 'active_sessions') else 0,
            "cache_devices": len(cache_manager.caches) if hasattr(cache_manager, 'caches') else 0
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
        "version": "1.0.0-hybrid",
        "description": "Hybrid: Simple DB + Full RBAC features",
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
            "Admin user management",
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
    # Production server - no reload to prevent database locks
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8002,
        reload=False,
        log_level="info"
    )
