# Zoe Module Requirements - MANDATORY

**Status**: Required for all modules  
**Enforcement**: Automated validation before enabling

---

## 🔴 CRITICAL REQUIREMENTS

These are **NON-NEGOTIABLE**. Modules MUST comply or they will not be enabled.

### 1. Network Configuration

**REQUIRED**: All modules MUST be on `zoe-network`

```yaml
# docker-compose.module.yml
services:
  your-module:
    networks:
      - zoe-network

networks:
  zoe-network:
    name: zoe-network  # CRITICAL: Prevents Docker auto-prefix
    external: true      # Joins existing network
```

**Why**: Modules must communicate with zoe-core and zoe-mcp-server.  
**Test**: `docker network inspect zoe-network | grep your-module-name`

---

### 2. Security - NEVER Commit Secrets

**FORBIDDEN** in repository:
- ❌ `.env` files
- ❌ API keys in code
- ❌ Private keys (`.pem`, `.key`)
- ❌ Passwords in any form
- ❌ Tokens hardcoded

**REQUIRED**: Use environment variables

```python
# ✅ CORRECT
API_KEY = os.getenv("SPOTIFY_API_KEY", "")

# ❌ WRONG
API_KEY = "sk_live_abc123..."
```

**Enforcement**: Validator checks for these patterns.

---

### 3. Code Execution Safety

**FORBIDDEN**:
```python
eval(user_input)      # ❌ NEVER
exec(code_string)     # ❌ NEVER
os.system(command)    # ⚠️  Review required
subprocess.run(cmd, shell=True)  # ⚠️  Review required
```

**Why**: Remote code execution vulnerabilities.  
**Enforcement**: Validator fails if `eval()` or `exec()` found.

---

### 4. Required Files

**MUST have** all of these:

```
modules/your-module/
├── main.py                      # FastAPI application
├── Dockerfile                   # Container config
├── requirements.txt             # Dependencies with versions
├── docker-compose.module.yml    # Service definition
└── README.md                    # Documentation
```

**Enforcement**: Validator checks file existence.

---

### 5. Health Endpoints

**REQUIRED**: Module MUST respond to health checks

```python
@app.get("/health")
async def health():
    return {"status": "healthy"}
```

```yaml
# docker-compose.module.yml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:PORT/health"]
  interval: 30s
  timeout: 10s
  retries: 3
```

**Why**: System monitoring and auto-recovery.  
**Enforcement**: Validator checks for `/health` endpoint.

---

## 🟡 STRONGLY RECOMMENDED

These should be followed unless you have good reason not to.

### 6. User Isolation

**MUST** filter by `user_id` in database queries:

```python
# ✅ CORRECT - User isolated
cursor.execute(
    "SELECT * FROM data WHERE user_id = ?",
    (user_id,)
)

# ❌ WRONG - Leaks data across users
cursor.execute("SELECT * FROM data")
```

**Why**: Privacy and data isolation.

---

### 7. MCP Tool Naming

**Format**: `domain.action`

✅ **Good**:
- `music.play_song`
- `music.search`
- `calendar.create_event`

❌ **Bad**:
- `play_song` (no domain)
- `music_play_song` (underscore separator)
- `playMusic` (camelCase)

**Why**: Consistency and discoverability.

---

### 8. Error Handling

**ALL endpoints MUST handle errors gracefully:**

```python
@app.post("/tools/your_action")
async def tool_your_action(request: YourRequest):
    try:
        result = await do_something(request.param)
        return {"success": True, "result": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")
```

**Why**: Graceful degradation, better debugging.

---

### 9. Logging

**Use structured logging:**

```python
import logging
logger = logging.getLogger(__name__)

# ✅ Good - informative
logger.info(f"✅ tool_search: query={query}, user_id={user_id}")
logger.error(f"❌ Search failed for user {user_id}: {error}")

# ❌ Bad - not helpful
logger.info("Search called")
logger.error("Error occurred")
```

**Why**: Debugging and monitoring.

---

### 10. Dependencies

**Pin versions** in requirements.txt:

```txt
# ✅ CORRECT - Reproducible
fastapi==0.104.1
pydantic==2.5.0

# ❌ WRONG - Breaks randomly
fastapi
pydantic>=2.0
```

**Why**: Reproducible builds.

---

## 🟢 BEST PRACTICES

Follow these for high-quality modules.

### 11. Database Migrations

**If module creates tables:**

```python
def init_db():
    """Initialize database schema."""
    schema_path = "/app/db/schema/your_feature.sql"
    if os.path.exists(schema_path):
        with open(schema_path, 'r') as f:
            cursor.executescript(f.read())
```

Store schema in `db/schema/your_feature.sql`.

---

### 12. Timeouts

**Set reasonable timeouts** for external APIs:

```python
async with httpx.AsyncClient(timeout=10.0) as client:
    response = await client.get(external_url)
```

**Default**: 10 seconds for most operations.

---

### 13. Rate Limiting

**Respect external API limits:**

```python
from ratelimit import limits, sleep_and_retry

@sleep_and_retry
@limits(calls=10, period=60)  # 10 calls per minute
async def call_external_api():
    ...
```

---

### 14. Testing

**Include basic tests:**

```python
# test_module.py
async def test_health():
    response = await client.get("/health")
    assert response.status_code == 200

async def test_tool_search():
    response = await client.post("/tools/search", json={"query": "test"})
    assert response.json()["success"] == True
```

---

### 15. Documentation

**README.md should include:**

```markdown
# Module Name

## Features
- What it does

## Installation
- How to enable

## MCP Tools
- List of tools provided

## Configuration
- Environment variables needed

## Testing
- How to test

## Troubleshooting
- Common issues
```

---

## 📋 Pre-Enable Checklist

Before enabling a module:

```bash
# 1. Validate structure
python tools/validate_module.py your-module

# 2. Build container
cd modules/your-module
docker compose -f docker-compose.module.yml build

# 3. Test standalone
docker compose -f docker-compose.module.yml up -d
curl http://localhost:YOUR_PORT/health

# 4. Test tools
curl -X POST http://localhost:YOUR_PORT/tools/your_action \
  -H "Content-Type: application/json" \
  -d '{"param": "test"}'

# 5. Enable in system
python tools/zoe_module.py enable your-module
python tools/generate_module_compose.py

# 6. Test integrated
docker compose -f docker-compose.yml -f docker-compose.modules.yml up -d
```

---

## 🚫 What NOT to Do

### ❌ Don't Modify Core

Modules should NEVER require changes to:
- `services/zoe-core/` (except adding volume mounts)
- `services/zoe-mcp-server/` (for tool registration - documented separately)
- Other modules

If you need to modify core, **you're doing it wrong**.

---

### ❌ Don't Share State

Modules should be **stateless** or use database:

```python
# ❌ WRONG - Lost on restart
cache = {}

# ✅ CORRECT - Persisted
def get_cache():
    cursor.execute("SELECT * FROM cache WHERE key = ?", (key,))
```

---

### ❌ Don't Hardcode Paths

```python
# ❌ WRONG
db_path = "/app/data/zoe.db"

# ✅ CORRECT
db_path = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
```

---

### ❌ Don't Block Event Loop

```python
# ❌ WRONG - Blocks
time.sleep(10)

# ✅ CORRECT - Async
await asyncio.sleep(10)
```

---

## 🔧 Validation Tool

**Run before enabling:**

```bash
python tools/validate_module.py your-module
```

**Checks:**
- ✅ Required files present
- ✅ Docker compose valid
- ✅ Network configuration correct
- ✅ No security issues
- ✅ Code quality standards
- ✅ Documentation present

**Exit codes:**
- `0` - All checks passed
- `1` - Validation failed

---

## 🚨 Security Review Triggers

**Manual review required if module:**
- Uses `subprocess` or `os.system`
- Accesses file system outside `/app/data`
- Makes external network calls to unknown domains
- Handles payment/financial data
- Processes sensitive personal information

---

## 📚 See Also

- [Building Modules Guide](BUILDING_MODULES.md) - Full development guide
- [Music Module Example](../../modules/zoe-music/README.md) - Reference implementation
- [Module Intent System](MODULE_INTENT_SYSTEM_COMPLETE.md) - Adding intents

---

## Version History

- **1.0** (2026-01-22) - Initial requirements
  - Network configuration mandatory
  - Security requirements
  - Validation tooling

---

**These requirements exist to keep Zoe stable, secure, and maintainable.**

**Before you build**: Read this document  
**Before you enable**: Run the validator  
**When in doubt**: Look at the music module
