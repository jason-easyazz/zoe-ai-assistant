"""
Behavioral Memory L1 for P1-1
Extract natural language patterns from conversations
Target: 7+ patterns per user, 70%+ accuracy
"""
import logging
import sqlite3
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from collections import Counter
import json

logger = logging.getLogger(__name__)


class BehavioralPattern:
    """Represents a behavioral pattern"""
    
    def __init__(self, pattern_type: str, text: str, confidence: float, source: str):
        self.pattern_type = pattern_type  # timing, interest, communication, task, preference
        self.text = text
        self.confidence = confidence
        self.source = source
        self.created_at = datetime.now().isoformat()
    
    def to_dict(self) -> Dict:
        return {
            "type": self.pattern_type,
            "text": self.text,
            "confidence": self.confidence,
            "source": self.source,
            "created_at": self.created_at
        }


class RuleBasedPatternExtractor:
    """Rule-based extraction (guaranteed to work, no LLM dependency)"""
    
    def __init__(self, db_path: str = "temporal_memory.db"):
        self.db_path = db_path
    
    def extract_patterns(self, user_id: str, min_conversations: int = 10) -> List[BehavioralPattern]:
        """
        Extract behavioral patterns using rule-based heuristics
        
        Args:
            user_id: User ID
            min_conversations: Minimum conversations needed
            
        Returns:
            List of BehavioralPatterns
        """
        patterns = []
        
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Check if user has enough data
            cursor.execute(
                "SELECT COUNT(*) as count FROM conversation_episodes WHERE user_id = ?",
                (user_id,)
            )
            count = cursor.fetchone()
            if count and count["count"] < min_conversations:
                logger.info(f"[Behavioral] Not enough data: {count['count']} < {min_conversations}")
                return patterns
            
            # Extract timing patterns
            timing_patterns = self._extract_timing_patterns(cursor, user_id)
            patterns.extend(timing_patterns)
            
            # Extract interest patterns (from memory categories)
            interest_patterns = self._extract_interest_patterns(cursor, user_id)
            patterns.extend(interest_patterns)
            
            # Extract communication patterns
            comm_patterns = self._extract_communication_patterns(cursor, user_id)
            patterns.extend(comm_patterns)
            
            # Extract task patterns
            task_patterns = self._extract_task_patterns(cursor, user_id)
            patterns.extend(task_patterns)
            
            conn.close()
            
            logger.info(f"[Behavioral] Extracted {len(patterns)} patterns for user {user_id}")
            return patterns
        
        except Exception as e:
            logger.error(f"[Behavioral] Extraction failed: {e}")
            return []
    
    def _extract_timing_patterns(self, cursor, user_id: str) -> List[BehavioralPattern]:
        """Extract activity timing patterns"""
        patterns = []
        
        try:
            cursor.execute("""
                SELECT strftime('%H', timestamp) as hour, COUNT(*) as count
                FROM conversation_episodes
                WHERE user_id = ?
                GROUP BY hour
                ORDER BY count DESC
                LIMIT 3
            """, (user_id,))
            
            hours_data = cursor.fetchall()
            if hours_data:
                peak_hours = [int(row["hour"]) for row in hours_data]
                
                # Determine time of day
                if peak_hours[0] >= 9 and peak_hours[0] <= 17:
                    time_desc = "business hours (9 AM - 5 PM)"
                elif peak_hours[0] >= 18 and peak_hours[0] <= 22:
                    time_desc = "evening (6 PM - 10 PM)"
                elif peak_hours[0] >= 6 and peak_hours[0] <= 8:
                    time_desc = "morning (6 AM - 8 AM)"
                else:
                    time_desc = f"around {peak_hours[0]}:00"
                
                pattern = BehavioralPattern(
                    pattern_type="timing",
                    text=f"Most active during {time_desc}",
                    confidence=0.85,
                    source="conversation_timestamps"
                )
                patterns.append(pattern)
        
        except Exception as e:
            logger.error(f"[Behavioral] Timing extraction failed: {e}")
        
        return patterns
    
    def _extract_interest_patterns(self, cursor, user_id: str) -> List[BehavioralPattern]:
        """Extract interest patterns from memory categories"""
        patterns = []
        
        try:
            # Check if memory tables exist
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='light_rag_facts'
            """)
            
            if not cursor.fetchone():
                return patterns
            
            cursor.execute("""
                SELECT category, COUNT(*) as count
                FROM light_rag_facts
                WHERE user_id = ?
                GROUP BY category
                ORDER BY count DESC
                LIMIT 3
            """, (user_id,))
            
            categories = cursor.fetchall()
            if categories and len(categories) > 0:
                top_category = categories[0]["category"]
                count = categories[0]["count"]
                
                if count >= 5:
                    pattern = BehavioralPattern(
                        pattern_type="interest",
                        text=f"Frequent discussions about {top_category}",
                        confidence=min(0.9, 0.6 + (count / 20)),
                        source="memory_categories"
                    )
                    patterns.append(pattern)
        
        except Exception as e:
            logger.error(f"[Behavioral] Interest extraction failed: {e}")
        
        return patterns
    
    def _extract_communication_patterns(self, cursor, user_id: str) -> List[BehavioralPattern]:
        """Extract communication style patterns"""
        patterns = []
        
        try:
            # This is a simplified pattern - in production would analyze message lengths, etc.
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM conversation_episodes
                WHERE user_id = ? AND context_type = 'chat'
            """, (user_id,))
            
            chat_count = cursor.fetchone()["count"]
            
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM conversation_episodes
                WHERE user_id = ?
            """, (user_id,))
            
            total_count = cursor.fetchone()["count"]
            
            if total_count > 0:
                chat_ratio = chat_count / total_count
                
                if chat_ratio < 0.3:
                    pattern = BehavioralPattern(
                        pattern_type="communication",
                        text="Direct communication style, task-focused",
                        confidence=0.75,
                        source="conversation_style"
                    )
                    patterns.append(pattern)
                elif chat_ratio > 0.6:
                    pattern = BehavioralPattern(
                        pattern_type="communication",
                        text="Conversational style, enjoys dialogue",
                        confidence=0.75,
                        source="conversation_style"
                    )
                    patterns.append(pattern)
        
        except Exception as e:
            logger.error(f"[Behavioral] Communication extraction failed: {e}")
        
        return patterns
    
    def _extract_task_patterns(self, cursor, user_id: str) -> List[BehavioralPattern]:
        """Extract task and routine patterns"""
        patterns = []
        
        try:
            # Analyze episode context types for routine detection
            cursor.execute("""
                SELECT context_type, COUNT(*) as count
                FROM conversation_episodes
                WHERE user_id = ?
                GROUP BY context_type
                ORDER BY count DESC
                LIMIT 2
            """, (user_id,))
            
            contexts = cursor.fetchall()
            for context_row in contexts:
                context_type = context_row["context_type"]
                count = context_row["count"]
                
                if count >= 5 and context_type in ["task", "list", "calendar"]:
                    pattern = BehavioralPattern(
                        pattern_type="task",
                        text=f"Regularly uses {context_type} features",
                        confidence=0.80,
                        source="feature_usage"
                    )
                    patterns.append(pattern)
        
        except Exception as e:
            logger.error(f"[Behavioral] Task extraction failed: {e}")
        
        return patterns


class BehavioralMemoryManager:
    """Manages behavioral memory storage and retrieval"""
    
    def __init__(self, db_path: str = "temporal_memory.db"):
        self.db_path = db_path
        self.extractor = RuleBasedPatternExtractor(db_path)
        self._init_db()
    
    def _init_db(self):
        """Initialize behavioral patterns table"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS behavioral_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    pattern_type TEXT NOT NULL,
                    pattern_text TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_behavioral_user 
                ON behavioral_patterns(user_id)
            """)
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[Behavioral] DB init failed: {e}")
    
    def update_patterns(self, user_id: str) -> int:
        """
        Update behavioral patterns for a user
        
        Returns:
            Number of patterns updated
        """
        try:
            # Extract patterns
            patterns = self.extractor.extract_patterns(user_id)
            
            if not patterns:
                logger.info(f"[Behavioral] No patterns extracted for user {user_id}")
                return 0
            
            # Store patterns
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Clear old patterns
            cursor.execute("DELETE FROM behavioral_patterns WHERE user_id = ?", (user_id,))
            
            # Insert new patterns
            for pattern in patterns:
                cursor.execute("""
                    INSERT INTO behavioral_patterns 
                    (user_id, pattern_type, pattern_text, confidence, source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_id,
                    pattern.pattern_type,
                    pattern.text,
                    pattern.confidence,
                    pattern.source,
                    pattern.created_at,
                    datetime.now().isoformat()
                ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"[Behavioral] Updated {len(patterns)} patterns for user {user_id}")
            return len(patterns)
        
        except Exception as e:
            logger.error(f"[Behavioral] Pattern update failed: {e}")
            return 0
    
    def get_patterns(self, user_id: str, min_confidence: float = 0.7) -> List[Dict]:
        """
        Get behavioral patterns for a user
        
        Args:
            user_id: User ID
            min_confidence: Minimum confidence threshold
            
        Returns:
            List of pattern dictionaries
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT pattern_type, pattern_text, confidence, source, created_at
                FROM behavioral_patterns
                WHERE user_id = ? AND confidence >= ?
                ORDER BY confidence DESC
            """, (user_id, min_confidence))
            
            patterns = [dict(row) for row in cursor.fetchall()]
            conn.close()
            
            return patterns
        
        except Exception as e:
            logger.error(f"[Behavioral] Pattern retrieval failed: {e}")
            return []
    
    def get_patterns_for_context(self, user_id: str, limit: int = 5) -> str:
        """
        Get patterns formatted for LLM context
        
        Returns:
            Formatted string of patterns
        """
        patterns = self.get_patterns(user_id)
        
        if not patterns:
            return ""
        
        # Group by type
        by_type = {}
        for p in patterns:
            ptype = p["pattern_type"]
            if ptype not in by_type:
                by_type[ptype] = []
            by_type[ptype].append(p["pattern_text"])
        
        # Format
        lines = ["User behavioral patterns:"]
        for ptype, texts in list(by_type.items())[:limit]:
            lines.append(f"- {ptype.title()}: {texts[0]}")
        
        return "\n".join(lines)


# Global instance
behavioral_memory = BehavioralMemoryManager()

