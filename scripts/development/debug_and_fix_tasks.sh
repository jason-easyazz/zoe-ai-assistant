#!/bin/bash
# Debug and fix tasks database

echo "Debugging Tasks Database"
echo "========================"

# First, check what tables and columns actually exist
docker exec zoe-core python3 << 'PYTHON'
import sqlite3

conn = sqlite3.connect("/app/data/zoe.db")
cursor = conn.cursor()

print("Current tables:")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
for table in cursor.fetchall():
    print(f"  - {table[0]}")
    cursor.execute(f"PRAGMA table_info({table[0]})")
    columns = cursor.fetchall()
    for col in columns:
        print(f"      {col[1]} ({col[2]})")

conn.close()
PYTHON

echo -e "\nRecreating tasks tables with correct schema..."

# Now recreate with the EXACT schema needed
docker exec zoe-core python3 << 'PYTHON'
import sqlite3

conn = sqlite3.connect("/app/data/zoe.db")
cursor = conn.cursor()

# Drop existing tables
for table in ['tasks', 'task_conversations', 'code_approvals']:
    cursor.execute(f"DROP TABLE IF EXISTS {table}")

# Create tasks table - note task_id is TEXT PRIMARY KEY
sql = """
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
"""
cursor.execute(sql)
print("Created tasks table")

# Create conversations table
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
print("Created task_conversations table")

# Create approvals table
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
print("Created code_approvals table")

conn.commit()

# Verify the schema
print("\nVerifying tasks table schema:")
cursor.execute("PRAGMA table_info(tasks)")
for col in cursor.fetchall():
    print(f"  {col[1]}: {col[2]}")

# Test insert
import uuid
test_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
cursor.execute("""
    INSERT INTO tasks (task_id, title, description, task_type, priority)
    VALUES (?, ?, ?, ?, ?)
""", (test_id, "Test Task", "Testing the schema", "test", "low"))
conn.commit()

cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (test_id,))
result = cursor.fetchone()
if result:
    print(f"\n✅ Test insert successful! Task ID: {test_id}")
else:
    print("\n❌ Test insert failed!")

conn.close()
PYTHON

# Now test the API
echo -e "\nTesting API endpoints..."

echo "1. Create a real task:"
curl -s -X POST http://localhost:8000/api/tasks/ \
    -H "Content-Type: application/json" \
    -d '{
        "title": "Implement user authentication",
        "description": "Add JWT authentication system",
        "task_type": "feature",
        "priority": "high"
    }' | jq '.'

echo -e "\n2. List all tasks:"
curl -s http://localhost:8000/api/tasks/ | jq '.'

echo -e "\n3. Get statistics (checking raw output first):"
curl -s http://localhost:8000/api/tasks/stats/summary

echo -e "\n\n✅ Debug complete!"
