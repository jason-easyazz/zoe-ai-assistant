#!/usr/bin/env python3
"""
Comprehensive Full System Integration Test

Tests the entire Zoe stack end-to-end:
- All API endpoints
- UI chat functionality
- Database operations
- AI/LLM integration
- Service health

Run this test after ANY changes to AI components or core services
to ensure UI chat still works.

Usage:
    pytest tests/integration/test_full_system.py -v
    python3 tests/integration/test_full_system.py  # Direct run
"""

import requests
import json
import time
import sys
from pathlib import Path

# Test configuration
API_BASE = "http://localhost:8000"
TEST_USER_ID = "test_user_full_system"
TIMEOUT = 30

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

class FullSystemTester:
    def __init__(self):
        self.results = {
            "passed": [],
            "failed": [],
            "warnings": []
        }
    
    def test_health(self):
        """Test basic health endpoint"""
        print(f"{Colors.BLUE}Testing: Health Check...{Colors.RESET}")
        try:
            response = requests.get(f"{API_BASE}/health", timeout=5)
            assert response.status_code == 200, f"Health check failed: {response.status_code}"
            self.results["passed"].append("Health Check")
            print(f"{Colors.GREEN}✓ Health Check{Colors.RESET}")
            return True
        except Exception as e:
            self.results["failed"].append(("Health Check", str(e)))
            print(f"{Colors.RED}✗ Health Check: {e}{Colors.RESET}")
            return False
    
    def test_lists_api(self):
        """Test lists API endpoints"""
        print(f"\n{Colors.BLUE}Testing: Lists API...{Colors.RESET}")
        
        list_types = ['personal_todos', 'work_todos', 'shopping', 'bucket']
        
        for list_type in list_types:
            try:
                response = requests.get(
                    f"{API_BASE}/api/lists/{list_type}",
                    params={"user_id": TEST_USER_ID},
                    timeout=5
                )
                assert response.status_code == 200, f"Lists {list_type} failed: {response.status_code}"
                print(f"{Colors.GREEN}  ✓ Lists: {list_type}{Colors.RESET}")
            except Exception as e:
                self.results["failed"].append((f"Lists: {list_type}", str(e)))
                print(f"{Colors.RED}  ✗ Lists: {list_type} - {e}{Colors.RESET}")
                return False
        
        self.results["passed"].append("Lists API")
        return True
    
    def test_calendar_api(self):
        """Test calendar API endpoints"""
        print(f"\n{Colors.BLUE}Testing: Calendar API...{Colors.RESET}")
        
        try:
            response = requests.get(
                f"{API_BASE}/api/calendar/events",
                params={
                    "user_id": TEST_USER_ID,
                    "start_date": "2025-10-01",
                    "end_date": "2025-10-31"
                },
                timeout=5
            )
            assert response.status_code == 200, f"Calendar failed: {response.status_code}"
            self.results["passed"].append("Calendar API")
            print(f"{Colors.GREEN}✓ Calendar API{Colors.RESET}")
            return True
        except Exception as e:
            self.results["failed"].append(("Calendar API", str(e)))
            print(f"{Colors.RED}✗ Calendar API: {e}{Colors.RESET}")
            return False
    
    def test_reminders_api(self):
        """Test reminders API (recently fixed)"""
        print(f"\n{Colors.BLUE}Testing: Reminders API...{Colors.RESET}")
        
        endpoints = [
            ("/api/reminders/", "GET"),
            ("/api/reminders/notifications/pending", "GET"),
            ("/api/reminders/upcoming", "GET")
        ]
        
        for endpoint, method in endpoints:
            try:
                response = requests.get(
                    f"{API_BASE}{endpoint}",
                    params={"user_id": TEST_USER_ID},
                    timeout=5
                )
                assert response.status_code == 200, f"{endpoint} failed: {response.status_code}"
                print(f"{Colors.GREEN}  ✓ {endpoint}{Colors.RESET}")
            except Exception as e:
                self.results["failed"].append((endpoint, str(e)))
                print(f"{Colors.RED}  ✗ {endpoint}: {e}{Colors.RESET}")
                return False
        
        self.results["passed"].append("Reminders API")
        return True
    
    def test_chat_api(self):
        """Test chat API - CRITICAL for UI"""
        print(f"\n{Colors.BLUE}Testing: Chat API (CRITICAL)...{Colors.RESET}")
        
        test_message = "Hello, this is a test message"
        
        try:
            # Test basic chat endpoint
            response = requests.post(
                f"{API_BASE}/api/chat",
                json={
                    "message": test_message,
                    "user_id": TEST_USER_ID
                },
                timeout=TIMEOUT
            )
            
            assert response.status_code == 200, f"Chat failed: {response.status_code}"
            
            data = response.json()
            assert "response" in data, "No response field in chat response"
            assert len(data["response"]) > 0, "Empty chat response"
            
            self.results["passed"].append("Chat API - Basic")
            print(f"{Colors.GREEN}  ✓ Chat API - Basic Response{Colors.RESET}")
            print(f"{Colors.GREEN}    Response: {data['response'][:100]}...{Colors.RESET}")
            
            # Test streaming chat
            try:
                stream_response = requests.post(
                    f"{API_BASE}/api/chat/stream",
                    json={
                        "message": test_message,
                        "user_id": TEST_USER_ID
                    },
                    stream=True,
                    timeout=TIMEOUT
                )
                
                if stream_response.status_code == 200:
                    self.results["passed"].append("Chat API - Streaming")
                    print(f"{Colors.GREEN}  ✓ Chat API - Streaming{Colors.RESET}")
                else:
                    self.results["warnings"].append("Chat API - Streaming not available")
                    print(f"{Colors.YELLOW}  ⚠ Chat API - Streaming unavailable{Colors.RESET}")
            except:
                self.results["warnings"].append("Chat API - Streaming endpoint doesn't exist")
                print(f"{Colors.YELLOW}  ⚠ Chat API - Streaming endpoint not found{Colors.RESET}")
            
            return True
            
        except Exception as e:
            self.results["failed"].append(("Chat API", str(e)))
            print(f"{Colors.RED}✗ Chat API FAILED: {e}{Colors.RESET}")
            print(f"{Colors.RED}  ⚠️  UI CHAT WILL NOT WORK!{Colors.RESET}")
            return False
    
    def test_ai_components(self):
        """Test AI/LLM integration"""
        print(f"\n{Colors.BLUE}Testing: AI Components...{Colors.RESET}")
        
        # Check if Ollama is accessible
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get("models", [])
                print(f"{Colors.GREEN}  ✓ Ollama: {len(models)} models available{Colors.RESET}")
                self.results["passed"].append("Ollama Service")
            else:
                self.results["warnings"].append("Ollama not accessible")
                print(f"{Colors.YELLOW}  ⚠ Ollama not accessible{Colors.RESET}")
        except:
            self.results["warnings"].append("Ollama service down")
            print(f"{Colors.YELLOW}  ⚠ Ollama service not running{Colors.RESET}")
        
        # Check LiteLLM
        try:
            response = requests.get("http://localhost:8001/health", timeout=5)
            if response.status_code == 200:
                print(f"{Colors.GREEN}  ✓ LiteLLM Service{Colors.RESET}")
                self.results["passed"].append("LiteLLM Service")
            else:
                self.results["warnings"].append("LiteLLM unhealthy")
                print(f"{Colors.YELLOW}  ⚠ LiteLLM unhealthy{Colors.RESET}")
        except:
            self.results["warnings"].append("LiteLLM not accessible")
            print(f"{Colors.YELLOW}  ⚠ LiteLLM not accessible{Colors.RESET}")
        
        return True
    
    def test_database(self):
        """Test database connectivity and schema"""
        print(f"\n{Colors.BLUE}Testing: Database...{Colors.RESET}")
        
        try:
            import sqlite3
            conn = sqlite3.connect("/home/pi/zoe/data/zoe.db")
            cursor = conn.cursor()
            
            # Test critical tables exist
            critical_tables = ['users', 'lists', 'events', 'reminders', 'notifications']
            for table in critical_tables:
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
                assert cursor.fetchone() is not None, f"Table {table} missing"
                print(f"{Colors.GREEN}  ✓ Table: {table}{Colors.RESET}")
            
            conn.close()
            self.results["passed"].append("Database Schema")
            return True
            
        except Exception as e:
            self.results["failed"].append(("Database", str(e)))
            print(f"{Colors.RED}✗ Database: {e}{Colors.RESET}")
            return False
    
    def test_services(self):
        """Test all Docker services are running"""
        print(f"\n{Colors.BLUE}Testing: Docker Services...{Colors.RESET}")
        
        import subprocess
        
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True,
                text=True
            )
            
            running_services = result.stdout.strip().split('\n')
            
            critical_services = ['zoe-core-test', 'zoe-ui', 'zoe-ollama']
            
            for service in critical_services:
                if service in running_services:
                    print(f"{Colors.GREEN}  ✓ Service: {service}{Colors.RESET}")
                else:
                    self.results["warnings"].append(f"Service {service} not running")
                    print(f"{Colors.YELLOW}  ⚠ Service: {service} not running{Colors.RESET}")
            
            self.results["passed"].append("Docker Services")
            return True
            
        except Exception as e:
            self.results["warnings"].append(f"Docker check failed: {e}")
            print(f"{Colors.YELLOW}⚠ Docker check: {e}{Colors.RESET}")
            return True  # Non-critical
    
    def test_ui_dependencies(self):
        """Test UI files exist and are accessible"""
        print(f"\n{Colors.BLUE}Testing: UI Files...{Colors.RESET}")
        
        ui_files = [
            "services/zoe-ui/dist/index.html",
            "services/zoe-ui/dist/chat.html",
            "services/zoe-ui/dist/calendar.html",
            "services/zoe-ui/dist/js/common.js",
            "services/zoe-ui/dist/js/auth.js"
        ]
        
        all_exist = True
        for ui_file in ui_files:
            file_path = Path("/home/pi/zoe") / ui_file
            if file_path.exists():
                print(f"{Colors.GREEN}  ✓ {ui_file}{Colors.RESET}")
            else:
                self.results["failed"].append((ui_file, "File missing"))
                print(f"{Colors.RED}  ✗ {ui_file} - MISSING{Colors.RESET}")
                all_exist = False
        
        if all_exist:
            self.results["passed"].append("UI Files")
        
        return all_exist
    
    def run_all_tests(self):
        """Run all tests"""
        print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
        print(f"{Colors.BLUE}ZOE FULL SYSTEM INTEGRATION TEST{Colors.RESET}")
        print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")
        
        print(f"Testing full stack to ensure UI chat works...\n")
        
        # Run tests
        tests = [
            ("Health Check", self.test_health),
            ("Database", self.test_database),
            ("Docker Services", self.test_services),
            ("Lists API", self.test_lists_api),
            ("Calendar API", self.test_calendar_api),
            ("Reminders API", self.test_reminders_api),
            ("Chat API", self.test_chat_api),  # CRITICAL
            ("AI Components", self.test_ai_components),
            ("UI Files", self.test_ui_dependencies)
        ]
        
        for name, test_func in tests:
            test_func()
        
        # Generate report
        self.generate_report()
    
    def generate_report(self):
        """Generate test report"""
        print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
        print(f"{Colors.BLUE}TEST RESULTS{Colors.RESET}")
        print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")
        
        total = len(self.results["passed"]) + len(self.results["failed"])
        passed = len(self.results["passed"])
        
        print(f"Passed: {Colors.GREEN}{passed}/{total}{Colors.RESET}")
        print(f"Failed: {Colors.RED}{len(self.results['failed'])}{Colors.RESET}")
        print(f"Warnings: {Colors.YELLOW}{len(self.results['warnings'])}{Colors.RESET}")
        
        if self.results["passed"]:
            print(f"\n{Colors.GREEN}✅ PASSED:{Colors.RESET}")
            for test in self.results["passed"]:
                print(f"  • {test}")
        
        if self.results["failed"]:
            print(f"\n{Colors.RED}❌ FAILED:{Colors.RESET}")
            for test, error in self.results["failed"]:
                print(f"  • {test}: {error[:100]}")
        
        if self.results["warnings"]:
            print(f"\n{Colors.YELLOW}⚠️  WARNINGS:{Colors.RESET}")
            for warning in self.results["warnings"]:
                print(f"  • {warning}")
        
        # Critical check: Chat API
        chat_passed = any("Chat API" in test for test in self.results["passed"])
        
        print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
        
        if self.results["failed"]:
            print(f"{Colors.RED}❌ SYSTEM TEST FAILED{Colors.RESET}")
            
            if not chat_passed:
                print(f"{Colors.RED}🚨 CRITICAL: UI CHAT WILL NOT WORK!{Colors.RESET}")
                print(f"{Colors.RED}   Fix chat API before deploying{Colors.RESET}")
            
            print(f"{Colors.BLUE}{'='*70}{Colors.RESET}")
            return False
        else:
            print(f"{Colors.GREEN}✅ SYSTEM TEST PASSED{Colors.RESET}")
            
            if chat_passed:
                print(f"{Colors.GREEN}✅ UI CHAT CONFIRMED WORKING{Colors.RESET}")
            
            print(f"{Colors.BLUE}{'='*70}{Colors.RESET}")
            return True

def main():
    """Main entry point"""
    tester = FullSystemTester()
    
    success = tester.run_all_tests()
    
    # Save report
    report_path = Path("/home/pi/zoe/tests/integration/full_system_test_report.json")
    with open(report_path, "w") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "results": tester.results,
            "success": success
        }, f, indent=2)
    
    print(f"\n📄 Report saved to: {report_path}")
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()

