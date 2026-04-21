"""
Unified Learning Engine (Phase 6B)
Learn from ALL archived data across multiple data types simultaneously
"""

import logging
import json
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime, timedelta

from memvid.retriever import MemvidRetriever

logger = logging.getLogger(__name__)


class UnifiedLearningEngine:
    """Learn from all data sources using memvid archives"""
    
    def __init__(self, archive_dir: str = "/app/data/learning-archives"):
        self.archive_dir = Path(archive_dir)
    
    async def analyze_complete_history(self, user_id: str) -> Dict[str, Any]:
        """
        Analyze user's complete interaction history across all data types.
        
        Returns patterns from chats, journals, tasks, behaviors for holistic understanding.
        """
        if not self.archive_dir.exists():
            return {"error": "Archive directory not found", "patterns": {}}
        
        all_archives = list(self.archive_dir.glob("*.mp4"))
        
        if not all_archives:
            return {
                "message": "No archives yet - data will accumulate over time",
                "patterns": {},
                "note": "First archival runs quarterly after 90 days"
            }
        
        # Analyze each data type
        patterns = {
            "communication_style": await self._analyze_communication(user_id, all_archives),
            "emotional_patterns": await self._analyze_emotions(user_id, all_archives),
            "productivity_patterns": await self._analyze_productivity(user_id, all_archives),
            "behavioral_patterns": await self._analyze_behaviors(user_id, all_archives)
        }
        
        # Cross-correlate patterns
        patterns["correlations"] = await self._cross_correlate_all(user_id, all_archives)
        
        return {
            "user_id": user_id,
            "analyzed_at": datetime.now().isoformat(),
            "archives_analyzed": len(all_archives),
            "patterns": patterns
        }
    
    async def _analyze_communication(self, user_id: str, archives: List[Path]) -> Dict:
        """Learn communication preferences from all chat history"""
        chat_archives = [a for a in archives if 'chats-' in a.name]
        
        if not chat_archives:
            return {"note": "No chat archives yet"}
        
        all_chats = []
        for archive in chat_archives:
            index_file = archive.with_suffix('.idx')
            if not index_file.exists():
                continue
            
            try:
                retriever = MemvidRetriever(str(archive))
                # Get all chats for user
                user_chats = retriever.search(f"user_id", top_k=1000)
                
                for chat in user_chats:
                    try:
                        data = json.loads(chat['text'])
                        if data.get('user_id') == user_id:
                            all_chats.append(data)
                    except:
                        pass
            except Exception as e:
                logger.error(f"Error analyzing {archive}: {e}")
        
        if not all_chats:
            return {"note": "No user chats found in archives"}
        
        # Analyze patterns
        return {
            "total_chats_analyzed": len(all_chats),
            "avg_response_time": self._calculate_avg(all_chats, 'response_time'),
            "quality_trend": self._analyze_quality_trend(all_chats),
            "common_topics": self._extract_common_patterns(all_chats, 'user_message'),
            "preferred_models": self._count_field_values(all_chats, 'model_used')
        }
    
    async def _analyze_emotions(self, user_id: str, archives: List[Path]) -> Dict:
        """Analyze emotional patterns from journal archives"""
        journal_archives = [a for a in archives if 'journals-' in a.name]
        
        if not journal_archives:
            return {"note": "No journal archives yet"}
        
        all_journals = []
        for archive in journal_archives:
            try:
                retriever = MemvidRetriever(str(archive))
                journals = retriever.search(f"user_id", top_k=1000)
                
                for journal in journals:
                    try:
                        data = json.loads(journal['text'])
                        if data.get('user_id') == user_id:
                            all_journals.append(data)
                    except:
                        pass
            except:
                pass
        
        if not all_journals:
            return {"note": "No journal entries in archives"}
        
        return {
            "total_entries_analyzed": len(all_journals),
            "mood_distribution": self._count_field_values(all_journals, 'mood'),
            "avg_mood_score": self._calculate_avg(all_journals, 'mood_score'),
            "stress_indicators": self._find_negative_patterns(all_journals),
            "happiness_factors": self._find_positive_patterns(all_journals)
        }
    
    async def _analyze_productivity(self, user_id: str, archives: List[Path]) -> Dict:
        """Analyze productivity patterns from task archives"""
        task_archives = [a for a in archives if 'tasks-' in a.name]
        
        if not task_archives:
            return {"note": "No task archives yet"}
        
        all_tasks = []
        for archive in task_archives:
            try:
                retriever = MemvidRetriever(str(archive))
                tasks = retriever.search(f"user_id", top_k=1000)
                
                for task in tasks:
                    try:
                        data = json.loads(task['text'])
                        if data.get('user_id') == user_id:
                            all_tasks.append(data)
                    except:
                        pass
            except:
                pass
        
        if not all_tasks:
            return {"note": "No completed tasks in archives"}
        
        return {
            "total_tasks_analyzed": len(all_tasks),
            "avg_completion_time_hours": self._calculate_avg(all_tasks, 'time_to_complete_hours'),
            "completion_by_priority": self._count_field_values(all_tasks, 'priority'),
            "most_common_lists": self._count_field_values(all_tasks, 'list_name')
        }
    
    async def _analyze_behaviors(self, user_id: str, archives: List[Path]) -> Dict:
        """Analyze behavioral patterns from pattern archives"""
        pattern_archives = [a for a in archives if 'patterns-' in a.name]
        
        if not pattern_archives:
            return {"note": "No pattern archives yet"}
        
        all_patterns = []
        for archive in pattern_archives:
            try:
                retriever = MemvidRetriever(str(archive))
                patterns = retriever.search(f"user_id", top_k=1000)
                
                for pattern in patterns:
                    try:
                        data = json.loads(pattern['text'])
                        if data.get('user_id') == user_id:
                            all_patterns.append(data)
                    except:
                        pass
            except:
                pass
        
        if not all_patterns:
            return {"note": "No behavioral patterns in archives"}
        
        return {
            "total_patterns_analyzed": len(all_patterns),
            "pattern_types": self._count_field_values(all_patterns, 'pattern_type'),
            "high_confidence_patterns": [p for p in all_patterns if p.get('confidence', 0) > 0.8]
        }
    
    async def _cross_correlate_all(self, user_id: str, archives: List[Path]) -> Dict:
        """
        Discover patterns across ALL data types.
        
        Examples:
        - Mood in journals vs task completion
        - Social chats vs emotional wellbeing
        - Productivity vs time patterns
        """
        # This would require actual data to correlate
        # For now, return placeholder structure
        return {
            "note": "Cross-correlation available when sufficient data archived",
            "examples": [
                "Mood vs productivity correlation",
                "Social interaction vs happiness",
                "Task completion vs stress levels",
                "Time-of-day vs success rates"
            ]
        }
    
    # Helper methods
    def _calculate_avg(self, items: List[Dict], field: str) -> Optional[float]:
        """Calculate average of a numeric field"""
        values = [item.get(field) for item in items if isinstance(item.get(field), (int, float))]
        return round(sum(values) / len(values), 2) if values else None
    
    def _count_field_values(self, items: List[Dict], field: str) -> Dict:
        """Count occurrences of field values"""
        counts = {}
        for item in items:
            value = item.get(field)
            if value:
                counts[str(value)] = counts.get(str(value), 0) + 1
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10])
    
    def _analyze_quality_trend(self, chats: List[Dict]) -> Dict:
        """Analyze quality scores over time"""
        scores = [c.get('quality_score') for c in chats if c.get('quality_score')]
        return {
            "avg_quality": round(sum(scores) / len(scores), 2) if scores else None,
            "total_rated": len(scores)
        }
    
    def _extract_common_patterns(self, items: List[Dict], field: str) -> List[str]:
        """Extract most common patterns from text field"""
        # Simplified - could use NLP for better extraction
        return ["Pattern extraction available when more data archived"]
    
    def _find_negative_patterns(self, journals: List[Dict]) -> List[str]:
        """Find stress/negative patterns in journals"""
        negative_moods = [j for j in journals if j.get('mood') in ['stressed', 'sad', 'anxious']]
        return [f"Found {len(negative_moods)} entries with negative mood indicators"]
    
    def _find_positive_patterns(self, journals: List[Dict]) -> List[str]:
        """Find happiness/positive patterns in journals"""
        positive_moods = [j for j in journals if j.get('mood') in ['happy', 'excited', 'content']]
        return [f"Found {len(positive_moods)} entries with positive mood indicators"]
    
    async def get_frequently_bought_together(
        self, user_id: str, item: str, min_confidence: float = 0.5
    ) -> List[str]:
        """
        Learn item pairings from user's actual behavior.
        No hardcoded milk→bread rules - learns from history.
        
        Args:
            user_id: User ID
            item: The item to find associations for
            min_confidence: Minimum confidence threshold (0-1)
            
        Returns:
            List of items frequently bought with the given item
        """
        import sqlite3
        
        try:
            conn = sqlite3.connect("/app/data/zoe.db")
            cursor = conn.cursor()
            
            # Find items added within 2 minutes of each other (shopping session)
            # More lenient time window and lower threshold for faster learning
            cursor.execute("""
                WITH item_sessions AS (
                    SELECT 
                        json_extract(tool_params, '$.item') as item,
                        timestamp,
                        datetime(timestamp, '-2 minutes') as session_start,
                        datetime(timestamp, '+2 minutes') as session_end
                    FROM action_logs
                    WHERE user_id = ?
                      AND tool_name = 'add_to_list'
                      AND json_extract(tool_params, '$.list_type') = 'shopping'
                      AND json_extract(tool_params, '$.item') LIKE ?
                )
                SELECT 
                    json_extract(tp.tool_params, '$.item') as paired_item,
                    COUNT(*) as frequency
                FROM action_logs tp
                INNER JOIN item_sessions ses ON 
                    tp.timestamp BETWEEN ses.session_start AND ses.session_end
                WHERE tp.user_id = ?
                  AND tp.tool_name = 'add_to_list'
                  AND json_extract(tp.tool_params, '$.list_type') = 'shopping'
                  AND json_extract(tp.tool_params, '$.item') NOT LIKE ?
                GROUP BY paired_item
                HAVING frequency >= 2
                ORDER BY frequency DESC
                LIMIT 5
            """, (user_id, f"%{item}%", user_id, f"%{item}%"))
            
            pairs = cursor.fetchall()
            conn.close()
            
            return [p[0] for p in pairs] if pairs else []
        
        except Exception as e:
            logger.error(f"Error getting frequently bought together: {e}")
            return []
    
    async def get_related_actions(
        self, user_id: str, tool_name: str, params: dict
    ) -> List[dict]:
        """
        Find related actions that commonly follow the given action.
        
        Args:
            user_id: User ID
            tool_name: The tool that was just executed
            params: Parameters of the tool execution
            
        Returns:
            List of related action suggestions
        """
        import sqlite3
        
        try:
            conn = sqlite3.connect("/app/data/zoe.db")
            cursor = conn.cursor()
            
            # Find actions that commonly happen within 10 minutes after this action
            cursor.execute("""
                WITH this_action AS (
                    SELECT timestamp
                    FROM action_logs
                    WHERE user_id = ?
                      AND tool_name = ?
                    ORDER BY timestamp DESC
                    LIMIT 10
                )
                SELECT 
                    al.tool_name,
                    al.tool_params,
                    COUNT(*) as frequency
                FROM action_logs al
                INNER JOIN this_action ta ON 
                    al.timestamp BETWEEN ta.timestamp 
                    AND datetime(ta.timestamp, '+10 minutes')
                WHERE al.user_id = ?
                  AND al.tool_name != ?
                  AND al.timestamp > datetime('now', '-30 days')
                GROUP BY al.tool_name, al.tool_params
                HAVING frequency >= 2
                ORDER BY frequency DESC
                LIMIT 3
            """, (user_id, tool_name, user_id, tool_name))
            
            results = cursor.fetchall()
            conn.close()
            
            return [
                {
                    "tool_name": row[0],
                    "params": json.loads(row[1]) if row[1] else {},
                    "frequency": row[2]
                }
                for row in results
            ]
        
        except Exception as e:
            logger.error(f"Error getting related actions: {e}")
            return []


# Global instance
unified_learner = UnifiedLearningEngine()

