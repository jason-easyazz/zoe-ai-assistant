# ⚠️ Standardized Error Handling Guide

**Date**: October 9, 2025  
**Status**: Implementation Ready  
**Priority**: Medium  
**Type**: Code Quality Improvement

---

## 🎯 Objective

Create consistent error handling across all API routers for better client experience and debugging.

---

## 🔍 Current State Problems

### Inconsistent Error Formats

**Router 1** (HTTPException):
```python
raise HTTPException(status_code=404, detail="Not found")
```
Returns:
```json
{
  "detail": "Not found"
}
```

**Router 2** (Custom dict):
```python
return {"error": "Not found", "status": 404}
```
Returns:
```json
{
  "error": "Not found",
  "status": 404
}
```

**Router 3** (JSONResponse):
```python
return JSONResponse(status_code=404, content={"detail": "Not found"})
```
Returns:
```json
{
  "detail": "Not found"
}
```

**Problems**:
- ❌ Clients can't rely on consistent error format
- ❌ Different status code locations
- ❌ No correlation IDs for debugging
- ❌ Missing timestamps
- ❌ No error categorization

---

## 🏗️ Proposed Standard

### Standard Error Response Model

```python
# services/zoe-core/models/errors.py

from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum

class ErrorCategory(str, Enum):
    """Error categories for classification"""
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    EXTERNAL_SERVICE = "external_service"

class ErrorDetail(BaseModel):
    """Detailed error information"""
    field: Optional[str] = None
    message: str
    code: Optional[str] = None

class ErrorResponse(BaseModel):
    """Standard error response format"""
    
    # Required fields
    error: str = Field(..., description="Error type/category")
    message: str = Field(..., description="Human-readable error message")
    status_code: int = Field(..., description="HTTP status code")
    timestamp: datetime = Field(default_factory=datetime.now)
    
    # Optional fields
    request_id: Optional[str] = Field(None, description="Request correlation ID")
    details: Optional[list[ErrorDetail]] = Field(None, description="Detailed error information")
    help_url: Optional[str] = Field(None, description="URL to documentation")
    
    class Config:
        json_schema_extra = {
            "example": {
                "error": "validation_error",
                "message": "Invalid input data",
                "status_code": 400,
                "timestamp": "2025-10-09T10:30:00Z",
                "request_id": "req_abc123",
                "details": [
                    {
                        "field": "email",
                        "message": "Invalid email format",
                        "code": "INVALID_EMAIL"
                    }
                ],
                "help_url": "https://docs.zoe.local/errors/validation"
            }
        }
```

---

## 🔧 Implementation

### 1. Create Custom Exception Classes

```python
# services/zoe-core/exceptions.py

from fastapi import HTTPException
from models.errors import ErrorResponse, ErrorCategory
from typing import Optional, List, Dict, Any
import uuid

class ZoeException(HTTPException):
    """Base exception for all Zoe errors"""
    
    def __init__(
        self,
        status_code: int,
        error: str,
        message: str,
        details: Optional[List[Dict[str, Any]]] = None,
        help_url: Optional[str] = None
    ):
        self.status_code = status_code
        self.error = error
        self.message = message
        self.details = details
        self.help_url = help_url
        self.request_id = str(uuid.uuid4())
        
        # FastAPI HTTPException compatibility
        super().__init__(
            status_code=status_code,
            detail=ErrorResponse(
                error=error,
                message=message,
                status_code=status_code,
                request_id=self.request_id,
                details=details,
                help_url=help_url
            ).model_dump()
        )

# Specific exception classes
class NotFoundException(ZoeException):
    """Resource not found"""
    def __init__(self, resource: str, identifier: str):
        super().__init__(
            status_code=404,
            error=ErrorCategory.NOT_FOUND,
            message=f"{resource} with ID '{identifier}' not found",
            help_url="https://docs.zoe.local/errors/not-found"
        )

class ValidationException(ZoeException):
    """Validation error"""
    def __init__(self, message: str, details: List[Dict[str, Any]]):
        super().__init__(
            status_code=400,
            error=ErrorCategory.VALIDATION,
            message=message,
            details=details,
            help_url="https://docs.zoe.local/errors/validation"
        )

class AuthenticationException(ZoeException):
    """Authentication failure"""
    def __init__(self, message: str = "Authentication required"):
        super().__init__(
            status_code=401,
            error=ErrorCategory.AUTHENTICATION,
            message=message,
            help_url="https://docs.zoe.local/errors/authentication"
        )

class AuthorizationException(ZoeException):
    """Authorization failure"""
    def __init__(self, message: str = "Insufficient permissions"):
        super().__init__(
            status_code=403,
            error=ErrorCategory.AUTHORIZATION,
            message=message,
            help_url="https://docs.zoe.local/errors/authorization"
        )

class ConflictException(ZoeException):
    """Resource conflict"""
    def __init__(self, message: str):
        super().__init__(
            status_code=409,
            error=ErrorCategory.CONFLICT,
            message=message,
            help_url="https://docs.zoe.local/errors/conflict"
        )

class RateLimitException(ZoeException):
    """Rate limit exceeded"""
    def __init__(self, message: str = "Rate limit exceeded", retry_after: int = 60):
        super().__init__(
            status_code=429,
            error=ErrorCategory.RATE_LIMIT,
            message=message,
            details=[{"retry_after_seconds": retry_after}],
            help_url="https://docs.zoe.local/errors/rate-limit"
        )

class ExternalServiceException(ZoeException):
    """External service error"""
    def __init__(self, service: str, message: str):
        super().__init__(
            status_code=502,
            error=ErrorCategory.EXTERNAL_SERVICE,
            message=f"{service}: {message}",
            help_url="https://docs.zoe.local/errors/external-service"
        )
```

### 2. Global Exception Handler

```python
# services/zoe-core/middleware/error_handler.py

from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from models.errors import ErrorResponse, ErrorCategory
from exceptions import ZoeException
import logging
import uuid
import traceback

logger = logging.getLogger(__name__)

async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle all unhandled exceptions"""
    
    request_id = str(uuid.uuid4())
    
    # Log the error
    logger.error(
        f"Request {request_id} failed: {str(exc)}",
        exc_info=True,
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method
        }
    )
    
    # Handle Zoe custom exceptions
    if isinstance(exc, ZoeException):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail
        )
    
    # Handle Starlette HTTP exceptions
    if isinstance(exc, StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error="http_error",
                message=str(exc.detail),
                status_code=exc.status_code,
                request_id=request_id
            ).model_dump()
        )
    
    # Handle validation errors
    if isinstance(exc, RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error=ErrorCategory.VALIDATION,
                message="Validation error",
                status_code=422,
                request_id=request_id,
                details=[
                    {
                        "field": ".".join(str(x) for x in err["loc"]),
                        "message": err["msg"],
                        "code": err["type"]
                    }
                    for err in exc.errors()
                ]
            ).model_dump()
        )
    
    # Handle all other exceptions as internal server errors
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error=ErrorCategory.SERVER_ERROR,
            message="An unexpected error occurred",
            status_code=500,
            request_id=request_id,
            help_url="https://docs.zoe.local/errors/server-error"
        ).model_dump()
    )

async def zoe_exception_handler(request: Request, exc: ZoeException) -> JSONResponse:
    """Handle Zoe custom exceptions"""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail
    )
```

### 3. Register Handlers in Main

```python
# services/zoe-core/main.py

from fastapi import FastAPI
from middleware.error_handler import global_exception_handler, zoe_exception_handler
from exceptions import ZoeException

app = FastAPI(title="Zoe Core API")

# Register exception handlers
app.add_exception_handler(ZoeException, zoe_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)
```

---

## 📝 Usage in Routers

### Before (Inconsistent)

```python
# routers/features/calendar.py - OLD

@router.get("/events/{event_id}")
async def get_event(event_id: int):
    event = get_event_from_db(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")  # ❌ Inconsistent
    return event
```

### After (Standardized)

```python
# routers/features/calendar.py - NEW

from exceptions import NotFoundException, ValidationException

@router.get("/events/{event_id}")
async def get_event(event_id: int, user_id: str = Depends(validate_session)):
    event = get_event_from_db(event_id)
    if not event:
        raise NotFoundException("Event", str(event_id))  # ✅ Standard
    
    if event.user_id != user_id:
        raise AuthorizationException("You don't have access to this event")  # ✅ Standard
    
    return event

@router.post("/events")
async def create_event(event: EventCreate, user_id: str = Depends(validate_session)):
    # Validate date
    if event.start_date > event.end_date:
        raise ValidationException(
            message="Invalid event dates",
            details=[{
                "field": "start_date",
                "message": "Start date must be before end date",
                "code": "INVALID_DATE_RANGE"
            }]
        )  # ✅ Standard
    
    try:
        result = create_event_in_db(event, user_id)
        return result
    except ExternalServiceError as e:
        raise ExternalServiceException("Calendar Service", str(e))  # ✅ Standard
```

---

## 📋 Error Response Examples

### 404 Not Found
```json
{
  "error": "not_found",
  "message": "Event with ID '123' not found",
  "status_code": 404,
  "timestamp": "2025-10-09T10:30:00Z",
  "request_id": "req_abc123",
  "help_url": "https://docs.zoe.local/errors/not-found"
}
```

### 400 Validation Error
```json
{
  "error": "validation",
  "message": "Invalid event dates",
  "status_code": 400,
  "timestamp": "2025-10-09T10:30:00Z",
  "request_id": "req_xyz789",
  "details": [
    {
      "field": "start_date",
      "message": "Start date must be before end date",
      "code": "INVALID_DATE_RANGE"
    }
  ],
  "help_url": "https://docs.zoe.local/errors/validation"
}
```

### 401 Authentication Error
```json
{
  "error": "authentication",
  "message": "Invalid or expired token",
  "status_code": 401,
  "timestamp": "2025-10-09T10:30:00Z",
  "request_id": "req_def456",
  "help_url": "https://docs.zoe.local/errors/authentication"
}
```

### 500 Server Error
```json
{
  "error": "server_error",
  "message": "An unexpected error occurred",
  "status_code": 500,
  "timestamp": "2025-10-09T10:30:00Z",
  "request_id": "req_ghi789",
  "help_url": "https://docs.zoe.local/errors/server-error"
}
```

---

## 📊 Implementation Checklist

### Phase 1: Setup (Day 1)
- [ ] Create `models/errors.py` with ErrorResponse model
- [ ] Create `exceptions.py` with custom exception classes
- [ ] Create `middleware/error_handler.py` with global handlers
- [ ] Register handlers in `main.py`
- [ ] Test with sample endpoints

### Phase 2: Update Routers (Days 2-4)
- [ ] Update `routers/core/auth.py`
- [ ] Update `routers/features/calendar.py`
- [ ] Update `routers/features/lists.py`
- [ ] Update `routers/features/memories.py`
- [ ] Update remaining routers incrementally

### Phase 3: Documentation (Day 5)
- [ ] Create error documentation at `/docs/errors/`
- [ ] Update OpenAPI schema
- [ ] Create client error handling guide
- [ ] Add error examples to API docs

### Phase 4: Testing (Day 6)
- [ ] Unit tests for each exception class
- [ ] Integration tests for error responses
- [ ] Verify consistent error format
- [ ] Check logging and monitoring

---

## ✅ Benefits

### For Developers
- ✅ Consistent error handling patterns
- ✅ Less boilerplate code
- ✅ Better debugging with request IDs
- ✅ Clear error categories

### For API Clients
- ✅ Predictable error format
- ✅ Structured error details
- ✅ Help URLs for documentation
- ✅ Request IDs for support tickets

### For Operations
- ✅ Better error logging
- ✅ Error categorization for monitoring
- ✅ Request correlation via IDs
- ✅ Easier troubleshooting

---

## 🧪 Testing Example

```python
# tests/unit/test_error_handling.py

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_not_found_error_format():
    """Test standard not found error format"""
    response = client.get("/api/v1/events/99999")
    assert response.status_code == 404
    
    data = response.json()
    assert "error" in data
    assert "message" in data
    assert "status_code" in data
    assert "timestamp" in data
    assert "request_id" in data
    assert data["error"] == "not_found"
    assert data["status_code"] == 404

def test_validation_error_format():
    """Test standard validation error format"""
    response = client.post("/api/v1/events", json={
        "start_date": "2025-12-31",
        "end_date": "2025-01-01"  # Invalid: before start
    })
    assert response.status_code == 400
    
    data = response.json()
    assert data["error"] == "validation"
    assert "details" in data
    assert len(data["details"]) > 0
    assert "field" in data["details"][0]
    assert "message" in data["details"][0]
```

---

*Document created: October 9, 2025*  
*Ready for implementation*

