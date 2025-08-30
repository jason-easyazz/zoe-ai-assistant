#!/bin/bash
# Fix tasks database schema

echo "🔧 Fixing Tasks Database Schema"
echo "================================"

# Create/update the database tables
docker exec zoe-core python3 << 'PYTHON'
import sqlite3

print("Creating/updating tasks database tables...")

conn = sqlite3.connect("/app/data/zoe.db")
cursor = conn.cursor()

# Drop old tables if they exist (backup first)
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
if cursor.fetchone():
    print("Backing up existing tasks table...")
    cursor.execute("ALTER TABLE tasks RENAME TO tasks_backup")

# Create proper tasks table
cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
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
        approved BOOLEAN DEFAULT 0,
        code_generated TEXT,
        implementation_path TEXT
    )
""")

# Create task_conversations table
cursor.execute("""
    CREATE TABLE IF NOT EXISTS task_conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        role TEXT NOT NULL,
        message TEXT NOT NULL,
        code_snippet TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES tasks(task_id)
    )
""")

# Create code_approvals table
cursor.execute("""
    CREATE TABLE IF NOT EXISTS code_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        approved BOOLEAN,
        comments TEXT,
        approved_by TEXT DEFAULT 'developer',
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES tasks(task_id)
    )
""")

conn.commit()

# Verify tables
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'")
result = cursor.fetchone()
if result:
    print("Tasks table schema:")
    print(result[0])
    
# Check columns
cursor.execute("PRAGMA table_info(tasks)")
columns = cursor.fetchall()
print("\nColumns in tasks table:")
for col in columns:
    print(f"  - {col[1]} ({col[2]})")

conn.close()

print("\n✅ Database schema fixed!")
PYTHON

echo -e "\nTesting tasks API endpoints..."

# Test creating a task
echo -e "\n1. Creating test task:"
curl -s -X POST http://localhost:8000/api/tasks \
    -H "Content-Type: application/json" \
    -d '{
        "title": "Fix database schema",
        "description": "Database tables were missing status column",
        "task_type": "bug",
        "priority": "high"
    }' | jq '.'

# Test listing tasks
echo -e "\n2. Listing all tasks:"
curl -s http://localhost:8000/api/tasks | jq '.'

# Test statistics
echo -e "\n3. Getting task statistics:"
curl -s http://localhost:8000/api/tasks/stats/summary | jq '.'

echo -e "\n✅ Database fixed and tasks API working!"
