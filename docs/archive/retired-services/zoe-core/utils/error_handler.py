"""
Error Handler Middleware
========================

FastAPI exception handlers and error response formatting.
"""

from fastapi import Request, FastAPI
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging
import traceback
from typing import Union

from .errors import ZoeError

logger = logging.getLogger(__name__)


def setup_error_handlers(app: FastAPI) -> None:
    """Register error handlers with FastAPI app."""
    
    @app.exception_handler(ZoeError)
    async def zoe_error_handler(request: Request, exc: ZoeError):
        """Handle custom Zoe errors."""
        logger.error(
            f"ZoeError: {exc.error_code} - {exc.message}",
            extra={
                "path": request.url.path,
                "details": exc.details,
                "retryable": exc.retryable
            }
        )
        
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict()
        )
    
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """Handle standard HTTP exceptions."""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "HTTP_ERROR",
                "message": exc.detail,
                "details": {},
                "retryable": exc.status_code >= 500
            }
        )
    
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        """Handle Pydantic validation errors."""
        errors = []
        for error in exc.errors():
            errors.append({
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
                "type": error["type"]
            })
        
        return JSONResponse(
            status_code=422,
            content={
                "error": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": {"errors": errors},
                "retryable": False
            }
        )
    
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle unexpected exceptions."""
        # Log full traceback
        logger.exception(
            f"Unhandled exception on {request.method} {request.url.path}",
            exc_info=exc
        )
        
        # Don't expose internal errors in production
        return JSONResponse(
            status_code=500,
            content={
                "error": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "details": {},
                "retryable": True
            }
        )


class ErrorLoggingMiddleware:
    """Middleware for logging all errors with context."""
    
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # Track response status
        response_started = False
        status_code = 200
        
        async def send_wrapper(message):
            nonlocal response_started, status_code
            
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
            
            await send(message)
        
        try:
            await self.app(scope, receive, send_wrapper)
            
            # Log errors (4xx and 5xx)
            if status_code >= 400:
                request_path = scope.get("path", "unknown")
                method = scope.get("method", "unknown")
                logger.warning(
                    f"Request failed: {method} {request_path} -> {status_code}"
                )
                
        except Exception as e:
            logger.exception(f"Middleware caught exception: {e}")
            raise


def create_error_response(
    error: Union[ZoeError, Exception],
    include_traceback: bool = False
) -> dict:
    """Create a standardized error response."""
    if isinstance(error, ZoeError):
        response = error.to_dict()
    else:
        response = {
            "error": "INTERNAL_ERROR",
            "message": str(error),
            "details": {},
            "retryable": True
        }
    
    if include_traceback:
        response["traceback"] = traceback.format_exc()
    
    return response

