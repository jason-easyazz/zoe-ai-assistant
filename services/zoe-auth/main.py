"""
Zoe Authentication Service
"""

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging
import uvicorn
import os
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import routers and core managers (auth_db is now PostgreSQL-backed)
from api.auth import router as auth_router
from api.admin import router as admin_router
from api.touch_panel import router as touch_panel_router
from api.sso import router as sso_router
from core.sessions import session_manager
from core.rbac import rbac_manager
from touch_panel.cache import cache_manager
from models.database import auth_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Zoe Authentication Service")

    try:
        logger.info("Database ready — PostgreSQL")
    except Exception as e:
        logger.warning(f"Startup warning: {e}")

    try:
        session_manager._cleanup_expired_sessions()
        cache_manager.cleanup_all_caches()
    except Exception as e:
        logger.warning(f"Cleanup warning: {e}")

    logger.info("Zoe Authentication Service started successfully")

    yield

    logger.info("Shutting down Zoe Authentication Service")


app = FastAPI(
    title="Zoe Authentication Service",
    description="Comprehensive authentication system with passcode support and RBAC",
    version="2.0.0",
    docs_url="/docs" if os.getenv("ENVIRONMENT") != "production" else None,
    redoc_url="/redoc" if os.getenv("ENVIRONMENT") != "production" else None,
    lifespan=lifespan
)

_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ZOE_AUTH_ALLOWED_ORIGINS",
        "http://localhost,http://localhost:3000,http://localhost:8000,"
        "http://127.0.0.1,http://127.0.0.1:8000,"
        "https://zoe.the411.life,http://zoe.local",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Session-ID"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = datetime.now()
    logger.info(f"Request: {request.method} {request.url} from {request.client.host if request.client else 'unknown'}")
    response = await call_next(request)
    process_time = (datetime.now() - start_time).total_seconds()
    logger.info(f"Response: {response.status_code} in {process_time:.3f}s")
    return response


app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(touch_panel_router)
app.include_router(sso_router)


@app.get("/health")
async def health_check():
    try:
        with auth_db.get_connection() as conn:
            conn.execute("SELECT 1").fetchone()

        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": "2.0.0",
            "database": "postgresql",
            "active_sessions": len(session_manager.active_sessions) if hasattr(session_manager, 'active_sessions') else 0,
            "cache_devices": len(cache_manager.caches) if hasattr(cache_manager, 'caches') else 0
        }

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


@app.get("/")
async def root():
    return {
        "service": "Zoe Authentication Service",
        "version": "2.0.0",
        "endpoints": {
            "health": "/health",
            "docs": "/docs",
            "auth": "/api/auth/*",
            "admin": "/api/admin/*",
            "touch_panel": "/api/touch-panel/*",
            "sso": "/api/auth/sso/oidc/*"
        },
        "features": [
            "Password authentication",
            "Passcode authentication",
            "Role-based access control",
            "Session management",
            "Touch panel optimization",
            "Offline support",
            "Admin user management",
            "SSO / OIDC integration"
        ]
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
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
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8002,
        reload=False,
        log_level="info"
    )
