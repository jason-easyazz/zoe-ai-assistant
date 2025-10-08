# Zoe Enhancement Implementation Plan
## Comprehensive Testing & Optimization Strategy

**Status**: Ready for Implementation  
**Priority**: High  
**Timeline**: 6 weeks (3 phases)  
**Dependencies**: Current Zoe stack analysis complete

---

## Executive Summary

This plan implements the 4 enhancement ideas identified in the comprehensive review, with a focus on **comprehensive testing** of all core components and **systematic optimization** based on real-world prompt testing. The plan builds on Zoe's existing architecture while adding temporal memory, improved orchestration, learning capabilities, and performance optimization.

### Core Components to Test & Optimize
- **LiteLLM** → unified API + multi-model router
- **RouteLLM** → intelligent query classifier / router  
- **LightRAG** → contextual vector memory with relationship graphs
- **Mem Agent** → specialized experts for structured tasks
- **MCP Server** → standardized tool interface and orchestration layer

---

## Phase 1: Foundation & Testing Infrastructure (Weeks 1-2)

### 1.1 Developer Task System Updates

**Update Developer Roadmap** with enhancement phases:

```python
# Add to /api/developer/tasks/create
{
    "title": "Temporal Memory System Implementation",
    "objective": "Extend Light RAG with episode management and time-based queries",
    "requirements": [
        "Extend memory_facts table with episode_id column",
        "Create conversation_episodes table",
        "Implement episode auto-creation with context-aware timeouts",
        "Add temporal search queries with proper indexing",
        "Integrate with existing chat.py and memories.py",
        "Maintain backward compatibility with existing Light RAG APIs"
    ],
    "constraints": [
        "Cannot break existing Light RAG functionality",
        "Must maintain user isolation (user_id scoping)",
        "Must use existing embedding infrastructure",
        "Migration must have rollback capability"
    ],
    "acceptance_criteria": [
        "Episode creation works with 30min chat timeout, 120min dev timeout",
        "Temporal search returns results ordered by time relevance",
        "Auto-summarization generates coherent episode summaries",
        "All existing memory operations continue to work",
        "Test suite passes with 90%+ score"
    ],
    "priority": "high",
    "estimated_duration_hours": 16
}
```

### 1.2 Comprehensive Testing Framework

**Create Enhanced Test Suite** building on existing `test_intelligent_system.py`:

```python
class ZoeCoreComponentTester:
    """Comprehensive testing of all Zoe core components"""
    
    def __init__(self):
        self.base_url = "http://localhost:8000"
        self.services = {
            "zoe-core": "http://localhost:8000",
            "mcp-server": "http://localhost:8003", 
            "mem-agent": "http://localhost:11435",
            "litellm": "http://localhost:8001",
            "ollama": "http://localhost:11434"
        }
        
        # Test scenarios for each core component
        self.component_tests = {
            "litellm": {
                "model_routing": ["conversation", "action", "memory", "reasoning"],
                "performance": ["response_time", "model_selection", "fallback"],
                "quality": ["coherence", "accuracy", "relevance"]
            },
            "routellm": {
                "query_classification": ["intent_detection", "confidence_scoring"],
                "routing_accuracy": ["correct_model_selection", "fallback_handling"]
            },
            "lightrag": {
                "semantic_search": ["vector_search", "relationship_awareness"],
                "memory_management": ["storage", "retrieval", "context"],
                "temporal_features": ["episode_management", "time_queries"]
            },
            "mem_agent": {
                "expert_coordination": ["task_decomposition", "multi_expert"],
                "action_execution": ["list_management", "calendar", "planning"],
                "orchestration": ["timeout_handling", "rollback", "progress"]
            },
            "mcp_server": {
                "tool_discovery": ["tool_listing", "capability_detection"],
                "tool_execution": ["parameter_validation", "error_handling"],
                "orchestration": ["multi_tool", "dependency_resolution"]
            }
        }
```

### 1.3 Prompt Test Suite

**Create 10 Specific Test Cases** for system validation:

```python
class PromptTestSuite:
    """Real-world prompt testing for Zoe capabilities"""
    
    def __init__(self):
        self.test_prompts = [
            {
                "prompt": "Remind me to call Mum tomorrow at 5 PM.",
                "expected_components": ["calendar_expert", "mcp_server"],
                "expected_actions": ["create_calendar_event"],
                "success_criteria": ["event_created", "correct_time", "correct_title"],
                "test_category": "calendar_management"
            },
            {
                "prompt": "What's on my schedule for tomorrow?",
                "expected_components": ["calendar_expert", "lightrag"],
                "expected_actions": ["get_calendar_events"],
                "success_criteria": ["events_retrieved", "formatted_response"],
                "test_category": "calendar_query"
            },
            {
                "prompt": "Can you move my dentist appointment to Thursday afternoon?",
                "expected_components": ["calendar_expert", "lightrag"],
                "expected_actions": ["update_calendar_event"],
                "success_criteria": ["event_found", "time_updated", "confirmation"],
                "test_category": "calendar_modification"
            },
            {
                "prompt": "Summarize my week.",
                "expected_components": ["calendar_expert", "lightrag", "litellm"],
                "expected_actions": ["get_calendar_events", "generate_summary"],
                "success_criteria": ["events_retrieved", "coherent_summary"],
                "test_category": "intelligent_summary"
            },
            {
                "prompt": "What did we talk about the last time I mentioned the solar project?",
                "expected_components": ["memory_expert", "lightrag", "temporal_memory"],
                "expected_actions": ["temporal_search", "context_retrieval"],
                "success_criteria": ["relevant_memories", "temporal_accuracy"],
                "test_category": "temporal_memory"
            },
            {
                "prompt": "Add milk, spinach, and eggs to my shopping list.",
                "expected_components": ["list_expert", "mcp_server"],
                "expected_actions": ["add_to_list"],
                "success_criteria": ["items_added", "correct_list", "confirmation"],
                "test_category": "list_management"
            },
            {
                "prompt": "Turn off the living room lights.",
                "expected_components": ["mcp_server", "homeassistant_bridge"],
                "expected_actions": ["control_home_assistant_device"],
                "success_criteria": ["device_controlled", "confirmation"],
                "test_category": "home_automation"
            },
            {
                "prompt": "Plan a trip from Geraldton to Merimbula with daily stops.",
                "expected_components": ["planning_expert", "litellm"],
                "expected_actions": ["create_plan", "generate_itinerary"],
                "success_criteria": ["plan_created", "daily_stops", "route_info"],
                "test_category": "complex_planning"
            },
            {
                "prompt": "What's the name of the person I met at the café in Fremantle?",
                "expected_components": ["memory_expert", "lightrag"],
                "expected_actions": ["semantic_search", "entity_extraction"],
                "success_criteria": ["person_identified", "location_context"],
                "test_category": "memory_retrieval"
            },
            {
                "prompt": "List everything you know about my van setup.",
                "expected_components": ["memory_expert", "lightrag"],
                "expected_actions": ["contextual_search", "entity_aggregation"],
                "success_criteria": ["comprehensive_info", "organized_response"],
                "test_category": "knowledge_aggregation"
            }
        ]
```

---

## Phase 2: Core Enhancements Implementation (Weeks 3-4)

### 2.1 Temporal Memory System

**Implementation Priority**: HIGH - Foundation for other enhancements

```python
# Database Schema Extensions (Non-Breaking)
class TemporalMemoryExtension:
    def __init__(self, light_rag_system):
        self.light_rag = light_rag_system
        self.db_path = light_rag_system.db_path
    
    def extend_schema(self):
        """Extend existing Light RAG schema with temporal capabilities"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Add episode_id to existing memory_facts table
        cursor.execute("""
            ALTER TABLE memory_facts 
            ADD COLUMN episode_id INTEGER DEFAULT NULL
        """)
        
        # Create conversation episodes table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                session_id TEXT,
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                summary TEXT,
                context_type TEXT DEFAULT 'chat',
                timeout_minutes INTEGER DEFAULT 30,
                message_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create temporal search indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_user_time ON conversation_episodes(user_id, start_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_facts_episode ON memory_facts(episode_id)")
        
        conn.commit()
        conn.close()
    
    def create_episode(self, user_id: str, context_type: str = "chat") -> int:
        """Create new episode with context-aware timeout"""
        timeout_minutes = 30 if context_type == "chat" else 120
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO conversation_episodes 
            (user_id, context_type, timeout_minutes)
            VALUES (?, ?, ?)
        """, (user_id, context_type, timeout_minutes))
        
        episode_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return episode_id
    
    def temporal_search(self, query: str, user_id: str, time_range: str = "all") -> List[Dict]:
        """Search memories with temporal awareness"""
        # Extend existing Light RAG search with time filtering
        base_results = self.light_rag.light_rag_search(query, limit=20)
        
        # Filter by time range
        if time_range != "all":
            filtered_results = self._filter_by_time_range(base_results, time_range)
        else:
            filtered_results = base_results
        
        return filtered_results
```

### 2.2 LLM-Based Task Decomposition

**Replace regex patterns** in expert coordination:

```python
class LLMTaskAnalyzer:
    """Use LLM for proper task decomposition instead of regex"""
    
    def __init__(self, ollama_client):
        self.ollama = ollama_client
    
    async def analyze_task_intent(self, query: str) -> Dict:
        """Use LLM for sophisticated task analysis"""
        prompt = f"""
        Analyze this user request and decompose it into expert tasks:
        
        User Query: "{query}"
        
        Available Experts:
        - ListExpert: shopping lists, tasks, items
        - CalendarExpert: events, scheduling, reminders  
        - MemoryExpert: semantic search, memory retrieval
        - PlanningExpert: goal decomposition, task planning
        
        Return JSON with:
        {{
            "primary_expert": "expert_name",
            "supporting_experts": ["expert1", "expert2"],
            "task_decomposition": [
                {{"expert": "expert_name", "action": "specific_action", "parameters": {{}}}}
            ],
            "dependencies": [
                {{"task": "task1", "depends_on": "task2"}}
            ],
            "timeout_seconds": 30,
            "confidence": 0.85,
            "reasoning": "explanation"
        }}
        """
        
        response = await self.ollama.generate(prompt, model="llama3.2:1b")
        return json.loads(response)
    
    async def coordinate_experts(self, analysis: Dict, user_id: str) -> Dict:
        """Coordinate multiple experts with proper dependency handling"""
        results = []
        executed_tasks = set()
        
        # Execute tasks in dependency order
        for task in analysis["task_decomposition"]:
            if self._can_execute_task(task, executed_tasks, analysis["dependencies"]):
                expert_result = await self._execute_expert_task(task, user_id)
                results.append(expert_result)
                executed_tasks.add(task["action"])
        
        return {
            "results": results,
            "executed_tasks": list(executed_tasks),
            "success_rate": len([r for r in results if r["success"]]) / len(results)
        }
```

### 2.3 User Feedback System

**Foundation for learning system**:

```python
class UserFeedbackSystem:
    """Collect and process user feedback for system improvement"""
    
    def __init__(self, db_path="/app/data/feedback.db"):
        self.db_path = db_path
        self.init_feedback_db()
    
    def init_feedback_db(self):
        """Initialize feedback database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                response_text TEXT,
                rating INTEGER NOT NULL,  -- 1=good, 0=bad
                feedback_type TEXT DEFAULT 'explicit',
                context TEXT,  -- JSON with context info
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS satisfaction_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                date DATE NOT NULL,
                total_interactions INTEGER DEFAULT 0,
                positive_feedback INTEGER DEFAULT 0,
                negative_feedback INTEGER DEFAULT 0,
                satisfaction_score REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, date)
            )
        """)
        
        conn.commit()
        conn.close()
    
    async def collect_feedback(self, user_id: str, message_id: str, 
                             response_text: str, rating: int) -> Dict:
        """Collect explicit user feedback"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO user_feedback 
            (user_id, message_id, response_text, rating)
            VALUES (?, ?, ?, ?)
        """, (user_id, message_id, response_text, rating))
        
        # Update daily satisfaction metrics
        await self._update_satisfaction_metrics(user_id, rating)
        
        conn.commit()
        conn.close()
        
        return {"status": "feedback_collected", "rating": rating}
    
    async def get_satisfaction_score(self, user_id: str) -> float:
        """Get current user satisfaction score"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT AVG(satisfaction_score) 
            FROM satisfaction_metrics 
            WHERE user_id = ? AND date >= date('now', '-30 days')
        """, (user_id,))
        
        score = cursor.fetchone()[0] or 0.5
        conn.close()
        
        return score
```

---

## Phase 3: Advanced Features & Optimization (Weeks 5-6)

### 3.1 Cross-Agent Orchestration

**Enhanced expert coordination** with timeout handling and rollback:

```python
class AdvancedOrchestrationEngine:
    """Advanced orchestration with timeout handling and rollback"""
    
    def __init__(self):
        self.experts = {
            "list": ListExpert(),
            "calendar": CalendarExpert(), 
            "memory": MemoryExpert(),
            "planning": PlanningExpert()
        }
        self.max_timeout = 30  # seconds per expert
        self.rollback_strategies = {}
    
    async def orchestrate_complex_task(self, query: str, user_id: str) -> Dict:
        """Orchestrate complex multi-expert tasks"""
        # Analyze task using LLM
        analyzer = LLMTaskAnalyzer(self.ollama_client)
        analysis = await analyzer.analyze_task_intent(query)
        
        # Execute with timeout handling
        results = []
        rollback_actions = []
        
        for task in analysis["task_decomposition"]:
            try:
                # Execute with timeout
                result = await asyncio.wait_for(
                    self._execute_expert_task(task, user_id),
                    timeout=self.max_timeout
                )
                
                if result["success"]:
                    results.append(result)
                    # Store rollback action
                    if result.get("rollback_action"):
                        rollback_actions.append(result["rollback_action"])
                else:
                    # Execute rollback for failed task
                    await self._execute_rollback(rollback_actions)
                    return {"status": "failed", "error": "Task failed, rollback executed"}
                    
            except asyncio.TimeoutError:
                logger.error(f"Expert {task['expert']} timed out")
                await self._execute_rollback(rollback_actions)
                return {"status": "timeout", "error": "Expert timed out, rollback executed"}
        
        return {
            "status": "success",
            "results": results,
            "executed_tasks": len(results),
            "rollback_available": len(rollback_actions) > 0
        }
```

### 3.2 Performance Optimization

**Context cache** only if benchmarks prove need:

```python
class PerformanceOptimizer:
    """Optimize system performance based on benchmarks"""
    
    def __init__(self):
        self.benchmarks = {}
        self.optimization_thresholds = {
            "context_fetch": 0.1,  # 100ms
            "memory_search": 0.05,  # 50ms
            "expert_execution": 2.0,  # 2 seconds
            "llm_response": 8.0  # 8 seconds
        }
    
    async def benchmark_system(self) -> Dict:
        """Benchmark all core components"""
        benchmarks = {}
        
        # Benchmark context fetch
        start_time = time.time()
        context = await self._fetch_user_context("test_user")
        benchmarks["context_fetch"] = time.time() - start_time
        
        # Benchmark memory search
        start_time = time.time()
        memories = await self._search_memories("test query")
        benchmarks["memory_search"] = time.time() - start_time
        
        # Benchmark expert execution
        start_time = time.time()
        result = await self._execute_expert_task("test_task")
        benchmarks["expert_execution"] = time.time() - start_time
        
        return benchmarks
    
    async def optimize_if_needed(self, benchmarks: Dict) -> Dict:
        """Apply optimizations only if benchmarks exceed thresholds"""
        optimizations_applied = []
        
        if benchmarks["context_fetch"] > self.optimization_thresholds["context_fetch"]:
            # Implement context cache
            cache_system = SimpleContextCache()
            optimizations_applied.append("context_cache")
        
        if benchmarks["memory_search"] > self.optimization_thresholds["memory_search"]:
            # Optimize memory search
            await self._optimize_memory_search()
            optimizations_applied.append("memory_search_optimization")
        
        return {
            "optimizations_applied": optimizations_applied,
            "performance_improvement": self._calculate_improvement(benchmarks)
        }
```

---

## Testing & Validation Strategy

### Comprehensive Test Suite

**Build on existing testing infrastructure**:

```python
class ZoeEnhancementTestSuite:
    """Comprehensive testing of all enhancements"""
    
    def __init__(self):
        self.test_framework = {
            "temporal_memory": {
                "episode_creation": 0.2,
                "temporal_search": 0.3,
                "memory_decay": 0.2,
                "episode_summary": 0.3
            },
            "orchestration": {
                "task_decomposition": 0.3,
                "dependency_resolution": 0.2,
                "timeout_handling": 0.2,
                "rollback_coordination": 0.3
            },
            "learning": {
                "feedback_collection": 0.4,
                "preference_detection": 0.3,
                "adaptation_limits": 0.3
            },
            "performance": {
                "response_time": 0.4,
                "memory_usage": 0.3,
                "scalability": 0.3
            }
        }
    
    async def run_comprehensive_tests(self) -> Dict:
        """Run all enhancement tests"""
        results = {}
        
        # Test each enhancement area
        for enhancement, weights in self.test_framework.items():
            results[enhancement] = await self._test_enhancement(enhancement, weights)
        
        # Test prompt scenarios
        results["prompt_scenarios"] = await self._test_prompt_scenarios()
        
        # Calculate overall system health
        results["overall_health"] = self._calculate_system_health(results)
        
        return results
    
    async def _test_prompt_scenarios(self) -> Dict:
        """Test the 10 specific prompt scenarios"""
        prompt_tester = PromptTestSuite()
        results = {}
        
        for i, test_case in enumerate(prompt_tester.test_prompts):
            result = await prompt_tester.execute_test(test_case)
            results[f"prompt_{i+1}"] = {
                "prompt": test_case["prompt"],
                "success": result["success"],
                "response_time": result["response_time"],
                "components_used": result["components_used"],
                "score": result["score"]
            }
        
        return results
```

### Production Readiness Criteria

**Thresholds for production deployment**:

- **Temporal Memory**: 90%+ test score required
- **Orchestration**: 85%+ test score required  
- **Learning**: 80%+ test score required
- **Performance**: Only optimize if baseline > thresholds
- **Prompt Scenarios**: 8/10 scenarios must pass with 80%+ score

---

## Documentation Requirements

### Architecture Decision Records (ADRs)

**Document all architectural decisions**:

```markdown
# ADR-003: Temporal Memory Architecture

## Decision
Extend Light RAG with temporal capabilities while maintaining existing APIs

## Rationale
- Light RAG provides 0.022s search performance
- Vector embeddings and relationship awareness exist
- User isolation properly implemented
- Episode management enables time-based queries

## Constraints
- Must use existing Light RAG APIs
- Cannot bypass with direct DB access
- All memory operations go through memory_system.py
- Episode timeout must be context-aware

## Non-Negotiable
- User isolation (all queries scoped to user_id)
- No external vector DBs (ChromaDB/Weaviate not needed)
- Episode auto-summarization using existing LLM infrastructure
- Migration script with rollback capability
```

### Integration Patterns

**Document correct integration patterns**:

```markdown
# Pattern: Adding New Memory Features

✅ CORRECT:
- Extend memory_facts table with new columns
- Use Light RAG APIs for embedding generation
- Integrate via routers/memories.py
- Add tests to test_memory_system.py

❌ INCORRECT:
- Create parallel memory system
- Bypass Light RAG
- Direct database manipulation
- Skip user_id scoping
```

---

## Implementation Timeline

### Week 1-2: Foundation
- [ ] Update developer task system with enhancement tasks
- [ ] Create comprehensive testing framework
- [ ] Implement prompt test suite
- [ ] Document architecture decisions

### Week 3-4: Core Enhancements  
- [ ] Implement temporal memory system
- [ ] Replace regex with LLM task analysis
- [ ] Build user feedback system
- [ ] Add timeout handling to experts

### Week 5-6: Advanced Features
- [ ] Implement cross-agent orchestration
- [ ] Add rollback coordination
- [ ] Build performance optimization
- [ ] Complete comprehensive testing

### Week 7: Validation & Deployment
- [ ] Run full test suite
- [ ] Validate prompt scenarios
- [ ] Performance benchmarking
- [ ] Production deployment

---

## Success Metrics

### Technical Metrics
- **Test Coverage**: 95%+ for all enhancements
- **Response Time**: <8s for complex queries
- **Memory Search**: <50ms average
- **Expert Coordination**: 90%+ success rate

### User Experience Metrics
- **Prompt Success Rate**: 8/10 scenarios pass
- **User Satisfaction**: 80%+ feedback score
- **System Reliability**: 99.9% uptime
- **Feature Adoption**: 70%+ users use new features

### Business Metrics
- **Development Velocity**: 2x faster task completion
- **System Maintainability**: Reduced technical debt
- **User Engagement**: 50% increase in daily usage
- **Feature Completeness**: All roadmap items delivered

---

## Risk Mitigation

### Technical Risks
- **Breaking Changes**: Comprehensive testing and rollback plans
- **Performance Degradation**: Benchmarking before/after each change
- **Data Loss**: Backup and migration scripts with rollback
- **Integration Issues**: Incremental deployment with monitoring

### User Experience Risks
- **Feature Confusion**: Gradual rollout with user education
- **Performance Impact**: Load testing and optimization
- **Data Privacy**: Maintain existing user isolation
- **Backward Compatibility**: Preserve all existing functionality

---

## Conclusion

This implementation plan provides a comprehensive approach to enhancing Zoe's capabilities while maintaining system reliability and user experience. The focus on **thorough testing** and **systematic optimization** ensures that each enhancement delivers measurable value while preserving the existing architecture's strengths.

The plan builds on Zoe's existing infrastructure (Light RAG, expert system, testing framework) while adding the temporal memory, improved orchestration, learning capabilities, and performance optimization needed to achieve Samantha-level intelligence.

**Next Steps**: Begin Phase 1 implementation with developer task system updates and testing framework creation.
