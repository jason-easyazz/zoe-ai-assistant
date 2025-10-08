# Zoe Architecture Decision Records (ADRs)

**Status**: Active  
**Version**: 2.1  
**Last Updated**: 2025-01-04

---

## ADR-001: Memory System Architecture

### Decision
Use Light RAG with SQLite-backed vector storage as the foundation for all memory operations.

### Rationale
- **Privacy-first**: Local storage ensures user data remains private
- **Proven Performance**: 0.022s average search time with 100% embedding coverage
- **Relationship Awareness**: Built-in support for entity relationships and context
- **User Isolation**: Proper user_id scoping throughout the system
- **Extensibility**: Clean extension points for temporal and advanced features

### Constraints
- Must use existing Light RAG APIs for all memory operations
- Cannot bypass with direct database access
- All memory operations must go through `memory_system.py` and `light_rag_memory.py`
- User isolation is non-negotiable (all queries scoped to user_id)

### Non-Negotiable
- **User Isolation**: All queries must be scoped to user_id
- **No External Vector DBs**: ChromaDB, Weaviate, or similar not needed
- **API Consistency**: All memory operations use consistent Light RAG APIs
- **Backward Compatibility**: Existing memory operations must continue to work

### Integration Pattern
```python
# ✅ CORRECT: Use Light RAG APIs
from light_rag_memory import LightRAGMemorySystem

light_rag = LightRAGMemorySystem()
results = light_rag.light_rag_search(query, limit=10)

# ❌ INCORRECT: Direct database access
conn = sqlite3.connect("/app/data/memory.db")
cursor.execute("SELECT * FROM memory_facts WHERE...")
```

---

## ADR-002: Expert Coordination Architecture

### Decision
Replace regex-based task analysis with LLM-based decomposition for sophisticated expert coordination.

### Rationale
- **Intelligence**: LLM-based analysis handles complex, multi-step queries
- **Flexibility**: Adapts to new query patterns without code changes
- **Accuracy**: Better intent detection and task decomposition
- **Dependencies**: Proper handling of task dependencies and sequencing
- **Existing Infrastructure**: Leverages existing Ollama infrastructure

### Constraints
- Must use existing Ollama infrastructure for LLM analysis
- Cannot change expert class interfaces (ListExpert, CalendarExpert, etc.)
- Must maintain backward compatibility with existing expert functionality
- Response time must be under 5 seconds for task analysis
- Must handle LLM failures gracefully with fallback mechanisms

### Non-Negotiable
- **LLM-Based Analysis**: No regex patterns for task decomposition
- **Timeout Handling**: 30-second maximum per expert execution
- **Rollback Coordination**: Failed tasks must be properly rolled back
- **Real-Time Progress**: WebSocket/SSE updates for long-running tasks

### Integration Pattern
```python
# ✅ CORRECT: LLM-based task analysis
class LLMTaskAnalyzer:
    async def analyze_task_intent(self, query: str) -> Dict:
        prompt = f"Analyze this request: {query}..."
        response = await self.ollama.generate(prompt, model="llama3.2:1b")
        return json.loads(response)

# ❌ INCORRECT: Regex-based analysis
def can_handle(self, query: str) -> float:
    for pattern in self.intent_patterns:
        if re.search(pattern, query_lower):  # Too simplistic
            return 0.9
```

---

## ADR-003: Temporal Memory Architecture

### Decision
Extend Light RAG with temporal capabilities while maintaining existing APIs and functionality.

### Rationale
- **Foundation for Learning**: Temporal memory enables user preference learning
- **Time-Based Queries**: Support for "what did we discuss last Tuesday?" queries
- **Episode Management**: Context-aware conversation episodes with auto-summarization
- **Non-Breaking**: Extends existing system without breaking current functionality
- **Performance**: Leverages existing vector search and caching infrastructure

### Constraints
- Must extend existing Light RAG without breaking it
- Episode timeout must be context-aware (30min chat, 120min dev work)
- Auto-summarization must use existing LLM infrastructure
- Migration script must have rollback capability
- All existing memory operations must continue to work

### Non-Negotiable
- **User Isolation**: All temporal queries scoped to user_id
- **Backward Compatibility**: Existing memory operations unchanged
- **Auto-Summarization**: Episode summaries generated using existing LLM
- **Migration Safety**: Rollback capability for all database changes

### Integration Pattern
```python
# ✅ CORRECT: Extend existing Light RAG
class TemporalMemoryExtension(LightRAGMemorySystem):
    def create_episode(self, user_id: str, context_type: str = "chat") -> int:
        timeout_minutes = 30 if context_type == "chat" else 120
        # Implementation details...
    
    def temporal_search(self, query: str, user_id: str, time_range: str = "all"):
        base_results = self.light_rag_search(query, limit=20)
        return self._filter_by_time_range(base_results, time_range)

# ❌ INCORRECT: Parallel memory system
class TemporalMemorySystem:  # Don't create parallel system
    def __init__(self):
        self.db = sqlite3.connect("/app/data/temporal.db")
```

---

## ADR-004: User Feedback Architecture

### Decision
Implement explicit user feedback system as foundation for learning and adaptation capabilities.

### Rationale
- **Learning Foundation**: Required for all adaptive and learning features
- **User Satisfaction**: Measure and track user satisfaction over time
- **Privacy-Preserving**: Feedback stored with proper user isolation
- **Non-Intrusive**: Optional feedback collection that doesn't disrupt user experience
- **Analytics**: Provide insights for system improvement

### Constraints
- Must maintain user data isolation
- Feedback must be optional and non-intrusive
- Must comply with privacy requirements
- Database must be efficient for large-scale usage
- Must handle missing feedback gracefully without breaking chat

### Non-Negotiable
- **User Isolation**: All feedback scoped to user_id
- **Optional Collection**: Users can opt out of feedback collection
- **Privacy Compliance**: No personal data in feedback storage
- **Graceful Handling**: System works without feedback

### Integration Pattern
```python
# ✅ CORRECT: Integrated feedback collection
@router.post("/api/chat/feedback")
async def collect_feedback(
    message_id: str,
    rating: int,  # 1=good, 0=bad
    user_id: str = Query(...)
):
    feedback_system.collect_feedback(user_id, message_id, rating)

# ❌ INCORRECT: Separate feedback system
class FeedbackSystem:  # Don't create separate system
    def __init__(self):
        self.db = sqlite3.connect("/app/data/feedback.db")
```

---

## ADR-005: Performance Optimization Architecture

### Decision
Apply performance optimizations only when benchmarks prove the need, avoiding premature optimization.

### Rationale
- **Data-Driven**: Optimization decisions based on actual performance measurements
- **Avoid Premature Optimization**: Don't add complexity without proven benefit
- **Measurable Impact**: All optimizations must show measurable improvement
- **Maintainability**: Keep system simple and maintainable
- **Cost-Benefit**: Optimization effort must be justified by performance gain

### Constraints
- Must benchmark current performance before optimizing
- Optimization must show >30% improvement to be justified
- Must not add unnecessary complexity
- Must maintain existing functionality
- Must handle performance degradation gracefully

### Non-Negotiable
- **Benchmark First**: Measure before optimizing
- **Proven Need**: Only optimize if baseline exceeds thresholds
- **Measurable Improvement**: Must show quantifiable performance gain
- **No Premature Optimization**: Avoid adding complexity without benefit

### Integration Pattern
```python
# ✅ CORRECT: Benchmark-driven optimization
class PerformanceOptimizer:
    async def benchmark_system(self) -> Dict:
        benchmarks = {}
        start_time = time.time()
        context = await self._fetch_user_context("test_user")
        benchmarks["context_fetch"] = time.time() - start_time
        
        if benchmarks["context_fetch"] > 0.1:  # Only optimize if > 100ms
            return await self._implement_context_cache()
        return {"optimization": "not_needed"}

# ❌ INCORRECT: Premature optimization
class ContextCache:  # Don't add without benchmarking
    def __init__(self):
        self.cache = {}  # Adding complexity without proven need
```

---

## Integration Patterns

### Pattern: Adding New Memory Features

#### ✅ CORRECT Approach
```python
# 1. Extend existing Light RAG schema
ALTER TABLE memory_facts ADD COLUMN new_feature_column TEXT;

# 2. Use Light RAG APIs for embedding generation
light_rag = LightRAGMemorySystem()
embedding = light_rag.generate_embedding(text)

# 3. Integrate via routers/memories.py
@router.post("/api/memories/new-feature")
async def new_memory_feature():
    return light_rag.new_feature_method()

# 4. Add tests to test_memory_system.py
def test_new_memory_feature():
    result = light_rag.new_feature_method()
    assert result.success
```

#### ❌ INCORRECT Approach
```python
# 1. Creating parallel memory system
class NewMemorySystem:  # Don't create parallel system
    def __init__(self):
        self.db = sqlite3.connect("/app/data/new_memory.db")

# 2. Bypassing Light RAG
conn = sqlite3.connect("/app/data/memory.db")  # Direct DB access
cursor.execute("INSERT INTO memory_facts...")

# 3. Skipping user_id scoping
def search_memories(query):  # Missing user_id parameter
    return memory_system.search(query)
```

### Pattern: Adding New Expert Capabilities

#### ✅ CORRECT Approach
```python
# 1. Extend existing expert class
class EnhancedListExpert(ListExpert):
    def __init__(self):
        super().__init__()
        self.llm_analyzer = LLMTaskAnalyzer()
    
    async def analyze_complex_request(self, query: str):
        analysis = await self.llm_analyzer.analyze_task_intent(query)
        return await self.execute_analysis(analysis)

# 2. Maintain existing API interface
def can_handle(self, query: str) -> float:
    return await self.llm_analyzer.get_confidence(query)

# 3. Add timeout handling
async def execute(self, query: str, user_id: str):
    return await asyncio.wait_for(
        self._execute_with_timeout(query, user_id),
        timeout=30
    )
```

#### ❌ INCORRECT Approach
```python
# 1. Changing expert interface
class NewExpert:
    def new_method(self, query, user_id, timeout):  # Breaking change
        pass

# 2. No timeout handling
async def execute(self, query: str, user_id: str):
    return await self._long_running_operation()  # No timeout

# 3. Regex-based analysis
def can_handle(self, query: str) -> float:
    for pattern in self.regex_patterns:  # Too simplistic
        if re.search(pattern, query):
            return 0.9
```

---

## Performance Baselines

### Current Performance Metrics
```yaml
Memory Search: 0.022s average (Light RAG)
Context Fetch: [TO BE MEASURED]
Chat Response: [TO BE MEASURED]
Expert Execution: [TO BE MEASURED]
LLM Response: [TO BE MEASURED]
```

### Optimization Thresholds
```yaml
Context Fetch: > 100ms triggers optimization
Memory Search: > 50ms triggers optimization
Expert Execution: > 2 seconds triggers optimization
LLM Response: > 8 seconds triggers optimization
```

### Performance Requirements
- **Response Time**: < 8 seconds for complex queries
- **Memory Search**: < 50ms average
- **Expert Coordination**: 90%+ success rate
- **System Uptime**: 99.9% availability

---

## Testing Requirements

### Test Coverage Requirements
```yaml
New Feature Checklist:
- [ ] Unit tests (>80% coverage)
- [ ] Integration tests (all APIs)
- [ ] Performance tests (baseline + new)
- [ ] Privacy tests (user isolation)
- [ ] Rollback tests (migration safety)
- [ ] Score: >85% for production readiness
```

### Enhancement-Specific Requirements
```yaml
Temporal Memory: 90%+ test score required
Orchestration: 85%+ test score required
Learning: 80%+ test score required
Performance: Only optimize if baseline > thresholds
Prompt Scenarios: 8/10 scenarios must pass with 80%+ score
```

---

## Migration Guidelines

### Safe Migration Pattern
```python
# 1. Create migration script with rollback
def migrate_temporal_memory():
    """Migrate existing memories to include temporal capabilities"""
    try:
        # Add new columns
        cursor.execute("ALTER TABLE memory_facts ADD COLUMN episode_id INTEGER")
        
        # Create new tables
        cursor.execute("CREATE TABLE conversation_episodes...")
        
        # Migrate existing data
        migrate_existing_data()
        
        conn.commit()
        logger.info("Migration completed successfully")
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Migration failed: {e}")
        raise

# 2. Create rollback script
def rollback_temporal_memory():
    """Rollback temporal memory migration"""
    try:
        # Remove new columns
        cursor.execute("ALTER TABLE memory_facts DROP COLUMN episode_id")
        
        # Drop new tables
        cursor.execute("DROP TABLE conversation_episodes")
        
        conn.commit()
        logger.info("Rollback completed successfully")
        
    except Exception as e:
        logger.error(f"Rollback failed: {e}")
        raise
```

### Rollback Triggers
- Test suite failure
- Performance degradation > 20%
- User data corruption
- System instability

---

## Developer Guidelines

### Code Standards
- **User Isolation**: All operations must include user_id parameter
- **Error Handling**: Graceful error handling with proper logging
- **Performance**: Consider performance impact of all changes
- **Testing**: Write tests before implementing features
- **Documentation**: Document all public APIs and major decisions

### Anti-Patterns to Avoid
- **Direct Database Access**: Use APIs, not direct DB queries
- **Hardcoded Values**: Use configuration and environment variables
- **Missing User Scoping**: Always include user_id in queries
- **Premature Optimization**: Benchmark before optimizing
- **Breaking Changes**: Maintain backward compatibility

### Best Practices
- **Incremental Changes**: Small, focused changes with testing
- **Feature Flags**: Use flags for gradual rollout
- **Monitoring**: Add monitoring and alerting for new features
- **Documentation**: Keep documentation up-to-date
- **Code Review**: All changes require code review

---

## Conclusion

These ADRs provide the architectural foundation for Zoe's enhancement implementation. They ensure that all enhancements maintain the system's core principles of user privacy, performance, and reliability while adding sophisticated new capabilities.

**Key Principles**:
1. **User Privacy First**: All operations maintain user isolation
2. **Performance Matters**: Benchmark before optimizing
3. **Backward Compatibility**: Don't break existing functionality
4. **Data-Driven Decisions**: Use metrics to guide implementation
5. **Safety First**: Always have rollback plans

**Next Steps**: Use these ADRs to guide implementation of the enhancement tasks, ensuring all changes align with established architectural principles.
