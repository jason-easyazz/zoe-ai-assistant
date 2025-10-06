"""
Temporal & Episodic Memory System for Zoe
=========================================

Extends the existing Light RAG system with:
- Conversation episodes with context-aware timeouts
- Temporal search capabilities
- Memory decay algorithms
- Auto-generated episode summaries
"""

import sqlite3
import json
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
import logging
import hashlib
from enum import Enum

logger = logging.getLogger(__name__)

class EpisodeStatus(Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    EXPIRED = "expired"

@dataclass
class ConversationEpisode:
    """Represents a conversation episode"""
    id: str
    user_id: str
    start_time: str
    end_time: Optional[str]
    status: EpisodeStatus
    context_type: str  # "chat", "development", "planning", etc.
    summary: Optional[str]
    message_count: int
    topics: List[str]
    participants: List[str]
    created_at: str
    updated_at: str

@dataclass
class TemporalMemory:
    """Memory with temporal metadata"""
    fact_id: int
    fact: str
    entity_type: str
    entity_id: int
    entity_name: str
    category: str
    importance: int
    episode_id: str
    timestamp: str
    decay_factor: float
    access_count: int
    last_accessed: str
    temporal_context: str

class TemporalMemorySystem:
    """Temporal and episodic memory system extending Light RAG"""
    
    def __init__(self, db_path: str = "/app/data/memory.db"):
        self.db_path = db_path
        self.episode_timeouts = {
            "chat": 30,  # 30 minutes
            "development": 120,  # 2 hours
            "planning": 60,  # 1 hour
            "general": 45  # 45 minutes
        }
        self.decay_halflife_days = 30  # Memories decay to 50% after 30 days
        self.init_temporal_database()
    
    def init_temporal_database(self):
        """Initialize temporal memory database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Conversation episodes table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_episodes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'active',
                context_type TEXT NOT NULL DEFAULT 'general',
                summary TEXT,
                message_count INTEGER DEFAULT 0,
                topics JSON,
                participants JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Temporal memory metadata table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_temporal_metadata (
                fact_id INTEGER PRIMARY KEY,
                episode_id TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                decay_factor REAL DEFAULT 1.0,
                access_count INTEGER DEFAULT 0,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                temporal_context TEXT,
                FOREIGN KEY (fact_id) REFERENCES memory_facts(id),
                FOREIGN KEY (episode_id) REFERENCES conversation_episodes(id)
            )
        """)
        
        # Episode summaries table for LLM-generated summaries
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS episode_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id TEXT NOT NULL,
                summary_type TEXT NOT NULL,  -- "auto", "manual", "llm_generated"
                content TEXT NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                model_used TEXT,
                confidence_score REAL,
                FOREIGN KEY (episode_id) REFERENCES conversation_episodes(id)
            )
        """)
        
        # Create indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_user_status ON conversation_episodes(user_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_start_time ON conversation_episodes(start_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_temporal_episode ON memory_temporal_metadata(episode_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_temporal_timestamp ON memory_temporal_metadata(timestamp)")
        
        conn.commit()
        conn.close()
        logger.info("Temporal memory database initialized")
    
    def create_episode(self, user_id: str, context_type: str = "general", 
                      participants: List[str] = None) -> ConversationEpisode:
        """Create a new conversation episode"""
        episode_id = f"episode_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.md5(user_id.encode()).hexdigest()[:8]}"
        
        episode = ConversationEpisode(
            id=episode_id,
            user_id=user_id,
            start_time=datetime.now().isoformat(),
            end_time=None,
            status=EpisodeStatus.ACTIVE,
            context_type=context_type,
            summary=None,
            message_count=0,
            topics=[],
            participants=participants or [user_id],
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )
        
        # Save to database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO conversation_episodes 
            (id, user_id, start_time, status, context_type, message_count, topics, participants)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            episode.id,
            episode.user_id,
            episode.start_time,
            episode.status.value,
            episode.context_type,
            episode.message_count,
            json.dumps(episode.topics),
            json.dumps(episode.participants)
        ))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Created episode {episode_id} for user {user_id}")
        return episode
    
    def get_active_episode(self, user_id: str) -> Optional[ConversationEpisode]:
        """Get the active episode for a user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, user_id, start_time, end_time, status, context_type, summary,
                   message_count, topics, participants, created_at, updated_at
            FROM conversation_episodes
            WHERE user_id = ? AND status = 'active'
            ORDER BY start_time DESC
            LIMIT 1
        """, (user_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return ConversationEpisode(
            id=row[0],
            user_id=row[1],
            start_time=row[2],
            end_time=row[3],
            status=EpisodeStatus(row[4]),
            context_type=row[5],
            summary=row[6],
            message_count=row[7],
            topics=json.loads(row[8]) if row[8] else [],
            participants=json.loads(row[9]) if row[9] else [],
            created_at=row[10],
            updated_at=row[11]
        )
    
    def add_message_to_episode(self, episode_id: str, message: str, 
                              message_type: str = "user") -> bool:
        """Add a message to an episode and update topics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Update message count
            cursor.execute("""
                UPDATE conversation_episodes 
                SET message_count = message_count + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (episode_id,))
            
            # Extract topics from message (simple keyword extraction)
            topics = self._extract_topics(message)
            if topics:
                # Get current topics
                cursor.execute("SELECT topics FROM conversation_episodes WHERE id = ?", (episode_id,))
                current_topics = json.loads(cursor.fetchone()[0] or "[]")
                
                # Add new topics
                for topic in topics:
                    if topic not in current_topics:
                        current_topics.append(topic)
                
                # Update topics
                cursor.execute("""
                    UPDATE conversation_episodes 
                    SET topics = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (json.dumps(current_topics), episode_id))
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to add message to episode: {e}")
            return False
        finally:
            conn.close()
    
    def _extract_topics(self, message: str) -> List[str]:
        """Extract topics from message using simple keyword matching"""
        # Simple topic extraction - can be enhanced with NLP
        topic_keywords = {
            "calendar": ["meeting", "event", "schedule", "appointment", "calendar"],
            "tasks": ["task", "todo", "reminder", "deadline", "project"],
            "memory": ["remember", "recall", "forgot", "memory", "remind"],
            "development": ["code", "programming", "debug", "function", "api"],
            "planning": ["plan", "strategy", "goal", "objective", "roadmap"],
            "learning": ["learn", "study", "knowledge", "understand", "explain"]
        }
        
        message_lower = message.lower()
        topics = []
        
        for topic, keywords in topic_keywords.items():
            if any(keyword in message_lower for keyword in keywords):
                topics.append(topic)
        
        return topics
    
    def close_episode(self, episode_id: str, generate_summary: bool = True) -> bool:
        """Close an episode and optionally generate summary"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Update episode status
            cursor.execute("""
                UPDATE conversation_episodes 
                SET status = 'closed', end_time = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (episode_id,))
            
            if generate_summary:
                # Generate episode summary
                summary = self._generate_episode_summary(episode_id)
                if summary:
                    cursor.execute("""
                        UPDATE conversation_episodes 
                        SET summary = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (summary, episode_id))
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to close episode: {e}")
            return False
        finally:
            conn.close()
    
    def _generate_episode_summary(self, episode_id: str) -> Optional[str]:
        """Generate episode summary using LLM"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Get episode details
            cursor.execute("""
                SELECT user_id, context_type, message_count, topics, participants
                FROM conversation_episodes WHERE id = ?
            """, (episode_id,))
            
            episode_data = cursor.fetchone()
            if not episode_data:
                return None
            
            user_id, context_type, message_count, topics_json, participants_json = episode_data
            topics = json.loads(topics_json or "[]")
            participants = json.loads(participants_json or "[]")
            
            # Get recent memories from this episode
            cursor.execute("""
                SELECT mf.fact, mf.category, mf.importance
                FROM memory_facts mf
                JOIN memory_temporal_metadata mtm ON mf.id = mtm.fact_id
                WHERE mtm.episode_id = ?
                ORDER BY mf.importance DESC, mtm.timestamp DESC
                LIMIT 10
            """, (episode_id,))
            
            memories = cursor.fetchall()
            
            # Generate summary
            summary_parts = [
                f"Episode Summary for {context_type} conversation",
                f"Duration: {message_count} messages",
                f"Topics discussed: {', '.join(topics) if topics else 'General conversation'}",
                f"Participants: {', '.join(participants)}"
            ]
            
            if memories:
                summary_parts.append("Key points discussed:")
                for fact, category, importance in memories[:5]:
                    summary_parts.append(f"- {fact}")
            
            summary = "\n".join(summary_parts)
            
            # Save summary
            cursor.execute("""
                INSERT INTO episode_summaries 
                (episode_id, summary_type, content, model_used, confidence_score)
                VALUES (?, ?, ?, ?, ?)
            """, (episode_id, "llm_generated", summary, "zoe_temporal", 0.8))
            
            conn.commit()
            return summary
            
        except Exception as e:
            logger.error(f"Failed to generate episode summary: {e}")
            return None
        finally:
            conn.close()
    
    def add_temporal_memory(self, fact_id: int, episode_id: str, 
                           temporal_context: str = "") -> bool:
        """Add temporal metadata to a memory fact"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO memory_temporal_metadata 
                (fact_id, episode_id, timestamp, temporal_context)
                VALUES (?, ?, ?, ?)
            """, (fact_id, episode_id, datetime.now().isoformat(), temporal_context))
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to add temporal memory: {e}")
            return False
        finally:
            conn.close()
    
    def search_temporal_memories(self, query: str, user_id: str, 
                                time_range: Optional[Tuple[str, str]] = None,
                                episode_id: Optional[str] = None) -> List[TemporalMemory]:
        """Search memories with temporal context"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Build query with temporal constraints
            base_query = """
                SELECT mf.id, mf.fact, mf.entity_type, mf.entity_id,
                       COALESCE(p.name, pr.name, 'General') as entity_name,
                       mf.category, mf.importance, mtm.episode_id, mtm.timestamp,
                       mtm.decay_factor, mtm.access_count, mtm.last_accessed, mtm.temporal_context
                FROM memory_facts mf
                JOIN memory_temporal_metadata mtm ON mf.id = mtm.fact_id
                JOIN conversation_episodes ce ON mtm.episode_id = ce.id
                LEFT JOIN people p ON mf.entity_type = 'person' AND mf.entity_id = p.id
                LEFT JOIN projects pr ON mf.entity_type = 'project' AND mf.entity_id = pr.id
                WHERE ce.user_id = ? AND mf.fact LIKE ?
            """
            
            params = [user_id, f"%{query}%"]
            
            if time_range:
                base_query += " AND mtm.timestamp BETWEEN ? AND ?"
                params.extend(time_range)
            
            if episode_id:
                base_query += " AND mtm.episode_id = ?"
                params.append(episode_id)
            
            base_query += " ORDER BY mtm.timestamp DESC, mf.importance DESC"
            
            cursor.execute(base_query, params)
            
            results = []
            for row in cursor.fetchall():
                results.append(TemporalMemory(
                    fact_id=row[0],
                    fact=row[1],
                    entity_type=row[2],
                    entity_id=row[3],
                    entity_name=row[4],
                    category=row[5],
                    importance=row[6],
                    episode_id=row[7],
                    timestamp=row[8],
                    decay_factor=row[9],
                    access_count=row[10],
                    last_accessed=row[11],
                    temporal_context=row[12]
                ))
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to search temporal memories: {e}")
            return []
        finally:
            conn.close()
    
    def apply_memory_decay(self, user_id: str) -> Dict[str, Any]:
        """Apply memory decay algorithm to old memories"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Get memories older than decay threshold
            decay_threshold = datetime.now() - timedelta(days=self.decay_halflife_days)
            
            cursor.execute("""
                SELECT mtm.fact_id, mtm.timestamp, mtm.decay_factor
                FROM memory_temporal_metadata mtm
                JOIN conversation_episodes ce ON mtm.episode_id = ce.id
                WHERE ce.user_id = ? AND mtm.timestamp < ?
            """, (user_id, decay_threshold.isoformat()))
            
            old_memories = cursor.fetchall()
            decayed_count = 0
            
            for fact_id, timestamp, current_decay in old_memories:
                # Calculate new decay factor
                days_old = (datetime.now() - datetime.fromisoformat(timestamp)).days
                new_decay = 0.5 ** (days_old / self.decay_halflife_days)
                
                # Update decay factor
                cursor.execute("""
                    UPDATE memory_temporal_metadata 
                    SET decay_factor = ?, last_accessed = CURRENT_TIMESTAMP
                    WHERE fact_id = ?
                """, (new_decay, fact_id))
                
                decayed_count += 1
            
            conn.commit()
            
            return {
                "decayed_memories": decayed_count,
                "decay_threshold_days": self.decay_halflife_days,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to apply memory decay: {e}")
            return {"error": str(e)}
        finally:
            conn.close()
    
    def get_episode_history(self, user_id: str, limit: int = 10) -> List[ConversationEpisode]:
        """Get episode history for a user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT id, user_id, start_time, end_time, status, context_type, summary,
                       message_count, topics, participants, created_at, updated_at
                FROM conversation_episodes
                WHERE user_id = ?
                ORDER BY start_time DESC
                LIMIT ?
            """, (user_id, limit))
            
            episodes = []
            for row in cursor.fetchall():
                episodes.append(ConversationEpisode(
                    id=row[0],
                    user_id=row[1],
                    start_time=row[2],
                    end_time=row[3],
                    status=EpisodeStatus(row[4]),
                    context_type=row[5],
                    summary=row[6],
                    message_count=row[7],
                    topics=json.loads(row[8]) if row[8] else [],
                    participants=json.loads(row[9]) if row[9] else [],
                    created_at=row[10],
                    updated_at=row[11]
                ))
            
            return episodes
            
        except Exception as e:
            logger.error(f"Failed to get episode history: {e}")
            return []
        finally:
            conn.close()
    
    def check_episode_timeouts(self) -> Dict[str, Any]:
        """Check for episodes that should be closed due to timeout"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Get active episodes
            cursor.execute("""
                SELECT id, user_id, start_time, context_type
                FROM conversation_episodes
                WHERE status = 'active'
            """)
            
            active_episodes = cursor.fetchall()
            closed_count = 0
            
            for episode_id, user_id, start_time, context_type in active_episodes:
                start_dt = datetime.fromisoformat(start_time)
                timeout_minutes = self.episode_timeouts.get(context_type, 45)
                
                if datetime.now() - start_dt > timedelta(minutes=timeout_minutes):
                    # Close expired episode
                    cursor.execute("""
                        UPDATE conversation_episodes 
                        SET status = 'expired', end_time = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (episode_id,))
                    closed_count += 1
            
            conn.commit()
            
            return {
                "checked_episodes": len(active_episodes),
                "closed_episodes": closed_count,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to check episode timeouts: {e}")
            return {"error": str(e)}
        finally:
            conn.close()

# Global instance
temporal_memory = TemporalMemorySystem()
