#!/usr/bin/env python3
"""Check self_facts stored for a user"""
import sqlite3

DB_PATH = "/home/zoe/assistant/data/zoe.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("=" * 80)
    print("SELF_FACTS FOR USER 'jason'")
    print("=" * 80)
    
    cursor.execute("SELECT id, fact_key, fact_value, created_at FROM self_facts WHERE user_id = 'jason' ORDER BY created_at DESC")
    rows = cursor.fetchall()
    
    if rows:
        for row in rows:
            print(f"\n📝 Fact #{row['id']}:")
            print(f"   Key: {row['fact_key']}")
            print(f"   Value: {row['fact_value']}")
            print(f"   Created: {row['created_at']}")
    else:
        print("\n❌ No self-facts found for user 'jason'")
    
    print("\n" + "=" * 80)
    print("CHAT_MESSAGES FOR USER 'jason'")
    print("=" * 80)
    
    cursor.execute("SELECT id, role, content, created_at FROM chat_messages WHERE session_id IN (SELECT id FROM chat_sessions WHERE user_id = 'jason') ORDER BY created_at DESC LIMIT 10")
    rows = cursor.fetchall()
    
    if rows:
        for row in rows:
            print(f"\n💬 Message #{row['id']} ({row['role']}):")
            print(f"   {row['content'][:200]}")
            print(f"   Time: {row['created_at']}")
    else:
        print("\n❌ No chat messages found for user 'jason'")
    
    conn.close()
    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()






