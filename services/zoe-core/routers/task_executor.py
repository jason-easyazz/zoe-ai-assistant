"""
Task Executor Module - Executes tasks with various step types
"""
import os
import json
import subprocess
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

class TaskExecutor:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.execution_id = None
        self.database_path = "/app/data/developer_tasks.db"
        self.backup_dir = Path("/app/backups")
        self.changes_made = []
        self.execution_log = []
        
    def execute_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a task plan with multiple steps"""
        try:
            self.execution_id = self._create_execution_record()
            
            # Extract steps from plan
            steps = plan.get('steps', [])
            if not steps:
                # Fallback for old format
                steps = self._convert_old_format(plan)
            
            self.log(f"Executing {len(steps)} steps for task {self.task_id}")
            
            # Execute each step
            for i, step in enumerate(steps):
                self.log(f"Step {i+1}: {step.get('description', 'No description')}")
                success = self._execute_step(step)
                if not success:
                    self.log(f"Step {i+1} failed, stopping execution")
                    return self._finalize_execution(False)
            
            # All steps succeeded
            return self._finalize_execution(True)
            
        except Exception as e:
            self.log(f"Execution error: {str(e)}")
            return self._finalize_execution(False, str(e))
    
    def _execute_step(self, step: Dict[str, Any]) -> bool:
        """Execute a single step based on its type"""
        step_type = step.get('type', 'shell')
        
        try:
            if step_type == 'shell':
                return self._execute_shell(step)
            elif step_type == 'file_create':
                return self._execute_file_create(step)
            elif step_type == 'file_modify':
                return self._execute_file_modify(step)
            elif step_type == 'test':
                return self._execute_test(step)
            elif step_type == 'backup':
                return self._execute_backup(step)
            else:
                self.log(f"Unknown step type: {step_type}")
                return True  # Skip unknown steps
                
        except Exception as e:
            self.log(f"Step execution error: {str(e)}")
            return False
    
    def _execute_shell(self, step: Dict[str, Any]) -> bool:
        """Execute a shell command"""
        command = step.get('command', '')
        if not command:
            return True
            
        try:
            # Execute in the container context
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd='/app'
            )
            
            self.log(f"Shell command: {command}")
            if result.returncode == 0:
                self.changes_made.append(f"Executed: {command}")
                return True
            else:
                self.log(f"Command failed: {result.stderr}")
                return False
                
        except Exception as e:
            self.log(f"Shell execution error: {str(e)}")
            return False
    
    def _execute_file_create(self, step: Dict[str, Any]) -> bool:
        """Create a file with content"""
        filepath = step.get('path', '')
        content = step.get('content', '')
        
        if not filepath:
            return True
            
        try:
            # Ensure directory exists
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            
            # Write content
            with open(filepath, 'w') as f:
                f.write(content)
            
            self.changes_made.append(f"Created file: {filepath}")
            self.log(f"Created file: {filepath}")
            return True
            
        except Exception as e:
            self.log(f"File creation error: {str(e)}")
            return False
    
    def _execute_file_modify(self, step: Dict[str, Any]) -> bool:
        """Modify an existing file"""
        filepath = step.get('path', '')
        old_content = step.get('old', '')
        new_content = step.get('new', '')
        
        if not filepath:
            return True
            
        try:
            # Read current content
            with open(filepath, 'r') as f:
                content = f.read()
            
            # Replace content
            if old_content:
                content = content.replace(old_content, new_content)
            else:
                content += new_content
            
            # Write back
            with open(filepath, 'w') as f:
                f.write(content)
            
            self.changes_made.append(f"Modified file: {filepath}")
            self.log(f"Modified file: {filepath}")
            return True
            
        except Exception as e:
            self.log(f"File modification error: {str(e)}")
            return False
    
    def _execute_test(self, step: Dict[str, Any]) -> bool:
        """Execute a test command"""
        test_command = step.get('command', '')
        if not test_command:
            return True
            
        try:
            result = subprocess.run(
                test_command,
                shell=True,
                capture_output=True,
                text=True,
                cwd='/app'
            )
            
            success = result.returncode == 0
            self.log(f"Test {'passed' if success else 'failed'}: {test_command}")
            return success
            
        except Exception as e:
            self.log(f"Test execution error: {str(e)}")
            return False
    
    def _execute_backup(self, step: Dict[str, Any]) -> bool:
        """Create a backup"""
        source = step.get('source', '/app')
        
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = self.backup_dir / f"backup_{self.task_id}_{timestamp}"
            backup_path.mkdir(parents=True, exist_ok=True)
            
            # Simple copy for now
            subprocess.run(
                f"cp -r {source}/* {backup_path}/",
                shell=True,
                check=True
            )
            
            self.changes_made.append(f"Created backup: {backup_path}")
            self.log(f"Created backup: {backup_path}")
            return True
            
        except Exception as e:
            self.log(f"Backup error: {str(e)}")
            return False
    
    def _convert_old_format(self, plan: Dict[str, Any]) -> List[Dict]:
        """Convert old plan format to new step format"""
        steps = []
        
        # Convert implementation_steps to steps
        for old_step in plan.get('implementation_steps', []):
            new_step = {
                'type': 'shell',
                'description': old_step.get('action', ''),
                'command': old_step.get('command', '')
            }
            if new_step['command']:
                steps.append(new_step)
        
        return steps if steps else [
            {
                'type': 'shell',
                'description': 'Default test step',
                'command': 'echo "Task executed"'
            }
        ]
    
    def log(self, message: str):
        """Add to execution log"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.execution_log.append(f"[{timestamp}] {message}")
        print(f"[TaskExecutor] {message}")
    
    def _create_execution_record(self) -> int:
        """Create execution record in database"""
        try:
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO task_executions (task_id, execution_time, system_state_before)
                VALUES (?, ?, ?)
            """, (self.task_id, datetime.now(), json.dumps({})))
            conn.commit()
            execution_id = cursor.lastrowid
            conn.close()
            return execution_id
        except Exception as e:
            print(f"Database error: {e}")
            return 0
    
    def _finalize_execution(self, success: bool, error: str = None) -> Dict[str, Any]:
        """Finalize execution and update database"""
        result = {
            'success': success,
            'changes': self.changes_made,
            'log': self.execution_log,
            'error': error
        }
        
        # Update database
        try:
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE task_executions 
                SET success = ?, execution_result = ?, changes_made = ?
                WHERE id = ?
            """, (success, json.dumps(result), json.dumps(self.changes_made), self.execution_id))
            
            # Update task status
            status = 'completed' if success else 'failed'
            cursor.execute("""
                UPDATE dynamic_tasks 
                SET status = ?, last_executed_at = ?
                WHERE id = ?
            """, (status, datetime.now(), self.task_id))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Database update error: {e}")
        
        return result

        def execute_task(self, task_id: str, plan: dict) -> dict:
            """Wrapper for execute_plan to match interface"""
            self.task_id = task_id
            return self.execute_plan(plan)
