"""Structured JSON logging middleware for Zoe services.

This module provides JSON-formatted logging with structured fields for better
integration with log aggregation systems like Loki, CloudWatch, and Datadog.
"""

import json
import logging
import time
from typing import Any, Dict, Optional
from pythonjsonlogger import jsonlogger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from contextvars import ContextVar

# Context variable for request-scoped logging metadata
_request_metadata_ctx: ContextVar[Optional[Dict[str, Any]]] = ContextVar("request_metadata", default=None)


class ZoeJsonFormatter(jsonlogger.JsonFormatter):
    """JSON log formatter for Zoe services with structured fields."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remove the default timestamp field since we add our own ISO format
        if "timestamp" in self._skip_fields:
            self._skip_fields.remove("timestamp")
    
    def add_fields(self, log_record: Dict[str, Any], record: logging.LogRecord, message_dict: Dict[str, Any]) -> None:
        """Add structured fields to the log record."""
        super().add_fields(log_record, record, message_dict)
        
        # Add standard Zoe logging fields
        log_record["timestamp"] = self.formatTime(record)
        log_record["level"] = record.levelname
        log_record["logger_name"] = record.name
        
        # Add request metadata if available
        metadata = _request_metadata_ctx.get()
        if metadata:
            log_record.update(metadata)
        
        # Ensure request_id is always present
        if "request_id" not in log_record:
            log_record["request_id"] = getattr(record, "request_id", "background")


def setup_json_logging(extra_filters=None) -> None:
    """Configure JSON logging for Zoe services.
    
    Args:
        extra_filters: Optional list of logging.Filter instances to add to the handler.
    """
    # Remove existing handlers to avoid duplicate logs
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create JSON formatter
    formatter = ZoeJsonFormatter(
        "%(timestamp)s %(level)s %(logger_name)s %(request_id)s %(message)s",
        rename_fields={
            "timestamp": "timestamp",
            "level": "level",
            "logger_name": "logger_name",
            "message": "message"
        }
    )
    
    # Add console handler for JSON output
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    if extra_filters:
        for f in extra_filters:
            handler.addFilter(f)
    root_logger.addHandler(handler)
    
    # Set default level
    root_logger.setLevel(logging.INFO)


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that adds structured logging with request context."""
    
    async def dispatch(self, request: Request, call_next):
        # Extract request ID from headers or generate
        request_id = (
            request.headers.get("X-Request-ID") or
            request.headers.get("X-Correlation-ID") or
            "background"
        )
        
        # Extract user ID from auth context (simplified)
        user_id = "anonymous"
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            # In a real implementation, we'd decode the JWT here
            user_id = "authenticated-user"
        
        metadata = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "user_agent": request.headers.get("user-agent", ""),
            "user_id": user_id,
        }
        
        # Set request metadata context
        token = _request_metadata_ctx.set(metadata)
        
        t0 = time.monotonic()
        try:
            response = await call_next(request)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            
            # Add response headers
            response.headers["X-Request-ID"] = request_id
            
            # Add response metadata
            metadata["status_code"] = response.status_code
            metadata["duration_ms"] = str(elapsed_ms)
            
            # Log the request completion
            logger = logging.getLogger(__name__)
            logger.info(
                "Request completed",
                extra=metadata,
            )
            
            return response
        finally:
            _request_metadata_ctx.reset(token)


def get_request_metadata() -> Dict[str, Any]:
    """Get current request metadata for logging context."""
    return _request_metadata_ctx.get() or {}


def log_with_context(level: str, message: str, **extra_fields) -> None:
    """Log a message with request context and additional structured fields."""
    logger = logging.getLogger()
    
    # Get current request metadata
    metadata = get_request_metadata()
    
    # Merge with additional fields
    log_fields = {**metadata, **extra_fields}
    
    # Log with appropriate level
    if level == "debug":
        logger.debug(message, extra=log_fields)
    elif level == "info":
        logger.info(message, extra=log_fields)
    elif level == "warning":
        logger.warning(message, extra=log_fields)
    elif level == "error":
        logger.error(message, extra=log_fields)
    elif level == "critical":
        logger.critical(message, extra=log_fields)
