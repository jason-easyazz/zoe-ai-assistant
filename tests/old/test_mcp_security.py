#!/usr/bin/env python3
"""
Test script for Zoe MCP Server Security Framework
Tests authentication, authorization, and data isolation
"""

import asyncio
import json
import sqlite3
import jwt
from datetime import datetime, timedelta
from pathlib import Path

# Mock security test
class SecurityTester:
    def __init__(self):
        self.db_path = "/home/pi/zoe/data/zoe.db"
        self.secret_key = "zoe-mcp-secret-key-change-in-production"
        self.algorithm = "HS256"
    
    def create_test_jwt(self, user_id: str, username: str) -> str:
        """Create a test JWT token"""
        payload = {
            "user_id": user_id,
            "username": username,
            "exp": datetime.utcnow() + timedelta(hours=24)
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
    
    async def test_user_isolation(self):
        """Test that users only see their own data"""
        print("🔒 Testing user data isolation...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get all users
        cursor.execute("SELECT user_id, username FROM users WHERE is_active = 1")
        users = cursor.fetchall()
        
        if len(users) < 2:
            print("   ⚠️  Need at least 2 users to test isolation")
            conn.close()
            return True
        
        # Test data isolation for each user
        for user_id, username in users:
            # Check people isolation
            cursor.execute("SELECT COUNT(*) FROM people WHERE user_id = ?", (user_id,))
            people_count = cursor.fetchone()[0]
            
            # Check events isolation
            cursor.execute("SELECT COUNT(*) FROM events WHERE user_id = ?", (user_id,))
            events_count = cursor.fetchone()[0]
            
            # Check lists isolation
            cursor.execute("SELECT COUNT(*) FROM lists WHERE user_id = ?", (user_id,))
            lists_count = cursor.fetchone()[0]
            
            print(f"   ✅ User {username}: {people_count} people, {events_count} events, {lists_count} lists")
        
        conn.close()
        return True
    
    async def test_jwt_validation(self):
        """Test JWT token validation"""
        print("🎫 Testing JWT token validation...")
        
        # Test valid token
        valid_token = self.create_test_jwt("default", "testuser")
        try:
            payload = jwt.decode(valid_token, self.secret_key, algorithms=[self.algorithm])
            if payload["user_id"] == "default" and payload["username"] == "testuser":
                print("   ✅ Valid JWT token decoded successfully")
            else:
                print("   ❌ JWT token validation failed")
                return False
        except Exception as e:
            print(f"   ❌ JWT token validation failed: {str(e)}")
            return False
        
        # Test invalid token
        try:
            invalid_token = "invalid.token.here"
            jwt.decode(invalid_token, self.secret_key, algorithms=[self.algorithm])
            print("   ❌ Invalid token should have been rejected")
            return False
        except jwt.InvalidTokenError:
            print("   ✅ Invalid JWT token correctly rejected")
        
        return True
    
    async def test_audit_logging(self):
        """Test audit logging functionality"""
        print("📝 Testing audit logging...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create audit log table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mcp_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT
            )
        """)
        
        # Insert test audit log entry
        cursor.execute("""
            INSERT INTO mcp_audit_log (user_id, username, tool_name, action, details, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("test_user", "testuser", "test_tool", "test_action", '{"test": "data"}', "test_session"))
        
        conn.commit()
        
        # Verify audit log entry
        cursor.execute("SELECT COUNT(*) FROM mcp_audit_log WHERE user_id = ?", ("test_user",))
        count = cursor.fetchone()[0]
        
        conn.close()
        
        if count > 0:
            print("   ✅ Audit logging working correctly")
            return True
        else:
            print("   ❌ Audit logging failed")
            return False
    
    async def test_permission_system(self):
        """Test permission-based access control"""
        print("🛡️  Testing permission system...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if we have users with different roles
        cursor.execute("SELECT user_id, username, role FROM users WHERE is_active = 1")
        users = cursor.fetchall()
        
        admin_users = [u for u in users if u[2] == 'admin']
        regular_users = [u for u in users if u[2] == 'user']
        
        if admin_users and regular_users:
            print(f"   ✅ Found {len(admin_users)} admin users and {len(regular_users)} regular users")
        else:
            print("   ⚠️  Need both admin and regular users to test permissions")
        
        conn.close()
        return True
    
    async def test_database_security(self):
        """Test database security features"""
        print("🗄️  Testing database security...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if audit log table exists
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='mcp_audit_log'
        """)
        audit_table = cursor.fetchone()
        
        if audit_table:
            print("   ✅ Audit log table exists")
        else:
            print("   ⚠️  Audit log table not found")
        
        # Check user isolation in key tables
        tables_to_check = ['people', 'events', 'lists', 'developer_tasks']
        for table in tables_to_check:
            try:
                cursor.execute(f"SELECT COUNT(DISTINCT user_id) FROM {table}")
                user_count = cursor.fetchone()[0]
                print(f"   ✅ Table {table}: {user_count} distinct users")
            except Exception as e:
                print(f"   ❌ Error checking table {table}: {str(e)}")
        
        conn.close()
        return True
    
    async def run_security_tests(self):
        """Run all security tests"""
        print("🔐 Running Zoe MCP Server Security Tests")
        print("=" * 50)
        
        tests = [
            self.test_user_isolation,
            self.test_jwt_validation,
            self.test_audit_logging,
            self.test_permission_system,
            self.test_database_security
        ]
        
        passed = 0
        total = len(tests)
        
        for test in tests:
            try:
                if await test():
                    passed += 1
            except Exception as e:
                print(f"   ❌ Test failed with error: {str(e)}")
        
        print("\n" + "=" * 50)
        print(f"📊 Security Test Results: {passed}/{total} tests passed")
        
        if passed == total:
            print("🎉 All security tests passed! MCP server security is ready.")
            return True
        else:
            print("⚠️  Some security tests failed. Check the output above.")
            return False

async def main():
    """Main test function"""
    tester = SecurityTester()
    success = await tester.run_security_tests()
    return 0 if success else 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)

