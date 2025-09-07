"""
GENIUS ZACK - Fixed to use REAL data and generate code
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import subprocess
import sqlite3
import json
import sys
import os
from datetime import datetime
import psutil
import logging
import uuid

sys.path.append("/app")
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

# Models
class DeveloperChat(BaseModel):
    message: str

class DeveloperTask(BaseModel):
    title: str
    description: str
    type: str = "feature"
    priority: str = "medium"

# Task storage
developer_tasks = {}

def execute_command(cmd: str, timeout: int = 10) -> dict:
    """Execute system commands and return real output"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/app"
        )
        return {
            "stdout": result.stdout[:5000],
            "stderr": result.stderr[:2000],
            "returncode": result.returncode,
            "success": result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Command timed out", "returncode": -1, "success": False}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1, "success": False}

def analyze_for_optimization() -> dict:
    """Get REAL system metrics"""
    analysis = {"metrics": {}, "recommendations": [], "issues": []}
    
    try:
        # Real CPU metrics
        analysis["metrics"]["cpu_percent"] = psutil.cpu_percent(interval=1)
        analysis["metrics"]["cpu_cores"] = psutil.cpu_count()
        
        # Real Memory metrics
        mem = psutil.virtual_memory()
        analysis["metrics"]["memory_percent"] = round(mem.percent, 1)
        analysis["metrics"]["memory_used_gb"] = round(mem.used / (1024**3), 2)
        analysis["metrics"]["memory_total_gb"] = round(mem.total / (1024**3), 2)
        analysis["metrics"]["memory_available_gb"] = round(mem.available / (1024**3), 2)
        
        # Real Disk metrics
        disk = psutil.disk_usage("/")
        analysis["metrics"]["disk_percent"] = round(disk.percent, 1)
        analysis["metrics"]["disk_free_gb"] = round(disk.free / (1024**3), 2)
        analysis["metrics"]["disk_total_gb"] = round(disk.total / (1024**3), 2)
        
        # Docker containers
        docker_result = execute_command("docker ps -q | wc -l")
        if docker_result["success"]:
            analysis["metrics"]["containers_running"] = int(docker_result["stdout"].strip())
        
        docker_all = execute_command("docker ps -aq | wc -l")
        if docker_all["success"]:
            analysis["metrics"]["containers_total"] = int(docker_all["stdout"].strip())
        
        # Generate REALISTIC recommendations based on Pi hardware
        analysis["recommendations"] = []
        
        if analysis["metrics"]["cpu_percent"] > 80:
            analysis["recommendations"].append(f"CPU at {analysis['metrics']['cpu_percent']}% - Stop unused containers")
        
        if analysis["metrics"]["memory_percent"] > 85:
            analysis["recommendations"].append(f"Memory critical at {analysis['metrics']['memory_used_gb']}GB - Restart services")
        
        if analysis["metrics"]["disk_percent"] > 90:
            analysis["recommendations"].append(f"Low disk ({analysis['metrics']['disk_free_gb']}GB free) - Clean logs")
        
        # Add practical suggestions for Raspberry Pi
        if not analysis["recommendations"]:
            analysis["recommendations"] = [
                "Add Redis caching using existing zoe-redis container",
                "Implement WebSocket for real-time updates",
                "Set up automated backups with cron",
                "Add API rate limiting with FastAPI middleware",
                "Enable gzip compression for responses"
            ]
        
        # Calculate health score
        analysis["health_score"] = 100
        if analysis["metrics"]["cpu_percent"] > 70:
            analysis["health_score"] -= 10
        if analysis["metrics"]["memory_percent"] > 70:
            analysis["health_score"] -= 10
        if analysis["metrics"]["disk_percent"] > 80:
            analysis["health_score"] -= 10
            
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        analysis["error"] = str(e)
        analysis["health_score"] = 0
    
    return analysis

@router.get("/status")
async def get_status():
    """Get system status with REAL data"""
    analysis = analyze_for_optimization()
    return {
        "status": "operational",
        "personality": "Zack",
        "mode": "genius-level",
        "metrics": analysis.get("metrics", {}),
        "health_score": analysis.get("health_score", 100)
    }

@router.post("/chat")
async def developer_chat(request: DeveloperChat):
    """Chat endpoint that uses REAL data and generates code"""
    
    message_lower = request.message.lower()
    
    # Always get fresh real data
    analysis = analyze_for_optimization()
    metrics = analysis.get("metrics", {})
    
    # Handle different request types
    if "memory" in message_lower or "ram" in message_lower:
        response = f"""**Memory Status (REAL):**
- Used: {metrics.get('memory_used_gb', 0):.2f}GB / {metrics.get('memory_total_gb', 0):.2f}GB
- Available: {metrics.get('memory_available_gb', 0):.2f}GB
- Usage: {metrics.get('memory_percent', 0):.1f}%
- Health: {'✅ Good' if metrics.get('memory_percent', 0) < 70 else '⚠️ High'}"""
    
    elif "disk" in message_lower or "storage" in message_lower:
        response = f"""**Disk Status (REAL):**
- Used: {metrics.get('disk_percent', 0):.1f}%
- Free: {metrics.get('disk_free_gb', 0):.1f}GB
- Total: {metrics.get('disk_total_gb', 0):.1f}GB
- Health: {'✅ Good' if metrics.get('disk_percent', 0) < 80 else '⚠️ Getting Full'}"""
    
    elif "cpu" in message_lower or "processor" in message_lower:
        # Get CPU temperature for Raspberry Pi
        temp_cmd = execute_command("cat /sys/class/thermal/thermal_zone0/temp")
        temp = "Unknown"
        if temp_cmd["success"]:
            try:
                temp = f"{int(temp_cmd['stdout'].strip())/1000:.1f}°C"
            except:
                temp = "N/A"
        
        response = f"""**CPU Status (REAL):**
- Usage: {metrics.get('cpu_percent', 0):.1f}%
- Cores: {metrics.get('cpu_cores', 0)}
- Temperature: {temp}
- Health: {'✅ Good' if metrics.get('cpu_percent', 0) < 70 else '⚠️ High'}"""
    
    elif "docker" in message_lower or "container" in message_lower:
        docker_cmd = execute_command("docker ps --format 'table {{.Names}}\t{{.Status}}' | head -10")
        response = f"""**Docker Status (REAL):**
- Running: {metrics.get('containers_running', 0)} containers
- Total: {metrics.get('containers_total', 0)} containers

Container List:
```
{docker_cmd['stdout'] if docker_cmd['success'] else 'Error getting containers'}
```"""
    
    elif any(word in message_lower for word in ["create", "build", "implement", "endpoint", "api", "code"]):
        # Generate ACTUAL code
        if "endpoint" in message_lower or "api" in message_lower:
            response = """**Generated API Endpoint Code:**

```python
# File: /app/routers/custom_feature.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

router = APIRouter(prefix="/api/custom", tags=["custom"])

class CustomRequest(BaseModel):
    name: str
    value: Optional[str] = None

class CustomResponse(BaseModel):
    id: int
    name: str
    created_at: datetime
    status: str

@router.get("/", response_model=List[CustomResponse])
async def get_items():
    \"\"\"Get all custom items\"\"\"
    # TODO: Connect to database
    return [
        CustomResponse(
            id=1,
            name="Example",
            created_at=datetime.now(),
            status="active"
        )
    ]

@router.post("/", response_model=CustomResponse)
async def create_item(request: CustomRequest):
    \"\"\"Create new custom item\"\"\"
    new_item = CustomResponse(
        id=1,
        name=request.name,
        created_at=datetime.now(),
        status="created"
    )
    return new_item

@router.get("/{item_id}", response_model=CustomResponse)
async def get_item(item_id: int):
    \"\"\"Get specific item by ID\"\"\"
    if item_id < 1:
        raise HTTPException(status_code=404, detail="Item not found")
    
    return CustomResponse(
        id=item_id,
        name="Retrieved Item",
        created_at=datetime.now(),
        status="active"
    )
```

**To implement:**
1. Save to `/app/routers/custom_feature.py`
2. In `main.py` add: `from routers import custom_feature`
3. Add router: `app.include_router(custom_feature.router)`
4. Restart: `docker compose restart zoe-core`"""
        
        elif "redis" in message_lower or "cache" in message_lower:
            response = """**Redis Caching Implementation:**

```python
# File: /app/utils/cache.py
import redis
import json
from typing import Optional, Any
from datetime import timedelta

# Connect to existing zoe-redis container
redis_client = redis.Redis(
    host='zoe-redis',
    port=6379,
    decode_responses=True
)

def cache_set(key: str, value: Any, expire: int = 300):
    \"\"\"Set cache with expiration (default 5 min)\"\"\"
    redis_client.setex(
        key,
        timedelta(seconds=expire),
        json.dumps(value)
    )

def cache_get(key: str) -> Optional[Any]:
    \"\"\"Get from cache\"\"\"
    value = redis_client.get(key)
    return json.loads(value) if value else None

def cache_delete(key: str):
    \"\"\"Delete from cache\"\"\"
    redis_client.delete(key)

# Usage in endpoints:
from utils.cache import cache_get, cache_set

@router.get("/data")
async def get_data():
    # Try cache first
    cached = cache_get("my_data")
    if cached:
        return cached
    
    # Generate data
    data = {"timestamp": datetime.now()}
    
    # Cache it
    cache_set("my_data", data, expire=600)
    
    return data
```"""
        else:
            response = "Please specify what you want to create. Examples: 'Create an API endpoint', 'Build a cache system', 'Implement task scheduler'"
    
    elif "optimize" in message_lower or "improve" in message_lower:
        response = f"""**System Optimization Plan (Based on REAL Metrics):**

**Current Performance:**
- CPU: {metrics.get('cpu_percent', 0):.1f}% ({'✅ Excellent' if metrics.get('cpu_percent', 0) < 30 else '⚠️ Moderate'})
- Memory: {metrics.get('memory_percent', 0):.1f}% ({'✅ Good' if metrics.get('memory_percent', 0) < 70 else '⚠️ High'})
- Disk: {metrics.get('disk_percent', 0):.1f}% ({'✅ Fine' if metrics.get('disk_percent', 0) < 80 else '⚠️ Getting Full'})

**Practical Optimizations for Raspberry Pi:**

1. **Redis Caching** (You have zoe-redis!)
   - Cache API responses for 5-10 minutes
   - Reduces database queries by 70%
   - Implementation code provided above

2. **Response Compression**
   ```python
   from fastapi.middleware.gzip import GZipMiddleware
   app.add_middleware(GZipMiddleware, minimum_size=1000)
   ```

3. **Database Connection Pooling**
   ```python
   from sqlalchemy.pool import QueuePool
   engine = create_engine(
       "sqlite:////app/data/zoe.db",
       poolclass=QueuePool,
       pool_size=5
   )
   ```

4. **Log Rotation** (Save disk space)
   ```bash
   # Add to crontab
   0 0 * * * find /app/logs -name "*.log" -mtime +7 -delete
   ```

5. **Container Memory Limits**
   ```yaml
   # In docker-compose.yml
   mem_limit: 512m
   memswap_limit: 1g
   ```"""
    
    else:
        # Default: Full system analysis with REAL data
        response = f"""**System Analysis (REAL-TIME DATA):**

📊 **Performance Metrics:**
- CPU: {metrics.get('cpu_percent', 0):.1f}% ({metrics.get('cpu_cores', 0)} cores)
- Memory: {metrics.get('memory_used_gb', 0):.2f}GB / {metrics.get('memory_total_gb', 0):.2f}GB ({metrics.get('memory_percent', 0):.1f}%)
- Disk: {metrics.get('disk_free_gb', 0):.1f}GB free ({metrics.get('disk_percent', 0):.1f}% used)
- Containers: {metrics.get('containers_running', 0)} running / {metrics.get('containers_total', 0)} total

🎯 **Realistic Features for Raspberry Pi 5:**
1. **Redis Caching** - Use existing zoe-redis container
2. **WebSocket Chat** - Real-time without polling
3. **Automated Backups** - SQLite + config files
4. **API Rate Limiting** - Prevent overload
5. **Simple Voice Input** - Browser Speech API
6. **Task Scheduler** - Cron-like with APScheduler
7. **Log Dashboard** - View all container logs
8. **Export/Import** - Backup user data as JSON

💡 **Commands I understand:**
- "What's the memory usage?" → Real RAM stats
- "Show CPU status" → Usage and temperature
- "List Docker containers" → Running containers
- "Create an API endpoint" → Generate code
- "Optimize the system" → Practical improvements
- "Build a cache system" → Redis implementation

Health Score: {analysis.get('health_score', 100)}%"""
    
    return {
        "response": response,
        "system_state": analysis,
        "health_score": analysis.get("health_score", 100)
    }

@router.post("/tasks")
async def create_task(task: DeveloperTask):
    """Create a development task"""
    task_id = str(uuid.uuid4())
    developer_tasks[task_id] = {
        "id": task_id,
        "title": task.title,
        "description": task.description,
        "type": task.type,
        "priority": task.priority,
        "created_at": datetime.now().isoformat(),
        "status": "pending"
    }
    return {"task_id": task_id, "status": "created"}

@router.get("/tasks")
async def get_tasks():
    """Get all development tasks"""
    return {"tasks": list(developer_tasks.values())}

@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete a task"""
    if task_id in developer_tasks:
        del developer_tasks[task_id]
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Task not found")
