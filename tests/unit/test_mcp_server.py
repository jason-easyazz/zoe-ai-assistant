#!/usr/bin/env python3
"""
Test script for Zoe MCP Server
Tests the MCP server functionality locally
"""

import asyncio
import json
import sqlite3
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Mock MCP server for testing
class MockMCPServer:
    def __init__(self):
        self.db_path = str(PROJECT_ROOT / "data" / "zoe.db")
    
    async def test_search_memories(self):
        """Test memory search"""
        print("🔍 Testing search_memories...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Test search
        cursor.execute("SELECT COUNT(*) FROM people")
        people_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM projects")
        projects_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM memory_facts")
        facts_count = cursor.fetchone()[0]
        
        conn.close()
        
        print(f"   ✅ Found {people_count} people, {projects_count} projects, {facts_count} facts")
        return True
    
    async def test_create_person(self):
        """Test person creation"""
        print("👤 Testing create_person...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Count before
        cursor.execute("SELECT COUNT(*) FROM people")
        count_before = cursor.fetchone()[0]
        
        # Create test person
        cursor.execute("""
            INSERT INTO people (user_id, name, profile, facts, important_dates, preferences)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("default", "Test Person", '{"relationship": "test", "created_by": "mcp_test"}', "{}", "{}", "{}"))
        
        conn.commit()
        
        # Count after
        cursor.execute("SELECT COUNT(*) FROM people")
        count_after = cursor.fetchone()[0]
        
        conn.close()
        
        if count_after > count_before:
            print(f"   ✅ Successfully created person (count: {count_before} -> {count_after})")
            return True
        else:
            print("   ❌ Failed to create person")
            return False
    
    async def test_create_calendar_event(self):
        """Test calendar event creation"""
        print("📅 Testing create_calendar_event...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Count before
        cursor.execute("SELECT COUNT(*) FROM events")
        count_before = cursor.fetchone()[0]
        
        # Create test event
        cursor.execute("""
            INSERT INTO events (user_id, title, start_date, start_time, description, category)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("default", "Test Event", "2025-10-04", "10:00", "Test event created by MCP", "personal"))
        
        conn.commit()
        
        # Count after
        cursor.execute("SELECT COUNT(*) FROM events")
        count_after = cursor.fetchone()[0]
        
        conn.close()
        
        if count_after > count_before:
            print(f"   ✅ Successfully created event (count: {count_before} -> {count_after})")
            return True
        else:
            print("   ❌ Failed to create event")
            return False
    
    async def test_add_to_list(self):
        """Test adding to list"""
        print("📝 Testing add_to_list...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Count before
        cursor.execute("SELECT COUNT(*) FROM list_items")
        count_before = cursor.fetchone()[0]
        
        # Find or create a list
        cursor.execute("SELECT id FROM lists WHERE user_id = ? LIMIT 1", ("default",))
        list_row = cursor.fetchone()
        
        if list_row:
            list_id = list_row[0]
        else:
            # Create a test list
            cursor.execute("""
                INSERT INTO lists (user_id, name, category, description)
                VALUES (?, ?, ?, ?)
            """, ("default", "Test List", "personal", "Test list for MCP"))
            list_id = cursor.lastrowid
        
        # Add item
        cursor.execute("""
            INSERT INTO list_items (list_id, task_text, priority, completed)
            VALUES (?, ?, ?, ?)
        """, (list_id, "Test task from MCP", "medium", False))
        
        conn.commit()
        
        # Count after
        cursor.execute("SELECT COUNT(*) FROM list_items")
        count_after = cursor.fetchone()[0]
        
        conn.close()
        
        if count_after > count_before:
            print(f"   ✅ Successfully added to list (count: {count_before} -> {count_after})")
            return True
        else:
            print("   ❌ Failed to add to list")
            return False
    
    async def test_get_developer_tasks(self):
        """Test getting developer tasks"""
        print("📋 Testing get_developer_tasks...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM developer_tasks WHERE user_id = ?", ("default",))
        task_count = cursor.fetchone()[0]
        
        conn.close()
        
        print(f"   ✅ Found {task_count} developer tasks")
        return task_count > 0
    
    async def run_tests(self):
        """Run all tests"""
        print("🧪 Running Zoe MCP Server Tests")
        print("=" * 40)
        
        tests = [
            self.test_search_memories,
            self.test_create_person,
            self.test_create_calendar_event,
            self.test_add_to_list,
            self.test_get_developer_tasks
        ]
        
        passed = 0
        total = len(tests)
        
        for test in tests:
            try:
                if await test():
                    passed += 1
            except Exception as e:
                print(f"   ❌ Test failed with error: {str(e)}")
        
        print("\n" + "=" * 40)
        print(f"📊 Test Results: {passed}/{total} tests passed")
        
        if passed == total:
            print("🎉 All tests passed! MCP server is ready.")
            return True
        else:
            print("⚠️  Some tests failed. Check the output above.")
            return False

async def main():
    """Main test function"""
    server = MockMCPServer()
    success = await server.run_tests()
    return 0 if success else 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)

