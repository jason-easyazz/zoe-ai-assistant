"""
Context Summarization Cache System for Zoe
==========================================

Implements intelligent caching of pre-computed context summaries to speed up responses.
Includes benchmarking, LLM-based summarization, and smart invalidation.
"""

import sqlite3
import json
import hashlib
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import asyncio

logger = logging.getLogger(__name__)

class CacheStatus(Enum):
    VALID = "valid"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"

class ContextType(Enum):
    MEMORY = "memory"
    CALENDAR = "calendar"
    LISTS = "lists"
    PROJECTS = "projects"
    CONVERSATION = "conversation"
    USER_PREFERENCES = "user_preferences"

@dataclass
class ContextSummary:
    """Represents a cached context summary"""
    id: str
    user_id: str
    context_type: ContextType
    context_key: str  # Hash of the original context
    summary: str
    original_context: Dict[str, Any]
    relevance_score: float
    created_at: str
    expires_at: str
    access_count: int
    last_accessed: str
    status: CacheStatus
    model_used: str
    confidence_score: float

class ContextCacheSystem:
    """Intelligent context summarization and caching system"""
    
    def __init__(self, db_path: str = "/app/data/context_cache.db"):
        self.db_path = db_path
        self.default_ttl_hours = 24  # Default cache TTL
        self.max_cache_size = 1000  # Maximum number of cached summaries
        self.performance_threshold_ms = 100  # Only cache if context fetch > 100ms
        self.init_database()
        self.performance_metrics = {}
    
    def init_database(self):
        """Initialize context cache database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Context summaries table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS context_summaries (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                context_type TEXT NOT NULL,
                context_key TEXT NOT NULL,
                summary TEXT NOT NULL,
                original_context TEXT NOT NULL,  -- JSON
                relevance_score REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                access_count INTEGER DEFAULT 0,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'valid',
                model_used TEXT DEFAULT 'zoe_summarizer',
                confidence_score REAL DEFAULT 0.8
            )
        """)
        
        # Performance metrics table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS performance_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                context_type TEXT NOT NULL,
                operation TEXT NOT NULL,  -- 'fetch', 'summarize', 'cache_hit', 'cache_miss'
                duration_ms REAL NOT NULL,
                success BOOLEAN NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT,
                context_size INTEGER,
                cache_hit BOOLEAN DEFAULT FALSE
            )
        """)
        
        # Cache invalidation log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache_invalidations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                context_type TEXT NOT NULL,
                user_id TEXT,
                invalidation_reason TEXT NOT NULL,
                affected_keys TEXT,  -- JSON array
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_context_user_type ON context_summaries(user_id, context_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_context_key ON context_summaries(context_key)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_context_expires ON context_summaries(expires_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_performance_type ON performance_metrics(context_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_performance_timestamp ON performance_metrics(timestamp)")
        
        conn.commit()
        conn.close()
        logger.info("Context cache database initialized")
    
    def _generate_context_key(self, user_id: str, context_type: ContextType, 
                            context_data: Dict[str, Any]) -> str:
        """Generate a unique key for context data"""
        # Create a deterministic hash of the context
        context_str = json.dumps(context_data, sort_keys=True)
        key_data = f"{user_id}:{context_type.value}:{context_str}"
        return hashlib.sha256(key_data.encode()).hexdigest()
    
    def _should_cache(self, context_type: ContextType, fetch_time_ms: float) -> bool:
        """Determine if context should be cached based on performance"""
        return fetch_time_ms > self.performance_threshold_ms
    
    def _record_performance(self, context_type: ContextType, operation: str, 
                          duration_ms: float, success: bool, user_id: str = None,
                          context_size: int = 0, cache_hit: bool = False):
        """Record performance metrics"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO performance_metrics
                (context_type, operation, duration_ms, success, user_id, context_size, cache_hit)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (context_type.value, operation, duration_ms, success, user_id, context_size, cache_hit))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to record performance metrics: {e}")
    
    def _summarize_context_with_llm(self, context_data: Dict[str, Any], 
                                   context_type: ContextType) -> Tuple[str, float]:
        """Summarize context using LLM (simplified implementation)"""
        try:
            # In a real implementation, this would call the actual LLM service
            # For now, use a simple summarization approach
            
            if context_type == ContextType.MEMORY:
                return self._summarize_memory_context(context_data)
            elif context_type == ContextType.CALENDAR:
                return self._summarize_calendar_context(context_data)
            elif context_type == ContextType.LISTS:
                return self._summarize_lists_context(context_data)
            elif context_type == ContextType.CONVERSATION:
                return self._summarize_conversation_context(context_data)
            else:
                return self._summarize_generic_context(context_data)
                
        except Exception as e:
            logger.error(f"LLM summarization failed: {e}")
            return "Context summarization failed", 0.0
    
    def _summarize_memory_context(self, context_data: Dict[str, Any]) -> Tuple[str, float]:
        """Summarize memory context"""
        memories = context_data.get("memories", [])
        if not memories:
            return "No memories found", 0.0
        
        # Simple summarization
        summary_parts = [f"Found {len(memories)} memories:"]
        for i, memory in enumerate(memories[:5]):  # Top 5 memories
            summary_parts.append(f"- {memory.get('fact', 'Unknown memory')}")
        
        if len(memories) > 5:
            summary_parts.append(f"... and {len(memories) - 5} more memories")
        
        return "\n".join(summary_parts), 0.8
    
    def _summarize_calendar_context(self, context_data: Dict[str, Any]) -> Tuple[str, float]:
        """Summarize calendar context"""
        events = context_data.get("events", [])
        if not events:
            return "No calendar events found", 0.0
        
        # Group by date
        events_by_date = {}
        for event in events:
            date = event.get("start_date", "Unknown date")
            if date not in events_by_date:
                events_by_date[date] = []
            events_by_date[date].append(event)
        
        summary_parts = [f"Calendar summary ({len(events)} events):"]
        for date, date_events in sorted(events_by_date.items()):
            summary_parts.append(f"{date}: {len(date_events)} events")
            for event in date_events[:3]:  # Top 3 per date
                summary_parts.append(f"  - {event.get('title', 'Untitled')} at {event.get('start_time', 'Unknown time')}")
        
        return "\n".join(summary_parts), 0.9
    
    def _summarize_lists_context(self, context_data: Dict[str, Any]) -> Tuple[str, float]:
        """Summarize lists context"""
        lists = context_data.get("lists", [])
        if not lists:
            return "No lists found", 0.0
        
        summary_parts = [f"Lists summary ({len(lists)} lists):"]
        for list_item in lists:
            list_name = list_item.get("name", "Unnamed list")
            items = list_item.get("items", [])
            completed = len([item for item in items if item.get("status") == "completed"])
            total = len(items)
            summary_parts.append(f"- {list_name}: {completed}/{total} completed")
        
        return "\n".join(summary_parts), 0.8
    
    def _summarize_conversation_context(self, context_data: Dict[str, Any]) -> Tuple[str, float]:
        """Summarize conversation context"""
        messages = context_data.get("messages", [])
        if not messages:
            return "No conversation history", 0.0
        
        summary_parts = [f"Conversation summary ({len(messages)} messages):"]
        
        # Extract key topics
        topics = set()
        for message in messages[-10:]:  # Last 10 messages
            text = message.get("content", "").lower()
            if "calendar" in text:
                topics.add("calendar")
            if "task" in text or "todo" in text:
                topics.add("tasks")
            if "memory" in text or "remember" in text:
                topics.add("memory")
        
        if topics:
            summary_parts.append(f"Topics discussed: {', '.join(topics)}")
        
        # Recent messages
        recent_messages = messages[-3:]
        for msg in recent_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:100]  # First 100 chars
            summary_parts.append(f"{role}: {content}...")
        
        return "\n".join(summary_parts), 0.7
    
    def _summarize_generic_context(self, context_data: Dict[str, Any]) -> Tuple[str, float]:
        """Summarize generic context"""
        keys = list(context_data.keys())
        summary = f"Context contains {len(keys)} data points: {', '.join(keys[:5])}"
        if len(keys) > 5:
            summary += f" and {len(keys) - 5} more"
        return summary, 0.6
    
    def get_cached_context(self, user_id: str, context_type: ContextType,
                          context_data: Dict[str, Any]) -> Optional[ContextSummary]:
        """Get cached context summary if available and valid"""
        try:
            context_key = self._generate_context_key(user_id, context_type, context_data)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, user_id, context_type, context_key, summary, original_context,
                       relevance_score, created_at, expires_at, access_count, last_accessed,
                       status, model_used, confidence_score
                FROM context_summaries
                WHERE user_id = ? AND context_type = ? AND context_key = ? 
                AND status = 'valid' AND expires_at > datetime('now')
            """, (user_id, context_type.value, context_key))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return None
            
            # Update access count and last accessed
            self._update_access_stats(context_key)
            
            return ContextSummary(
                id=row[0],
                user_id=row[1],
                context_type=ContextType(row[2]),
                context_key=row[3],
                summary=row[4],
                original_context=json.loads(row[5]),
                relevance_score=row[6],
                created_at=row[7],
                expires_at=row[8],
                access_count=row[9],
                last_accessed=row[10],
                status=CacheStatus(row[11]),
                model_used=row[12],
                confidence_score=row[13]
            )
            
        except Exception as e:
            logger.error(f"Failed to get cached context: {e}")
            return None
    
    def cache_context(self, user_id: str, context_type: ContextType,
                     context_data: Dict[str, Any], ttl_hours: int = None) -> Optional[str]:
        """Cache a context summary"""
        try:
            start_time = time.time()
            
            # Generate context key
            context_key = self._generate_context_key(user_id, context_type, context_data)
            
            # Check if already cached
            existing = self.get_cached_context(user_id, context_type, context_data)
            if existing:
                return existing.id
            
            # Summarize context
            summary, confidence = self._summarize_context_with_llm(context_data, context_type)
            
            # Create cache entry
            cache_id = f"cache_{int(time.time())}_{hashlib.md5(context_key.encode()).hexdigest()[:8]}"
            ttl = ttl_hours or self.default_ttl_hours
            expires_at = datetime.now() + timedelta(hours=ttl)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO context_summaries
                (id, user_id, context_type, context_key, summary, original_context,
                 relevance_score, expires_at, model_used, confidence_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cache_id,
                user_id,
                context_type.value,
                context_key,
                summary,
                json.dumps(context_data),
                0.8,  # Default relevance score
                expires_at.isoformat(),
                "zoe_summarizer",
                confidence
            ))
            
            conn.commit()
            conn.close()
            
            # Record performance
            duration_ms = (time.time() - start_time) * 1000
            self._record_performance(
                context_type, "summarize", duration_ms, True, user_id, 
                len(json.dumps(context_data)), False
            )
            
            # Clean up old entries if cache is full
            self._cleanup_old_entries()
            
            logger.info(f"Cached context summary: {cache_id}")
            return cache_id
            
        except Exception as e:
            logger.error(f"Failed to cache context: {e}")
            return None
    
    def _update_access_stats(self, context_key: str):
        """Update access statistics for a cached context"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE context_summaries
                SET access_count = access_count + 1, last_accessed = datetime('now')
                WHERE context_key = ?
            """, (context_key,))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to update access stats: {e}")
    
    def _cleanup_old_entries(self):
        """Clean up old and expired cache entries"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Remove expired entries
            cursor.execute("""
                DELETE FROM context_summaries
                WHERE expires_at < datetime('now') OR status = 'invalidated'
            """)
            
            # If still too many entries, remove least recently used
            cursor.execute("SELECT COUNT(*) FROM context_summaries")
            count = cursor.fetchone()[0]
            
            if count > self.max_cache_size:
                excess = count - self.max_cache_size
                cursor.execute("""
                    DELETE FROM context_summaries
                    WHERE id IN (
                        SELECT id FROM context_summaries
                        ORDER BY last_accessed ASC, access_count ASC
                        LIMIT ?
                    )
                """, (excess,))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to cleanup old entries: {e}")
    
    def invalidate_context(self, user_id: str, context_type: ContextType, 
                          reason: str = "manual_invalidation"):
        """Invalidate cached context for a user and context type"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get affected keys
            cursor.execute("""
                SELECT context_key FROM context_summaries
                WHERE user_id = ? AND context_type = ? AND status = 'valid'
            """, (user_id, context_type.value))
            
            affected_keys = [row[0] for row in cursor.fetchall()]
            
            # Mark as invalidated
            cursor.execute("""
                UPDATE context_summaries
                SET status = 'invalidated'
                WHERE user_id = ? AND context_type = ?
            """, (user_id, context_type.value))
            
            # Log invalidation
            cursor.execute("""
                INSERT INTO cache_invalidations
                (context_type, user_id, invalidation_reason, affected_keys)
                VALUES (?, ?, ?, ?)
            """, (context_type.value, user_id, reason, json.dumps(affected_keys)))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Invalidated {len(affected_keys)} cache entries for {user_id}:{context_type.value}")
            
        except Exception as e:
            logger.error(f"Failed to invalidate context: {e}")
    
    def get_performance_metrics(self, context_type: ContextType = None, 
                               days: int = 7) -> Dict[str, Any]:
        """Get performance metrics for context operations"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Base query
            query = """
                SELECT operation, AVG(duration_ms) as avg_duration, 
                       COUNT(*) as count, SUM(CASE WHEN success THEN 1 ELSE 0 END) as success_count,
                       SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) as cache_hits
                FROM performance_metrics
                WHERE timestamp > datetime('now', '-{} days')
            """.format(days)
            
            params = []
            if context_type:
                query += " AND context_type = ?"
                params.append(context_type.value)
            
            query += " GROUP BY operation"
            
            cursor.execute(query, params)
            results = cursor.fetchall()
            
            metrics = {}
            for row in results:
                operation, avg_duration, count, success_count, cache_hits = row
                metrics[operation] = {
                    "average_duration_ms": avg_duration,
                    "total_operations": count,
                    "success_rate": (success_count / count * 100) if count > 0 else 0,
                    "cache_hit_rate": (cache_hits / count * 100) if count > 0 else 0
                }
            
            conn.close()
            return metrics
            
        except Exception as e:
            logger.error(f"Failed to get performance metrics: {e}")
            return {}
    
    def get_cache_stats(self, user_id: str = None) -> Dict[str, Any]:
        """Get cache statistics"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Base query
            query = """
                SELECT context_type, COUNT(*) as count, 
                       AVG(access_count) as avg_access,
                       SUM(CASE WHEN status = 'valid' THEN 1 ELSE 0 END) as valid_count
                FROM context_summaries
            """
            
            params = []
            if user_id:
                query += " WHERE user_id = ?"
                params.append(user_id)
            
            query += " GROUP BY context_type"
            
            cursor.execute(query, params)
            results = cursor.fetchall()
            
            stats = {}
            for row in results:
                context_type, count, avg_access, valid_count = row
                stats[context_type] = {
                    "total_entries": count,
                    "valid_entries": valid_count,
                    "average_access_count": avg_access or 0,
                    "hit_rate": (valid_count / count * 100) if count > 0 else 0
                }
            
            conn.close()
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get cache stats: {e}")
            return {}

# Global instance
context_cache = ContextCacheSystem()

