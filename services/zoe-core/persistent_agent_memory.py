"""
Persistent Agent Memory System (Phase 3: crewAI-inspired)
Agents remember past successes/failures and learn from experience
"""

import sqlite3
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class PersistentAgentMemory:
    """Agent memory system for learning from experience"""
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        self.db_path = db_path
    
    async def remember(self, 
                      agent_type: str,
                      user_id: str,
                      orchestration_id: str,
                      task_description: str,
                      success: bool,
                      result: Dict[str, Any]) -> None:
        """
        Store what the agent learned from this task execution.
        
        Args:
            agent_type: Type of agent (calendar, lists, memory, etc.)
            user_id: User ID for isolation
            orchestration_id: ID of the orchestration
            task_description: What the task was
            success: Whether the task succeeded
            result: Task execution result
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            learned_pattern = None
            failure_reason = None
            
            if success:
                # Extract successful pattern
                if 'action' in result and 'method' in result:
                    learned_pattern = f"Successfully {result.get('action')} using {result.get('method')}"
                elif 'data' in result:
                    learned_pattern = f"Task completed: {task_description}"
                else:
                    learned_pattern = f"Successful execution for: {task_description}"
            else:
                # Extract failure reason
                failure_reason = result.get('error', 'Unknown error')
            
            cursor.execute("""
                INSERT INTO agent_memory 
                (agent_type, user_id, orchestration_id, task_description, 
                 success, learned_pattern, failure_reason, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                agent_type,
                user_id,
                orchestration_id,
                task_description,
                success,
                learned_pattern,
                failure_reason,
                datetime.now().isoformat()
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"📝 Agent {agent_type} memory saved: {learned_pattern or failure_reason}")
            
        except Exception as e:
            logger.error(f"Error saving agent memory: {e}")
    
    async def recall(self, 
                    agent_type: str,
                    user_id: str,
                    task_description: str,
                    limit: int = 5) -> List[str]:
        """
        Recall relevant past experiences for this agent.
        
        Args:
            agent_type: Type of agent
            user_id: User ID
            task_description: Current task description
            limit: Max patterns to return
            
        Returns:
            List of learned patterns
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get recent successful patterns for this agent and user
            cursor.execute("""
                SELECT learned_pattern, timestamp
                FROM agent_memory
                WHERE agent_type = ? 
                  AND user_id = ? 
                  AND success = 1
                  AND learned_pattern IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT ?
            """, (agent_type, user_id, limit))
            
            patterns = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            if patterns:
                logger.info(f"🧠 Recalled {len(patterns)} patterns for {agent_type}")
            
            return patterns
            
        except Exception as e:
            logger.error(f"Error recalling agent memory: {e}")
            return []
    
    async def get_agent_stats(self, 
                             agent_type: str,
                             user_id: str,
                             days: int = 30) -> Dict[str, Any]:
        """
        Get success rate and statistics for an agent.
        
        Args:
            agent_type: Type of agent
            user_id: User ID
            days: Days to look back
            
        Returns:
            Dictionary with stats
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            since_date = (datetime.now() - timedelta(days=days)).isoformat()
            
            # Get success/failure counts
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failed
                FROM agent_memory
                WHERE agent_type = ? 
                  AND user_id = ?
                  AND timestamp > ?
            """, (agent_type, user_id, since_date))
            
            row = cursor.fetchone()
            total = row[0] or 0
            successful = row[1] or 0
            failed = row[2] or 0
            
            # Get top patterns
            cursor.execute("""
                SELECT learned_pattern, COUNT(*) as count
                FROM agent_memory
                WHERE agent_type = ? 
                  AND user_id = ?
                  AND success = 1
                  AND timestamp > ?
                  AND learned_pattern IS NOT NULL
                GROUP BY learned_pattern
                ORDER BY count DESC
                LIMIT 5
            """, (agent_type, user_id, since_date))
            
            top_patterns = [
                {"pattern": row[0], "count": row[1]} 
                for row in cursor.fetchall()
            ]
            
            conn.close()
            
            success_rate = (successful / total * 100) if total > 0 else 0
            
            return {
                "agent_type": agent_type,
                "total_tasks": total,
                "successful": successful,
                "failed": failed,
                "success_rate": round(success_rate, 1),
                "top_patterns": top_patterns,
                "period_days": days
            }
            
        except Exception as e:
            logger.error(f"Error getting agent stats: {e}")
            return {
                "agent_type": agent_type,
                "error": str(e)
            }
    
    async def cleanup_old_memories(self, days: int = 90) -> int:
        """
        Clean up old agent memories to prevent unbounded growth.
        
        Args:
            days: Keep memories from last N days
            
        Returns:
            Number of memories deleted
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
            
            cursor.execute("""
                DELETE FROM agent_memory
                WHERE timestamp < ?
            """, (cutoff_date,))
            
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            
            logger.info(f"🧹 Cleaned up {deleted} old agent memories (>{days} days)")
            return deleted
            
        except Exception as e:
            logger.error(f"Error cleaning up memories: {e}")
            return 0


# Global instance
agent_memory = PersistentAgentMemory()




