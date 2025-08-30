#!/bin/bash
# Fix database path issue

echo "Checking database paths..."

# First, find where the database actually is
docker exec zoe-core find / -name "zoe.db" 2>/dev/null

# Check what the API is using
docker exec zoe-core python3 -c "
import os
print('DB_PATH from env:', os.getenv('DATABASE_PATH', 'not set'))
print('Default path: /app/data/zoe.db')
print('File exists at /app/data/zoe.db:', os.path.exists('/app/data/zoe.db'))
print('File exists at /data/zoe.db:', os.path.exists('/data/zoe.db'))
"

# Create tables in the CORRECT location
echo -e "\nCreating tables in correct database..."
docker exec zoe-core python3 << 'PYTHON'
import sqlite3
import os
import uuid

# Try different possible paths
paths = ['/app/data/zoe.db', '/data/zoe.db']

for db_path in paths:
    if os.path.exists(db_path):
        print(f"\nFound database at: {db_path}")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Drop and recreate
        cursor.execute("DROP TABLE IF EXISTS tasks")
        cursor.execute("DROP TABLE IF EXISTS task_conversations")
        cursor.execute("DROP TABLE IF EXISTS code_approvals")
        
        # Create with correct schema
        cursor.execute("""
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                task_type TEXT DEFAULT 'feature',
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
            )
        """)
        
        cursor.execute("""
            CREATE TABLE task_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                code_snippet TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE code_approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                approved INTEGER,
                comments TEXT,
                approved_by TEXT DEFAULT 'developer',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        
        # Test insert
        test_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
        cursor.execute("""
            INSERT INTO tasks (task_id, title, priority)
            VALUES (?, 'Test Task', 'low')
        """)
        conn.commit()
        
        # Verify
        cursor.execute("SELECT COUNT(*) FROM tasks")
        count = cursor.fetchone()[0]
        print(f"Tasks in database: {count}")
        
        cursor.execute("PRAGMA table_info(tasks)")
        cols = cursor.fetchall()
        print("Columns:", [col[1] for col in cols])
        
        conn.close()
PYTHON

# Test the API again
echo -e "\nTesting API..."
curl -s -X POST http://localhost:8000/api/tasks/ \
    -H "Content-Type: application/json" \
    -d '{"title": "Database Test", "priority": "high"}' | python3 -m json.tool

curl -sL http://localhost:8000/api/tasks | python3 -m json.tool
