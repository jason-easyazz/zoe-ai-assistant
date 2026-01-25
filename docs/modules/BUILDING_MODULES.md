# Building Zoe Modules - Developer Guide

**Version**: 1.0  
**Last Updated**: 2026-01-22

---

## Overview

Zoe modules are self-contained services that extend Zoe's capabilities. They follow the **MCP (Model Context Protocol) pattern** used by Home Assistant and N8N bridges.

**Key benefits:**
- Isolated development (work on music without breaking calendar)
- Optional loading (users choose what to run)
- AI-accessible (Zoe can control your module via tools)
- Community-friendly (standard structure for contributions)

---

## Quick Start

### 1. Copy the Template

Use the music module as a reference template:

```bash
cp -r modules/zoe-music modules/your-module-name
cd modules/your-module-name
```

### 2. Update Module Structure

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

```python
@app.post("/tools/your_action")
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

# Add endpoint handler:
@app.post("/tools/your_module_action")
async def your_module_action(request: YourRequest):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{YOUR_MODULE_URL}/tools/your_action",
                json=request.dict(),
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

# Test a tool
curl -X POST http://localhost:YOUR_PORT/tools/your_action \
  -H "Content-Type: application/json" \
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

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, List
import logging
import os

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Zoe Your-Feature Module",
    description="Your module description",
    version="1.0.0"
)

# Configuration
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://zoe-mcp-server:8003")
DATABASE_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

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

# Tool endpoints
@app.post("/tools/action1")
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
      - "YOUR_PORT:YOUR_PORT"
    volumes:
      - .:/app
      - ../../data:/app/data
    environment:
      - PYTHONUNBUFFERED=1
      - DATABASE_PATH=/app/data/zoe.db
      - MCP_SERVER_URL=http://zoe-mcp-server:8003
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
- 8009: n8n-mcp-bridge
- 8010: zoe-code-execution
- 8100: zoe-music
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

## Example: Music Module

**Reference implementation**: [`modules/zoe-music/`](../../modules/zoe-music/)

**What it demonstrates:**
- 12 MCP tools (search, play, pause, volume, queue, etc.)
- Multi-service integration (YouTube, Spotify, Apple Music)
- Platform-aware features (ML on Jetson, CPU on Pi)
- Database integration (music history, affinity, zones)
- Complex business logic (recommendation engine, zone management)

**Study the music module** to understand the complete pattern.

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

- **Reference**: Study [`modules/zoe-music/`](../../modules/zoe-music/)
- **Template**: Copy music module structure
- **Documentation**: See other guides in `docs/modules/`
- **Issues**: Report problems on GitHub (after public release)

---

## Module Categories

**Feature Modules** (core functionality):
- Music, calendar, tasks, notes, journal
- Run as separate containers
- Optional for users

**Integration Modules** (external services):
- Home Assistant, N8N, Matrix, Notion, etc.
- Bridge to external APIs
- Use `-mcp-bridge` suffix

**Utility Modules** (developer tools):
- Code execution, Docker management, testing
- Power-user features
- Not needed for basic usage

---

## Next Steps

1. Copy music module as template
2. Modify for your use case
3. Test standalone
4. Register with MCP server
5. Enable and test integrated
6. Document thoroughly
7. Share with community (when open source)

---

**Happy module building!** 🚀
