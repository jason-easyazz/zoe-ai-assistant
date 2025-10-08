"""
Session Middleware for FastAPI
Automatically handles session validation and activity updates
"""

from fastapi import Request, HTTPException, Header
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional, Callable
import logging
import time

from session_manager import session_manager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SessionMiddleware(BaseHTTPMiddleware):
    """
    Middleware for automatic session management
    
    Features:
    - Automatic session validation
    - Session activity updates
    - Session timeout handling
    - Optional session requirement for protected routes
    """
    
    def __init__(self, app, 
                 protected_paths: Optional[list] = None,
                 excluded_paths: Optional[list] = None,
                 auto_update_activity: bool = True,
                 session_header: str = "X-Session-ID"):
        """
        Initialize session middleware
        
        Args:
            app: FastAPI application
            protected_paths: List of paths that require session validation
            excluded_paths: List of paths to exclude from session handling
            auto_update_activity: Whether to automatically update session activity
            session_header: Header name containing session ID
        """
        super().__init__(app)
        self.protected_paths = protected_paths or []
        self.excluded_paths = excluded_paths or [
            "/docs", "/redoc", "/openapi.json", "/health", "/sessions/create"
        ]
        self.auto_update_activity = auto_update_activity
        self.session_header = session_header
    
    async def dispatch(self, request: Request, call_next: Callable):
        """
        Process request through session middleware
        
        Args:
            request: Incoming request
            call_next: Next middleware/handler in chain
            
        Returns:
            Response with session handling applied
        """
        start_time = time.time()
        
        try:
            # Skip session handling for excluded paths
            if self._should_skip_session_handling(request):
                response = await call_next(request)
                return response
            
            # Extract session ID from header
            session_id = request.headers.get(self.session_header)
            
            # Check if this is a protected path
            is_protected = self._is_protected_path(request)
            
            if is_protected and not session_id:
                return JSONResponse(
                    status_code=401,
                    content={"error": "Session required", "detail": "X-Session-ID header required for this endpoint"}
                )
            
            # Validate session if provided
            session = None
            if session_id:
                session = session_manager.get_session(session_id)
                if not session:
                    return JSONResponse(
                        status_code=401,
                        content={"error": "Invalid session", "detail": "Session not found or expired"}
                    )
                
                # Update session activity if enabled
                if self.auto_update_activity:
                    session_manager.update_session_activity(session_id)
            
            # Add session to request state for use in route handlers
            request.state.session = session
            request.state.session_id = session_id
            
            # Process request
            response = await call_next(request)
            
            # Add session info to response headers
            if session:
                response.headers["X-Session-User"] = session.user_id
                response.headers["X-Session-Expires"] = session.expires_at.isoformat()
            
            # Log request processing time
            process_time = time.time() - start_time
            logger.debug(f"Request processed in {process_time:.3f}s - Session: {session_id or 'None'}")
            
            return response
            
        except Exception as e:
            logger.error(f"Error in session middleware: {e}")
            return JSONResponse(
                status_code=500,
                content={"error": "Internal server error", "detail": "Session middleware error"}
            )
    
    def _should_skip_session_handling(self, request: Request) -> bool:
        """Check if session handling should be skipped for this path"""
        path = request.url.path
        
        # Check excluded paths
        for excluded_path in self.excluded_paths:
            if path.startswith(excluded_path):
                return True
        
        return False
    
    def _is_protected_path(self, request: Request) -> bool:
        """Check if the current path requires session validation"""
        path = request.url.path
        
        # Check protected paths
        for protected_path in self.protected_paths:
            if path.startswith(protected_path):
                return True
        
        return False

def create_session_middleware(protected_paths: Optional[list] = None, 
                            excluded_paths: Optional[list] = None,
                            auto_update_activity: bool = True) -> SessionMiddleware:
    """
    Factory function to create session middleware with custom configuration
    
    Args:
        protected_paths: List of paths that require session validation
        excluded_paths: List of paths to exclude from session handling
        auto_update_activity: Whether to automatically update session activity
        
    Returns:
        Configured SessionMiddleware instance
    """
    return SessionMiddleware(
        app=None,  # Will be set when added to FastAPI app
        protected_paths=protected_paths,
        excluded_paths=excluded_paths,
        auto_update_activity=auto_update_activity
    )

# Default middleware configuration
DEFAULT_PROTECTED_PATHS = [
    "/api/chat",
    "/developer",
    "/tasks",
    "/memory",
    "/settings"
]

DEFAULT_EXCLUDED_PATHS = [
    "/docs",
    "/redoc", 
    "/openapi.json",
    "/health",
    "/sessions/create",
    "/sessions/stats",
    "/sessions/validate"
]

# Pre-configured middleware for common use cases
def create_default_session_middleware() -> SessionMiddleware:
    """Create session middleware with default configuration"""
    return create_session_middleware(
        protected_paths=DEFAULT_PROTECTED_PATHS,
        excluded_paths=DEFAULT_EXCLUDED_PATHS,
        auto_update_activity=True
    )
