# Building Zoe Modules - Developer Guide

**Version**: 1.0  
**Last Updated**: 2026-01-22

---

## Overview

Zoe modules are self-contained services that extend Zoe's capabilities. They follow the **MCP (Model Context Protocol) pattern** used by the Home Assistant bridge. (The n8n bridge and n8n itself were retired in March 2026 — OpenClaw cron/skills/exec covers automation; only the n8n SSO code path under zoe-auth remains.)

**Key benefits:**
- Isolated development (work on music without breaking calendar)
- Optional loading (users choose what to run)
- AI-accessible (Zoe can control your module via tools)
- Community-friendly (standard structure for contributions)

---

## Quick Start

### 1. Create the Module From Scratch

There is **no copyable scaffold in the tree**, and `modules/omnigent` is not a substitute:
it is a specialised container-only module with no `main.py`, `requirements.txt`,
`services/` or `intents/`, so a copy fails `tools/validate_module.py` immediately and its
compose file would collide with the real `zoe-omnigent` deployment.

Create the directory, then write the five files `tools/validate_module.py` requires —
`main.py`, `Dockerfile`, `requirements.txt`, `docker-compose.module.yml`, `README.md` —
from the structure and examples in the sections below:

```bash
mkdir -p modules/your-module-name/{services,intents}
```

Validate as you go — from the **repository root**, passing the module NAME (the
validator prepends `modules/` itself, so a path would become
`modules/modules/…`). It names every missing piece:

```bash
python3 tools/validate_module.py your-module-name
```

### 2. Module Structure

```
modules/your-module-name/
├── main.py                    # FastAPI server with tool endpoints
├── Dockerfile                 # Container build config
├── requirements.txt           # Python dependencies
├── docker-compose.module.yml  # Service configuration
├── services/                  # Business logic
│   └── your_feature/
├── db/schema/                 # Database schemas (if needed)
└── README.md                  # Documentation
```

### 3. Define Your Tools

In `main.py`, define tools that Zoe AI can call:

State-changing routes are **token-gated and fail closed** — see
`require_service_token` in the full `main.py` template below. This is the module
contract (`modules/AGENTS.md`), not optional hardening.

```python
@app.post("/tools/your_action", dependencies=[Depends(require_service_token)])
async def tool_your_action(request: YourRequest):
    """
    Tool: your_module.your_action
    
    Description of what this tool does.
    """
    try:
        # Your implementation
        result = await your_service.do_something(request.parameter)
        
        return {
            "success": True,
            "result": result,
            "tool_name": "your_action"
        }
    except Exception as e:
        logger.error(f"Tool failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

### 4. Register with MCP Server

Add your tools to [`services/zoe-mcp-server/http_mcp_server.py`](../../services/zoe-mcp-server/http_mcp_server.py):

```python
# In list_tools() function, add:
{"name": "your_module_action", "description": "What your tool does"},

# Add endpoint handler. It MUST forward the service token: the module's
# state-changing routes are gated, so a proxy that posts only the JSON body gets
# 401 on every MCP-mediated call even though a direct authenticated curl works.
# Provision the SAME secret here as in the module's compose environment.
YOUR_MODULE_TOKEN = os.getenv("ZOE_YOURMODULE_SERVICE_TOKEN", "")

@app.post("/tools/your_module_action")
async def your_module_action(request: YourRequest):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{YOUR_MODULE_URL}/tools/your_action",
                json=request.dict(),
                headers={"X-Zoe-Service-Token": YOUR_MODULE_TOKEN},
                timeout=10.0
            )
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### 5. Test Your Module

```bash
# Build
cd modules/your-module-name
docker compose -f docker-compose.module.yml build

# Start standalone
docker compose -f docker-compose.module.yml up -d

# Test health
curl http://localhost:YOUR_PORT/health

# Test a tool. The token header is REQUIRED: without it the gate returns 401,
# and if ZOE_YOURMODULE_SERVICE_TOKEN is unset the module fails closed with 503.
curl -X POST http://localhost:YOUR_PORT/tools/your_action \
  -H "Content-Type: application/json" \
  -H "X-Zoe-Service-Token: ${ZOE_YOURMODULE_SERVICE_TOKEN}" \
  -d '{"parameter": "value"}'
```

### 6. Enable Your Module

```bash
# Add to Zoe
python tools/zoe_module.py enable your-module-name

# Regenerate compose
python tools/generate_module_compose.py

# Restart with module
docker compose -f docker-compose.yml \
               -f docker-compose.jetson.yml \
               -f docker-compose.modules.yml \
               up -d
```

---

## Module Structure Details

### main.py Template

```python
#!/usr/bin/env python3
"""
Zoe Your-Feature Module
=======================

Brief description of what your module does.
"""

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, List
import logging
import os
import secrets

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Zoe Your-Feature Module",
    description="Your module description",
    version="1.0.0"
)

# Configuration
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://zoe-mcp-server:8003")
DATABASE_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# --- REQUIRED: the shared-service-token gate (modules/AGENTS.md) --------------
# Every state-changing /tools/* route must be gated by this token and FAIL
# CLOSED (503) until it is set. The in-cluster caller sends the same value as
# X-Zoe-Service-Token. This is not optional hardening — it is the module
# contract, and the from-scratch flow must carry it just as the old copyable
# template did (cross-review, #1653).
SERVICE_TOKEN = os.getenv("ZOE_YOURMODULE_SERVICE_TOKEN", "")


def require_service_token(x_zoe_service_token: str = Header(default="")) -> None:
    if not SERVICE_TOKEN:
        # Fail CLOSED: an unset token means the gate is unconfigured, never open.
        raise HTTPException(status_code=503, detail="module service token not configured")
    if not secrets.compare_digest(x_zoe_service_token, SERVICE_TOKEN):
        raise HTTPException(status_code=401, detail="bad or missing X-Zoe-Service-Token")


# Pydantic models
class YourRequest(BaseModel):
    parameter: str
    user_id: Optional[str] = None

# Health endpoints
@app.get("/")
async def root():
    return {
        "service": "Zoe Your-Feature Module",
        "status": "healthy",
        "version": "1.0.0",
        "tools": ["your_module.action1", "your_module.action2"]
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}

# Tool endpoints — state-changing routes are TOKEN-GATED.
# /health and / stay open so the container healthcheck works.
@app.post("/tools/action1", dependencies=[Depends(require_service_token)])
async def tool_action1(request: YourRequest):
    """Tool: your_module.action1"""
    try:
        # Implementation
        return {"success": True, "result": "..."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=YOUR_PORT)
```

### Dockerfile Template

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE YOUR_PORT

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:YOUR_PORT/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "YOUR_PORT"]
```

### docker-compose.module.yml Template

```yaml
services:
  your-module-name:
    build: .
    container_name: your-module-name
    restart: unless-stopped
    ports:
      # LOOPBACK ONLY (modules/AGENTS.md). A bare "YOUR_PORT:YOUR_PORT" publishes
      # on 0.0.0.0 and [::], exposing the module's tools to every host on the LAN.
      # In-cluster callers reach it by service name over zoe-network, so nothing
      # legitimate needs the wider bind.
      - "127.0.0.1:YOUR_PORT:YOUR_PORT"
    volumes:
      - .:/app
      - ../../data:/app/data
    environment:
      - PYTHONUNBUFFERED=1
      - DATABASE_PATH=/app/data/zoe.db
      - MCP_SERVER_URL=http://zoe-mcp-server:8003
      # The gate's shared secret. Unset => the module fails closed with 503.
      - ZOE_YOURMODULE_SERVICE_TOKEN=${ZOE_YOURMODULE_SERVICE_TOKEN:-}
      - YOUR_API_KEY=${YOUR_API_KEY:-}
    networks:
      - zoe-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:YOUR_PORT/health"]
      interval: 30s
      timeout: 10s
      retries: 3

networks:
  zoe-network:
    name: zoe-network
    external: true
```

---

## Tool Naming Convention

**Use domain.action pattern:**

✅ Good:
- `music.play_song`
- `music.search`
- `calendar.create_event`
- `tasks.add`

❌ Bad:
- `play_song` (no domain)
- `music_play_song` (underscore separator)
- `playMusic` (camelCase)

---

## Database Access

**Modules can share the main `zoe.db` database:**

```python
import sqlite3
import os

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def get_data(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM your_table WHERE user_id = ?", (user_id,))
    results = cursor.fetchall()
    conn.close()
    return results
```

**Important**: Always filter by `user_id` for multi-user isolation!

**Create module-specific tables**:

```sql
-- db/schema/your_feature.sql
CREATE TABLE IF NOT EXISTS your_feature_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_your_feature_user ON your_feature_data(user_id);
```

---

## Best Practices

### 1. Error Handling

Always handle exceptions gracefully:

```python
try:
    result = await your_service.action()
    return {"success": True, "result": result}
except ValueError as e:
    raise HTTPException(status_code=400, detail=str(e))
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    raise HTTPException(status_code=500, detail="Internal error")
```

### 2. Logging

Log tool calls for debugging:

```python
logger.info(f"✅ your_action: param={request.param}, user_id={request.user_id}")
```

### 3. Timeouts

Set reasonable timeouts for external APIs:

```python
async with httpx.AsyncClient(timeout=10.0) as client:
    response = await client.get(url)
```

### 4. User Isolation

Always filter by user_id:

```python
cursor.execute("SELECT * FROM data WHERE user_id = ?", (user_id,))
```

### 5. Platform Awareness

Adapt to hardware capabilities:

```python
from services.platform import detect_hardware, get_platform_capabilities

PLATFORM = detect_hardware()  # jetson, pi5, unknown
CAPS = get_platform_capabilities()

if CAPS["ml_enabled"]:
    # Use ML-based approach
else:
    # Use lighter approach
```

---

## Port Assignment

**Reserved ports:**
- 8000: zoe-core
- 8001: zoe-litellm
- 8002: zoe-auth
- 8003: zoe-mcp-server
- 8007: homeassistant-mcp-bridge
- 8010: zoe-code-execution
- 9001: zoe-whisper
- 9002: zoe-tts
- 9003: zoe-voice-agent

**Available ranges:**
- 8101-8199: Feature modules
- 8200-8299: Integration bridges
- 9100-9199: Voice/audio modules

---

## Testing Checklist

- [ ] Module builds successfully
- [ ] Container starts and health check passes
- [ ] All tools respond correctly
- [ ] Tools registered with MCP server
- [ ] AI can call tools via MCP
- [ ] Database access works (if applicable)
- [ ] User isolation enforced
- [ ] Error handling works
- [ ] Logs are clear and helpful
- [ ] Module can be disabled without breaking core

---

## Example: the retired music module

> `modules/zoe-music` was **deleted** 2026-08-05 — it is not a reference implementation
> any more, and it is **not** the live music system (that is `zoe-music-assistant`, the
> upstream Music Assistant container). Recover the code for study with
> `git log --all -- modules/zoe-music`. See [docs/CANONICAL.md](../CANONICAL.md).

It remains a useful illustration of the module shape:

**What it demonstrates:**
- 12 MCP tools (search, play, pause, volume, queue, etc.)
- Multi-service integration (YouTube, Spotify, Apple Music)
- Platform-aware features (ML on Jetson, CPU on Pi)
- Database integration (music history, affinity, zones)
- Complex business logic (recommendation engine, zone management)

**To study the complete pattern**, recover the deleted code:
`git log --all -- modules/zoe-music`. Do not reconstruct it as a starting point —
build from the templates above; they are current, and it is not.

---

## Module Manifest (Optional)

While not required yet, you can add `module.yaml` for richer metadata:

```yaml
module:
  name: "your-module-name"
  version: "1.0.0"
  description: "What your module does"
  author: "Your Name"
  license: "MIT"
  
  # Module classification
  type: "feature"  # feature | integration | core
  category: "your-category"  # entertainment | productivity | smart-home

  # Dependencies
  dependencies:
    core_modules:
      - zoe-core
      - zoe-auth
    optional_modules:
      - another-module
  
  # Tools provided
  tools:
    - name: "action1"
      description: "What it does"
    - name: "action2"
      description: "What it does"
```

---

## Submitting Modules

**Once Zoe is open source**, you can submit modules:

1. Create GitHub repository for your module
2. Follow this structure and naming conventions
3. Include comprehensive README
4. Add tests for all tools
5. Submit to Zoe module registry (TBD)

---

## Getting Help

- **Reference**: Study [`modules/omnigent/`](../../modules/omnigent/) for compose/Dockerfile
  shape — but it is container-only, NOT a copyable FastAPI scaffold (see Quick Start)
- **Documentation**: See other guides in `docs/modules/`
- **Issues**: Report problems on GitHub (after public release)

---

## Module Categories

**Feature Modules** (core functionality):
- Music, calendar, tasks, notes, journal
- Run as separate containers
- Optional for users

**Integration Modules** (external services):
- Home Assistant, Matrix, Notion, etc.
- Bridge to external APIs
- Use `-mcp-bridge` suffix

**Utility Modules** (developer tools):
- Code execution, Docker management, testing
- Power-user features
- Not needed for basic usage

---

## Next Steps

1. Create the module from scratch (Quick Start above) — there is no scaffold to copy
2. Write the five required files from the templates in this guide
3. Test standalone
4. Register with MCP server
5. Enable and test integrated
6. Document thoroughly
7. Share with community (when open source)

---

**Happy module building!** 🚀
