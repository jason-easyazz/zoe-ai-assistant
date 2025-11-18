# Security Review - November 16, 2025

## Critical Security Issues Fixed

### 1. Push Notification Endpoints - Authentication Issues
**Status**: ✅ FIXED

**Issue**: Multiple endpoints in `/api/push` referenced `user_id` without assigning it from the session, causing `NameError`.

**Affected Endpoints**:
- `POST /api/push/subscribe`
- `GET /api/push/preferences`
- `POST /api/push/send`
- `GET /api/push/subscriptions`

**Fix Applied**:
- Added `user_id = session.user_id` at the start of each affected endpoint
- Added missing imports: `Depends`, `AuthenticatedSession`
- All push endpoints now properly authenticate users before processing

### 2. Workflow Router - SQL Syntax Errors
**Status**: ✅ FIXED

**Issue**: Python assignment `user_id = session.user_id` was embedded inside SQL strings, making SQL invalid.

**Affected Endpoints**:
- `GET /api/workflows/`
- `POST /api/workflows/`
- `GET /api/workflows/{workflow_id}`
- `POST /api/workflows/{workflow_id}/toggle`
- `POST /api/workflows/{workflow_id}/run`
- `GET /api/workflows/{workflow_id}/runs`

**Fix Applied**:
- Moved `user_id = session.user_id` assignment outside of SQL strings
- All workflow operations now properly extract user_id before executing queries
- SQL statements are now syntactically correct

### 3. Settings Routes - Complete Security Overhaul
**Status**: ✅ FIXED

**Issue**: All `/api/settings` routes lacked authentication and stored API keys as plain JSON despite ".enc" file extension.

**Problems**:
- No authentication on ANY settings endpoints
- API keys stored in plain JSON at `/app/data/api_keys.enc`
- Keys also written to `.env` file in plaintext
- Anyone who could reach the API could extract or overwrite credentials

**Fix Applied**:

#### Authentication Added:
- All settings routes now require `validate_session` dependency
- Admin-only operations marked with role checks (import, clear, N8N config)
- User-specific data isolation (settings stored per user_id)

#### Encryption Implemented:
- Created `encryption_util.py` with Fernet symmetric encryption
- Encryption key generated once and stored with 0o600 permissions
- All API keys now encrypted before storage
- User-specific encrypted key files: `/app/data/api_keys_{user_id}.enc`
- Keys returned masked for display (e.g., "****abcd")
- Decryption only happens internally when keys are needed

#### Settings Made User-Specific:
- Intelligence settings: `intelligence_settings_{user_id}.json`
- Calendar settings: `calendar_settings_{user_id}.json`
- Time/location settings: `time_location_settings_{user_id}.json`
- N8N settings: `n8n_settings_{user_id}.json` (with encrypted password/API key)

### 4. Rate Limiting - Implementation Complete
**Status**: ✅ FIXED

**Issue**: Rate limiting dependency was an empty stub that just passed through.

**Fix Applied**:
- Implemented Redis-based sliding window rate limiting
- Uses sorted sets for accurate request counting
- Falls back to thread-safe in-memory tracking if Redis unavailable
- Tracks by user_id (if authenticated) or IP address
- Returns 429 with Retry-After header when limit exceeded
- Automatic cleanup of old entries

**Usage**:
```python
from api.dependencies import rate_limit

@router.post("/sensitive-endpoint", dependencies=[Depends(rate_limit(5, 60))])
async def sensitive_operation():
    # Max 5 requests per 60 seconds
    pass
```

### 5. Docker Security - Recommendations
**Status**: ⚠️ REQUIRES MANUAL REVIEW

**Issue**: The `zoe-core` container has excessive host access:
```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
  - /home/zoe/assistant:/home/zoe/assistant:rw
  - /usr/bin/docker:/usr/bin/docker:ro
  - /proc:/host/proc:ro
  - /sys:/host/sys:ro
environment:
  - FULL_ACCESS=true
```

**Security Concerns**:
1. **Docker Socket Mount**: Grants root-equivalent access to host
2. **Host Home Directory**: Full read/write access to user's home
3. **/proc and /sys Mounts**: Access to host system information
4. **FULL_ACCESS=true**: Disables security guardrails

**Recommended Mitigations**:

#### Option 1: Split Privileged Operations (Recommended)
Create a separate hardened worker container for privileged operations:

```yaml
# Secure API container (no privileged access)
zoe-core:
  volumes:
    - ./services/zoe-core:/app
    - ./data:/app/data
  environment:
    - FULL_ACCESS=false
    - WORKER_SERVICE_URL=http://zoe-worker:8100

# Privileged worker (isolated, minimal surface)
zoe-worker:
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro  # Read-only
  cap_drop:
    - ALL
  cap_add:
    - NET_BIND_SERVICE
  security_opt:
    - no-new-privileges:true
```

#### Option 2: User Namespaces
Enable user namespace remapping in Docker daemon:
```json
{
  "userns-remap": "default"
}
```

#### Option 3: Least-Privilege Mounts (Minimum Viable)
If socket access is required:
```yaml
zoe-core:
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro  # Read-only
    - ./services/zoe-core:/app  # Only service directory
    - ./data:/app/data  # Only data directory
    # Remove: /proc, /sys, full home directory mounts
  cap_drop:
    - ALL
  cap_add:
    - NET_BIND_SERVICE
  security_opt:
    - no-new-privileges:true
  read_only: true
  tmpfs:
    - /tmp
```

#### Immediate Actions Recommended:
1. **Audit**: Identify what actually requires Docker socket access
2. **Minimize**: Remove /proc, /sys mounts unless specifically needed
3. **Scope**: Replace full home directory with specific project path
4. **Monitor**: Add logging for Docker API calls
5. **Sandbox**: Run privileged operations in dedicated container

## Summary

| Issue | Severity | Status | Impact |
|-------|----------|--------|--------|
| Push endpoints `user_id` not assigned | 🔴 Critical | ✅ Fixed | 100% failure rate on all push operations |
| Workflow SQL syntax errors | 🔴 Critical | ✅ Fixed | All workflow operations broken |
| Settings auth missing + plain JSON keys | 🔴 Critical | ✅ Fixed | Complete credential exposure |
| Rate limiting empty stub | 🟠 High | ✅ Fixed | No protection against abuse |
| Docker excessive privileges | 🔴 Critical | ⚠️ Review | Root-equivalent host access |

## Next Steps

1. **Test All Fixed Endpoints**: Verify authentication and encryption work correctly
2. **Review Docker Requirements**: Determine minimum privileges needed for zoe-core
3. **Implement Docker Hardening**: Apply appropriate mitigation strategy
4. **Security Audit**: Schedule comprehensive security review
5. **Monitoring**: Add alerting for rate limit violations and authentication failures

## Files Modified

- `services/zoe-core/routers/push.py` - Fixed authentication
- `services/zoe-core/routers/workflows.py` - Fixed SQL errors
- `services/zoe-core/routers/settings.py` - Complete security overhaul
- `services/zoe-core/encryption_util.py` - NEW: Encryption manager
- `services/zoe-auth/api/dependencies.py` - Implemented rate limiting

## Additional Recommendations

### Immediate (Within 1 Week):
1. Review Docker security and implement Option 1 or 3
2. Rotate all existing API keys (old ones stored in plaintext)
3. Add rate limiting to sensitive endpoints (login, registration, API key updates)
4. Enable audit logging for authentication failures

### Short Term (Within 1 Month):
1. Implement API key rotation mechanism
2. Add MFA support for admin operations
3. Set up intrusion detection monitoring
4. Create incident response procedures

### Long Term (Within 3 Months):
1. Complete penetration testing
2. Implement secrets management solution (Vault, AWS Secrets Manager)
3. Add security headers middleware
4. Set up security scanning in CI/CD pipeline

---

**Report Generated**: 2025-11-16  
**Reviewer**: AI Security Analysis  
**Priority**: Critical issues resolved, Docker security requires manual review

