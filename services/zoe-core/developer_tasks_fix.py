"""
Add tasks endpoint to existing developer.py
"""
import sys
sys.path.append('/app')

# Read existing developer.py
with open('/app/routers/developer.py', 'r') as f:
    content = f.read()

# Check if tasks endpoint exists
if '@router.post("/tasks")' not in content:
    print("Adding tasks endpoint...")
    
    # Add the imports if needed
    if 'import uuid' not in content:
        content = "import uuid\n" + content
    
    # Add the task endpoint at the end
    task_endpoint = '''

# Task Management Endpoints
@router.post("/tasks")
async def create_task(task: DevelopmentTask):
    """Create a development task"""
    import uuid
    import sqlite3
    
    try:
        task_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
        
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        
        # Ensure table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                title TEXT,
                description TEXT,
                type TEXT,
                priority TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Insert task
        cursor.execute(
            "INSERT INTO tasks (task_id, title, description, type, priority) VALUES (?, ?, ?, ?, ?)",
            (task_id, task.title, task.description, task.type, task.priority)
        )
        
        conn.commit()
        conn.close()
        
        return {"task_id": task_id, "status": "created", "title": task.title}
        
    except Exception as e:
        return {"error": str(e), "status": "failed"}

@router.get("/tasks")
async def get_tasks():
    """Get all tasks"""
    import sqlite3
    
    try:
        conn = sqlite3.connect("/app/data/zoe.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50")
        tasks = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        return {"tasks": tasks, "count": len(tasks)}
        
    except Exception as e:
        return {"tasks": [], "error": str(e)}
'''
    
    # Add to the content
    content += task_endpoint
    
    # Save back
    with open('/app/routers/developer.py', 'w') as f:
        f.write(content)
    
    print("✅ Tasks endpoint added")
else:
    print("✅ Tasks endpoint already exists")

# Verify the model exists
if 'class DevelopmentTask' not in content:
    print("Adding DevelopmentTask model...")
    model_code = '''
class DevelopmentTask(BaseModel):
    title: str
    description: str
    type: str = "feature"
    priority: str = "medium"
'''
    # Find where to insert (after other models)
    import_line = content.find('from pydantic import BaseModel')
    if import_line > 0:
        # Find the next class definition
        next_line = content.find('\n\n', import_line)
        if next_line > 0:
            content = content[:next_line] + model_code + content[next_line:]
            with open('/app/routers/developer.py', 'w') as f:
                f.write(content)
            print("✅ DevelopmentTask model added")
