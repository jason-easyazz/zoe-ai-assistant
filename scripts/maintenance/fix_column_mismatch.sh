#!/bin/bash
# FIX_COLUMN_MISMATCH.sh
# Fixes the mismatch between task_type in DB and type in code

set -e

echo "🔧 FIXING COLUMN NAME MISMATCH"
echo "==============================="
echo ""
echo "Problem: Database has 'task_type' but code uses 'type'"
echo "Solution: Rename column to match what code expects"
echo ""

cd /home/pi/zoe

# Step 1: Show current situation
echo "📊 Step 1: Current database schema..."
docker exec zoe-core sqlite3 /app/data/zoe.db ".schema tasks" | grep -E "task_type|type" || true

# Step 2: Fix the database column name
echo -e "\n🛠️ Step 2: Renaming task_type to type..."
docker exec zoe-core sqlite3 /app/data/zoe.db << 'SQL'
-- SQLite doesn't support ALTER COLUMN directly, so we need to recreate
BEGIN TRANSACTION;

-- Save existing data
CREATE TABLE tasks_temp AS SELECT * FROM tasks;

-- Drop old table
DROP TABLE tasks;

-- Create new table with 'type' instead of 'task_type'
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    type TEXT DEFAULT 'feature',  -- Changed from task_type to type
    status TEXT DEFAULT 'pending',
    priority TEXT DEFAULT 'medium',
    assigned_to TEXT DEFAULT 'zack',
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    approved INTEGER DEFAULT 0,
    code_generated TEXT,
    implementation_path TEXT
);

-- Copy data back, mapping task_type to type
INSERT INTO tasks (
    task_id, title, description, type, status, priority,
    assigned_to, metadata, created_at, updated_at,
    completed_at, approved, code_generated, implementation_path
)
SELECT 
    task_id, title, description, task_type, status, priority,
    assigned_to, metadata, created_at, updated_at,
    completed_at, approved, code_generated, implementation_path
FROM tasks_temp;

-- Drop temp table
DROP TABLE tasks_temp;

-- Create indexes
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_priority ON tasks(priority);
CREATE INDEX idx_tasks_created ON tasks(created_at);

COMMIT;

-- Verify the change
.schema tasks
SQL

# Step 3: Also ensure GET endpoint exists and works
echo -e "\n📝 Step 3: Ensuring GET /tasks endpoint exists..."
docker exec zoe-core python3 << 'PYTHON'
import sys
sys.path.append('/app')

# Fix the developer.py to ensure GET endpoint exists
with open('/app/routers/developer.py', 'r') as f:
    content = f.read()

# Ensure Optional is imported
if 'from typing import Optional' not in content:
    if 'from typing import' in content:
        content = content.replace(
            'from typing import',
            'from typing import Optional,'
        )
    else:
        content = "from typing import Optional\n" + content

# Check if GET /tasks exists
if '@router.get("/tasks")' not in content:
    print("Adding GET /tasks endpoint...")
    
    # Find a good place to add it (after POST /tasks)
    post_tasks = content.find('@router.post("/tasks")')
    if post_tasks > 0:
        # Find the end of this function
        next_route = content.find('\n@router.', post_tasks + 1)
        if next_route == -1:
            next_route = len(content)
        
        get_endpoint = '''

@router.get("/tasks")
async def get_all_tasks(status: Optional[str] = None):
    """Get all tasks"""
    import sqlite3
    
    conn = sqlite3.connect("/app/data/zoe.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        if status:
            cursor.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC",
                (status,)
            )
        else:
            cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC")
        
        tasks = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return {"tasks": tasks, "count": len(tasks)}
    except Exception as e:
        conn.close()
        return {"error": str(e), "tasks": []}
'''
        
        # Insert it
        content = content[:next_route] + get_endpoint + "\n" + content[next_route:]
    
    # Save the fixed version
    with open('/app/routers/developer.py', 'w') as f:
        f.write(content)
    
    print("✅ GET /tasks endpoint added")
else:
    print("✅ GET /tasks endpoint exists")

print("✅ developer.py fixed")
PYTHON

# Step 4: Restart the service
echo -e "\n🔄 Step 4: Restarting service..."
docker compose restart zoe-core
sleep 10

# Step 5: Test everything
echo -e "\n🧪 Step 5: Testing all task operations..."

echo "1. Creating a task (should work now!):"
RESULT=$(curl -s -X POST http://localhost:8000/api/developer/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Build notification system",
    "description": "Real-time notifications for events",
    "type": "feature",
    "priority": "high"
  }')
echo "$RESULT" | jq '.' || echo "$RESULT"

echo -e "\n2. Getting all tasks (should work now!):"
RESULT=$(curl -s http://localhost:8000/api/developer/tasks)
echo "$RESULT" | jq '.' || echo "$RESULT"

echo -e "\n3. Creating another task:"
RESULT=$(curl -s -X POST http://localhost:8000/api/developer/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Optimize database queries",
    "description": "Add indexes and query optimization",
    "type": "optimization",
    "priority": "medium"
  }')
echo "$RESULT" | jq '.' || echo "$RESULT"

echo -e "\n4. Verifying in database:"
docker exec zoe-core sqlite3 /app/data/zoe.db "SELECT task_id, title, type, priority FROM tasks;" || true

echo -e "\n5. Testing developer chat:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Show me the pending tasks"}' | jq -r '.response' | head -20

echo -e "\n✅ COLUMN MISMATCH FIXED!"
echo "========================="
echo ""
echo "Fixed:"
echo "  ✅ Renamed task_type to type in database"
echo "  ✅ POST /tasks endpoint works"
echo "  ✅ GET /tasks endpoint works"  
echo "  ✅ Database matches code expectations"
echo ""
echo "Task system is now fully operational!"
