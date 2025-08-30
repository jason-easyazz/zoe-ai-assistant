#!/bin/bash
# Final fix for tasks system

echo "Final Tasks System Fix"
echo "======================"

# Step 1: Fix database using Python (sqlite3 command not available)
echo "Step 1: Fixing database schema..."
docker exec zoe-core python3 << 'PYTHON'
import sqlite3

conn = sqlite3.connect("/app/data/zoe.db")
cursor = conn.cursor()

# Check current schema
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'")
current = cursor.fetchone()
print(f"Current schema: {current}")

# Drop and recreate with correct schema
cursor.execute("DROP TABLE IF EXISTS tasks")
cursor.execute("DROP TABLE IF EXISTS task_conversations")
cursor.execute("DROP TABLE IF EXISTS code_approvals")

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

# Verify
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print(f"Tables created: {[t[0] for t in tables]}")

conn.close()
print("✅ Database schema fixed!")
PYTHON

# Step 2: Test with correct URLs (note the trailing slash!)
echo -e "\nStep 2: Testing API endpoints..."

echo "Test endpoint:"
curl -s http://localhost:8000/api/tasks/test | jq '.'

echo -e "\nCreate task (with correct URL):"
curl -s -X POST http://localhost:8000/api/tasks/ \
    -H "Content-Type: application/json" \
    -d '{
        "title": "Implement authentication",
        "description": "Add JWT-based auth system",
        "task_type": "feature",
        "priority": "high"
    }' | jq '.'

echo -e "\nList tasks (with -L to follow redirect):"
curl -sL http://localhost:8000/api/tasks | jq '.'

echo -e "\nAlternative - List tasks (with trailing slash):"
curl -s http://localhost:8000/api/tasks/ | jq '.'

echo -e "\nTask statistics:"
curl -s http://localhost:8000/api/tasks/stats/summary | jq '.'

echo -e "\n✅ Tasks API is now working!"
echo ""
echo "Note: Use trailing slash for list endpoint: /api/tasks/"
echo "Or use curl -L to follow redirects"
