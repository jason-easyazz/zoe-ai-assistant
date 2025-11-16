#!/usr/bin/env python3
"""
Migration: Create "self" entries for existing users
Migrates user_profiles data into people table with is_self=true

Date: 2025-11-13
Author: Zoe AI Assistant
"""

import sqlite3
import json
from datetime import datetime

DB_PATH = "/app/data/zoe.db"

def migrate_self_entries():
    """Create self entries for users in the people table"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("🔄 Starting self-entry migration...")
    print("=" * 80)
    
    # 1. Get all unique user_ids from existing tables
    unique_users = set()
    
    # Check people table
    cursor.execute("SELECT DISTINCT user_id FROM people")
    unique_users.update([row[0] for row in cursor.fetchall()])
    
    # Check auth_users table
    cursor.execute("SELECT user_id, username FROM auth_users")
    auth_users = cursor.fetchall()
    for user_id, username in auth_users:
        unique_users.add(user_id)
    
    # Check user_profiles table
    cursor.execute("SELECT user_id, name FROM user_profiles")
    profile_users = cursor.fetchall()
    for user_id, name in profile_users:
        unique_users.add(user_id)
    
    print(f"📊 Found {len(unique_users)} unique users: {list(unique_users)[:5]}...")
    print()
    
    # 2. For each user, check if they have a self entry
    created_count = 0
    skipped_count = 0
    
    for user_id in unique_users:
        # Check if self entry already exists
        cursor.execute("SELECT id FROM people WHERE user_id = ? AND is_self = 1", (user_id,))
        existing = cursor.fetchone()
        
        if existing:
            print(f"  ⏭️  {user_id}: Already has self entry (ID:{existing[0]})")
            skipped_count += 1
            continue
        
        # Get name from various sources
        name = None
        
        # Try auth_users
        cursor.execute("SELECT username FROM auth_users WHERE user_id = ?", (user_id,))
        auth_result = cursor.fetchone()
        if auth_result:
            name = auth_result[0]
        
        # Try user_profiles
        cursor.execute("SELECT name FROM user_profiles WHERE user_id = ?", (user_id,))
        profile_result = cursor.fetchone()
        if profile_result and profile_result[0]:
            name = profile_result[0]
        
        # Default name if none found
        if not name:
            name = f"User {user_id[:8]}"
        
        # Migrate user_profiles data if available
        cursor.execute("""
            SELECT birthday, location, timezone, bio, age_range, 
                   personality_traits, values_priority, interests, life_goals,
                   communication_styles, social_energy, current_life_phase, 
                   daily_routine_type, ai_insights, observed_patterns
            FROM user_profiles 
            WHERE user_id = ?
        """, (user_id,))
        profile_data = cursor.fetchone()
        
        facts = {}
        preferences = {}
        personality_traits = {}
        interests = {}
        metadata = {}
        
        if profile_data:
            birthday, location, timezone, bio, age_range, p_traits, values, ints, goals, comm, energy, life_phase, routine, insights, patterns = profile_data
            
            # Map profile data to new schema
            if location:
                facts['location'] = location
            if timezone:
                preferences['timezone'] = timezone
            if bio:
                facts['bio'] = bio
            if age_range:
                facts['age_range'] = age_range
            
            # JSON fields
            try:
                personality_traits = json.loads(p_traits) if p_traits else {}
            except:
                pass
            try:
                interests = json.loads(ints) if ints else {}
            except:
                pass
            try:
                metadata['values'] = json.loads(values) if values else {}
            except:
                pass
            try:
                metadata['life_goals'] = json.loads(goals) if goals else {}
            except:
                pass
            try:
                metadata['communication_styles'] = json.loads(comm) if comm else []
            except:
                pass
            
            if energy:
                metadata['social_energy'] = energy
            if life_phase:
                metadata['life_phase'] = life_phase
            if routine:
                metadata['routine_type'] = routine
            
            try:
                metadata['ai_insights'] = json.loads(insights) if insights else []
            except:
                pass
            try:
                metadata['observed_patterns'] = json.loads(patterns) if patterns else []
            except:
                pass
        
        # Create self entry
        cursor.execute("""
            INSERT INTO people (
                user_id, name, relationship, is_self, birthday,
                facts, preferences, personality_traits, interests, metadata,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (
            user_id,
            name,
            "self",
            1,
            profile_data[0] if profile_data else None,  # birthday
            json.dumps(facts) if facts else None,
            json.dumps(preferences) if preferences else None,
            json.dumps(personality_traits) if personality_traits else None,
            json.dumps(interests) if interests else None,
            json.dumps(metadata) if metadata else None
        ))
        
        new_id = cursor.lastrowid
        print(f"  ✅ {user_id}: Created self entry (ID:{new_id}, Name:{name})")
        created_count += 1
    
    conn.commit()
    conn.close()
    
    print()
    print("=" * 80)
    print(f"✅ Migration complete!")
    print(f"   Created: {created_count}")
    print(f"   Skipped: {skipped_count}")
    print(f"   Total users: {len(unique_users)}")
    print()
    print("📝 Note: user_profiles table is now deprecated. All data is in people table.")
    
    return created_count

if __name__ == "__main__":
    try:
        migrate_self_entries()
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)





