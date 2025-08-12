#!/usr/bin/env python3
import asyncio
import aiosqlite
import os
from pathlib import Path

async def migrate_database():
    """Migrate database to enhanced calendar schema"""
    
    # Find the database file
    db_paths = [
        "/app/data/zoe.db",
        "./data/zoe.db", 
        "../data/zoe.db",
        "../../data/zoe.db"
    ]
    
    db_path = None
    for path in db_paths:
        if os.path.exists(path):
            db_path = path
            break
    
    if not db_path:
        print("❌ Database file not found!")
        return False
    
    print(f"📍 Found database: {db_path}")
    
    try:
        async with aiosqlite.connect(db_path) as db:
            print("🔄 Applying database migrations...")
            
            # Add new columns to events table
            try:
                await db.execute("ALTER TABLE events ADD COLUMN duration INTEGER DEFAULT 60")
                print("✅ Added duration column to events")
            except:
                print("ℹ️  Duration column already exists")
            
            try:
                await db.execute("ALTER TABLE events ADD COLUMN category TEXT DEFAULT 'general'")
                print("✅ Added category column to events")
            except:
                print("ℹ️  Category column already exists")
            
            try:
                await db.execute("ALTER TABLE events ADD COLUMN priority TEXT DEFAULT 'medium'")
                print("✅ Added priority column to events")
            except:
                print("ℹ️  Priority column already exists")
            
            # Create event_notifications table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS event_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    days_before INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (event_id) REFERENCES events (id) ON DELETE CASCADE
                )
            """)
            print("✅ Created event_notifications table")
            
            # Update tasks table
            try:
                await db.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT 'general'")
                print("✅ Added task_type column to tasks")
            except:
                print("ℹ️  Task_type column already exists")
            
            try:
                await db.execute("ALTER TABLE tasks ADD COLUMN event_id INTEGER")
                print("✅ Added event_id column to tasks")
            except:
                print("ℹ️  Event_id column already exists")
            
            # Create user_preferences table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    date_format TEXT DEFAULT 'DD/MM/YYYY',
                    time_format TEXT DEFAULT '12h',
                    timezone TEXT DEFAULT 'UTC',
                    default_reminder_days INTEGER DEFAULT 1,
                    default_event_duration INTEGER DEFAULT 60,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id)
                )
            """)
            print("✅ Created user_preferences table")
            
            # Insert default preferences
            await db.execute("""
                INSERT OR IGNORE INTO user_preferences (user_id) VALUES ('default')
            """)
            print("✅ Inserted default user preferences")
            
            await db.commit()
            print("✅ Database migration completed successfully!")
            return True
            
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(migrate_database())
    if success:
        print("🎉 Database migration completed!")
    else:
        print("💥 Database migration failed!")
        exit(1)
