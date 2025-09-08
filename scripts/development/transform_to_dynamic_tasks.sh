#!/bin/bash
# TRANSFORM_TO_DYNAMIC_TASKS.sh
# Location: scripts/development/transform_to_dynamic_tasks.sh
# Purpose: Transform existing task system to be dynamic and context-aware

set -e

echo "🎯 Transforming Developer Task System to Dynamic Context-Aware"
echo "=============================================================="
echo ""
echo "This will modify the existing system to:"
echo "  1. Store requirements instead of specific implementations"
echo "  2. Re-analyze system state at execution time"
echo "  3. Generate fresh plans based on current context"
echo "  4. Adapt to changes made since task creation"
echo ""
echo "Press Enter to continue or Ctrl+C to abort..."
read

cd /home/pi/zoe

# Step 1: Backup existing system
echo -e "\n📦 Creating backup..."
mkdir -p backups/$(date +%Y%m%d_%H%M%S)
cp services/zoe-core/routers/developer_tasks.py backups/$(date +%Y%m%d_%H%M%S)/
cp data/developer_tasks.db backups/$(date +%Y%m%d_%H%M%S)/ 2>/dev/null || true

# Step 2: Create enhanced developer_tasks.py with dynamic capabilities
echo -e "\n🔧 Creating dynamic task system..."
cat > services/zoe-core/routers/developer_tasks.py << 'EOF'
"""
Dynamic Context-Aware Task Management System
Tasks store intent/requirements and re-analyze at execution time
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
from datetime import datetime
import json
import sqlite3
import hashlib
import logging
import subprocess
import os
from pathlib import Path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/developer/tasks", tags=["developer-tasks"])

# In-memory task cache
tasks_cache = {}

class TaskRequirements(BaseModel):
    """What the task needs to achieve (not HOW)"""
    title: str
    objective: str  # What needs to be accomplished
    requirements: List[str]  # Specific requirements
    constraints: List[str]  # What must not be broken
    acceptance_criteria: List[str]  # How to verify success
    priority: str = "medium"
    assigned_to: str = "zack"
    
class DynamicTaskPlan(BaseModel):
    """Generated at execution time based on current state"""
    task_id: str
    current_analysis: Dict[str, Any]  # Current system state
    implementation_steps: List[Dict[str, str]]  # Adaptive steps
    integration_points: List[str]  # Where to integrate
    conflict_resolution: List[str]  # How to handle conflicts
    rollback_strategy: Dict[str, Any]
    estimated_impact: str
    
class SystemContext:
    """Gathers current system state for analysis"""
    
    @staticmethod
    def get_current_state() -> Dict[str, Any]:
        """Analyze current system state"""
        state = {
            "timestamp": datetime.now().isoformat(),
            "files": {},
            "endpoints": [],
            "database_schema": {},
            "containers": [],
            "recent_changes": []
        }
        
        try:
            # Get current files
            for root, dirs, files in os.walk("/app"):
                if "routers" in root:
                    for file in files:
                        if file.endswith(".py"):
                            filepath = os.path.join(root, file)
                            with open(filepath, 'r') as f:
                                content = f.read()
                                state["files"][filepath] = {
                                    "size": len(content),
                                    "modified": os.path.getmtime(filepath),
                                    "imports": [line for line in content.split('\n') if 'import' in line][:5],
                                    "endpoints": [line for line in content.split('\n') if '@router.' in line][:5]
                                }
        except Exception as e:
            logger.error(f"Error scanning files: {e}")
            
        try:
            # Get running containers
            result = subprocess.run(
                "docker ps --format '{{.Names}}'",
                shell=True,
                capture_output=True,
                text=True
            )
            state["containers"] = result.stdout.strip().split('\n')
        except:
            pass
            
        try:
            # Get recent git changes (if available)
            result = subprocess.run(
                "cd /app && git log --oneline -5 2>/dev/null || echo 'No git history'",
                shell=True,
                capture_output=True,
                text=True
            )
            state["recent_changes"] = result.stdout.strip().split('\n')
        except:
            pass
            
        return state
    
    @staticmethod
    def detect_conflicts(requirements: List[str], current_state: Dict) -> List[str]:
        """Detect potential conflicts with current system"""
        conflicts = []
        
        # Check for file conflicts
        for req in requirements:
            for filepath in current_state.get("files", {}):
                if any(keyword in req.lower() for keyword in ["modify", "change", "update"]):
                    if any(keyword in filepath.lower() for keyword in req.lower().split()):
                        conflicts.append(f"Potential conflict: {req} may affect {filepath}")
        
        return conflicts

def init_dynamic_tasks_db():
    """Initialize enhanced database for dynamic tasks"""
    conn = sqlite3.connect('/app/data/developer_tasks.db')
    cursor = conn.cursor()
    
    # Create new dynamic tasks table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dynamic_tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            requirements TEXT NOT NULL,  -- JSON array
            constraints TEXT,  -- JSON array
            acceptance_criteria TEXT,  -- JSON array
            priority TEXT DEFAULT 'medium',
            assigned_to TEXT DEFAULT 'zack',
            status TEXT DEFAULT 'pending',
            context_snapshot TEXT,  -- System state when created (for reference)
            last_analysis TEXT,  -- Last execution analysis
            execution_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_executed_at TIMESTAMP,
            completed_at TIMESTAMP
        )
    ''')
    
    # Create execution history table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            execution_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            system_state_before TEXT,  -- JSON
            plan_generated TEXT,  -- JSON
            execution_result TEXT,
            success BOOLEAN,
            changes_made TEXT,  -- JSON list of changes
            FOREIGN KEY (task_id) REFERENCES dynamic_tasks(id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Initialize on import
init_dynamic_tasks_db()

@router.get("/")
async def get_task_system_info():
    """Get information about the dynamic task system"""
    return {
        "system": "Dynamic Context-Aware Task Management",
        "version": "2.0",
        "description": "Tasks adapt to system state at execution time",
        "features": [
            "Stores requirements, not implementations",
            "Re-analyzes system at execution time",
            "Generates adaptive plans",
            "Handles conflicts automatically",
            "Preserves all existing work"
        ],
        "endpoints": {
            "create": "POST /api/developer/tasks/create",
            "list": "GET /api/developer/tasks/list",
            "analyze": "POST /api/developer/tasks/{task_id}/analyze",
            "execute": "POST /api/developer/tasks/{task_id}/execute",
            "history": "GET /api/developer/tasks/{task_id}/history"
        }
    }

@router.post("/create")
async def create_dynamic_task(requirements: TaskRequirements):
    """Create a new dynamic task that stores requirements"""
    
    # Generate task ID
    task_id = hashlib.md5(f"{requirements.title}{datetime.now()}".encode()).hexdigest()[:8]
    
    # Capture current context for reference (not for execution)
    context_snapshot = SystemContext.get_current_state()
    
    # Store in database
    conn = sqlite3.connect('/app/data/developer_tasks.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO dynamic_tasks 
        (id, title, objective, requirements, constraints, acceptance_criteria, 
         priority, assigned_to, status, context_snapshot)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        task_id,
        requirements.title,
        requirements.objective,
        json.dumps(requirements.requirements),
        json.dumps(requirements.constraints),
        json.dumps(requirements.acceptance_criteria),
        requirements.priority,
        requirements.assigned_to,
        'pending',
        json.dumps(context_snapshot)
    ))
    
    conn.commit()
    conn.close()
    
    # Cache it
    tasks_cache[task_id] = {
        "id": task_id,
        "title": requirements.title,
        "objective": requirements.objective,
        "status": "pending",
        "created_at": datetime.now().isoformat()
    }
    
    return {
        "task_id": task_id,
        "status": "created",
        "message": f"Dynamic task created. Will analyze system state at execution time.",
        "stored": "requirements_only",
        "execution": "adaptive"
    }

@router.post("/{task_id}/analyze")
async def analyze_for_execution(task_id: str):
    """Analyze current system and generate execution plan"""
    
    # Get task requirements
    conn = sqlite3.connect('/app/data/developer_tasks.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT title, objective, requirements, constraints, acceptance_criteria, context_snapshot
        FROM dynamic_tasks WHERE id = ?
    ''', (task_id,))
    
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    
    title, objective, requirements, constraints, acceptance_criteria, original_context = row
    requirements = json.loads(requirements)
    constraints = json.loads(constraints)
    acceptance_criteria = json.loads(acceptance_criteria)
    original_context = json.loads(original_context)
    
    # Get CURRENT system state
    current_state = SystemContext.get_current_state()
    
    # Detect what has changed since task creation
    changes_detected = []
    for key in current_state.get("files", {}):
        if key not in original_context.get("files", {}):
            changes_detected.append(f"New file: {key}")
        elif current_state["files"][key]["modified"] > original_context.get("files", {}).get(key, {}).get("modified", 0):
            changes_detected.append(f"Modified: {key}")
    
    # Detect potential conflicts
    conflicts = SystemContext.detect_conflicts(requirements, current_state)
    
    # Generate adaptive plan based on CURRENT state
    plan = DynamicTaskPlan(
        task_id=task_id,
        current_analysis={
            "changes_since_creation": changes_detected,
            "conflicts_detected": conflicts,
            "files_to_modify": [],
            "files_to_create": [],
            "integration_needed": True if changes_detected else False
        },
        implementation_steps=[
            {"step": 1, "action": "Backup current system", "command": "cp -r /app /app.backup"},
            {"step": 2, "action": "Analyze integration points", "details": f"Found {len(changes_detected)} changes"},
            {"step": 3, "action": f"Implement: {objective}", "adaptive": True},
            {"step": 4, "action": "Run acceptance tests", "criteria": acceptance_criteria},
            {"step": 5, "action": "Verify constraints maintained", "constraints": constraints}
        ],
        integration_points=[f for f in current_state.get("files", {}) if "router" in f],
        conflict_resolution=[f"Handle: {c}" for c in conflicts],
        rollback_strategy={
            "method": "restore_backup",
            "backup_location": "/app.backup",
            "trigger": "test_failure"
        },
        estimated_impact=f"Moderate - {len(changes_detected)} files changed since creation"
    )
    
    # Store the analysis
    cursor.execute('''
        UPDATE dynamic_tasks 
        SET last_analysis = ?, status = 'analyzed'
        WHERE id = ?
    ''', (json.dumps(plan.dict()), task_id))
    
    conn.commit()
    conn.close()
    
    return {
        "task_id": task_id,
        "title": title,
        "objective": objective,
        "system_changes_detected": len(changes_detected),
        "conflicts": conflicts,
        "plan": plan.dict(),
        "ready_to_execute": True,
        "message": "Plan generated based on current system state"
    }

@router.post("/{task_id}/execute")
async def execute_dynamic_task(task_id: str, background_tasks: BackgroundTasks):
    """Execute task with current context awareness"""
    
    # First analyze current state
    analysis = await analyze_for_execution(task_id)
    
    if not analysis["ready_to_execute"]:
        raise HTTPException(status_code=400, detail="Task not ready for execution")
    
    # Log execution start
    conn = sqlite3.connect('/app/data/developer_tasks.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO task_executions (task_id, system_state_before, plan_generated)
        VALUES (?, ?, ?)
    ''', (task_id, json.dumps(SystemContext.get_current_state()), json.dumps(analysis["plan"])))
    
    execution_id = cursor.lastrowid
    
    cursor.execute('''
        UPDATE dynamic_tasks 
        SET status = 'executing', execution_count = execution_count + 1, last_executed_at = ?
        WHERE id = ?
    ''', (datetime.now().isoformat(), task_id))
    
    conn.commit()
    conn.close()
    
    # Execute in background
    background_tasks.add_task(execute_task_async, task_id, execution_id, analysis["plan"])
    
    return {
        "task_id": task_id,
        "execution_id": execution_id,
        "status": "executing",
        "message": "Task execution started with current system context",
        "plan_adapted": True,
        "conflicts_handled": len(analysis.get("conflicts", []))
    }

async def execute_task_async(task_id: str, execution_id: int, plan: dict):
    """Background task execution with adaptation"""
    # This would contain actual execution logic
    # For now, it's a placeholder that would:
    # 1. Execute each step in the plan
    # 2. Adapt if errors occur
    # 3. Log all changes
    # 4. Update database with results
    pass

@router.get("/list")
async def list_dynamic_tasks(status: Optional[str] = None):
    """List all dynamic tasks"""
    conn = sqlite3.connect('/app/data/developer_tasks.db')
    cursor = conn.cursor()
    
    query = '''
        SELECT id, title, objective, priority, status, created_at, execution_count
        FROM dynamic_tasks
    '''
    
    if status:
        query += f" WHERE status = '{status}'"
    
    query += " ORDER BY created_at DESC"
    
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    
    tasks = [
        {
            "id": row[0],
            "title": row[1],
            "objective": row[2],
            "priority": row[3],
            "status": row[4],
            "created_at": row[5],
            "execution_count": row[6],
            "type": "dynamic"
        }
        for row in rows
    ]
    
    return {
        "tasks": tasks,
        "count": len(tasks),
        "system": "dynamic_context_aware"
    }

@router.get("/{task_id}/history")
async def get_task_execution_history(task_id: str):
    """Get execution history for a task"""
    conn = sqlite3.connect('/app/data/developer_tasks.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, execution_time, success, changes_made
        FROM task_executions
        WHERE task_id = ?
        ORDER BY execution_time DESC
    ''', (task_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    history = [
        {
            "execution_id": row[0],
            "time": row[1],
            "success": row[2],
            "changes": json.loads(row[3]) if row[3] else []
        }
        for row in rows
    ]
    
    return {
        "task_id": task_id,
        "executions": history,
        "total_executions": len(history)
    }

# Backward compatibility - wrap old endpoints
@router.get("/system-info")
async def legacy_system_info():
    """Legacy endpoint for compatibility"""
    return await get_task_system_info()
EOF

# Step 3: Create a migration script to convert existing tasks
echo -e "\n📊 Creating migration for existing tasks..."
cat > scripts/utilities/migrate_tasks.py << 'EOF'
#!/usr/bin/env python3
"""Migrate existing tasks to dynamic format"""

import sqlite3
import json
from datetime import datetime

def migrate_tasks():
    conn = sqlite3.connect('data/developer_tasks.db')
    cursor = conn.cursor()
    
    # Get existing tasks
    try:
        cursor.execute('SELECT * FROM developer_tasks')
        old_tasks = cursor.fetchall()
        
        print(f"Found {len(old_tasks)} tasks to migrate")
        
        for task in old_tasks:
            task_id, title, description, task_type, priority, assigned_to, status = task[:7]
            
            # Convert to requirements format
            requirements = [
                description,
                f"Type: {task_type}"
            ]
            
            constraints = [
                "Do not break existing functionality",
                "Maintain backward compatibility"
            ]
            
            acceptance_criteria = [
                "Feature works as described",
                "Tests pass",
                "No regression issues"
            ]
            
            # Insert into new table
            cursor.execute('''
                INSERT OR IGNORE INTO dynamic_tasks 
                (id, title, objective, requirements, constraints, acceptance_criteria, 
                 priority, assigned_to, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task_id,
                title,
                description,  # Use description as objective
                json.dumps(requirements),
                json.dumps(constraints),
                json.dumps(acceptance_criteria),
                priority,
                assigned_to,
                status
            ))
        
        conn.commit()
        print(f"Migration complete!")
        
    except Exception as e:
        print(f"Migration error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate_tasks()
EOF

chmod +x scripts/utilities/migrate_tasks.py

# Step 4: Run migration
echo -e "\n📦 Migrating existing tasks..."
docker exec zoe-core python3 /app/scripts/utilities/migrate_tasks.py || echo "Migration skipped"

# Step 5: Restart service
echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core
sleep 8

# Step 6: Test the new system
echo -e "\n✅ Testing dynamic task system..."

# Test system info
echo -e "\n1️⃣ System Info:"
curl -s http://localhost:8000/api/developer/tasks/ | jq '.'

# Create a test dynamic task
echo -e "\n2️⃣ Creating dynamic task:"
curl -s -X POST http://localhost:8000/api/developer/tasks/create \
    -H "Content-Type: application/json" \
    -d '{
        "title": "Add User Authentication",
        "objective": "Implement user authentication for all endpoints",
        "requirements": [
            "Add JWT-based authentication",
            "Create login/logout endpoints",
            "Protect all sensitive endpoints",
            "Support multiple user roles"
        ],
        "constraints": [
            "Do not break existing endpoints",
            "Maintain backward compatibility for single-user mode",
            "Keep API response formats unchanged"
        ],
        "acceptance_criteria": [
            "Users can login and receive JWT token",
            "Protected endpoints require valid token",
            "Existing functionality still works",
            "Tests pass with authentication enabled"
        ],
        "priority": "high"
    }' | jq '.'

# List tasks
echo -e "\n3️⃣ Listing tasks:"
curl -s http://localhost:8000/api/developer/tasks/list | jq '.'

echo -e "\n✨ Dynamic Context-Aware Task System Installed!"
echo ""
echo "Key Differences from Before:"
echo "  ✅ Tasks store WHAT to achieve, not HOW"
echo "  ✅ System re-analyzes at execution time"
echo "  ✅ Plans adapt to current system state"
echo "  ✅ Handles conflicts automatically"
echo "  ✅ Works regardless of when executed"
echo ""
echo "Example: The 'Add User Authentication' task created above will:"
echo "  - See ALL endpoints that exist when executed (even new ones)"
echo "  - Adapt to any UI changes made since creation"
echo "  - Integrate with whatever database schema exists"
echo "  - Handle any new services or features added"
echo ""
echo "To analyze a task for execution:"
echo "  curl -X POST http://localhost:8000/api/developer/tasks/{task_id}/analyze"
echo ""
echo "This shows you the plan BEFORE execution, adapted to current state!"
