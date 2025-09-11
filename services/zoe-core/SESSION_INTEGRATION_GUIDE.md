# Session Management Integration Guide

This guide explains how to integrate the session management system into the Zoe AI Assistant.

## Overview

The session management system provides:
- **Session tokens**: Secure session identification
- **Session storage**: Persistent session data in SQLite
- **Timeout handling**: Automatic session expiration
- **Concurrent session support**: Multiple sessions per user
- **Middleware integration**: Automatic session validation
- **API endpoints**: RESTful session management

## Quick Start

### 1. Basic Integration

```python
from fastapi import FastAPI
from session_middleware import create_default_session_middleware
from routers.sessions import router as session_router

app = FastAPI()

# Add session middleware
app.add_middleware(create_default_session_middleware())

# Include session API routes
app.include_router(session_router)
```

### 2. Using Sessions in Routes

```python
from fastapi import Depends
from session_auth import require_session, optional_session
from session_manager import Session

@app.post("/api/chat")
async def chat_endpoint(
    message: str,
    session: Session = Depends(require_session)
):
    # Session is automatically validated and provided
    user_id = session.user_id
    return {"response": f"Hello {user_id}!"}

@app.get("/public/info")
async def public_endpoint(
    session: Optional[Session] = Depends(optional_session)
):
    # Optional session - works with or without authentication
    if session:
        return {"message": f"Hello {session.user_id}!"}
    else:
        return {"message": "Hello anonymous user!"}
```

## Configuration

### Environment Variables

```bash
# Session database path
SESSION_DB_PATH=data/sessions.db

# Session timeout (seconds)
SESSION_DEFAULT_TIMEOUT=3600
SESSION_MAX_TIMEOUT=86400
SESSION_MIN_TIMEOUT=300

# Cleanup settings
SESSION_CLEANUP_INTERVAL=300
SESSION_MAX_PER_USER=10

# Security settings
SESSION_REQUIRE_HTTPS=false
SESSION_SECURE_COOKIES=false
SESSION_HEADER=X-Session-ID

# Middleware settings
SESSION_AUTO_UPDATE=true
SESSION_PROTECTED_PATHS=/api/chat,/developer,/tasks
SESSION_EXCLUDED_PATHS=/docs,/health,/sessions/create

# Logging
SESSION_LOG_LEVEL=INFO
SESSION_LOG_REQUESTS=true
```

### Programmatic Configuration

```python
from session_config import SessionConfig, get_session_config

# Use default configuration
config = get_session_config()

# Or create custom configuration
config = SessionConfig(
    default_timeout=7200,  # 2 hours
    max_sessions_per_user=5,
    protected_paths=["/api", "/admin"],
    excluded_paths=["/health", "/docs"]
)
```

## API Endpoints

### Session Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/sessions/create` | Create new session |
| GET | `/sessions/{session_id}` | Get session info |
| POST | `/sessions/{session_id}/activity` | Update activity |
| POST | `/sessions/{session_id}/extend` | Extend session |
| DELETE | `/sessions/{session_id}` | Invalidate session |
| DELETE | `/sessions/user/{user_id}` | Invalidate user sessions |
| GET | `/sessions/user/{user_id}/sessions` | Get user sessions |
| GET | `/sessions/stats` | Get session statistics |
| GET | `/sessions/validate` | Validate current session |

### Example Usage

```bash
# Create a session
curl -X POST "http://localhost:8000/sessions/create" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user123", "timeout": 3600}'

# Use session in subsequent requests
curl -X GET "http://localhost:8000/api/chat" \
  -H "X-Session-ID: <session_id>"

# Extend session
curl -X POST "http://localhost:8000/sessions/<session_id>/extend" \
  -H "Content-Type: application/json" \
  -d '{"additional_seconds": 1800}'
```

## Middleware Configuration

### Default Configuration

```python
from session_middleware import create_default_session_middleware

# Uses sensible defaults
app.add_middleware(create_default_session_middleware())
```

### Custom Configuration

```python
from session_middleware import SessionMiddleware

app.add_middleware(
    SessionMiddleware,
    protected_paths=["/api", "/admin", "/developer"],
    excluded_paths=["/health", "/docs", "/static"],
    auto_update_activity=True,
    session_header="X-Session-ID"
)
```

## Authentication Dependencies

### Required Session

```python
from session_auth import require_session

@app.get("/protected")
async def protected_route(session: Session = Depends(require_session)):
    return {"user_id": session.user_id}
```

### Optional Session

```python
from session_auth import optional_session

@app.get("/optional")
async def optional_route(session: Optional[Session] = Depends(optional_session)):
    if session:
        return {"authenticated": True, "user_id": session.user_id}
    else:
        return {"authenticated": False}
```

### Role-Based Access

```python
from session_auth import admin_user, developer_user

@app.get("/admin")
async def admin_route(session: Session = Depends(admin_user)):
    return {"admin": session.user_id}

@app.get("/developer")
async def dev_route(session: Session = Depends(developer_user)):
    return {"developer": session.user_id}
```

### Permission-Based Access

```python
from session_auth import user_with_permission

@app.get("/write")
async def write_route(session: Session = Depends(user_with_permission("write"))):
    return {"message": "Write access granted"}
```

## Session Metadata

Sessions support custom metadata for storing user preferences, roles, and other data:

```python
# Create session with metadata
session = session_manager.create_session(
    user_id="user123",
    metadata={
        "role": "admin",
        "permissions": ["read", "write", "delete"],
        "preferences": {"theme": "dark", "language": "en"},
        "custom_data": {"department": "engineering"}
    }
)

# Access metadata in routes
@app.get("/profile")
async def get_profile(session: Session = Depends(require_session)):
    return {
        "user_id": session.user_id,
        "role": session.metadata.get("role"),
        "permissions": session.metadata.get("permissions", []),
        "preferences": session.metadata.get("preferences", {})
    }
```

## Error Handling

The session system provides comprehensive error handling:

```python
from fastapi import HTTPException

try:
    session = session_manager.create_session("user123")
except Exception as e:
    raise HTTPException(status_code=500, detail="Failed to create session")

# Common error responses:
# 401 - Authentication required
# 403 - Access denied (role/permission)
# 404 - Session not found
# 500 - Internal server error
```

## Testing

### Unit Tests

```python
import pytest
from session_manager import SessionManager

def test_session_creation():
    sm = SessionManager(db_path=":memory:")
    session = sm.create_session("testuser")
    assert session.user_id == "testuser"
    assert session.is_active is True
```

### Integration Tests

```python
from fastapi.testclient import TestClient

def test_session_api():
    client = TestClient(app)
    
    # Create session
    response = client.post("/sessions/create", json={"user_id": "testuser"})
    assert response.status_code == 200
    
    session_id = response.json()["session_id"]
    
    # Use session
    response = client.get("/api/chat", headers={"X-Session-ID": session_id})
    assert response.status_code == 200
```

## Performance Considerations

### Database Optimization

- Sessions are stored in SQLite with proper indexing
- Automatic cleanup of expired sessions
- Memory caching for active sessions

### Memory Management

- Configurable maximum sessions per user
- Automatic cleanup thread
- Efficient session storage

### Scalability

- Stateless session validation
- Concurrent session support
- Database-backed persistence

## Security Best Practices

1. **Use HTTPS in production** - Set `SESSION_REQUIRE_HTTPS=true`
2. **Secure session tokens** - Use cryptographically secure UUIDs
3. **Regular cleanup** - Expired sessions are automatically removed
4. **Session limits** - Limit sessions per user
5. **Timeout handling** - Set appropriate session timeouts

## Migration from Existing Systems

If you have an existing authentication system:

1. **Gradual migration** - Use optional sessions initially
2. **Session mapping** - Map existing user IDs to sessions
3. **Metadata migration** - Move user data to session metadata
4. **API updates** - Update endpoints to use session dependencies

## Troubleshooting

### Common Issues

1. **Session not found** - Check session ID and expiration
2. **Permission denied** - Verify user roles and permissions
3. **Database errors** - Check database permissions and path
4. **Middleware conflicts** - Ensure proper middleware order

### Debugging

Enable debug logging:

```python
import logging
logging.getLogger("session_manager").setLevel(logging.DEBUG)
```

### Monitoring

Use session statistics endpoint:

```bash
curl http://localhost:8000/sessions/stats
```

## Examples

See `session_integration_example.py` for complete examples of:
- Chat endpoints with session authentication
- Admin-only endpoints
- Developer tools with role-based access
- Profile management with session metadata
- Session refresh and extension
