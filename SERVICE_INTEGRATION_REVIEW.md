# Service Integration & Interoperability Review

**Reviewer**: Service Integration Specialist Agent  
**Date**: January 2025  
**Perspective**: Service Integration & Interoperability  
**Focus**: Inter-service communication, API contracts, dependency management, resilience patterns

---

## 1. Chosen Perspective

**Service Integration & Interoperability** - Analyzing how services communicate, coordinate, and maintain reliability as a cohesive distributed system.

---

## 2. Summary of Observations

Zoe is a microservices architecture with **14+ services** communicating via HTTP/REST APIs, WebSockets, and the Model Context Protocol (MCP). The system demonstrates:

- **Strong service boundaries** with clear separation of concerns
- **MCP bridge pattern** for integrating external systems (Home Assistant, N8N)
- **Centralized authentication** via `zoe-auth` with session validation
- **Docker Compose orchestration** with explicit dependency chains
- **Mixed resilience patterns** - some services have timeouts/retries, others don't
- **No API versioning** despite documented strategy
- **Hardcoded service URLs** scattered throughout codebase
- **Inconsistent error handling** across services

**Key Integration Points**:
- `zoe-core` → `zoe-auth` (session validation)
- `zoe-core` → `zoe-mcp-server` → `homeassistant-mcp-bridge` / `n8n-mcp-bridge`
- `zoe-core` → `zoe-mem-agent` (semantic search)
- `zoe-core` → `zoe-litellm` → `zoe-llamacpp` (LLM inference chain)
- `zoe-voice-agent` → `zoe-core`, `zoe-tts`, `zoe-whisper`, `livekit`
- `zoe-ui` (nginx) → proxies to all backend services

---

## 3. Strengths

### 3.1 Clear Service Boundaries
- **Well-defined microservices**: Each service has a single responsibility
- **MCP bridge pattern**: Clean abstraction for external integrations (Home Assistant, N8N)
- **Separation of concerns**: Auth, memory, LLM, UI, voice all isolated

### 3.2 Docker Compose Orchestration
- **Explicit dependencies**: `depends_on` chains clearly defined
- **Health checks**: Most services have health check endpoints
- **Network isolation**: All services on `zoe-network` bridge network
- **Volume management**: Proper data persistence configuration

### 3.3 Authentication Integration
- **Centralized auth**: `zoe-auth` service handles all authentication
- **Session validation**: `auth_integration.py` provides reusable dependency
- **Dev mode bypass**: Sensible localhost fallback for development
- **User isolation**: All queries filter by `user_id`

### 3.4 MCP Bridge Architecture
- **Consistent pattern**: Both Home Assistant and N8N bridges follow same structure
- **Error handling**: Bridges handle timeouts and connection errors gracefully
- **API abstraction**: Clean REST APIs wrapping external systems

### 3.5 Async Communication
- **FastAPI async**: All services use async/await for non-blocking I/O
- **httpx clients**: Modern async HTTP client usage
- **Connection pooling**: Some services (mem-agent) use persistent sessions

---

## 4. Issues / Risks / Weak Points

### 4.1 🔴 CRITICAL: Hardcoded Service URLs

**Problem**: Service URLs hardcoded throughout codebase instead of using service discovery.

**Examples**:
```python
# services/zoe-core/routers/chat.py:841
llm_url = "http://zoe-litellm:8001/v1/chat/completions"

# services/zoe-core/routers/chat.py:1498
"http://zoe-mcp-server:8003/tools/list"

# services/zoe-mcp-server/main.py:50
self.zoe_api_url = os.getenv("ZOE_API_URL", "http://zoe-core:8000")
```

**Impact**:
- ❌ Can't easily change service ports/names
- ❌ Hard to test with mock services
- ❌ Difficult to scale horizontally
- ❌ No service discovery mechanism

**Recommendation**:
- Create service registry/config module
- Use environment variables consistently
- Consider Consul/etcd for dynamic discovery (future)

### 4.2 🔴 CRITICAL: No API Versioning Implementation

**Problem**: API versioning strategy documented but **not implemented**.

**Documentation exists**: `docs/architecture/API_VERSIONING_STRATEGY.md`  
**Reality**: All endpoints use `/api/` without version prefix

**Current**:
```python
# services/zoe-core/routers/chat.py
@router.post("/api/chat")  # No version
```

**Should be**:
```python
@router.post("/api/v1/chat")  # Versioned
```

**Impact**:
- ❌ Can't make breaking changes safely
- ❌ Clients tightly coupled to current API
- ❌ No deprecation path

**Recommendation**:
- Implement Phase 1 from `API_VERSIONING_STRATEGY.md`
- Add `/api/v1/` prefix to all routers
- Maintain `/api/` as legacy (with deprecation headers)

### 4.3 🔴 CRITICAL: Inconsistent Error Handling

**Problem**: Different services return different error formats.

**Examples**:
```python
# homeassistant-mcp-bridge/main.py:184-190
return {
    "entities": [],
    "count": 0,
    "error": e.detail,
    "status": e.status_code
}

# n8n-mcp-bridge/main.py:204
raise HTTPException(status_code=500, detail=str(e))

# zoe-core/routers/chat.py:1707
except httpx.TimeoutException:
    logger.warning("⏱️ Code execution timeout")
    return {"error": "timeout"}  # Different format
```

**Impact**:
- ❌ Clients can't rely on consistent error structure
- ❌ Hard to build generic error handlers
- ❌ Poor debugging experience

**Recommendation**:
- Implement `ERROR_HANDLING_STANDARD.md` (already documented!)
- Use standardized `ErrorResponse` model
- Add global exception handlers

### 4.4 🟡 HIGH: Missing Retry Logic

**Problem**: Most inter-service calls have no retry mechanism.

**Examples**:
```python
# services/zoe-core/auth_integration.py:80
async with httpx.AsyncClient(timeout=2.0) as client:
    resp = await client.get(validate_url, headers=headers)
    # No retry on failure
```

**Impact**:
- ❌ Transient failures cause permanent failures
- ❌ No resilience to network hiccups
- ❌ Poor user experience during brief outages

**Recommendation**:
- Add retry decorator with exponential backoff
- Use `tenacity` library for retries
- Implement circuit breakers for external services

### 4.5 🟡 HIGH: Inconsistent Timeout Values

**Problem**: Timeout values vary wildly across services.

**Examples**:
```python
# 1 second timeout
timeout=1.0  # Memory search

# 2 second timeout  
timeout=2.0  # Auth validation

# 10 second timeout
timeout=10.0  # MCP tool calls

# 60 second timeout
timeout=60.0  # LLM requests

# 120 second timeout
timeout=120.0  # Long operations
```

**Impact**:
- ❌ No clear timeout strategy
- ❌ Some operations timeout too quickly
- ❌ Others may hang indefinitely

**Recommendation**:
- Define timeout tiers (fast/medium/slow)
- Document timeout rationale
- Use configuration constants

### 4.6 🟡 HIGH: No Service Discovery Mechanism

**Problem**: Services hardcode each other's URLs.

**Current**:
```python
# Hardcoded in code
ZOE_AUTH_URL = "http://zoe-auth:8002"
```

**Impact**:
- ❌ Can't change service names/ports easily
- ❌ Hard to run multiple environments
- ❌ No dynamic service registration

**Recommendation**:
- Create service registry/config module
- Use environment variables with defaults
- Consider Consul/etcd for production (future)

### 4.7 🟡 HIGH: Missing Request Correlation IDs

**Problem**: No request ID propagation across services.

**Impact**:
- ❌ Can't trace requests across service boundaries
- ❌ Difficult to debug distributed issues
- ❌ No correlation in logs

**Recommendation**:
- Add `X-Request-ID` header middleware
- Propagate request ID in all inter-service calls
- Include in all log statements

### 4.8 🟡 MEDIUM: Health Check Inconsistencies

**Problem**: Health checks vary in implementation.

**Examples**:
```yaml
# docker-compose.yml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  
# Some services use Python
healthcheck:
  test: ["CMD", "python", "-c", "import sqlite3; ..."]
  
# Some accept 404 as OK
healthcheck:
  test: ["CMD-SHELL", "curl -f http://localhost:8001/health || exit 0"]
```

**Impact**:
- ❌ Inconsistent health check reliability
- ❌ Some services may report unhealthy incorrectly

**Recommendation**:
- Standardize health check endpoint format
- Use consistent health check implementation
- Add health check aggregation endpoint

### 4.9 🟡 MEDIUM: No Circuit Breaker Pattern

**Problem**: Services don't implement circuit breakers for external dependencies.

**Impact**:
- ❌ Cascading failures possible
- ❌ No protection against slow external services
- ❌ Resources wasted on failing calls

**Recommendation**:
- Implement circuit breakers for:
  - `zoe-auth` → `zoe-core` calls
  - `zoe-core` → `zoe-mcp-server` calls
  - External service calls (Home Assistant, N8N)
- Use `pybreaker` or similar library

### 4.10 🟡 MEDIUM: Missing Service Mesh Features

**Problem**: No service mesh for advanced features.

**Missing**:
- ❌ Distributed tracing (OpenTelemetry)
- ❌ Service-to-service mTLS
- ❌ Advanced load balancing
- ❌ Request routing policies

**Recommendation**:
- Consider Linkerd/Istio for production
- Add OpenTelemetry instrumentation
- Implement distributed tracing

### 4.11 🟢 LOW: No API Gateway

**Problem**: Direct service-to-service communication without gateway.

**Impact**:
- ❌ No centralized rate limiting
- ❌ No API key management
- ❌ No request transformation

**Recommendation**:
- Consider Kong/Tyk for API gateway
- Centralize authentication/authorization
- Add rate limiting at gateway level

### 4.12 🟢 LOW: Inconsistent Logging Format

**Problem**: Log formats vary across services.

**Examples**:
```python
logger.info("✅ mem-agent client initialized")
logger.warning("⚠️ Memory search timeout")
logger.error(f"❌ Failed to register router {router_name}: {e}")
```

**Impact**:
- ❌ Hard to parse logs programmatically
- ❌ No structured logging standard

**Recommendation**:
- Use structured logging (JSON format)
- Standardize log levels and formats
- Add correlation IDs to all logs

---

## 5. Recommendations for Improvement

### 5.1 Immediate Actions (This Week)

#### 5.1.1 Implement Service Configuration Module
```python
# services/zoe-core/config/services.py
class ServiceConfig:
    ZOE_AUTH_URL = os.getenv("ZOE_AUTH_URL", "http://zoe-auth:8002")
    ZOE_MCP_SERVER_URL = os.getenv("ZOE_MCP_SERVER_URL", "http://zoe-mcp-server:8003")
    ZOE_MEM_AGENT_URL = os.getenv("ZOE_MEM_AGENT_URL", "http://zoe-mem-agent:8000")
    # ... all service URLs
```

#### 5.1.2 Add Request Correlation IDs
```python
# services/zoe-core/middleware/correlation.py
@app.middleware("http")
async def add_correlation_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
```

#### 5.1.3 Standardize Error Responses
```python
# Implement ERROR_HANDLING_STANDARD.md
# Use ErrorResponse model consistently
```

### 5.2 Short-Term Actions (This Month)

#### 5.2.1 Implement API Versioning
- Add `/api/v1/` prefix to all routers
- Maintain `/api/` as legacy with deprecation headers
- Follow `API_VERSIONING_STRATEGY.md`

#### 5.2.2 Add Retry Logic
```python
# services/zoe-core/utils/retry.py
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10)
)
async def call_service_with_retry(url: str, **kwargs):
    async with httpx.AsyncClient() as client:
        return await client.get(url, **kwargs)
```

#### 5.2.3 Implement Circuit Breakers
```python
# services/zoe-core/utils/circuit_breaker.py
from pybreaker import CircuitBreaker

auth_breaker = CircuitBreaker(fail_max=5, timeout_duration=60)

@auth_breaker
async def validate_session(session_id: str):
    # Auth validation logic
    pass
```

#### 5.2.4 Standardize Timeouts
```python
# services/zoe-core/config/timeouts.py
class TimeoutConfig:
    FAST = 2.0      # Auth, health checks
    MEDIUM = 10.0   # MCP tool calls
    SLOW = 60.0     # LLM requests
    VERY_SLOW = 120.0  # Long operations
```

### 5.3 Medium-Term Actions (Next Quarter)

#### 5.3.1 Add Distributed Tracing
- Integrate OpenTelemetry
- Add trace IDs to all requests
- Visualize service dependencies

#### 5.3.2 Implement Service Mesh
- Evaluate Linkerd vs Istio
- Add mTLS between services
- Implement advanced routing

#### 5.3.3 Add API Gateway
- Evaluate Kong vs Tyk
- Centralize rate limiting
- Add API key management

#### 5.3.4 Service Discovery
- Implement Consul/etcd integration
- Dynamic service registration
- Health check aggregation

### 5.4 Long-Term Actions (Future)

#### 5.4.1 Event-Driven Architecture
- Consider message queue (RabbitMQ/Kafka)
- Decouple services via events
- Improve resilience

#### 5.4.2 GraphQL Federation
- Consider GraphQL for complex queries
- Reduce over-fetching
- Better client experience

---

## 6. Optional Questions for the Developer

1. **Service Discovery**: Are you planning to implement dynamic service discovery (Consul/etcd), or will you stick with Docker Compose service names?

2. **API Versioning**: The versioning strategy is documented but not implemented. What's blocking implementation? Can I help implement Phase 1?

3. **Error Handling**: The error handling standard is documented but not implemented. Should we prioritize this?

4. **Service Mesh**: For production deployments, are you considering a service mesh (Linkerd/Istio) for advanced features like mTLS and distributed tracing?

5. **API Gateway**: Do you plan to add an API gateway (Kong/Tyk) for centralized rate limiting and API key management?

6. **Request Correlation**: Would you like me to implement request correlation IDs across all services for better debugging?

7. **Circuit Breakers**: Which external services should have circuit breakers? (Home Assistant, N8N, LLM services?)

8. **Timeout Strategy**: What's the rationale behind different timeout values? Should we standardize them?

9. **Health Checks**: Should we create a centralized health check aggregator endpoint that checks all services?

10. **Service URLs**: Would you prefer a centralized service configuration module, or environment variables are sufficient?

---

## 7. Integration Patterns Analysis

### 7.1 Current Patterns

**Pattern 1: Direct HTTP Calls**
```python
# zoe-core → zoe-auth
async with httpx.AsyncClient(timeout=2.0) as client:
    resp = await client.get(f"{ZOE_AUTH_URL}/api/auth/user")
```
✅ Simple, direct  
❌ No retry, no circuit breaker

**Pattern 2: MCP Bridge**
```python
# zoe-core → zoe-mcp-server → homeassistant-mcp-bridge → Home Assistant
```
✅ Clean abstraction  
❌ Extra hop, potential latency

**Pattern 3: Service Chain**
```python
# zoe-core → zoe-litellm → zoe-llamacpp
```
✅ Clear dependency chain  
❌ Cascading failures possible

### 7.2 Recommended Patterns

**Pattern 1: Service Client with Retry**
```python
class AuthClient:
    @retry(stop=stop_after_attempt(3))
    async def validate_session(self, session_id: str):
        # Implementation
```

**Pattern 2: Circuit Breaker Wrapper**
```python
@circuit_breaker(fail_max=5, timeout=60)
async def call_mcp_tool(tool_name: str, args: dict):
    # Implementation
```

**Pattern 3: Event-Driven (Future)**
```python
# Publish event instead of direct call
await event_bus.publish("user.authenticated", user_id)
```

---

## 8. Dependency Graph Analysis

### 8.1 Current Dependencies

```
zoe-ui (nginx)
  ├── zoe-core:8000
  ├── zoe-auth:8002
  ├── zoe-tts:9002
  ├── zoe-whisper:9001
  └── livekit:7880

zoe-core:8000
  ├── zoe-auth:8002 (session validation)
  ├── zoe-mcp-server:8003 (tools)
  ├── zoe-mem-agent:8000 (memory search)
  ├── zoe-litellm:8001 (LLM gateway)
  └── zoe-code-execution:8010 (code execution)

zoe-mcp-server:8003
  ├── zoe-core:8000 (API calls)
  ├── homeassistant-mcp-bridge:8007 (HA tools)
  └── n8n-mcp-bridge:8009 (N8N tools)

zoe-litellm:8001
  └── zoe-llamacpp:11434 (LLM inference)

zoe-voice-agent:9003
  ├── zoe-core:8000 (chat API)
  ├── zoe-tts:9002 (text-to-speech)
  ├── zoe-whisper:9001 (speech-to-text)
  └── livekit:7880 (WebRTC)
```

### 8.2 Critical Paths

**Critical Path 1: Chat Request**
```
User → zoe-ui → zoe-core → zoe-auth (validate)
                              ↓
                         zoe-mem-agent (search)
                              ↓
                         zoe-litellm → zoe-llamacpp (generate)
                              ↓
                         zoe-mcp-server (tools if needed)
```

**Critical Path 2: Voice Request**
```
User → zoe-voice-agent → zoe-whisper (STT)
                              ↓
                         zoe-core (process)
                              ↓
                         zoe-tts (TTS)
                              ↓
                         livekit (stream)
```

### 8.3 Single Points of Failure

- ❌ **zoe-auth**: All requests depend on it
- ❌ **zoe-core**: Central orchestrator
- ❌ **zoe-llamacpp**: Only LLM inference service
- ❌ **zoe-mcp-server**: All tool calls go through it

**Recommendation**: Add redundancy for critical services

---

## 9. Resilience Scorecard

| Service | Retry Logic | Circuit Breaker | Timeout | Health Check | Score |
|---------|------------|-----------------|---------|--------------|-------|
| zoe-core | ❌ | ❌ | ✅ | ✅ | 2/5 |
| zoe-auth | ❌ | ❌ | ✅ | ✅ | 2/5 |
| zoe-mcp-server | ❌ | ❌ | ✅ | ✅ | 2/5 |
| zoe-mem-agent | ❌ | ❌ | ✅ | ✅ | 2/5 |
| homeassistant-mcp-bridge | ❌ | ❌ | ✅ | ✅ | 2/5 |
| n8n-mcp-bridge | ❌ | ❌ | ✅ | ✅ | 2/5 |

**Overall Resilience**: ⚠️ **40%** - Missing retry logic and circuit breakers

---

## 10. Conclusion

Zoe demonstrates **solid microservices architecture** with clear service boundaries and good separation of concerns. However, **inter-service communication patterns need improvement**:

**Strengths**:
- ✅ Clear service boundaries
- ✅ Docker Compose orchestration
- ✅ Centralized authentication
- ✅ MCP bridge pattern

**Critical Gaps**:
- ❌ No API versioning (despite documentation)
- ❌ Inconsistent error handling
- ❌ Missing retry logic
- ❌ No circuit breakers
- ❌ Hardcoded service URLs

**Priority Actions**:
1. Implement service configuration module
2. Add request correlation IDs
3. Standardize error handling
4. Implement API versioning
5. Add retry logic and circuit breakers

The foundation is strong, but **resilience patterns** need to be added to make this production-ready for distributed deployments.

---

*Review completed: January 2025*  
*Next review recommended: After implementing priority actions*
