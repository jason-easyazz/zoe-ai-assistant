# Integration Patterns for Zoe Enhancement Systems

## Overview
This document defines the correct patterns for integrating with Zoe's enhancement systems and prevents bypassing established architectural decisions.

## Core Principles

### 1. User Privacy First
**All operations must be user-scoped**

✅ **CORRECT**:
```python
# All API calls include user_id
@router.get("/api/memories/search")
async def search_memories(
    query: str,
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    results = memory_system.search(query, user_id=user_id)
    return results
```

❌ **INCORRECT**:
```python
# Global search without user isolation
@router.get("/api/memories/search")
async def search_memories(query: str):
    results = memory_system.search(query)  # Violates privacy
    return results
```

### 2. Light RAG Integration
**Must use existing Light RAG APIs, not direct database access**

✅ **CORRECT**:
```python
# Use Light RAG memory system
from light_rag_memory import light_rag_memory

def add_memory(user_id: str, fact: str):
    return light_rag_memory.add_memory_with_embedding(
        entity_type="general",
        entity_id=0,
        fact=fact,
        user_id=user_id
    )
```

❌ **INCORRECT**:
```python
# Direct database manipulation bypasses Light RAG
import sqlite3

def add_memory(fact: str):
    conn = sqlite3.connect("/app/data/memory.db")
    cursor.execute("INSERT INTO memory_facts (fact) VALUES (?)", (fact,))
    conn.commit()  # Bypasses embedding generation and user isolation
```

### 3. Temporal Memory Integration
**Extend temporal system for time-based queries**

✅ **CORRECT**:
```python
from temporal_memory import temporal_memory

# Create episode for conversation context
episode = temporal_memory.create_episode(
    user_id=user_id,
    context_type="development",
    participants=[user_id, "assistant"]
)

# Add memory with temporal context
temporal_memory.add_temporal_memory(
    fact_id=memory_fact_id,
    episode_id=episode.id,
    temporal_context="Project planning session"
)
```

❌ **INCORRECT**:
```python
# Creating parallel temporal system
class MyTemporalSystem:
    def __init__(self):
        self.episodes = {}  # Bypasses established temporal system
```

### 4. Cross-Agent Orchestration
**Use orchestrator for complex multi-step tasks**

✅ **CORRECT**:
```python
from cross_agent_collaboration import orchestrator

# Complex task requiring multiple experts
result = await orchestrator.orchestrate_task(
    user_id=user_id,
    request="Schedule a meeting for tomorrow and add items to shopping list",
    context={"priority": "high"}
)
```

❌ **INCORRECT**:
```python
# Manual coordination without orchestrator
calendar_result = await calendar_expert.create_event(...)
list_result = await list_expert.add_items(...)
# No coordination, timeout handling, or result synthesis
```

### 5. User Satisfaction Integration
**Record interactions for satisfaction measurement**

✅ **CORRECT**:
```python
from user_satisfaction import satisfaction_system

# Record interaction for implicit analysis
satisfaction_system.record_interaction(
    interaction_id=interaction_id,
    user_id=user_id,
    request_text=user_message,
    response_text=assistant_response,
    response_time=response_duration
)
```

❌ **INCORRECT**:
```python
# Ignoring satisfaction measurement
def handle_request(user_message):
    response = generate_response(user_message)
    return response  # No satisfaction tracking
```

### 6. Context Cache Integration
**Use cache for expensive context operations**

✅ **CORRECT**:
```python
from context_cache import context_cache

# Check cache first
cached_summary = context_cache.get_cached_context(
    user_id=user_id,
    context_type=ContextType.MEMORY,
    context_data=memory_data
)

if not cached_summary:
    # Cache miss - generate and cache
    cache_id = context_cache.cache_context(
        user_id=user_id,
        context_type=ContextType.MEMORY,
        context_data=memory_data
    )
```

❌ **INCORRECT**:
```python
# Always fetching full context without caching
def get_user_context(user_id):
    memories = fetch_all_memories(user_id)  # Expensive operation
    calendar = fetch_all_events(user_id)    # Every time
    lists = fetch_all_lists(user_id)
    return combine_context(memories, calendar, lists)
```

## API Design Patterns

### Request/Response Models
**Use Pydantic models for type safety**

✅ **CORRECT**:
```python
class MemoryRequest(BaseModel):
    fact: str
    category: str = "general"
    importance: int = Field(ge=1, le=10, default=5)
    context: Optional[Dict[str, Any]] = None

@router.post("/api/memories")
async def add_memory(
    request: MemoryRequest,
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    # Type-safe, validated input
    pass
```

### Error Handling
**Consistent error responses**

✅ **CORRECT**:
```python
@router.get("/api/memories/{memory_id}")
async def get_memory(memory_id: int, user_id: str = Query(...)):
    try:
        memory = memory_system.get_memory(memory_id, user_id)
        if not memory:
            raise HTTPException(status_code=404, detail="Memory not found")
        return memory
    except Exception as e:
        logger.error(f"Failed to get memory {memory_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
```

### Async Operations
**Proper async/await usage**

✅ **CORRECT**:
```python
@router.post("/api/orchestration/orchestrate")
async def orchestrate_task(request: OrchestrationRequest, user_id: str = Query(...)):
    try:
        result = await orchestrator.orchestrate_task(
            user_id=user_id,
            request=request.request,
            context=request.context
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="Task orchestration timed out")
```

## Database Patterns

### User Isolation
**All queries must include user_id**

✅ **CORRECT**:
```sql
SELECT * FROM memory_facts mf
JOIN memory_temporal_metadata mtm ON mf.id = mtm.fact_id
JOIN conversation_episodes ce ON mtm.episode_id = ce.id
WHERE ce.user_id = ? AND mf.fact LIKE ?
```

❌ **INCORRECT**:
```sql
SELECT * FROM memory_facts WHERE fact LIKE ?
-- Missing user isolation
```

### Indexing Strategy
**Proper indexes for user-scoped queries**

✅ **CORRECT**:
```sql
CREATE INDEX idx_episodes_user_status ON conversation_episodes(user_id, status);
CREATE INDEX idx_feedback_user ON user_feedback(user_id);
CREATE INDEX idx_context_user_type ON context_summaries(user_id, context_type);
```

### Transaction Management
**Use transactions for multi-table operations**

✅ **CORRECT**:
```python
def create_episode_with_memory(user_id: str, context_type: str, initial_fact: str):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Start transaction
        cursor.execute("BEGIN")
        
        # Create episode
        cursor.execute("INSERT INTO conversation_episodes (...) VALUES (...)")
        episode_id = cursor.lastrowid
        
        # Add memory fact
        cursor.execute("INSERT INTO memory_facts (...) VALUES (...)")
        fact_id = cursor.lastrowid
        
        # Link temporal metadata
        cursor.execute("INSERT INTO memory_temporal_metadata (...) VALUES (...)")
        
        # Commit transaction
        conn.commit()
        return episode_id
        
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
```

## Testing Patterns

### Test Structure
**Comprehensive test coverage with scoring**

✅ **CORRECT**:
```python
class TestTemporalMemory:
    def test_episode_creation(self):
        """Test episode creation with proper assertions"""
        episode = temporal_memory.create_episode("test_user", "development")
        
        assert episode.user_id == "test_user"
        assert episode.context_type == "development"
        assert episode.status == EpisodeStatus.ACTIVE
        
    def test_user_isolation(self):
        """Test that users cannot access each other's data"""
        temporal_memory.create_episode("user1", "chat")
        temporal_memory.create_episode("user2", "chat")
        
        user1_episodes = temporal_memory.get_episode_history("user1")
        user2_episodes = temporal_memory.get_episode_history("user2")
        
        assert len(user1_episodes) == 1
        assert len(user2_episodes) == 1
        assert user1_episodes[0].user_id == "user1"
        assert user2_episodes[0].user_id == "user2"
```

### Performance Testing
**Benchmark critical operations**

✅ **CORRECT**:
```python
def test_search_performance(self):
    """Test that search operations meet performance targets"""
    import time
    
    start_time = time.time()
    results = light_rag_memory.light_rag_search("test query", limit=10)
    duration = time.time() - start_time
    
    assert duration < 0.1  # Should complete within 100ms
    assert len(results) >= 0
```

## Security Patterns

### Input Validation
**Validate all user inputs**

✅ **CORRECT**:
```python
@router.post("/api/satisfaction/feedback")
async def submit_feedback(
    feedback_request: FeedbackRequest,
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    if not 1 <= feedback_request.rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
    
    # Sanitize feedback text
    feedback_text = feedback_request.feedback_text
    if feedback_text and len(feedback_text) > 1000:
        raise HTTPException(status_code=400, detail="Feedback text too long")
```

### SQL Injection Prevention
**Use parameterized queries**

✅ **CORRECT**:
```python
cursor.execute("""
    SELECT * FROM memory_facts 
    WHERE user_id = ? AND fact LIKE ?
""", (user_id, f"%{query}%"))
```

❌ **INCORRECT**:
```python
cursor.execute(f"""
    SELECT * FROM memory_facts 
    WHERE user_id = '{user_id}' AND fact LIKE '%{query}%'
""")  # SQL injection vulnerability
```

## Performance Patterns

### Caching Strategy
**Cache expensive operations intelligently**

✅ **CORRECT**:
```python
def get_user_context(user_id: str, context_type: ContextType):
    # Check cache first
    cached = context_cache.get_cached_context(user_id, context_type, {})
    if cached:
        return cached.summary
    
    # Cache miss - fetch and cache
    start_time = time.time()
    context_data = fetch_context_data(user_id, context_type)
    fetch_time = (time.time() - start_time) * 1000
    
    # Only cache if fetch was expensive
    if context_cache._should_cache(context_type, fetch_time):
        context_cache.cache_context(user_id, context_type, context_data)
    
    return context_data
```

### Memory Management
**Clean up resources properly**

✅ **CORRECT**:
```python
def process_large_dataset(user_id: str):
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        
        # Process in chunks to avoid memory issues
        offset = 0
        chunk_size = 1000
        
        while True:
            cursor.execute("""
                SELECT * FROM memory_facts 
                WHERE user_id = ? 
                LIMIT ? OFFSET ?
            """, (user_id, chunk_size, offset))
            
            chunk = cursor.fetchall()
            if not chunk:
                break
                
            process_chunk(chunk)
            offset += chunk_size
            
    finally:
        conn.close()  # Always clean up
```

## Monitoring Patterns

### Health Checks
**Include all systems in health monitoring**

✅ **CORRECT**:
```python
@app.get("/health")
async def health_check():
    health_status = {
        "status": "healthy",
        "systems": {
            "temporal_memory": check_temporal_memory_health(),
            "orchestration": check_orchestration_health(),
            "satisfaction": check_satisfaction_health(),
            "context_cache": check_context_cache_health()
        }
    }
    
    # Overall status based on individual systems
    all_healthy = all(system["healthy"] for system in health_status["systems"].values())
    health_status["status"] = "healthy" if all_healthy else "degraded"
    
    return health_status
```

### Metrics Collection
**Track key performance indicators**

✅ **CORRECT**:
```python
def record_operation_metrics(operation: str, duration: float, success: bool, user_id: str):
    metrics = {
        "operation": operation,
        "duration_ms": duration * 1000,
        "success": success,
        "user_id": user_id,
        "timestamp": datetime.now().isoformat()
    }
    
    # Store metrics for analysis
    metrics_collector.record(metrics)
```

## Migration Patterns

### Schema Evolution
**Safe database migrations**

✅ **CORRECT**:
```python
def migrate_to_v2():
    """Add new columns safely"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if column already exists
        cursor.execute("PRAGMA table_info(memory_facts)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'embedding_vector' not in columns:
            cursor.execute("ALTER TABLE memory_facts ADD COLUMN embedding_vector BLOB")
            
        # Migrate existing data
        cursor.execute("SELECT id, fact FROM memory_facts WHERE embedding_vector IS NULL")
        for fact_id, fact_text in cursor.fetchall():
            embedding = generate_embedding(fact_text)
            cursor.execute("UPDATE memory_facts SET embedding_vector = ? WHERE id = ?", 
                         (embedding, fact_id))
        
        conn.commit()
        
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()
```

## Common Anti-Patterns to Avoid

### ❌ Bypassing User Isolation
```python
# DON'T: Global operations without user scoping
def get_all_memories():
    return memory_system.get_all()  # Violates privacy
```

### ❌ Direct Database Access
```python
# DON'T: Bypass established APIs
conn = sqlite3.connect(memory_db)
cursor.execute("SELECT * FROM memory_facts")  # Bypasses Light RAG
```

### ❌ Blocking Operations
```python
# DON'T: Block the event loop
def slow_operation():
    time.sleep(10)  # Blocks entire application
    return result
```

### ❌ Missing Error Handling
```python
# DON'T: Ignore potential failures
def risky_operation():
    result = external_api_call()  # Could fail
    return result.data  # Could crash
```

### ❌ Hardcoded Values
```python
# DON'T: Hardcode configuration
TIMEOUT = 30  # Should be configurable
API_KEY = "abc123"  # Should be in environment variables
```

## Conclusion

Following these integration patterns ensures:
- **Consistency** across all enhancement systems
- **Security** through proper user isolation and input validation
- **Performance** through intelligent caching and resource management
- **Maintainability** through clear architectural boundaries
- **Scalability** through proper database design and async operations

All new features must follow these patterns to maintain system integrity and user privacy.


