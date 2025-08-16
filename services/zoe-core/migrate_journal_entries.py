#!/usr/bin/env python3
import asyncio
import aiosqlite
import os
from pathlib import Path

async def migrate_database():
    """Migrate database to include extended journal entry fields"""

    db_paths = [
        "/app/data/zoe.db",
        "./data/zoe.db",
        "../data/zoe.db",
        "../../data/zoe.db"
    ]

    db_path = next((p for p in db_paths if os.path.exists(p)), None)
    if not db_path:
        print("❌ Database file not found!")
        return False

    print(f"📍 Found database: {db_path}")

    try:
        async with aiosqlite.connect(db_path) as db:
            print("🔄 Applying journal entry migrations...")

            try:
                await db.execute("ALTER TABLE journal_entries ADD COLUMN mood TEXT")
                print("✅ Added mood column to journal_entries")
            except Exception:
                print("ℹ️  Mood column already exists")

            try:
                await db.execute("ALTER TABLE journal_entries ADD COLUMN mood_confidence REAL")
                print("✅ Added mood_confidence column to journal_entries")
            except Exception:
                print("ℹ️  Mood_confidence column already exists")

            try:
                await db.execute("ALTER TABLE journal_entries ADD COLUMN photo TEXT")
                print("✅ Added photo column to journal_entries")
            except Exception:
                print("ℹ️  Photo column already exists")

            try:
                await db.execute("ALTER TABLE journal_entries ADD COLUMN health_info TEXT")
                print("✅ Added health_info column to journal_entries")
            except Exception:
                print("ℹ️  Health_info column already exists")

            try:
                await db.execute("ALTER TABLE journal_entries ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                print("✅ Added updated_at column to journal_entries")
            except Exception:
                print("ℹ️  Updated_at column already exists")

            await db.commit()
            print("✅ Journal entry migrations completed successfully!")
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
