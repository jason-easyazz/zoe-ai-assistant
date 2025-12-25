#!/usr/bin/env python3
"""Find all test data references in the database"""
import sqlite3
import json

DB_PATH = "/home/zoe/assistant/data/zoe.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    test_names = ['Sarah', 'John', 'Smith', 'Integration Test', 'Test Person']
    
    print("=" * 80)
    print("SEARCHING FOR TEST DATA")
    print("=" * 80)
    
    # Check people table
    print("\n📋 PEOPLE TABLE:")
    cursor.execute("SELECT id, name, relationship, user_id FROM people")
    rows = cursor.fetchall()
    found = False
    for row in rows:
        name = row['name'] or ""
        relationship = row['relationship'] or ""
        if any(test_name.lower() in name.lower() or test_name.lower() in relationship.lower() for test_name in test_names):
            print(f"  ❌ ID={row['id']}, name='{row['name']}', relationship='{row['relationship']}', user_id='{row['user_id']}'")
            found = True
    if not found:
        print("  ✅ No test data found")
    
    # Check self_facts table
    print("\n📋 SELF_FACTS TABLE:")
    cursor.execute("SELECT id, fact_key, fact_value, user_id FROM self_facts")
    rows = cursor.fetchall()
    found = False
    for row in rows:
        fact_value = row['fact_value'] or ""
        if any(test_name.lower() in fact_value.lower() for test_name in test_names):
            print(f"  ❌ ID={row['id']}, key='{row['fact_key']}', value='{row['fact_value']}', user_id='{row['user_id']}'")
            found = True
    if not found:
        print("  ✅ No test data found")
    
    # Check chat_messages table
    print("\n📋 CHAT_MESSAGES TABLE:")
    cursor.execute("SELECT id, role, content, user_id FROM chat_messages WHERE role='assistant'")
    rows = cursor.fetchall()
    found = False
    for row in rows:
        content = row['content'] or ""
        if any(test_name.lower() in content.lower() for test_name in test_names):
            print(f"  ❌ ID={row['id']}, role='{row['role']}', user_id='{row['user_id']}'")
            print(f"      Content snippet: {content[:200]}...")
            found = True
    if not found:
        print("  ✅ No test data found")
    
    # Check list_items table
    print("\n📋 LIST_ITEMS TABLE:")
    cursor.execute("SELECT id, item_name, list_name, user_id FROM list_items")
    rows = cursor.fetchall()
    found = False
    for row in rows:
        item_name = row['item_name'] or ""
        if any(test_name.lower() in item_name.lower() for test_name in test_names):
            print(f"  ❌ ID={row['id']}, item='{row['item_name']}', list='{row['list_name']}', user_id='{row['user_id']}'")
            found = True
    if not found:
        print("  ✅ No test data found")
    
    # Check calendar_events table
    print("\n📋 CALENDAR_EVENTS TABLE:")
    cursor.execute("SELECT id, title, description, user_id FROM calendar_events")
    rows = cursor.fetchall()
    found = False
    for row in rows:
        title = row['title'] or ""
        description = row['description'] or ""
        if any(test_name.lower() in title.lower() or test_name.lower() in (description or "").lower() for test_name in test_names):
            print(f"  ❌ ID={row['id']}, title='{row['title']}', user_id='{row['user_id']}'")
            found = True
    if not found:
        print("  ✅ No test data found")
    
    conn.close()
    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()






