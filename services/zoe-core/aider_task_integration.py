"""
Aider Task Integration for Zoe AI Assistant
Allows Aider to work with the dynamic developer task system
"""
import sqlite3
import json
import os
from typing import Dict, List, Any, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class AiderTaskIntegration:
    """Integration between Aider and the dynamic task system"""
    
    def __init__(self):
        self.db_path = "/app/data/zoe.db"
        self.workspace_root = "/app"
        
    def get_task_context(self, task_id: str = None) -> Dict[str, Any]:
        """Get task context for Aider to work with"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if task_id:
                # Get specific task
                cursor.execute('''
                    SELECT id, title, objective, requirements, constraints, 
                           acceptance_criteria, priority, status, assigned_to,
                           created_at, last_executed_at, execution_count
                    FROM dynamic_tasks WHERE id = ?
                ''', (task_id,))
                
                task = cursor.fetchone()
                if task:
                    return {
                        "task": {
                            "id": task[0],
                            "title": task[1],
                            "objective": task[2],
                            "requirements": json.loads(task[3]) if task[3] else [],
                            "constraints": json.loads(task[4]) if task[4] else [],
                            "acceptance_criteria": json.loads(task[5]) if task[5] else [],
                            "priority": task[6],
                            "status": task[7],
                            "assigned_to": task[8],
                            "created_at": task[9],
                            "last_executed_at": task[10],
                            "execution_count": task[11]
                        }
                    }
                else:
                    return {"error": "Task not found"}
            else:
                # Get all pending tasks
                cursor.execute('''
                    SELECT id, title, objective, priority, status
                    FROM dynamic_tasks 
                    WHERE status = 'pending' 
                    ORDER BY 
                        CASE priority 
                            WHEN 'critical' THEN 1 
                            WHEN 'high' THEN 2 
                            WHEN 'medium' THEN 3 
                            WHEN 'low' THEN 4 
                        END,
                        created_at ASC
                    LIMIT 10
                ''')
                
                tasks = cursor.fetchall()
                return {
                    "tasks": [
                        {
                            "id": task[0],
                            "title": task[1],
                            "objective": task[2],
                            "priority": task[3],
                            "status": task[4]
                        }
                        for task in tasks
                    ]
                }
                
        except Exception as e:
            logger.error(f"Failed to get task context: {e}")
            return {"error": str(e)}
        finally:
            if 'conn' in locals():
                conn.close()
    
    def create_aider_request_for_task(self, task_id: str) -> Dict[str, Any]:
        """Create an Aider request based on a specific task"""
        try:
            task_context = self.get_task_context(task_id)
            if "error" in task_context:
                return task_context
            
            task = task_context["task"]
            
            # Build comprehensive request for Aider
            request_parts = [
                f"Task: {task['title']}",
                f"Objective: {task['objective']}",
                "",
                "Requirements:"
            ]
            
            for i, req in enumerate(task['requirements'], 1):
                request_parts.append(f"{i}. {req}")
            
            if task['constraints']:
                request_parts.extend(["", "Constraints:"])
                for i, constraint in enumerate(task['constraints'], 1):
                    request_parts.append(f"{i}. {constraint}")
            
            if task['acceptance_criteria']:
                request_parts.extend(["", "Acceptance Criteria:"])
                for i, criteria in enumerate(task['acceptance_criteria'], 1):
                    request_parts.append(f"{i}. {criteria}")
            
            request_parts.extend([
                "",
                "Please implement this task by:",
                "1. Analyzing the current codebase structure",
                "2. Creating or modifying the necessary files",
                "3. Following the requirements and constraints",
                "4. Ensuring the acceptance criteria are met",
                "5. Adding appropriate error handling and logging",
                "6. Including any necessary tests or documentation"
            ])
            
            # Get relevant context files
            context_files = self._get_relevant_context_files(task)
            
            return {
                "request": "\n".join(request_parts),
                "context_files": context_files,
                "task_id": task_id,
                "priority": task['priority']
            }
            
        except Exception as e:
            logger.error(f"Failed to create Aider request: {e}")
            return {"error": str(e)}
    
    def _get_relevant_context_files(self, task: Dict[str, Any]) -> List[str]:
        """Get relevant context files based on task content"""
        context_files = []
        
        # Always include core files
        core_files = [
            "/app/routers/developer.py",
            "/app/ai_client.py",
            "/app/route_llm.py"
        ]
        
        for file_path in core_files:
            if os.path.exists(file_path):
                context_files.append(file_path)
        
        # Add task-specific files based on keywords
        task_content = f"{task['title']} {task['objective']}".lower()
        
        if "api" in task_content or "endpoint" in task_content:
            context_files.extend([
                "/app/routers/developer.py",
                "/app/main.py"
            ])
        
        if "database" in task_content or "db" in task_content:
            context_files.extend([
                "/app/data/zoe.db"
            ])
        
        if "ui" in task_content or "frontend" in task_content:
            context_files.extend([
                "/app/services/zoe-ui/dist/index.html",
                "/app/templates/main-ui/calendar.html"
            ])
        
        if "ai" in task_content or "llm" in task_content:
            context_files.extend([
                "/app/ai_client.py",
                "/app/route_llm.py",
                "/app/llm_models.py"
            ])
        
        if "backup" in task_content:
            context_files.extend([
                "/app/backup_system.py"
            ])
        
        if "test" in task_content:
            context_files.extend([
                "/app/self_test_suite.py"
            ])
        
        # Remove duplicates and non-existent files
        context_files = list(set([f for f in context_files if os.path.exists(f)]))
        
        return context_files
    
    def execute_task_with_aider(self, task_id: str, model: str = "ollama/llama3.2") -> Dict[str, Any]:
        """Execute a task using Aider"""
        try:
            # Create Aider request
            aider_request = self.create_aider_request_for_task(task_id)
            if "error" in aider_request:
                return aider_request
            
            # Import Aider integration
            from aider_integration import aider_integration
            
            # Execute with Aider
            result = aider_integration.generate_code(
                request=aider_request["request"],
                context_files=aider_request["context_files"],
                model=model
            )
            
            # Add task context to result
            result["task_context"] = {
                "task_id": task_id,
                "priority": aider_request["priority"],
                "context_files_used": len(aider_request["context_files"])
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to execute task with Aider: {e}")
            return {"error": str(e)}
    
    def get_next_task_for_aider(self) -> Optional[Dict[str, Any]]:
        """Get the next high-priority task for Aider to work on"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get highest priority pending task
            cursor.execute('''
                SELECT id, title, objective, priority
                FROM dynamic_tasks 
                WHERE status = 'pending' 
                ORDER BY 
                    CASE priority 
                        WHEN 'critical' THEN 1 
                        WHEN 'high' THEN 2 
                        WHEN 'medium' THEN 3 
                        WHEN 'low' THEN 4 
                    END,
                    created_at ASC
                LIMIT 1
            ''')
            
            task = cursor.fetchone()
            if task:
                return {
                    "task_id": task[0],
                    "title": task[1],
                    "objective": task[2],
                    "priority": task[3]
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get next task: {e}")
            return None
        finally:
            if 'conn' in locals():
                conn.close()
    
    def update_task_status(self, task_id: str, status: str, notes: str = None) -> bool:
        """Update task status after Aider execution"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if notes:
                cursor.execute('''
                    UPDATE dynamic_tasks 
                    SET status = ?, last_executed_at = datetime('now'), 
                        execution_count = execution_count + 1
                    WHERE id = ?
                ''', (status, task_id))
            else:
                cursor.execute('''
                    UPDATE dynamic_tasks 
                    SET status = ?, last_executed_at = datetime('now'), 
                        execution_count = execution_count + 1
                    WHERE id = ?
                ''', (status, task_id))
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to update task status: {e}")
            return False
        finally:
            if 'conn' in locals():
                conn.close()
    
    def get_aider_work_summary(self) -> Dict[str, Any]:
        """Get summary of Aider's work on tasks"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get task statistics
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_tasks,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
                    AVG(execution_count) as avg_executions
                FROM dynamic_tasks
            ''')
            
            stats = cursor.fetchone()
            
            # Get recent Aider work
            cursor.execute('''
                SELECT id, title, status, last_executed_at, execution_count
                FROM dynamic_tasks 
                WHERE last_executed_at IS NOT NULL
                ORDER BY last_executed_at DESC
                LIMIT 5
            ''')
            
            recent_work = cursor.fetchall()
            
            return {
                "statistics": {
                    "total_tasks": stats[0],
                    "completed": stats[1],
                    "pending": stats[2],
                    "in_progress": stats[3],
                    "avg_executions": round(stats[4] or 0, 2)
                },
                "recent_work": [
                    {
                        "task_id": work[0],
                        "title": work[1],
                        "status": work[2],
                        "last_executed": work[3],
                        "execution_count": work[4]
                    }
                    for work in recent_work
                ]
            }
            
        except Exception as e:
            logger.error(f"Failed to get work summary: {e}")
            return {"error": str(e)}
        finally:
            if 'conn' in locals():
                conn.close()

# Global instance
aider_task_integration = AiderTaskIntegration()
