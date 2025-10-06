"""
Learning System for Zoe AI Assistant
Tracks success patterns and improves system behavior over time
"""
import sqlite3
import json
import os
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class LearningSystem:
    """System that learns from task execution patterns and improves over time"""
    
    def __init__(self):
        self.db_path = "/app/data/learning.db"
        self.init_database()
        
    def init_database(self):
        """Initialize learning database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Learning patterns table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS learning_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,
                pattern_data TEXT NOT NULL,  -- JSON
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Task execution history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS task_execution_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                task_title TEXT,
                execution_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                success BOOLEAN NOT NULL,
                execution_duration REAL,
                error_message TEXT,
                system_context TEXT,  -- JSON
                improvements_applied TEXT,  -- JSON
                learning_insights TEXT  -- JSON
            )
        ''')
        
        # Knowledge base
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence_score REAL DEFAULT 0.0,
                usage_count INTEGER DEFAULT 0,
                last_used TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # System improvements
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_improvements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                improvement_type TEXT NOT NULL,
                description TEXT NOT NULL,
                implementation TEXT,  -- JSON
                effectiveness_score REAL DEFAULT 0.0,
                applied_count INTEGER DEFAULT 0,
                last_applied TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def record_task_execution(self, task_id: str, task_title: str, success: bool, 
                            execution_duration: float = None, error_message: str = None,
                            system_context: Dict = None) -> bool:
        """Record task execution for learning"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO task_execution_history 
                (task_id, task_title, success, execution_duration, error_message, system_context)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                task_id,
                task_title,
                success,
                execution_duration,
                error_message,
                json.dumps(system_context) if system_context else None
            ))
            
            conn.commit()
            conn.close()
            
            # Trigger learning analysis
            self._analyze_execution_patterns()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to record task execution: {e}")
            return False
    
    def _analyze_execution_patterns(self):
        """Analyze execution patterns and update learning"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get recent executions
            cursor.execute('''
                SELECT * FROM task_execution_history 
                WHERE execution_time > datetime('now', '-7 days')
                ORDER BY execution_time DESC
            ''')
            
            recent_executions = cursor.fetchall()
            
            # Analyze patterns
            patterns = self._extract_patterns(recent_executions)
            
            # Update learning patterns
            for pattern_type, pattern_data in patterns.items():
                self._update_learning_pattern(pattern_type, pattern_data)
            
            # Generate improvements
            improvements = self._generate_improvements(recent_executions)
            for improvement in improvements:
                self._record_improvement(improvement)
            
            conn.close()
            
        except Exception as e:
            logger.error(f"Pattern analysis failed: {e}")
    
    def _extract_patterns(self, executions: List) -> Dict[str, Any]:
        """Extract learning patterns from executions"""
        patterns = {}
        
        # Success/failure patterns
        success_count = sum(1 for ex in executions if ex[4])  # success column
        failure_count = len(executions) - success_count
        
        patterns["success_rate"] = {
            "success_count": success_count,
            "failure_count": failure_count,
            "success_rate": success_count / len(executions) if executions else 0
        }
        
        # Error patterns
        error_messages = [ex[6] for ex in executions if ex[6]]  # error_message column
        if error_messages:
            error_patterns = {}
            for error in error_messages:
                error_type = self._classify_error(error)
                error_patterns[error_type] = error_patterns.get(error_type, 0) + 1
            
            patterns["error_patterns"] = error_patterns
        
        # Duration patterns
        durations = [ex[5] for ex in executions if ex[5]]  # execution_duration column
        if durations:
            patterns["duration_patterns"] = {
                "avg_duration": sum(durations) / len(durations),
                "max_duration": max(durations),
                "min_duration": min(durations)
            }
        
        # Task type patterns
        task_titles = [ex[2] for ex in executions]  # task_title column
        task_types = {}
        for title in task_titles:
            task_type = self._classify_task_type(title)
            task_types[task_type] = task_types.get(task_type, 0) + 1
        
        patterns["task_type_patterns"] = task_types
        
        return patterns
    
    def _classify_error(self, error_message: str) -> str:
        """Classify error message into categories"""
        error_lower = error_message.lower()
        
        if "timeout" in error_lower:
            return "timeout"
        elif "connection" in error_lower:
            return "connection"
        elif "permission" in error_lower or "access" in error_lower:
            return "permission"
        elif "not found" in error_lower:
            return "not_found"
        elif "validation" in error_lower:
            return "validation"
        else:
            return "other"
    
    def _classify_task_type(self, task_title: str) -> str:
        """Classify task into types"""
        title_lower = task_title.lower()
        
        if "api" in title_lower or "endpoint" in title_lower:
            return "api"
        elif "database" in title_lower or "db" in title_lower:
            return "database"
        elif "ui" in title_lower or "frontend" in title_lower:
            return "ui"
        elif "test" in title_lower:
            return "testing"
        elif "backup" in title_lower:
            return "backup"
        elif "ai" in title_lower or "ml" in title_lower:
            return "ai"
        else:
            return "general"
    
    def _update_learning_pattern(self, pattern_type: str, pattern_data: Dict[str, Any]):
        """Update or create learning pattern"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if pattern exists
            cursor.execute('''
                SELECT id, success_count, failure_count FROM learning_patterns 
                WHERE pattern_type = ?
            ''', (pattern_type,))
            
            existing = cursor.fetchone()
            
            if existing:
                # Update existing pattern
                pattern_id, success_count, failure_count = existing
                new_success = pattern_data.get("success_count", 0)
                new_failure = pattern_data.get("failure_count", 0)
                
                cursor.execute('''
                    UPDATE learning_patterns 
                    SET success_count = ?, failure_count = ?, 
                        pattern_data = ?, last_updated = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (
                    success_count + new_success,
                    failure_count + new_failure,
                    json.dumps(pattern_data),
                    pattern_id
                ))
            else:
                # Create new pattern
                cursor.execute('''
                    INSERT INTO learning_patterns 
                    (pattern_type, pattern_data, success_count, failure_count)
                    VALUES (?, ?, ?, ?)
                ''', (
                    pattern_type,
                    json.dumps(pattern_data),
                    pattern_data.get("success_count", 0),
                    pattern_data.get("failure_count", 0)
                ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to update learning pattern: {e}")
    
    def _generate_improvements(self, executions: List) -> List[Dict[str, Any]]:
        """Generate system improvements based on execution history"""
        improvements = []
        
        # Analyze failure patterns
        failures = [ex for ex in executions if not ex[4]]  # success column
        
        if len(failures) > len(executions) * 0.3:  # More than 30% failures
            improvements.append({
                "improvement_type": "error_handling",
                "description": "High failure rate detected, improve error handling",
                "implementation": {
                    "action": "add_retry_logic",
                    "timeout_increase": True,
                    "fallback_mechanisms": True
                }
            })
        
        # Analyze duration patterns
        durations = [ex[5] for ex in executions if ex[5]]
        if durations and max(durations) > 30:  # Tasks taking more than 30 seconds
            improvements.append({
                "improvement_type": "performance",
                "description": "Long execution times detected, optimize performance",
                "implementation": {
                    "action": "optimize_queries",
                    "add_caching": True,
                    "parallel_processing": True
                }
            })
        
        # Analyze error types
        error_types = {}
        for ex in failures:
            if ex[6]:  # error_message
                error_type = self._classify_error(ex[6])
                error_types[error_type] = error_types.get(error_type, 0) + 1
        
        for error_type, count in error_types.items():
            if count > 2:  # Same error type multiple times
                improvements.append({
                    "improvement_type": f"fix_{error_type}",
                    "description": f"Multiple {error_type} errors detected",
                    "implementation": {
                        "action": f"fix_{error_type}_issues",
                        "prevention": True
                    }
                })
        
        return improvements
    
    def _record_improvement(self, improvement: Dict[str, Any]):
        """Record system improvement"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO system_improvements 
                (improvement_type, description, implementation)
                VALUES (?, ?, ?)
            ''', (
                improvement["improvement_type"],
                improvement["description"],
                json.dumps(improvement["implementation"])
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to record improvement: {e}")
    
    def get_learning_insights(self) -> Dict[str, Any]:
        """Get current learning insights"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get success rate
            cursor.execute('''
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful,
                    AVG(execution_duration) as avg_duration
                FROM task_execution_history 
                WHERE execution_time > datetime('now', '-30 days')
            ''')
            
            stats = cursor.fetchone()
            
            # Get top error patterns
            cursor.execute('''
                SELECT error_message, COUNT(*) as count
                FROM task_execution_history 
                WHERE success = 0 AND error_message IS NOT NULL
                AND execution_time > datetime('now', '-30 days')
                GROUP BY error_message
                ORDER BY count DESC
                LIMIT 5
            ''')
            
            error_patterns = cursor.fetchall()
            
            # Get recent improvements
            cursor.execute('''
                SELECT improvement_type, description, effectiveness_score
                FROM system_improvements
                ORDER BY created_at DESC
                LIMIT 10
            ''')
            
            improvements = cursor.fetchall()
            
            conn.close()
            
            return {
                "success_rate": stats[1] / stats[0] if stats[0] > 0 else 0,
                "total_executions": stats[0],
                "successful_executions": stats[1],
                "avg_duration": stats[2] or 0,
                "top_errors": [{"error": err[0], "count": err[1]} for err in error_patterns],
                "recent_improvements": [
                    {"type": imp[0], "description": imp[1], "score": imp[2]} 
                    for imp in improvements
                ]
            }
            
        except Exception as e:
            logger.error(f"Failed to get learning insights: {e}")
            return {"error": str(e)}
    
    def get_recommendations(self) -> List[Dict[str, Any]]:
        """Get system improvement recommendations"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get patterns with high failure rates
            cursor.execute('''
                SELECT pattern_type, success_count, failure_count
                FROM learning_patterns
                WHERE failure_count > success_count
                ORDER BY failure_count DESC
            ''')
            
            problematic_patterns = cursor.fetchall()
            
            recommendations = []
            
            for pattern_type, success_count, failure_count in problematic_patterns:
                if failure_count > 0:
                    recommendations.append({
                        "type": "pattern_improvement",
                        "pattern": pattern_type,
                        "success_rate": success_count / (success_count + failure_count),
                        "recommendation": f"Improve {pattern_type} pattern - current success rate: {success_count / (success_count + failure_count):.2%}"
                    })
            
            # Get unused improvements
            cursor.execute('''
                SELECT improvement_type, description
                FROM system_improvements
                WHERE applied_count = 0
                ORDER BY created_at DESC
                LIMIT 5
            ''')
            
            unused_improvements = cursor.fetchall()
            
            for imp_type, description in unused_improvements:
                recommendations.append({
                    "type": "unused_improvement",
                    "improvement": imp_type,
                    "recommendation": f"Consider applying: {description}"
                })
            
            conn.close()
            
            return recommendations
            
        except Exception as e:
            logger.error(f"Failed to get recommendations: {e}")
            return []
    
    def apply_improvement(self, improvement_id: int) -> bool:
        """Apply a system improvement"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get improvement details
            cursor.execute('''
                SELECT improvement_type, implementation FROM system_improvements
                WHERE id = ?
            ''', (improvement_id,))
            
            improvement = cursor.fetchone()
            if not improvement:
                return False
            
            imp_type, implementation = improvement
            implementation_data = json.loads(implementation)
            
            # Apply the improvement (simplified - in real system would implement actual changes)
            cursor.execute('''
                UPDATE system_improvements
                SET applied_count = applied_count + 1,
                    last_applied = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (improvement_id,))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Applied improvement: {imp_type}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to apply improvement: {e}")
            return False

# Global instance
learning_system = LearningSystem()
