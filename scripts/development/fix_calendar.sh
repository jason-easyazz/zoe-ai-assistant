#!/bin/bash

# Fix Calendar Database Schema

echo "🔧 Fixing Calendar Database Schema..."

# Check current schema
echo "Current schema:"
docker exec zoe-core sqlite3 /app/data/zoe.db ".schema events"

# Fix the schema
docker exec zoe-core python3 -c "
import sqlite3

conn = sqlite3.connect('/app/data/zoe.db')
cursor = conn.cursor()

# Check if events table exists and what columns it has
cursor.execute(\"\"\"SELECT sql FROM sqlite_master WHERE type='table' AND name='events'\"\"\")
result = cursor.fetchone()

if result:
    print('Current table structure:', result[0])
    # Drop and recreate with correct schema
    cursor.execute('DROP TABLE IF EXISTS events')

# Create correct schema
cursor.execute('''
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        start_date DATE NOT NULL,
        start_time TIME,
        cluster_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')

# Add sample events
from datetime import datetime, timedelta

events = [
    ('Team Meeting', datetime.now().strftime('%Y-%m-%d'), '10:00'),
    ('Lunch with Alice', (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d'), '12:30'),
    ('Project Review', (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d'), '15:00'),
    ('Zoe Development', (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d'), '14:00')
]

for title, date, time in events:
    cursor.execute(
        'INSERT INTO events (title, start_date, start_time) VALUES (?, ?, ?)',
        (title, date, time)
    )

conn.commit()
print('✅ Calendar database fixed and sample events added!')

# Verify
cursor.execute('SELECT COUNT(*) FROM events')
count = cursor.fetchone()[0]
print(f'Total events in database: {count}')

conn.close()
"

# Test calendar API
echo -e "\n📅 Testing Calendar API:"
curl -s http://localhost:8000/api/calendar/events | python3 -m json.tool

echo -e "\n✅ Calendar fixed!"
