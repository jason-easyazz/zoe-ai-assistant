# 🔢 API Versioning Strategy

**Date**: October 9, 2025  
**Status**: Implementation Ready  
**Priority**: Medium  
**Type**: Best Practice Implementation

---

## 🎯 Objective

Implement API versioning to enable graceful evolution of the API without breaking existing clients.

---

## 🔍 Current State

**Problem**: No versioning system
```python
# Current endpoints
/api/calendar/events
/api/lists
/api/memories
```

**Issues**:
- ❌ Can't make breaking changes
- ❌ No deprecation path
- ❌ Difficult to evolve API
- ❌ Clients tightly coupled to current structure

---

## 🏗️ Proposed Approach: URI Path Versioning

**Chosen Strategy**: Version in URL path (industry standard)

**Format**: `/api/v{version}/{resource}`

**Examples**:
```
/api/v1/calendar/events
/api/v1/lists
/api/v1/memories
```

**Alternatives Considered**:
1. ❌ Header versioning (`Accept: application/vnd.zoe.v1+json`) - Complex for clients
2. ❌ Query parameter (`/api/calendar?version=1`) - Not RESTful
3. ✅ **URI path versioning** - Simple, visible, cacheable

---

## 📋 Implementation Plan

### Phase 1: Add v1 Prefix (Backward Compatible)

**Goal**: Support both `/api/` and `/api/v1/` simultaneously

**Implementation**:

```python
# services/zoe-core/main.py

from fastapi import FastAPI
from routers.features import calendar, lists, memories
from routers.core import auth

app = FastAPI(title="Zoe Core API", version="5.2")

# ============================================================================
# VERSION 1 API (New - explicit versioning)
# ============================================================================

app.include_router(calendar.router, prefix="/api/v1")
app.include_router(lists.router, prefix="/api/v1")
app.include_router(memories.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")

# ============================================================================
# LEGACY API (Maintain for backward compatibility)
# ============================================================================

app.include_router(calendar.router, prefix="/api", tags=["legacy"])
app.include_router(lists.router, prefix="/api", tags=["legacy"])
app.include_router(memories.router, prefix="/api", tags=["legacy"])
app.include_router(auth.router, prefix="/api", tags=["legacy"])
```

**Result**:
- ✅ `/api/calendar/events` - Still works (legacy)
- ✅ `/api/v1/calendar/events` - New versioned endpoint
- ✅ Zero breaking changes
- ✅ Clients can migrate at their own pace

### Phase 2: Deprecation Warning Middleware

**Add deprecation headers** to legacy endpoints:

```python
# services/zoe-core/middleware/deprecation.py

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime, timedelta

class DeprecationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # Check if using legacy (non-versioned) API
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/v"):
            # Add deprecation headers
            deprecation_date = datetime.now() + timedelta(days=180)  # 6 months
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = deprecation_date.strftime("%a, %d %b %Y %H:%M:%S GMT")
            response.headers["Link"] = f'<{request.url.path.replace("/api/", "/api/v1/")}>; rel="alternate"'
            response.headers["X-API-Warn"] = "This endpoint is deprecated. Please use /api/v1/ endpoints."
        
        return response
```

**Add to main.py**:
```python
from middleware.deprecation import DeprecationMiddleware

app.add_middleware(DeprecationMiddleware)
```

**Client sees**:
```http
HTTP/1.1 200 OK
Deprecation: true
Sunset: Sat, 05 Apr 2026 00:00:00 GMT
Link: </api/v1/calendar/events>; rel="alternate"
X-API-Warn: This endpoint is deprecated. Please use /api/v1/ endpoints.
```

### Phase 3: Version-Specific Routers

**For breaking changes**, create v2 routers:

```python
# routers/features/v2/calendar.py
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/calendar", tags=["calendar-v2"])

@router.get("/events")
async def get_events(
    user_id: str = Depends(validate_session),
    include_metadata: bool = True,  # NEW PARAMETER (breaking change)
    response_format: str = "detailed"  # NEW PARAMETER (breaking change)
):
    """
    Get calendar events (V2 - Enhanced response format)
    
    Breaking changes from v1:
    - Returns more detailed metadata by default
    - Different JSON structure for events
    - New filter capabilities
    """
    pass
```

**Update main.py**:
```python
from routers.features.v2 import calendar as calendar_v2

# V2 API (new breaking changes)
app.include_router(calendar_v2.router, prefix="/api/v2")

# V1 API (stable, maintained)
app.include_router(calendar.router, prefix="/api/v1")

# Legacy API (deprecated, will be removed)
app.include_router(calendar.router, prefix="/api", tags=["legacy"])
```

---

## 📐 Versioning Rules

### When to Increment Version

**MINOR Version (v1.1, v1.2)** - Backward Compatible:
- ✅ Add new optional parameters
- ✅ Add new endpoints
- ✅ Add new response fields
- ✅ Relax validation rules
- ✅ Add new features

**MAJOR Version (v2, v3)** - Breaking Changes:
- ❌ Remove endpoints
- ❌ Remove required parameters
- ❌ Change response format
- ❌ Change authentication method
- ❌ Rename fields
- ❌ Change data types

### Version Support Policy

| Version | Status | Support Level | Sunset Period |
|---------|--------|---------------|---------------|
| v2 | Current | Full support | N/A |
| v1 | Stable | Security fixes only | 12 months |
| Legacy (no version) | Deprecated | None | 6 months |

---

## 🔄 Migration Guide for Clients

### Step 1: Identify Current Usage

```bash
# Find all API calls in client code
grep -r "/api/" client-app/
```

### Step 2: Update to v1

```javascript
// OLD
const response = await fetch('http://localhost:8000/api/calendar/events');

// NEW
const response = await fetch('http://localhost:8000/api/v1/calendar/events');
```

### Step 3: Test Thoroughly

```bash
# Run client tests against v1 API
npm test
```

### Step 4: Monitor Deprecation Headers

```javascript
fetch('http://localhost:8000/api/v1/calendar/events')
  .then(response => {
    if (response.headers.get('Deprecation') === 'true') {
      console.warn('Endpoint deprecated:', response.headers.get('X-API-Warn'));
      console.warn('Sunset date:', response.headers.get('Sunset'));
      console.warn('Use instead:', response.headers.get('Link'));
    }
    return response.json();
  });
```

---

## 📊 Implementation Checklist

### Phase 1: Basic Versioning (Week 1)
- [ ] Add `/api/v1/` prefix to all routers
- [ ] Maintain `/api/` legacy endpoints
- [ ] Update OpenAPI docs to show both versions
- [ ] Test all endpoints work with both URLs

### Phase 2: Deprecation (Week 2)
- [ ] Create DeprecationMiddleware
- [ ] Add deprecation headers to legacy endpoints
- [ ] Document migration guide
- [ ] Announce deprecation to API users

### Phase 3: Client Migration (Months 1-6)
- [ ] Update Zoe UI to use v1 endpoints
- [ ] Update internal services to use v1
- [ ] Notify external API users (if any)
- [ ] Monitor usage of legacy endpoints

### Phase 4: Cleanup (Month 6)
- [ ] Remove legacy endpoint support
- [ ] Update documentation
- [ ] Celebrate! 🎉

---

## 🧪 Testing Strategy

### Unit Tests

```python
# tests/unit/test_versioning.py

import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_v1_endpoint_works():
    """V1 endpoint should work"""
    response = client.get("/api/v1/calendar/events")
    assert response.status_code == 200

def test_legacy_endpoint_works():
    """Legacy endpoint should still work"""
    response = client.get("/api/calendar/events")
    assert response.status_code == 200

def test_legacy_has_deprecation_headers():
    """Legacy endpoint should have deprecation headers"""
    response = client.get("/api/calendar/events")
    assert "Deprecation" in response.headers
    assert response.headers["Deprecation"] == "true"
    assert "Sunset" in response.headers
    assert "X-API-Warn" in response.headers

def test_v1_no_deprecation_headers():
    """V1 endpoint should NOT have deprecation headers"""
    response = client.get("/api/v1/calendar/events")
    assert "Deprecation" not in response.headers

def test_both_return_same_data():
    """Both versions should return same data (for now)"""
    legacy = client.get("/api/calendar/events").json()
    v1 = client.get("/api/v1/calendar/events").json()
    assert legacy == v1
```

### Integration Tests

```python
# tests/integration/test_version_migration.py

def test_client_can_migrate_gradually():
    """Client can mix v1 and legacy endpoints during migration"""
    # Legacy endpoint
    lists = client.get("/api/lists").json()
    
    # V1 endpoint
    events = client.get("/api/v1/calendar/events").json()
    
    # Both should work
    assert lists is not None
    assert events is not None
```

---

## 📈 Benefits

### For Zoe Team
- ✅ Can evolve API safely
- ✅ Clear deprecation path
- ✅ Better version control
- ✅ Industry standard practice

### For API Clients
- ✅ No sudden breaking changes
- ✅ Clear migration timeline
- ✅ Predictable API evolution
- ✅ Time to update code

### For Documentation
- ✅ Clear version documentation
- ✅ Migration guides
- ✅ Changelog per version
- ✅ Better API discovery

---

## 🎯 Success Metrics

**Before**:
- ❌ No versioning
- ❌ Breaking changes block releases
- ❌ Clients fear updates

**After**:
- ✅ Clear versioning system
- ✅ Safe API evolution
- ✅ Confident updates
- ✅ Professional API management

---

## 📚 References

- **REST API Versioning Best Practices**: https://www.freecodecamp.org/news/rest-api-best-practices-rest-endpoint-design-examples/
- **Semantic Versioning**: https://semver.org/
- **FastAPI Advanced Features**: https://fastapi.tiangolo.com/advanced/

---

*Document created: October 9, 2025*  
*Ready for implementation*

