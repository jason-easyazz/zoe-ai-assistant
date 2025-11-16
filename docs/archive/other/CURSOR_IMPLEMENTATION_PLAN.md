# 🚀 Zoe AI Assistant - Integration & Optimization Implementation Plan

**Purpose**: Systematic implementation guide to close integration gaps and optimize Zoe
**Timeline**: 6 weeks, phased approach
**Priority**: Critical integration issues first, then optimization

---

## 📋 PHASE 1: CRITICAL INTEGRATION FIXES (Week 1-2)

### Task 1.1: Rewrite Chat Router for Enhancement System Integration

**File**: `services/zoe-core/routers/chat.py`

**Current Problem**:
- Chat router is a simple LLM passthrough
- Doesn't use temporal memory (exists at `/temporal_memory_integration.py`)
- Doesn't route to multi-expert orchestration (exists at `/cross_agent_collaboration.py`)
- 30% of queries timeout instead of being decomposed

**Required Changes**:

```python
# services/zoe-core/routers/chat.py
from fastapi import APIRouter, HTTPException, Depends
from temporal_memory_integration import TemporalMemory
from cross_agent_collaboration import MultiExpertOrchestrator
from enhanced_mem_agent_client import EnhancedMEMAgentClient
import asyncio

router = APIRouter()

# Initialize systems
temporal_memory = TemporalMemory()
orchestrator = MultiExpertOrchestrator()
mem_agent = EnhancedMEMAgentClient()

@router.post("/api/chat/enhanced")
async def enhanced_chat(request: ChatRequest, user_id: str = Depends(get_current_user)):
    """
    Enhanced chat with full integration of:
    - Temporal memory context
    - Multi-expert orchestration
    - Intent classification
    - Timeout handling with auto-decomposition
    """

    # Step 1: Load temporal context (last 5 interactions)
    temporal_context = await temporal_memory.get_recent_context(
        user_id=user_id,
        limit=5
    )

    # Step 2: Classify intent and determine if expert routing needed
    intent = await mem_agent.classify_intent(request.message)

    # Step 3: Route based on intent
    if intent.requires_action:
        # Route to appropriate expert
        result = await orchestrator.execute_with_experts(
            query=request.message,
            user_id=user_id,
            context=temporal_context,
            timeout=25.0  # Prevent timeouts
        )
    else:
        # Standard conversational response with context
        try:
            result = await asyncio.wait_for(
                _standard_chat_with_context(request.message, temporal_context),
                timeout=25.0
            )
        except asyncio.TimeoutError:
            # Auto-decompose on timeout
            result = await orchestrator.decompose_and_execute(
                query=request.message,
                user_id=user_id
            )

    # Step 4: Store in temporal memory
    await temporal_memory.store_interaction(
        user_id=user_id,
        query=request.message,
        response=result.response,
        metadata={
            "intent": intent.type,
            "experts_used": result.experts_used,
            "actions_taken": result.actions
        }
    )

    # Step 5: Return with rich metadata
    return {
        "response": result.response,
        "actions_taken": result.actions,
        "experts_consulted": result.experts_used,
        "context_used": len(temporal_context),
        "execution_time": result.execution_time
    }

async def _standard_chat_with_context(message: str, temporal_context: list):
    """Standard LLM chat with temporal context injected"""
    # Build context-aware prompt
    context_summary = "\n".join([
        f"- {ctx.timestamp}: User: {ctx.query} | Zoe: {ctx.response}"
        for ctx in temporal_context
    ])

    enhanced_prompt = f"""
Previous conversation context:
{context_summary}

Current user message: {message}

Respond naturally, referencing past context when relevant.
"""

    # Call LLM with enhanced prompt
    response = await ai_client.complete(enhanced_prompt)
    return response
```

**Acceptance Criteria**:
- [ ] Chat uses temporal memory for context
- [ ] Complex queries route to multi-expert orchestration
- [ ] No timeouts (25s max, auto-decompose if needed)
- [ ] All interactions stored in temporal memory
- [ ] Tests pass: `pytest tests/integration/test_chat_integration.py`

**Files to Modify**:
- `services/zoe-core/routers/chat.py` - Main rewrite
- `services/zoe-core/temporal_memory_integration.py` - Ensure integrated
- `services/zoe-core/cross_agent_collaboration.py` - Ensure integrated
- `services/zoe-core/enhanced_mem_agent_client.py` - Add intent classification

**Files to Create**:
- `tests/integration/test_chat_integration.py` - New integration tests

---

### Task 1.2: Add Intent Classification to MEM Agent

**File**: `services/zoe-core/enhanced_mem_agent_client.py`

**Required Addition**:

```python
# enhanced_mem_agent_client.py

class Intent:
    def __init__(self, type: str, confidence: float, requires_action: bool, target_expert: str = None):
        self.type = type
        self.confidence = confidence
        self.requires_action = requires_action
        self.target_expert = target_expert

class EnhancedMEMAgentClient:

    async def classify_intent(self, query: str) -> Intent:
        """
        Classify user intent and determine routing strategy

        Returns:
            Intent with type, confidence, and routing info
        """

        # Action-requiring patterns
        action_patterns = {
            "list_add": r"(add|put|include).*(to|on|in).*(list|shopping|todo)",
            "event_create": r"(create|add|schedule|make).*(event|appointment|meeting)",
            "reminder_set": r"(remind|notification|alert).*(me|us)",
            "journal_entry": r"(journal|diary|log).*(entry|today|yesterday)",
            "person_remember": r"(remember|save|store).*(person|friend|colleague|about)",
            "search_memory": r"(what|who|when|where).*(did|said|told|about)",
            "plan_task": r"(plan|organize|break down|help me with)",
        }

        # Check each pattern
        for intent_type, pattern in action_patterns.items():
            if re.search(pattern, query.lower()):
                expert_map = {
                    "list_add": "ListExpert",
                    "event_create": "CalendarExpert",
                    "reminder_set": "ReminderExpert",
                    "journal_entry": "JournalExpert",
                    "person_remember": "PersonExpert",
                    "search_memory": "MemoryExpert",
                    "plan_task": "PlanningExpert"
                }

                return Intent(
                    type=intent_type,
                    confidence=0.85,
                    requires_action=True,
                    target_expert=expert_map.get(intent_type)
                )

        # Default: conversational (no action needed)
        return Intent(
            type="conversational",
            confidence=1.0,
            requires_action=False,
            target_expert=None
        )
```

**Acceptance Criteria**:
- [ ] Intent classification working for all 7 expert types
- [ ] Confidence scores reasonable (0.7-1.0 range)
- [ ] Tests pass: `pytest tests/unit/test_intent_classification.py`

---

### Task 1.3: Add Timeout Handling with Auto-Decomposition

**File**: `services/zoe-core/cross_agent_collaboration.py`

**Required Addition**:

```python
# cross_agent_collaboration.py

class MultiExpertOrchestrator:

    async def decompose_and_execute(self, query: str, user_id: str) -> OrchestrationResult:
        """
        When a query times out, automatically decompose into subtasks
        and execute in parallel
        """

        # Use LLM to break down complex query
        decomposition_prompt = f"""
Break down this complex request into 2-4 simple subtasks:

User request: {query}

Output format (JSON):
[
  {{"task": "subtask 1 description", "expert": "ListExpert"}},
  {{"task": "subtask 2 description", "expert": "CalendarExpert"}}
]
"""

        subtasks = await self._llm_decompose(decomposition_prompt)

        # Execute subtasks in parallel
        results = await asyncio.gather(*[
            self.execute_with_expert(
                expert_name=subtask["expert"],
                query=subtask["task"],
                user_id=user_id
            )
            for subtask in subtasks
        ])

        # Synthesize results
        synthesis_prompt = f"""
User asked: {query}

We completed these subtasks:
{self._format_results(results)}

Provide a natural response summarizing what was done.
"""

        final_response = await self._llm_synthesize(synthesis_prompt)

        return OrchestrationResult(
            response=final_response,
            actions=[r.action for r in results],
            experts_used=[r.expert for r in results],
            execution_time=sum(r.time for r in results)
        )
```

**Acceptance Criteria**:
- [ ] Complex queries automatically decompose instead of timing out
- [ ] Subtasks execute in parallel for speed
- [ ] Results synthesized into coherent response
- [ ] Tests pass: `pytest tests/integration/test_timeout_handling.py`

---

## 📋 PHASE 2: ADD MISSING EXPERTS (Week 3)

### Task 2.1: Create JournalExpert

**File**: `services/zoe-mcp-server/experts/journal_expert.py` (NEW)

**Implementation**:

```python
# services/zoe-mcp-server/experts/journal_expert.py

import re
import httpx
from datetime import datetime
from experts.base_expert import BaseExpert

class JournalExpert(BaseExpert):
    """
    Expert for journal entry management

    Handles:
    - Creating journal entries
    - Mood tracking
    - Retrieving past entries
    """

    def __init__(self):
        super().__init__(name="JournalExpert")
        self.api_base = "http://zoe-core:8000/api/journal"

    def can_handle(self, query: str) -> bool:
        """Check if this expert should handle the query"""
        journal_patterns = [
            r"journal",
            r"diary",
            r"log (today|yesterday|my day)",
            r"how (am i|was i) feeling",
            r"my mood"
        ]
        return any(re.search(pattern, query.lower()) for pattern in journal_patterns)

    async def handle_request(self, query: str, user_id: str, context: dict = None) -> dict:
        """Handle journal-related requests"""

        # Determine intent
        if re.search(r"(create|add|log|write)", query.lower()):
            return await self._create_entry(query, user_id)
        elif re.search(r"(show|get|retrieve|what did i)", query.lower()):
            return await self._retrieve_entries(query, user_id)
        else:
            return await self._analyze_mood(query, user_id)

    async def _create_entry(self, query: str, user_id: str) -> dict:
        """Create a new journal entry"""

        # Extract content and mood from query using LLM
        extracted = await self._extract_journal_info(query)

        # Create entry via API
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/entries",
                json={
                    "user_id": user_id,
                    "content": extracted["content"],
                    "mood": extracted["mood"],
                    "mood_score": extracted["mood_score"],
                    "tags": extracted["tags"]
                }
            )

            if response.status_code == 200:
                return {
                    "success": True,
                    "message": f"Journal entry created with {extracted['mood']} mood",
                    "action": "journal_entry_created",
                    "entry_id": response.json()["id"]
                }
            else:
                return {"success": False, "error": response.text}

    async def _extract_journal_info(self, query: str) -> dict:
        """Use LLM to extract journal content and mood"""
        # Implementation: Call LLM to parse query
        # Returns: {"content": "...", "mood": "happy", "mood_score": 8, "tags": [...]}
        pass
```

**Acceptance Criteria**:
- [ ] JournalExpert created and registered
- [ ] Can create entries via natural language
- [ ] Can retrieve past entries
- [ ] Mood extraction working
- [ ] Tests pass: `pytest tests/unit/test_journal_expert.py`

**Files to Create**:
- `services/zoe-mcp-server/experts/journal_expert.py`
- `tests/unit/test_journal_expert.py`

---

### Task 2.2: Create ReminderExpert

**File**: `services/zoe-mcp-server/experts/reminder_expert.py` (NEW)

**Implementation Pattern**: Similar to JournalExpert but for reminders

**Handles**:
- "Remind me to X at Y time"
- "What reminders do I have?"
- "Snooze this reminder"
- Natural language time parsing

**Acceptance Criteria**:
- [ ] ReminderExpert created and registered
- [ ] Natural language time parsing working
- [ ] Can create/list/snooze reminders
- [ ] Tests pass: `pytest tests/unit/test_reminder_expert.py`

---

### Task 2.3: Create WeatherExpert

**File**: `services/zoe-mcp-server/experts/weather_expert.py` (NEW)

**Note**: You already have `services/zoe-core/routers/weather.py`

**Implementation**: Create expert that calls existing weather router

**Handles**:
- "What's the weather today?"
- "Will it rain tomorrow?"
- "Should I bring an umbrella?"
- Weather-based scheduling suggestions

**Acceptance Criteria**:
- [ ] WeatherExpert created and registered
- [ ] Integrates with existing weather router
- [ ] Context-aware suggestions (e.g., "It'll rain, maybe reschedule outdoor event")
- [ ] Tests pass: `pytest tests/unit/test_weather_expert.py`

---

### Task 2.4: Create HomeAssistantExpert

**File**: `services/zoe-mcp-server/experts/homeassistant_expert.py` (NEW)

**Note**: You have `homeassistant-mcp-bridge` service and router

**Implementation**: Expert that calls HomeAssistant bridge

**Handles**:
- "Turn on living room lights"
- "Set temperature to 72"
- "Is the garage door closed?"
- "Run bedtime routine"

**Acceptance Criteria**:
- [ ] HomeAssistantExpert created and registered
- [ ] Integrates with homeassistant-mcp-bridge
- [ ] Device control working
- [ ] Scene execution working
- [ ] Tests pass: `pytest tests/unit/test_homeassistant_expert.py`

---

## 📋 PHASE 3: DATABASE MIGRATION SYSTEM (Week 4)

### Task 3.1: Implement Alembic Migrations

**Setup**:

```bash
cd services/zoe-core
pip install alembic
alembic init migrations
```

**File**: `services/zoe-core/alembic.ini` (generated)

**File**: `services/zoe-core/migrations/env.py` (configure)

```python
# migrations/env.py

from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os

# Import your SQLAlchemy models
from database import Base  # Adjust import path

config = context.config

# Set database URL
database_path = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

target_metadata = Base.metadata

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

run_migrations_online()
```

**Create Initial Migration**:

```bash
# Generate migration from current schema
alembic revision --autogenerate -m "initial_schema_v2.4.0"

# Review generated migration in migrations/versions/
# Edit if needed

# Apply migration
alembic upgrade head
```

**Acceptance Criteria**:
- [ ] Alembic configured and working
- [ ] Initial migration captures current schema
- [ ] Can run `alembic upgrade head` safely
- [ ] Can run `alembic downgrade -1` to rollback
- [ ] Documentation updated: `docs/guides/database-migrations.md`

---

### Task 3.2: Create Upgrade Script for Existing Users

**File**: `scripts/maintenance/upgrade_database.sh` (NEW)

```bash
#!/bin/bash
# Upgrade existing Zoe database to latest schema version

set -e

echo "🔄 Zoe Database Upgrade Script"
echo "================================"

# Backup first
BACKUP_DIR="/app/data/backups"
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "📦 Creating backup..."
cp /app/data/zoe.db "$BACKUP_DIR/zoe.db.$TIMESTAMP"
cp /app/data/memory.db "$BACKUP_DIR/memory.db.$TIMESTAMP"
echo "✅ Backup created: $BACKUP_DIR/*.$TIMESTAMP"

# Run migrations
echo "🔄 Running database migrations..."
cd /app
alembic upgrade head

echo "✅ Database upgraded to latest version"
echo "🔍 Verifying database integrity..."

# Verify critical tables exist
python3 - <<EOF
import sqlite3
conn = sqlite3.connect('/app/data/zoe.db')
cursor = conn.cursor()

critical_tables = ['users', 'lists', 'calendar_events', 'journal_entries', 'developer_sessions', 'agent_memory']
for table in critical_tables:
    cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
    if not cursor.fetchone():
        raise Exception(f"Critical table missing: {table}")
    print(f"✅ {table}")

conn.close()
EOF

echo "✅ Database upgrade complete!"
echo "📝 Backup location: $BACKUP_DIR/zoe.db.$TIMESTAMP"
```

**Acceptance Criteria**:
- [ ] Script creates backup before upgrade
- [ ] Runs migrations safely
- [ ] Verifies critical tables exist
- [ ] Rollback instructions documented
- [ ] Tests on copy of production data

---

## 📋 PHASE 4: RESOURCE OPTIMIZATION (Week 5)

### Task 4.1: Audit and Reduce AI Models

**Investigation Script**:

```bash
#!/bin/bash
# scripts/maintenance/audit_ollama_models.sh

echo "🤖 Ollama Model Audit"
echo "====================="

# List all models with sizes
ollama list

# Calculate total storage
TOTAL_SIZE=$(docker exec zoe-ollama du -sh /root/.ollama/models | awk '{print $1}')
echo ""
echo "💾 Total storage used: $TOTAL_SIZE"

# Check model usage from logs (if available)
echo ""
echo "📊 Model usage analysis (placeholder - implement based on your logging):"
# Parse logs to see which models actually get used

echo ""
echo "💡 Recommendation:"
echo "Keep only top 3-5 most-used models"
echo "Others can be pulled on-demand when needed"
```

**Cleanup Script**:

```bash
#!/bin/bash
# scripts/maintenance/optimize_ollama_models.sh

# Keep only essential models
KEEP_MODELS=(
    "llama3.2:3b"      # Primary small model
    "deepseek-r1:14b"  # Reasoning tasks
    "qwen2.5:7b"       # Multilingual
)

echo "🗑️  Removing unused Ollama models..."

# Get all models
ALL_MODELS=$(ollama list | tail -n +2 | awk '{print $1}')

# Remove models not in keep list
for model in $ALL_MODELS; do
    if [[ ! " ${KEEP_MODELS[@]} " =~ " ${model} " ]]; then
        echo "Removing: $model"
        ollama rm "$model"
    fi
done

echo "✅ Optimization complete"
ollama list
```

**Acceptance Criteria**:
- [ ] Model usage analyzed
- [ ] Reduced to 3-5 most-used models
- [ ] Storage freed (target: reduce by 50%+)
- [ ] Chat still works with reduced model set
- [ ] Documentation updated with recommended models

---

### Task 4.2: Add Resource Limits to Docker Compose

**File**: `docker-compose.yml`

**Changes**:

```yaml
services:
  zoe-core:
    # ... existing config ...
    deploy:
      resources:
        limits:
          memory: 512M
        reservations:
          memory: 256M

  zoe-ollama:
    # ... existing config ...
    deploy:
      resources:
        limits:
          memory: 4G  # AI models need more
        reservations:
          memory: 2G

  zoe-litellm:
    # ... existing config ...
    deploy:
      resources:
        limits:
          memory: 256M
        reservations:
          memory: 128M

  # Add limits to all services...
```

**Acceptance Criteria**:
- [ ] All services have memory limits
- [ ] Limits are reasonable for Pi 5 (total < 6GB)
- [ ] Services restart if they exceed limits (restart: unless-stopped)
- [ ] Monitoring confirms no OOM kills
- [ ] Tests pass after adding limits

---

### Task 4.3: Consolidate Microservices (Optional)

**Evaluation**:

```bash
# Check if people-service and collections-service are heavily used
docker stats --no-stream people-service collections-service

# Check request counts (if metrics available)
curl http://localhost:8001/metrics 2>/dev/null | grep request_count
curl http://localhost:8005/metrics 2>/dev/null | grep request_count
```

**If Low Usage**: Consider merging into zoe-core

**Implementation** (if consolidating):

1. Move `services/people-service/` code to `services/zoe-core/routers/people.py`
2. Move `services/collections-service/` code to `services/zoe-core/routers/collections.py`
3. Remove separate services from docker-compose.yml
4. Update MCP server to call zoe-core instead of separate services

**Acceptance Criteria**:
- [ ] Services evaluated for usage
- [ ] Decision documented (consolidate or keep separate)
- [ ] If consolidated: all functionality preserved
- [ ] If consolidated: tests still pass
- [ ] Container count reduced (target: 17 → 12-14)

---

## 📋 PHASE 5: MONITORING & OBSERVABILITY (Week 5)

### Task 5.1: Deploy Grafana Dashboard

**File**: `docker-compose.yml` (add services)

```yaml
services:
  # ... existing services ...

  prometheus:
    image: prom/prometheus:latest
    container_name: zoe-prometheus
    restart: unless-stopped
    ports:
      - "9090:9090"
    volumes:
      - ./config/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
    networks:
      - zoe-network

  grafana:
    image: grafana/grafana:latest
    container_name: zoe-grafana
    restart: unless-stopped
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./config/grafana-dashboard.json:/etc/grafana/provisioning/dashboards/zoe.json
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_INSTALL_PLUGINS=
    networks:
      - zoe-network
    depends_on:
      - prometheus

volumes:
  prometheus_data:
  grafana_data:
```

**File**: `config/prometheus.yml` (already exists, verify)

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'zoe-core'
    static_configs:
      - targets: ['zoe-core:8000']
    metrics_path: '/metrics'

  - job_name: 'zoe-mcp-server'
    static_configs:
      - targets: ['zoe-mcp-server:8003']
    metrics_path: '/metrics'
```

**Acceptance Criteria**:
- [ ] Prometheus scraping metrics from all services
- [ ] Grafana dashboard showing key metrics
- [ ] Alerts configured for:
  - Memory usage > 80%
  - Chat timeout rate > 10%
  - Auth service unhealthy
  - Response time > 15s
- [ ] Documentation: `docs/guides/monitoring.md`

---

### Task 5.2: Add Structured Logging

**File**: `services/zoe-core/logging_config.py` (NEW)

```python
# logging_config.py

import logging
import json
from datetime import datetime

class JSONFormatter(logging.Formatter):
    """Format logs as JSON for easy parsing"""

    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields
        if hasattr(record, 'user_id'):
            log_data['user_id'] = record.user_id
        if hasattr(record, 'request_id'):
            log_data['request_id'] = record.request_id

        return json.dumps(log_data)

def configure_logging():
    """Configure structured logging for the application"""

    # Create handlers
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(JSONFormatter())

    file_handler = logging.FileHandler('/app/data/logs/zoe.log')
    file_handler.setFormatter(JSONFormatter())

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
```

**File**: `services/zoe-core/main.py` (update)

```python
# Add at top of main.py
from logging_config import configure_logging

# Call during startup
configure_logging()
```

**Acceptance Criteria**:
- [ ] All logs in JSON format
- [ ] Logs include timestamp, level, module, function
- [ ] Exceptions logged with full stack traces
- [ ] Request ID tracking working
- [ ] Log files rotating (add rotation config)

---

## 📋 PHASE 6: END-TO-END TESTING (Week 6)

### Task 6.1: Create Comprehensive E2E Test Suite

**File**: `tests/e2e/test_user_workflows.py` (NEW)

```python
# tests/e2e/test_user_workflows.py

import pytest
import httpx
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8000"

@pytest.mark.e2e
class TestCompleteUserWorkflows:
    """End-to-end tests for complete user journeys"""

    async def test_complete_assistant_workflow(self):
        """
        Test: User interacts naturally with Zoe across multiple features

        Workflow:
        1. User asks to create event
        2. Verify event created
        3. User asks about upcoming events
        4. Verify Zoe remembers and retrieves
        5. User adds items to shopping list
        6. User asks "what's on my list"
        7. User creates journal entry
        8. User asks "how was I feeling yesterday"
        """

        async with httpx.AsyncClient() as client:
            # Step 1: Create event via chat
            response = await client.post(
                f"{BASE_URL}/api/chat/enhanced",
                json={"message": "Create dentist appointment tomorrow at 2pm"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["actions_taken"]
            assert "CalendarExpert" in data["experts_consulted"]

            # Step 2: Verify event exists
            response = await client.get(f"{BASE_URL}/api/calendar/events")
            events = response.json()
            assert any("dentist" in e["title"].lower() for e in events)

            # Step 3: Ask about events (tests temporal memory)
            response = await client.post(
                f"{BASE_URL}/api/chat/enhanced",
                json={"message": "What's on my calendar?"}
            )
            assert "dentist" in response.json()["response"].lower()

            # Step 4: Add to shopping list
            response = await client.post(
                f"{BASE_URL}/api/chat/enhanced",
                json={"message": "Add bread and milk to shopping list"}
            )
            assert response.status_code == 200
            assert "ListExpert" in response.json()["experts_consulted"]

            # Step 5: Query list
            response = await client.post(
                f"{BASE_URL}/api/chat/enhanced",
                json={"message": "What's on my shopping list?"}
            )
            resp_text = response.json()["response"].lower()
            assert "bread" in resp_text
            assert "milk" in resp_text

            # Step 6: Journal entry
            response = await client.post(
                f"{BASE_URL}/api/chat/enhanced",
                json={"message": "Journal: Had a great day, feeling happy"}
            )
            assert "JournalExpert" in response.json()["experts_consulted"]

            # Step 7: Query past mood (temporal memory)
            response = await client.post(
                f"{BASE_URL}/api/chat/enhanced",
                json={"message": "How was I feeling earlier today?"}
            )
            assert "happy" in response.json()["response"].lower()

    async def test_temporal_memory_recall(self):
        """Test that Zoe remembers previous conversation"""

        async with httpx.AsyncClient() as client:
            # First interaction
            await client.post(
                f"{BASE_URL}/api/chat/enhanced",
                json={"message": "My favorite color is blue"}
            )

            # Second interaction (should remember)
            response = await client.post(
                f"{BASE_URL}/api/chat/enhanced",
                json={"message": "What's my favorite color?"}
            )

            assert "blue" in response.json()["response"].lower()

    async def test_complex_multi_expert_orchestration(self):
        """Test complex request requiring multiple experts"""

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{BASE_URL}/api/chat/enhanced",
                json={
                    "message": "Plan a dinner party next Friday, add wine to shopping list, and remind me to clean the house the day before"
                }
            )

            data = response.json()

            # Should involve multiple experts
            assert len(data["experts_consulted"]) >= 3
            assert "CalendarExpert" in data["experts_consulted"]  # dinner party event
            assert "ListExpert" in data["experts_consulted"]      # wine
            assert "ReminderExpert" in data["experts_consulted"]  # cleaning reminder

            # Should take multiple actions
            assert len(data["actions_taken"]) >= 3

            # Should complete in reasonable time (no timeout)
            assert data["execution_time"] < 25.0

    async def test_timeout_auto_decomposition(self):
        """Test that complex queries decompose instead of timing out"""

        # Create an intentionally complex query
        complex_query = """
        Create events for all my doctor appointments next month,
        add vitamins to my shopping list,
        journal about my health goals,
        and remind me to exercise three times a week
        """

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{BASE_URL}/api/chat/enhanced",
                json={"message": complex_query},
                timeout=30.0  # Allow 30s max
            )

            # Should succeed (not timeout)
            assert response.status_code == 200

            # Should have decomposed into subtasks
            data = response.json()
            assert len(data["actions_taken"]) >= 4
```

**Acceptance Criteria**:
- [ ] All E2E tests pass
- [ ] Tests cover critical user workflows
- [ ] Tests verify temporal memory working
- [ ] Tests verify multi-expert orchestration
- [ ] Tests verify no timeouts on complex queries
- [ ] Run daily in CI/CD

---

### Task 6.2: Create Performance Regression Tests

**File**: `tests/performance/test_performance_budgets.py` (update)

```python
# tests/performance/test_performance_budgets.py

import pytest
import httpx
import time

PERFORMANCE_BUDGETS = {
    "chat_simple": 3.0,      # Simple queries: 3s max
    "chat_complex": 15.0,    # Complex queries: 15s max
    "memory_search": 1.0,    # Memory search: 1s max
    "api_endpoint": 0.5,     # Standard APIs: 0.5s max
    "auth": 0.1,             # Auth: 0.1s max
}

@pytest.mark.performance
class TestPerformanceBudgets:
    """Enforce performance budgets - fail if exceeded"""

    async def test_chat_simple_performance(self):
        """Simple chat must complete in < 3s"""

        async with httpx.AsyncClient() as client:
            start = time.time()
            response = await client.post(
                "http://localhost:8000/api/chat/enhanced",
                json={"message": "Hello, how are you?"}
            )
            duration = time.time() - start

            assert response.status_code == 200
            assert duration < PERFORMANCE_BUDGETS["chat_simple"], \
                f"Chat took {duration:.2f}s (budget: {PERFORMANCE_BUDGETS['chat_simple']}s)"

    async def test_chat_complex_performance(self):
        """Complex multi-expert chat must complete in < 15s"""

        async with httpx.AsyncClient() as client:
            start = time.time()
            response = await client.post(
                "http://localhost:8000/api/chat/enhanced",
                json={"message": "Add bread to list, create event tomorrow at 3pm, remind me tonight"}
            )
            duration = time.time() - start

            assert response.status_code == 200
            assert duration < PERFORMANCE_BUDGETS["chat_complex"], \
                f"Complex chat took {duration:.2f}s (budget: {PERFORMANCE_BUDGETS['chat_complex']}s)"

    # Add more performance tests...
```

**Configure pytest to FAIL build if performance budgets exceeded**:

**File**: `pytest.ini`

```ini
[pytest]
markers =
    e2e: End-to-end tests
    performance: Performance tests

# Fail build if performance tests fail
addopts = --strict-markers -v
```

**Acceptance Criteria**:
- [ ] Performance budgets defined for all critical paths
- [ ] Tests fail if budgets exceeded
- [ ] CI/CD fails build on performance regression
- [ ] Results tracked over time

---

## 📋 PHASE 7: POLISH & DOCUMENTATION (Week 6)

### Task 7.1: Update Documentation

**Files to Update**:

1. `README.md` - Update status badges, remove "50% chat working" warnings
2. `PROJECT_STATUS.md` - Update to reflect integration complete
3. `CHANGELOG.md` - Add v2.5.0 with all integration improvements
4. `docs/guides/chat-integration.md` (NEW) - Document how chat integration works

**Key Additions**:

```markdown
# docs/guides/chat-integration.md

# Chat Integration Architecture

## How It Works

1. **User sends message** → Chat Router
2. **Chat Router** loads temporal context (last 5 interactions)
3. **Intent Classifier** determines if action needed
4. **If action needed** → Route to Multi-Expert Orchestrator
5. **Orchestrator** executes with appropriate expert(s)
6. **Result** stored in temporal memory
7. **Response** returned to user with action summary

## Experts Available

- **ListExpert**: Shopping lists, todos, tasks
- **CalendarExpert**: Events, scheduling
- **ReminderExpert**: Reminders, notifications
- **JournalExpert**: Journal entries, mood tracking
- **MemoryExpert**: Semantic search, knowledge retrieval
- **PlanningExpert**: Goal decomposition, planning
- **PersonExpert**: People management
- **WeatherExpert**: Weather queries
- **HomeAssistantExpert**: Smart home control

## Testing

Run integration tests:
```bash
pytest tests/e2e/test_user_workflows.py -v
```

## Troubleshooting

### Chat Timeouts
- Should not happen after v2.5.0
- If they do, check orchestrator logs
- Complex queries auto-decompose

### Expert Not Triggered
- Check intent classification patterns
- Add new patterns to `enhanced_mem_agent_client.py`
```

**Acceptance Criteria**:
- [ ] All documentation updated
- [ ] New integration guide created
- [ ] Troubleshooting section added
- [ ] Examples updated to show new capabilities

---

### Task 7.2: Update UI Feedback

**File**: `services/zoe-ui/dist/chat-v2.html` (or consolidate chat.html)

**Add Action Feedback to UI**:

```html
<!-- After chat message response -->
<div class="response-metadata" v-if="response.actions_taken">
    <div class="actions-summary">
        <strong>Actions Taken:</strong>
        <ul>
            <li v-for="action in response.actions_taken">{{ action }}</li>
        </ul>
    </div>
    <div class="experts-used">
        <strong>Experts:</strong> {{ response.experts_consulted.join(', ') }}
    </div>
</div>
```

**Acceptance Criteria**:
- [ ] UI shows which experts were consulted
- [ ] UI shows what actions were taken
- [ ] UI shows execution time
- [ ] UI has "undo last action" button (nice-to-have)

---

## 🎯 ACCEPTANCE CRITERIA FOR COMPLETE PROJECT

### Critical Must-Haves

- [ ] **Integration Complete**: Chat router uses temporal memory, orchestration, experts
- [ ] **No Timeouts**: 0% chat timeouts (down from 30%)
- [ ] **All Tests Pass**: 37/37 tests passing (up from 26/37)
- [ ] **E2E Tests**: Complete user workflows verified end-to-end
- [ ] **Database Migrations**: Safe upgrade path for existing users
- [ ] **Resource Optimized**: Total memory < 6GB, models reduced to 3-5
- [ ] **Monitoring**: Grafana dashboard showing key metrics
- [ ] **Documentation**: All guides updated, integration documented

### Success Metrics

**Before Implementation**:
- API Tests: 10/10 ✅
- Chat Tests: 5/10 ❌
- Timeout Rate: 30% ❌
- Integration: Partial ⚠️

**After Implementation**:
- API Tests: 10/10 ✅
- Chat Tests: 10/10 ✅
- Timeout Rate: 0% ✅
- Integration: Complete ✅

### Verification Commands

```bash
# Run all tests
pytest tests/ -v

# Check resource usage
docker stats --no-stream

# Verify integration
python3 tests/e2e/test_user_workflows.py

# Check database migrations
alembic current
alembic history

# Verify monitoring
curl http://localhost:9090/api/v1/targets  # Prometheus targets
curl http://localhost:3000  # Grafana dashboard
```

---

## 📅 TIMELINE SUMMARY

| Phase | Duration | Focus | Deliverables |
|-------|----------|-------|--------------|
| Phase 1 | Week 1-2 | Critical Integration | Chat router rewrite, intent classification, timeout handling |
| Phase 2 | Week 3 | Missing Experts | Journal, Reminder, Weather, HomeAssistant experts |
| Phase 3 | Week 4 | Database | Alembic migrations, upgrade scripts |
| Phase 4 | Week 5 | Optimization | Model reduction, resource limits, service consolidation |
| Phase 5 | Week 5 | Monitoring | Prometheus, Grafana, structured logging |
| Phase 6 | Week 6 | Testing | E2E tests, performance regression tests |
| Phase 7 | Week 6 | Polish | Documentation, UI feedback, final verification |

**Total: 6 weeks to production-ready integrated system**

---

## 🚀 GETTING STARTED

### For Cursor AI

**Recommended approach**:

1. **Start with Phase 1, Task 1.1** (Chat Router Integration)
   - This is the highest impact change
   - Read `services/zoe-core/routers/chat.py`
   - Read `temporal_memory_integration.py`
   - Read `cross_agent_collaboration.py`
   - Rewrite chat.py to integrate these systems

2. **Then Phase 1, Task 1.2** (Intent Classification)
   - Add to `enhanced_mem_agent_client.py`
   - Test with various user queries

3. **Then Phase 1, Task 1.3** (Timeout Handling)
   - Add to `cross_agent_collaboration.py`
   - Test with complex queries

4. **Continue in order through all phases**

### Testing As You Go

After each task:
```bash
# Run relevant tests
pytest tests/integration/test_chat_integration.py -v

# Manual verification
curl -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{"message": "Add bread to list and remind me to shop tomorrow"}'
```

---

**This plan closes all gaps identified in the comprehensive review.**
