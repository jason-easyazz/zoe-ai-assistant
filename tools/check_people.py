#!/usr/bin/env python3
"""Check people table for user jason"""
import sqlite3

DB_PATH = "/home/zoe/assistant/data/zoe.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("=" * 80)
    print("PEOPLE TABLE FOR USER 'jason'")
    print("=" * 80)
    
    cursor.execute("SELECT id, name, is_self, facts, user_id FROM people WHERE user_id = 'jason'")
    rows = cursor.fetchall()
    
    if rows:
        for row in rows:
            print(f"\n📝 Person #{row['id']}:")
            print(f"   Name: {row['name']}")
            print(f"   Is Self: {row['is_self']}")
            print(f"   Facts: {row['facts']}")
            print(f"   User ID: {row['user_id']}")
    else:
        print("\n❌ No people records found for user 'jason'")
    
    conn.close()
    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()






