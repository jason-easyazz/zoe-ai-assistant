#!/usr/bin/env python3
"""Create clean self entry for testing"""
import sqlite3
import json

DB_PATH = "/home/zoe/assistant/data/zoe.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Clean up existing data for jason
    cursor.execute('DELETE FROM people WHERE user_id = "jason"')
    print("🧹 Deleted existing data for user 'jason'")
    
    # Create proper self entry
    facts = {
        'name': 'Jason',
        'favorite_color': 'blue'
    }
    cursor.execute('''
        INSERT INTO people (user_id, name, is_self, facts, created_at, updated_at)
        VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ''', ('jason', 'Jason', json.dumps(facts)))
    
    conn.commit()
    print("✅ Created clean self entry for Jason")
    
    # Verify
    cursor.execute('SELECT id, name, facts FROM people WHERE user_id = "jason" AND is_self = 1')
    result = cursor.fetchone()
    print(f"✅ Verified:")
    print(f"   ID: {result[0]}")
    print(f"   Name: {result[1]}")
    print(f"   Facts: {result[2]}")
    
    conn.close()

if __name__ == "__main__":
    main()






