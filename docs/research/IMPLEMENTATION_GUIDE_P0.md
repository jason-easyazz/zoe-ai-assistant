# Quick-Start Implementation Guide: P0 Recommendations
**Target:** Week 1-2 (High-Impact Quick Wins)  
**Expected Outcome:** 40-60% hallucination reduction + behavioral memory layer

---

## Overview: What We're Building

You're adding three strategic enhancements to your existing solid architecture:

1. **P0-2:** Context Validation (Day 1-2) - *Start here for quick win*
2. **P0-3:** Confidence Expression (Day 3) - *User-facing trust building*
3. **P0-1:** Behavioral Memory (Day 4-6) - *Foundation for "Samantha-like" memory*

---

## P0-2: Intent-Aware Context Validation (Days 1-2)

### Goal
Skip unnecessary context fetching for deterministic intents → 40-60% hallucination reduction

### Files to Create

**File 1:** `services/zoe-core/intent_system/validation/__init__.py`
```python
"""Intent validation module"""
from .context_validator import ContextValidator

__all__ = ["ContextValidator"]
```

**File 2:** `services/zoe-core/intent_system/validation/context_validator.py`
```python
"""
Context Validation for Intent System
=====================================
Determines whether context retrieval is needed before intent execution.
Prevents hallucinations on deterministic intents.
"""

import logging
from typing import List
from ..classifiers import ZoeIntent

logger = logging.getLogger(__name__)


class ContextValidator:
    """
    Validates whether context retrieval is needed before execution.
    Prevents hallucinations on deterministic intents.
    """
    
    # Intents that REQUIRE context retrieval
    RETRIEVAL_REQUIRED_INTENTS = [
        "TimeQuery", "WeatherQuery", "ListShow", "CalendarShow",
        "PeopleQuery", "ProjectQuery", "MemoryRecall"
    ]
    
    # Keywords indicating memory retrieval needed
    MEMORY_KEYWORDS = [
        "remember", "recall", "who is", "what did", "last time", 
        "when did", "tell me about", "what's my", "do I have"
    ]
    
    # Context types required by intent
    CONTEXT_MAPPING = {
        "ListAdd": ["lists"],
        "ListShow": ["lists"],
        "ListRemove": ["lists"],
        "ListComplete": ["lists"],
        "ListClear": ["lists"],
        "CalendarShow": ["calendar"],
        "CalendarAdd": ["calendar"],
        "CalendarRemove": ["calendar"],
        "PeopleQuery": ["people", "behavioral"],
        "ProjectQuery": ["projects"],
        "MemoryRecall": ["semantic_memory", "temporal_memory", "behavioral"],
    }
    
    @staticmethod
    def should_retrieve_context(intent: ZoeIntent, query: str) -> bool:
        """
        Determine if context retrieval is needed for this intent.
        
        Args:
            intent: Classified intent
            query: User query text
        
        Returns:
            True if memory/context search needed
            False if intent is deterministic (pattern-matched)
        """
        
        # Tier 0 intents (HassIL pattern-matched) don't need LLM context
        if intent.tier == 0:
            # Exception: ListShow needs to fetch list items
            if intent.name in ["ListShow", "CalendarShow"]:
                logger.info(f"[Validation] Tier 0 '{intent.name}' - REQUIRES data retrieval")
                return True
            
            logger.info(f"[Validation] Tier 0 intent '{intent.name}' - SKIP context retrieval (deterministic)")
            return False
        
        # Check if intent explicitly requires retrieval
        if intent.name in ContextValidator.RETRIEVAL_REQUIRED_INTENTS:
            logger.info(f"[Validation] Intent '{intent.name}' - REQUIRES context retrieval")
            return True
        
        # Check for memory keywords in query
        query_lower = query.lower()
        if any(keyword in query_lower for keyword in ContextValidator.MEMORY_KEYWORDS):
            logger.info(f"[Validation] Memory keyword detected - REQUIRES context retrieval")
            return True
        
        # Complex queries (Tier 2+) need full context
        if intent.tier >= 2:
            logger.info(f"[Validation] Tier {intent.tier} intent - REQUIRES context retrieval")
            return True
        
        # Default: simple Tier 1 actions don't need context
        logger.info(f"[Validation] Intent '{intent.name}' - SKIP context retrieval (deterministic)")
        return False
    
    @staticmethod
    def get_required_context_types(intent: ZoeIntent) -> List[str]:
        """
        Determine which context types are needed for this intent.
        Avoids fetching unnecessary data.
        
        Args:
            intent: Classified intent
        
        Returns:
            List of context types: ["calendar", "lists", "people", "behavioral"]
        """
        
        # Use predefined mapping
        if intent.name in ContextValidator.CONTEXT_MAPPING:
            context_types = ContextValidator.CONTEXT_MAPPING[intent.name]
            logger.info(f"[Validation] Intent '{intent.name}' needs: {context_types}")
            return context_types
        
        # Default: fetch all context types for unknown intents
        logger.info(f"[Validation] Intent '{intent.name}' needs: all context types")
        return ["calendar", "lists", "people", "behavioral", "semantic_memory"]
```

### Files to Update

**File:** `services/zoe-core/routers/chat.py`

**Changes:**
1. Import the validator
2. Add validation before context fetching
3. Skip context for deterministic intents
4. Execute Tier 0 intents directly

```python
# ADD IMPORT at top of file
from intent_system.validation import ContextValidator

# UPDATE chat endpoint (around line 800-900)
@router.post("/chat", response_model=ChatResponse)
async def chat_with_zoe(
    request: ChatMessage, 
    session: AuthenticatedSession = Depends(validate_session)
):
    start_time = time.time()
    user_id = session.user_id
    message = request.message
    
    # Classify intent
    intent = None
    if USE_INTENT_SYSTEM and intent_classifier:
        intent = intent_classifier.classify(message)
        if intent:
            logger.info(
                f"[Intent] Classified: {intent.name} "
                f"(tier={intent.tier}, confidence={intent.confidence:.2f})"
            )
    
    # ✅ NEW: Validate if context retrieval needed
    needs_context = True
    context_types = ["all"]
    
    if intent:
        needs_context = ContextValidator.should_retrieve_context(intent, message)
        if needs_context:
            context_types = ContextValidator.get_required_context_types(intent)
    
    # Fetch context only if needed
    context = {}
    if needs_context:
        context = await get_user_context_selective(user_id, message, context_types)
        logger.info(f"[Context] Fetched types: {context_types}")
    else:
        logger.info("[Context] SKIPPED - deterministic intent execution")
    
    # ✅ NEW: Execute Tier 0 intents directly (skip LLM)
    if intent and intent.tier == 0 and not needs_context:
        # Direct execution for deterministic intents
        execution_start = time.time()
        result = await intent_executor.execute(intent, user_id, context)
        execution_time = (time.time() - execution_start) * 1000
        
        if result.get("success"):
            return ChatResponse(
                response=result["message"],
                confidence=intent.confidence,
                intent=intent.name,
                execution_time_ms=execution_time,
                tier=intent.tier,
                sources=[]
            )
    
    # Otherwise, generate LLM response with context
    # ... (rest of existing code)

# ✅ NEW: Add selective context fetching function
async def get_user_context_selective(
    user_id: str, 
    query: str, 
    context_types: List[str]
) -> Dict:
    """
    Fetch only the required context types (performance optimization).
    
    Args:
        user_id: User identifier
        query: User query
        context_types: List of context types to fetch
    
    Returns:
        Dictionary with requested context data
    """
    context = {}
    
    # Fetch based on requested types
    if "calendar" in context_types or "all" in context_types:
        try:
            context["calendar_events"] = await fetch_calendar_events(user_id)
        except Exception as e:
            logger.warning(f"Calendar fetch failed: {e}")
    
    if "lists" in context_types or "all" in context_types:
        try:
            context["active_lists"] = await fetch_lists(user_id)
        except Exception as e:
            logger.warning(f"Lists fetch failed: {e}")
    
    if "people" in context_types or "all" in context_types:
        try:
            context["people"] = await fetch_people(user_id)
        except Exception as e:
            logger.warning(f"People fetch failed: {e}")
    
    if "semantic_memory" in context_types or "all" in context_types:
        try:
            memories = await search_memories(query, user_id)
            context["semantic_results"] = memories.get("semantic_results", [])
            context["temporal_results"] = memories.get("temporal_results", [])
        except Exception as e:
            logger.warning(f"Memory search failed: {e}")
    
    # Note: behavioral patterns will be added in P0-1
    
    return context
```

### Testing

**Test Case 1: Deterministic Intent (Should Skip Context)**
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "add milk to shopping list", "user_id": "test_user"}'

# Expected: Tier 0 execution, < 10ms, no context fetching in logs
```

**Test Case 2: Memory Query (Should Fetch Context)**
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "what did we talk about yesterday?", "user_id": "test_user"}'

# Expected: Context fetching logged, semantic + temporal memory searched
```

**Success Metrics:**
- ✅ Tier 0 intents execute in < 10ms
- ✅ Log shows "[Context] SKIPPED" for deterministic intents
- ✅ Log shows "[Context] Fetched types: ['lists']" for list operations
- ✅ Response quality maintained or improved

---

## P0-3: Confidence-Aware Uncertainty Expression (Day 3)

### Goal
Express uncertainty transparently based on confidence scores → builds user trust

### Files to Create

**File:** `services/zoe-core/intent_system/formatters/response_formatter.py`
```python
"""
Response Formatting with Confidence-Aware Uncertainty Expression
================================================================
Transparently expresses uncertainty based on confidence scores.
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


class ResponseFormatter:
    """
    Formats responses with confidence-aware uncertainty expression.
    Builds user trust by being honest about uncertainty.
    """
    
    CONFIDENCE_THRESHOLDS = {
        "high": 0.85,      # Very confident
        "medium": 0.70,    # Reasonably confident
        "low": 0.50,       # Uncertain
        "very_low": 0.30   # Very uncertain
    }
    
    @staticmethod
    def format_with_confidence(
        response_text: str, 
        confidence: float, 
        sources: Optional[List[str]] = None,
        uncertainty_reason: Optional[str] = None
    ) -> str:
        """
        Add confidence indicators to response.
        
        Args:
            response_text: Generated response
            confidence: Confidence score 0.0-1.0
            sources: List of sources used (memory IDs, context types)
            uncertainty_reason: Why confidence is low (optional)
        
        Returns:
            Formatted response with appropriate uncertainty language
        """
        
        # High confidence: Return as-is (maybe add sources)
        if confidence >= ResponseFormatter.CONFIDENCE_THRESHOLDS["high"]:
            if sources and len(sources) > 0:
                source_text = ", ".join(sources)
                return f"{response_text}\n\n_Based on: {source_text}_"
            return response_text
        
        # Medium confidence: Soft qualifier
        elif confidence >= ResponseFormatter.CONFIDENCE_THRESHOLDS["medium"]:
            prefix = "Based on what I know, "
            # Only add prefix if not already present
            if not response_text.lower().startswith(("based on", "according to", "from what")):
                response_text = f"{prefix}{response_text}"
            
            if sources and len(sources) > 0:
                source_text = ", ".join(sources)
                return f"{response_text}\n\n_Sources: {source_text}_"
            return response_text
        
        # Low confidence: Express uncertainty
        elif confidence >= ResponseFormatter.CONFIDENCE_THRESHOLDS["low"]:
            prefix = "I'm not entirely sure, but "
            if not response_text.lower().startswith(("i'm not", "i don't", "not sure")):
                response_text = f"{prefix}{response_text}"
            
            if uncertainty_reason:
                return f"{response_text}\n\n_(Uncertainty: {uncertainty_reason})_"
            return response_text
        
        # Very low confidence: Honest "I don't know"
        else:
            if uncertainty_reason:
                return f"I don't have reliable information about that. {uncertainty_reason}"
            elif not sources or len(sources) == 0:
                return "I don't have information about that in my memory. Could you provide more context, or would you like me to search more thoroughly?"
            else:
                return f"I found some potentially relevant information, but I'm not confident about the answer: {response_text}\n\n_Please verify this information._"
    
    @staticmethod
    def should_express_uncertainty(confidence: float, intent_tier: int) -> bool:
        """
        Determine if uncertainty should be expressed.
        
        Tier 0 (pattern-matched): Never express uncertainty (deterministic)
        Tier 1 (keywords): Express if confidence < 0.7
        Tier 2+ (LLM): Express if confidence < 0.7
        
        Args:
            confidence: Confidence score
            intent_tier: Intent classification tier
        
        Returns:
            True if uncertainty should be expressed
        """
        if intent_tier == 0:
            return False  # Deterministic, no uncertainty
        elif intent_tier <= 2:
            return confidence < ResponseFormatter.CONFIDENCE_THRESHOLDS["medium"]
        else:
            return confidence < ResponseFormatter.CONFIDENCE_THRESHOLDS["medium"]
    
    @staticmethod
    def get_confidence_level(confidence: float) -> str:
        """Get confidence level label"""
        if confidence >= ResponseFormatter.CONFIDENCE_THRESHOLDS["high"]:
            return "high"
        elif confidence >= ResponseFormatter.CONFIDENCE_THRESHOLDS["medium"]:
            return "medium"
        elif confidence >= ResponseFormatter.CONFIDENCE_THRESHOLDS["low"]:
            return "low"
        else:
            return "very_low"
```

### Files to Update

**File:** `services/zoe-core/routers/chat.py`

```python
# ADD IMPORT
from intent_system.formatters import ResponseFormatter

# UPDATE response generation (around existing LLM call)
async def generate_llm_response_with_confidence(
    message: str, 
    user_id: str, 
    context: Dict, 
    intent: Optional[ZoeIntent]
) -> ChatResponse:
    """Generate LLM response with confidence-aware formatting"""
    
    # Generate raw response
    raw_response = await llm_provider.generate(
        prompt=message,
        model=model,
        context=context,
        temperature=0.7
    )
    
    # Determine confidence
    if intent:
        confidence = intent.confidence
        tier = intent.tier
    else:
        confidence = 0.5  # Default for unclassified
        tier = 3
    
    # Collect sources for transparency
    sources = []
    if context.get("behavioral_patterns"):
        sources.append(f"{len(context['behavioral_patterns'])} behavioral patterns")
    if context.get("semantic_results"):
        sources.append(f"{len(context['semantic_results'])} memories")
    if context.get("temporal_results"):
        sources.append("recent conversations")
    if context.get("calendar_events"):
        sources.append("calendar")
    if context.get("active_lists"):
        sources.append("lists")
    
    # Check if uncertainty should be expressed
    should_express = ResponseFormatter.should_express_uncertainty(confidence, tier)
    
    # Format response with confidence
    if should_express:
        uncertainty_reason = None
        if not sources or len(sources) == 0:
            uncertainty_reason = "No relevant memories or context found"
        elif confidence < 0.5:
            uncertainty_reason = "Low confidence in classification"
        
        formatted_response = ResponseFormatter.format_with_confidence(
            raw_response, 
            confidence, 
            sources, 
            uncertainty_reason
        )
    else:
        # High confidence: add sources if available
        formatted_response = ResponseFormatter.format_with_confidence(
            raw_response,
            confidence,
            sources if sources else None
        )
    
    confidence_level = ResponseFormatter.get_confidence_level(confidence)
    
    return ChatResponse(
        response=formatted_response,
        confidence=confidence,
        confidence_level=confidence_level,
        intent=intent.name if intent else "Conversation",
        sources=sources,
        tier=tier
    )

# UPDATE main chat endpoint to use new function
@router.post("/chat", response_model=ChatResponse)
async def chat_with_zoe(...):
    # ... existing intent classification and context fetching ...
    
    # Generate response with confidence formatting
    response = await generate_llm_response_with_confidence(
        message, user_id, context, intent
    )
    
    return response
```

### Testing

**Test Case 1: High Confidence**
```python
# Input: "add milk to shopping list"
# Expected: "I've added milk to your shopping list! ✓"
# No uncertainty qualifier
```

**Test Case 2: Medium Confidence**
```python
# Input: "what's my next Arduino project?"
# Expected: "Based on what I know, you mentioned wanting to build an ESP32 temperature sensor."
# Sources: 3 memories, behavioral patterns
```

**Test Case 3: Low Confidence**
```python
# Input: "when did I last talk to Sarah?"
# Expected: "I'm not entirely sure, but I found a conversation mentioning Sarah about 2 weeks ago."
# Uncertainty: Limited conversation history
```

**Test Case 4: Very Low Confidence**
```python
# Input: "what's my favorite color?"
# Expected: "I don't have information about that in my memory. Could you tell me?"
```

**Success Metrics:**
- ✅ High confidence responses feel natural
- ✅ Medium confidence adds "Based on what I know"
- ✅ Low confidence expresses "I'm not entirely sure"
- ✅ Very low confidence says "I don't have information"
- ✅ Sources listed when available

---

## P0-1: Behavioral Memory Extraction (Days 4-6)

### Goal
Extract natural language behavioral patterns from conversations → "Samantha-like" memory

### Files to Create

**File:** `services/zoe-core/behavioral_memory.py`
```python
"""
Behavioral Memory System (Layer 1 Natural Language Memory)
===========================================================
Extracts human-readable behavioral patterns from conversations.
Runs as nightly batch job.
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import asyncio

from llm_provider import get_llm_provider
from temporal_memory import temporal_memory

logger = logging.getLogger(__name__)


class BehavioralMemoryExtractor:
    """
    Extract natural language behavioral summaries from L0 memory.
    Runs as nightly batch job.
    """
    
    def __init__(self, db_path: str = "/app/data/memory.db"):
        self.db_path = db_path
        self.llm_provider = get_llm_provider()
        self._init_database()
    
    def _init_database(self):
        """Create behavioral memory table"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS behavioral_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                pattern_type TEXT NOT NULL,  -- 'communication', 'timing', 'interest', 'task'
                pattern_text TEXT NOT NULL,
                confidence REAL DEFAULT 0.8,
                supporting_episodes TEXT,  -- JSON list of episode IDs
                supporting_facts TEXT,     -- JSON list of fact IDs
                first_observed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 0,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_behavioral_user 
            ON behavioral_memory(user_id, last_updated)
        """)
        
        conn.commit()
        conn.close()
        logger.info("Behavioral memory database initialized")
    
    async def extract_daily_patterns(self, user_id: str, days_back: int = 1) -> Dict:
        """
        Analyze recent conversations and extract behavioral insights.
        
        Args:
            user_id: User identifier
            days_back: How many days back to analyze
        
        Returns:
            Dict with extraction results
        """
        logger.info(f"Extracting behavioral patterns for user {user_id} (last {days_back} days)")
        
        # 1. Get recent temporal episodes
        episodes = temporal_memory.get_episode_history(user_id, limit=20)
        recent_episodes = [
            e for e in episodes 
            if datetime.fromisoformat(e.start_time) > datetime.now() - timedelta(days=days_back)
        ]
        
        if not recent_episodes:
            logger.info(f"No recent episodes found for user {user_id}")
            return {"patterns_extracted": 0, "reason": "No recent episodes"}
        
        # 2. Get recent Light RAG facts
        memories = await self._get_recent_memories(user_id, days_back)
        
        # 3. Extract patterns using LLM
        patterns = await self._extract_patterns(user_id, recent_episodes, memories)
        
        # 4. Store behavioral facts
        stored_count = await self._store_behavioral_facts(user_id, patterns, recent_episodes)
        
        logger.info(f"Extracted {stored_count} behavioral patterns for user {user_id}")
        
        return {
            "patterns_extracted": stored_count,
            "episodes_analyzed": len(recent_episodes),
            "memories_analyzed": len(memories),
            "timestamp": datetime.now().isoformat()
        }
    
    async def _get_recent_memories(self, user_id: str, days_back: int) -> List[Dict]:
        """Get recent Light RAG memories"""
        from light_rag_memory import light_rag
        
        cutoff = datetime.now() - timedelta(days=days_back)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, fact, category, importance, created_at
            FROM memory_facts
            WHERE entity_type = 'person' 
            AND created_at > ?
            ORDER BY importance DESC, created_at DESC
            LIMIT 50
        """, (cutoff.isoformat(),))
        
        memories = []
        for row in cursor.fetchall():
            memories.append({
                "id": row[0],
                "fact": row[1],
                "category": row[2],
                "importance": row[3],
                "created_at": row[4]
            })
        
        conn.close()
        return memories
    
    async def _extract_patterns(
        self, 
        user_id: str,
        episodes: List, 
        memories: List[Dict]
    ) -> List[Dict]:
        """Use LLM to identify behavioral patterns"""
        
        # Build context for LLM
        episode_summaries = []
        for ep in episodes[:10]:  # Top 10 recent episodes
            episode_summaries.append({
                "topics": ep.topics,
                "message_count": ep.message_count,
                "context_type": ep.context_type,
                "summary": ep.summary
            })
        
        memory_summaries = [
            f"- {m['fact']} (category: {m['category']}, importance: {m['importance']})"
            for m in memories[:20]  # Top 20 memories
        ]
        
        prompt = f"""
Analyze these conversations and identify behavioral patterns for personalization.

## Recent Conversation Episodes:
{json.dumps(episode_summaries, indent=2)}

## Recent Memories:
{chr(10).join(memory_summaries)}

## Task:
Extract 5-10 behavioral patterns in these categories:
1. **Communication Preferences**: Tone, detail level, formality, response style
2. **Timing Patterns**: When active, response urgency, time-of-day preferences
3. **Topic Interests**: Recurring themes, passions, areas of focus
4. **Task Preferences**: How they organize, prioritize, work style

## Format (JSON array):
[
  {{
    "type": "communication|timing|interest|task",
    "text": "Concise pattern statement (max 20 words)",
    "confidence": 0.7-1.0,
    "supporting_evidence": "Brief reason"
  }}
]

Be specific and actionable. Focus on patterns that help personalize responses.
"""
        
        try:
            # Use lightweight model (llama3.2:3b)
            result = await self.llm_provider.generate(
                prompt,
                model="zoe-chat",
                temperature=0.3,  # Low temp for factual extraction
                response_format="json"
            )
            
            # Parse patterns
            patterns = json.loads(result)
            
            # Validate format
            validated_patterns = []
            for p in patterns:
                if all(k in p for k in ["type", "text", "confidence"]):
                    validated_patterns.append(p)
            
            return validated_patterns
            
        except Exception as e:
            logger.error(f"Pattern extraction failed: {e}")
            return []
    
    async def _store_behavioral_facts(
        self, 
        user_id: str, 
        patterns: List[Dict],
        episodes: List
    ) -> int:
        """Store behavioral insights as L1 memory"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        episode_ids = [e.id for e in episodes]
        stored_count = 0
        
        try:
            for pattern in patterns:
                # Check if similar pattern already exists
                cursor.execute("""
                    SELECT id, confidence, supporting_episodes
                    FROM behavioral_memory
                    WHERE user_id = ? AND pattern_type = ? AND pattern_text = ?
                """, (user_id, pattern["type"], pattern["text"]))
                
                existing = cursor.fetchone()
                
                if existing:
                    # Update existing pattern
                    existing_id, existing_confidence, existing_episodes = existing
                    new_confidence = max(existing_confidence, pattern["confidence"])
                    
                    # Merge supporting episodes
                    existing_ep_ids = json.loads(existing_episodes) if existing_episodes else []
                    merged_episodes = list(set(existing_ep_ids + episode_ids))
                    
                    cursor.execute("""
                        UPDATE behavioral_memory
                        SET confidence = ?, supporting_episodes = ?, last_updated = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (new_confidence, json.dumps(merged_episodes), existing_id))
                    
                else:
                    # Insert new pattern
                    cursor.execute("""
                        INSERT INTO behavioral_memory 
                        (user_id, pattern_type, pattern_text, confidence, supporting_episodes)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        user_id, 
                        pattern["type"], 
                        pattern["text"], 
                        pattern["confidence"],
                        json.dumps(episode_ids)
                    ))
                
                stored_count += 1
            
            conn.commit()
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to store behavioral patterns: {e}")
        finally:
            conn.close()
        
        return stored_count
    
    async def get_active_patterns(
        self, 
        user_id: str, 
        limit: int = 5,
        pattern_type: Optional[str] = None
    ) -> List[Dict]:
        """Get active behavioral patterns for a user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            if pattern_type:
                cursor.execute("""
                    SELECT id, pattern_type, pattern_text, confidence, last_updated
                    FROM behavioral_memory
                    WHERE user_id = ? AND pattern_type = ?
                    ORDER BY confidence DESC, last_updated DESC
                    LIMIT ?
                """, (user_id, pattern_type, limit))
            else:
                cursor.execute("""
                    SELECT id, pattern_type, pattern_text, confidence, last_updated
                    FROM behavioral_memory
                    WHERE user_id = ?
                    ORDER BY confidence DESC, last_updated DESC
                    LIMIT ?
                """, (user_id, limit))
            
            patterns = []
            for row in cursor.fetchall():
                patterns.append({
                    "id": row[0],
                    "pattern_type": row[1],
                    "pattern_text": row[2],
                    "confidence": row[3],
                    "last_updated": row[4]
                })
            
            # Update access count
            for p in patterns:
                cursor.execute("""
                    UPDATE behavioral_memory 
                    SET access_count = access_count + 1, last_accessed = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (p["id"],))
            
            conn.commit()
            return patterns
            
        except Exception as e:
            logger.error(f"Failed to get behavioral patterns: {e}")
            return []
        finally:
            conn.close()


# Global instance
behavioral_memory = BehavioralMemoryExtractor()
```

### Files to Update

**File:** `services/zoe-core/routers/chat.py`

```python
# ADD IMPORT
from behavioral_memory import behavioral_memory

# UPDATE get_user_context_selective function
async def get_user_context_selective(
    user_id: str, 
    query: str, 
    context_types: List[str]
) -> Dict:
    """Fetch only the required context types"""
    context = {}
    
    # ... existing context fetching ...
    
    # ✅ NEW: Add behavioral patterns
    if "behavioral" in context_types or "all" in context_types:
        try:
            patterns = await behavioral_memory.get_active_patterns(user_id, limit=5)
            if patterns:
                context["behavioral_patterns"] = patterns
                logger.info(f"✅ Loaded {len(patterns)} behavioral patterns")
        except Exception as e:
            logger.warning(f"Behavioral memory unavailable: {e}")
    
    return context

# UPDATE system prompt building
def build_system_prompt(user_id: str, mode: str, context: Dict) -> str:
    """Build system prompt with behavioral insights"""
    
    if mode == "developer":
        system = DEVELOPER_SYSTEM_PROMPT
    else:
        system = """You are Zoe, a warm and intelligent AI assistant. 
You remember conversations, understand context, and personalize responses."""
    
    # ✅ NEW: Include behavioral patterns
    if context.get("behavioral_patterns"):
        system += "\n\n## User Behavioral Patterns:\n"
        for pattern in context["behavioral_patterns"]:
            system += f"- {pattern['pattern_text']}\n"
        system += "\nUse these insights to personalize your responses naturally.\n"
    
    # ... rest of existing prompt building ...
    
    return system
```

**File (NEW):** `services/zoe-core/cron_jobs/nightly_behavioral_extraction.py`
```python
"""
Nightly Behavioral Pattern Extraction
======================================
Runs at 3am to extract behavioral patterns from previous day's conversations.
"""

import asyncio
import logging
from datetime import datetime
import sqlite3

from behavioral_memory import behavioral_memory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def extract_patterns_for_all_users():
    """Extract behavioral patterns for all active users"""
    logger.info("Starting nightly behavioral pattern extraction")
    
    # Get all users with activity in last 7 days
    conn = sqlite3.connect("/app/data/memory.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT user_id
        FROM conversation_episodes
        WHERE start_time > datetime('now', '-7 days')
    """)
    
    active_users = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    logger.info(f"Found {len(active_users)} active users")
    
    # Extract patterns for each user
    results = []
    for user_id in active_users:
        try:
            result = await behavioral_memory.extract_daily_patterns(user_id, days_back=1)
            results.append({
                "user_id": user_id,
                "success": True,
                "patterns_extracted": result["patterns_extracted"]
            })
            logger.info(f"✅ User {user_id}: {result['patterns_extracted']} patterns")
        except Exception as e:
            logger.error(f"❌ User {user_id} failed: {e}")
            results.append({
                "user_id": user_id,
                "success": False,
                "error": str(e)
            })
    
    # Log summary
    success_count = sum(1 for r in results if r["success"])
    total_patterns = sum(r.get("patterns_extracted", 0) for r in results if r["success"])
    
    logger.info(f"""
Nightly extraction complete:
- Users processed: {len(active_users)}
- Successful: {success_count}
- Total patterns extracted: {total_patterns}
- Timestamp: {datetime.now().isoformat()}
    """)


if __name__ == "__main__":
    asyncio.run(extract_patterns_for_all_users())
```

**File (UPDATE):** `docker-compose.yml` - Add cron job
```yaml
services:
  zoe-core:
    # ... existing config ...
    volumes:
      - ./services/zoe-core/cron_jobs:/app/cron_jobs
    environment:
      - ENABLE_BEHAVIORAL_EXTRACTION=true

  # ✅ NEW: Cron service for nightly extraction
  zoe-cron:
    image: zoe-core:latest
    container_name: zoe-cron
    volumes:
      - ./services/zoe-core:/app
      - ./data:/app/data
    environment:
      - PYTHONPATH=/app
    command: >
      sh -c "
        echo '0 3 * * * cd /app && python3 cron_jobs/nightly_behavioral_extraction.py >> /app/data/logs/behavioral_extraction.log 2>&1' | crontab - &&
        crond -f -l 2
      "
    networks:
      - zoe-network
```

### Testing

**Manual Test: Extract Patterns**
```bash
# Run extraction manually for testing
docker exec -it zoe-core python3 -c "
import asyncio
from behavioral_memory import behavioral_memory

async def test():
    result = await behavioral_memory.extract_daily_patterns('test_user', days_back=7)
    print('Extraction result:', result)
    
    patterns = await behavioral_memory.get_active_patterns('test_user')
    print('\\nExtracted patterns:')
    for p in patterns:
        print(f\"  - [{p['pattern_type']}] {p['pattern_text']} (confidence: {p['confidence']:.2f})\")

asyncio.run(test())
"
```

**Expected Output:**
```
Extraction result: {'patterns_extracted': 6, 'episodes_analyzed': 12, ...}

Extracted patterns:
  - [communication] Prefers technical details over general explanations (confidence: 0.85)
  - [timing] Most active between 8pm-11pm (confidence: 0.80)
  - [interest] Focuses on Arduino and IoT projects (confidence: 0.90)
  - [task] Organizes work into lists and projects (confidence: 0.75)
  - [communication] Appreciates concise responses without fluff (confidence: 0.82)
  - [interest] Learning about smart home automation (confidence: 0.78)
```

**Integration Test: Use Patterns in Response**
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "tell me about my Arduino projects", "user_id": "test_user"}'

# Expected: Response uses behavioral patterns to personalize tone/detail level
```

**Success Metrics:**
- ✅ Nightly cron job runs successfully (check logs)
- ✅ 5-10 behavioral patterns extracted per active user
- ✅ Patterns appear in system prompts
- ✅ Responses feel more personalized after 7 days of extraction

---

## Verification & Monitoring

### Daily Checks (Week 1)

**Check 1: Context Validation Working**
```bash
grep "SKIP context retrieval" /app/data/logs/zoe-core.log | tail -10
# Should see logs for Tier 0 intents
```

**Check 2: Confidence Expression Active**
```bash
grep "confidence_level" /app/data/logs/zoe-core.log | tail -10
# Should see confidence levels logged with responses
```

**Check 3: Behavioral Extraction Running**
```bash
cat /app/data/logs/behavioral_extraction.log
# Should show nightly extraction results
```

### Week 1 Metrics

Track these metrics to measure P0 success:

| Metric | Baseline | Target | How to Measure |
|--------|----------|--------|----------------|
| Hallucination rate | ~15-20% | < 5% | Manual review of 100 responses |
| Tier 0 latency | <10ms | <10ms | Check logs for execution_time_ms |
| Confidence expression | 0% | 80% | Count responses with uncertainty qualifiers |
| Behavioral patterns | 0 | 5-10/user | Query behavioral_memory table |
| User satisfaction | Baseline | +20% | Feedback form ratings |

---

## Troubleshooting

### Issue: Context still being fetched for Tier 0 intents
**Solution:** Check that `ContextValidator.should_retrieve_context()` returns `False` for the intent. Log intent classification tier.

### Issue: Confidence always shows as 0.5
**Solution:** Ensure intent confidence is being passed through to response formatter. Check intent classification is working.

### Issue: Behavioral extraction returns 0 patterns
**Solution:** 
1. Check if temporal episodes exist for user
2. Verify Light RAG has memories
3. Check LLM response parsing (might be invalid JSON)
4. Lower temperature to 0.1 if getting creative responses

### Issue: Patterns not showing in system prompt
**Solution:** Verify `get_user_context_selective()` includes "behavioral" in context_types and patterns are being fetched.

---

## Next Steps After P0

Once P0 is stable (end of Week 2):

1. **Measure Impact:**
   - Hallucination rate reduction
   - User feedback on uncertainty expression
   - Quality of behavioral patterns

2. **Iterate:**
   - Adjust confidence thresholds based on user feedback
   - Refine behavioral pattern extraction prompts
   - Add more context validation rules

3. **Proceed to P1** (if P0 successful):
   - Temporal-aware similarity scoring
   - Platform-aware context budgets
   - Response grounding verification

---

## Support & Questions

If you encounter issues during implementation:

1. Check logs: `/app/data/logs/zoe-core.log`
2. Verify database schema: `sqlite3 /app/data/memory.db ".schema"`
3. Test components independently before integration
4. Use `logger.info()` extensively for debugging

**Good luck!** Start with P0-2 (context validation) Monday morning - it's the quickest win that proves the approach works. 🚀





