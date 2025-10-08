#!/usr/bin/env python3
"""
Comprehensive Test Script for Family/Group System
Tests all aspects of the family calendar system inspired by Skylight Calendar
"""

import requests
import json
import time
from datetime import datetime, timedelta
import sys
import os

# Configuration
BASE_URL = "http://localhost:8000"
API_BASE = f"{BASE_URL}/api"

# Test data
TEST_USERS = [
    {"username": "admin", "password": "admin", "role": "admin"},
    {"username": "parent1", "password": "password", "role": "user"},
    {"username": "parent2", "password": "password", "role": "user"},
    {"username": "child1", "password": "password", "role": "user"},
    {"username": "housemate1", "password": "password", "role": "user"},
]

TEST_FAMILIES = [
    {
        "name": "The Johnson Family",
        "description": "Our happy family of four",
        "family_type": "family"
    },
    {
        "name": "123 Main Street",
        "description": "Shared house with 5 roommates",
        "family_type": "household"
    }
]

class FamilySystemTester:
    def __init__(self):
        self.session = requests.Session()
        self.tokens = {}
        self.families = {}
        self.events = {}
        self.test_results = []
    
    def log_test(self, test_name, success, message=""):
        """Log test result"""
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} {test_name}: {message}")
        self.test_results.append({
            "test": test_name,
            "success": success,
            "message": message,
            "timestamp": datetime.now().isoformat()
        })
    
    def authenticate_user(self, username, password):
        """Authenticate a user and store token"""
        try:
            response = self.session.post(f"{API_BASE}/auth/login", json={
                "username": username,
                "password": password
            })
            
            if response.status_code == 200:
                data = response.json()
                self.tokens[username] = data["access_token"]
                self.session.headers.update({
                    "Authorization": f"Bearer {data['access_token']}"
                })
                return True
            else:
                return False
        except Exception as e:
            print(f"Authentication error for {username}: {e}")
            return False
    
    def test_authentication(self):
        """Test user authentication system"""
        print("\n🔐 Testing Authentication System...")
        
        for user in TEST_USERS:
            success = self.authenticate_user(user["username"], user["password"])
            self.log_test(
                f"Authenticate {user['username']}",
                success,
                f"Role: {user['role']}" if success else "Authentication failed"
            )
    
    def test_family_creation(self):
        """Test family/group creation"""
        print("\n👨‍👩‍👧‍👦 Testing Family Creation...")
        
        # Test as admin user
        self.session.headers.update({
            "Authorization": f"Bearer {self.tokens['admin']}"
        })
        
        for i, family_data in enumerate(TEST_FAMILIES):
            try:
                response = self.session.post(f"{API_BASE}/family/create", json=family_data)
                
                if response.status_code == 200:
                    data = response.json()
                    self.families[family_data["name"]] = data["family_id"]
                    self.log_test(
                        f"Create family: {family_data['name']}",
                        True,
                        f"Family ID: {data['family_id']}"
                    )
                else:
                    self.log_test(
                        f"Create family: {family_data['name']}",
                        False,
                        f"Status: {response.status_code}, Response: {response.text}"
                    )
            except Exception as e:
                self.log_test(
                    f"Create family: {family_data['name']}",
                    False,
                    f"Error: {str(e)}"
                )
    
    def test_family_membership(self):
        """Test family membership and invitations"""
        print("\n👥 Testing Family Membership...")
        
        # Test getting user families
        for username in ["parent1", "parent2", "child1", "housemate1"]:
            if username in self.tokens:
                self.session.headers.update({
                    "Authorization": f"Bearer {self.tokens[username]}"
                })
                
                try:
                    response = self.session.get(f"{API_BASE}/family/my-families")
                    
                    if response.status_code == 200:
                        families = response.json()
                        self.log_test(
                            f"Get families for {username}",
                            True,
                            f"Found {len(families)} families"
                        )
                    else:
                        self.log_test(
                            f"Get families for {username}",
                            False,
                            f"Status: {response.status_code}"
                        )
                except Exception as e:
                    self.log_test(
                        f"Get families for {username}",
                        False,
                        f"Error: {str(e)}"
                    )
        
        # Test family invitations (simulated)
        if "The Johnson Family" in self.families:
            family_id = self.families["The Johnson Family"]
            
            # Test inviting parent2 to family
            self.session.headers.update({
                "Authorization": f"Bearer {self.tokens['parent1']}"
            })
            
            try:
                response = self.session.post(f"{API_BASE}/family/{family_id}/invite", json={
                    "email": "parent2@example.com",
                    "role": "member",
                    "message": "Join our family calendar!"
                })
                
                self.log_test(
                    "Invite family member",
                    response.status_code == 200,
                    f"Status: {response.status_code}"
                )
            except Exception as e:
                self.log_test(
                    "Invite family member",
                    False,
                    f"Error: {str(e)}"
                )
    
    def test_event_creation(self):
        """Test event creation for different types"""
        print("\n📅 Testing Event Creation...")
        
        # Test personal events
        self.session.headers.update({
            "Authorization": f"Bearer {self.tokens['parent1']}"
        })
        
        personal_event = {
            "title": "Personal Doctor Appointment",
            "description": "Annual checkup",
            "start_time": (datetime.now() + timedelta(days=1)).isoformat(),
            "end_time": (datetime.now() + timedelta(days=1, hours=1)).isoformat(),
            "event_type": "personal",
            "visibility": "private",
            "category": "medical"
        }
        
        try:
            response = self.session.post(f"{API_BASE}/calendar/create-event", json=personal_event)
            
            if response.status_code == 200:
                data = response.json()
                self.events["personal"] = data["event_id"]
                self.log_test(
                    "Create personal event",
                    True,
                    f"Event ID: {data['event_id']}"
                )
            else:
                self.log_test(
                    "Create personal event",
                    False,
                    f"Status: {response.status_code}"
                )
        except Exception as e:
            self.log_test(
                "Create personal event",
                False,
                f"Error: {str(e)}"
            )
        
        # Test family events
        if "The Johnson Family" in self.families:
            family_id = self.families["The Johnson Family"]
            
            family_event = {
                "title": "Family Dinner",
                "description": "Weekly family dinner",
                "start_time": (datetime.now() + timedelta(days=2)).isoformat(),
                "end_time": (datetime.now() + timedelta(days=2, hours=2)).isoformat(),
                "event_type": "family",
                "visibility": "family",
                "family_id": family_id,
                "category": "family"
            }
            
            try:
                response = self.session.post(f"{API_BASE}/calendar/create-event", json=family_event)
                
                if response.status_code == 200:
                    data = response.json()
                    self.events["family"] = data["event_id"]
                    self.log_test(
                        "Create family event",
                        True,
                        f"Event ID: {data['event_id']}"
                    )
                else:
                    self.log_test(
                        "Create family event",
                        False,
                        f"Status: {response.status_code}"
                    )
            except Exception as e:
                self.log_test(
                    "Create family event",
                    False,
                    f"Error: {str(e)}"
                )
        
        # Test child activity events
        child_event = {
            "title": "Soccer Practice",
            "description": "Weekly soccer practice for child1",
            "start_time": (datetime.now() + timedelta(days=3)).isoformat(),
            "end_time": (datetime.now() + timedelta(days=3, hours=1)).isoformat(),
            "event_type": "child_activity",
            "visibility": "family",
            "family_id": family_id,
            "assigned_to": "child1",
            "category": "sports"
        }
        
        try:
            response = self.session.post(f"{API_BASE}/calendar/create-event", json=child_event)
            
            if response.status_code == 200:
                data = response.json()
                self.events["child_activity"] = data["event_id"]
                self.log_test(
                    "Create child activity event",
                    True,
                    f"Event ID: {data['event_id']}"
                )
            else:
                self.log_test(
                    "Create child activity event",
                    False,
                    f"Status: {response.status_code}"
                )
        except Exception as e:
            self.log_test(
                "Create child activity event",
                False,
                f"Error: {str(e)}"
            )
        
        # Test household events
        if "123 Main Street" in self.families:
            household_id = self.families["123 Main Street"]
            
            household_event = {
                "title": "Kitchen Cleaning",
                "description": "Weekly kitchen deep clean",
                "start_time": (datetime.now() + timedelta(days=4)).isoformat(),
                "end_time": (datetime.now() + timedelta(days=4, hours=2)).isoformat(),
                "event_type": "household",
                "visibility": "family",
                "family_id": household_id,
                "category": "household"
            }
            
            try:
                response = self.session.post(f"{API_BASE}/calendar/create-event", json=household_event)
                
                if response.status_code == 200:
                    data = response.json()
                    self.events["household"] = data["event_id"]
                    self.log_test(
                        "Create household event",
                        True,
                        f"Event ID: {data['event_id']}"
                    )
                else:
                    self.log_test(
                        "Create household event",
                        False,
                        f"Status: {response.status_code}"
                    )
            except Exception as e:
                self.log_test(
                    "Create household event",
                    False,
                    f"Error: {str(e)}"
                )
    
    def test_event_permissions(self):
        """Test event-level permissions"""
        print("\n🔒 Testing Event Permissions...")
        
        if "family" in self.events:
            event_id = self.events["family"]
            
            # Test getting permissions
            self.session.headers.update({
                "Authorization": f"Bearer {self.tokens['parent1']}"
            })
            
            try:
                response = self.session.get(f"{API_BASE}/events/{event_id}/permissions")
                
                if response.status_code == 200:
                    permissions = response.json()
                    self.log_test(
                        "Get event permissions",
                        True,
                        f"Can read: {permissions['can_read']}, Can write: {permissions['can_write']}"
                    )
                else:
                    self.log_test(
                        "Get event permissions",
                        False,
                        f"Status: {response.status_code}"
                    )
            except Exception as e:
                self.log_test(
                    "Get event permissions",
                    False,
                    f"Error: {str(e)}"
                )
            
            # Test sharing event
            try:
                response = self.session.post(f"{API_BASE}/events/{event_id}/share", json={
                    "share_with": ["parent2"],
                    "permission_level": "read"
                })
                
                self.log_test(
                    "Share event with user",
                    response.status_code == 200,
                    f"Status: {response.status_code}"
                )
            except Exception as e:
                self.log_test(
                    "Share event with user",
                    False,
                    f"Error: {str(e)}"
                )
    
    def test_unified_calendar(self):
        """Test unified calendar view"""
        print("\n📊 Testing Unified Calendar...")
        
        for username in ["parent1", "parent2", "child1"]:
            if username in self.tokens:
                self.session.headers.update({
                    "Authorization": f"Bearer {self.tokens[username]}"
                })
                
                try:
                    response = self.session.get(f"{API_BASE}/calendar/unified-events")
                    
                    if response.status_code == 200:
                        events = response.json()
                        self.log_test(
                            f"Get unified events for {username}",
                            True,
                            f"Found {len(events)} events"
                        )
                    else:
                        self.log_test(
                            f"Get unified events for {username}",
                            False,
                            f"Status: {response.status_code}"
                        )
                except Exception as e:
                    self.log_test(
                        f"Get unified events for {username}",
                        False,
                        f"Error: {str(e)}"
                    )
    
    def test_dashboard(self):
        """Test family dashboard"""
        print("\n🏠 Testing Family Dashboard...")
        
        for username in ["parent1", "parent2"]:
            if username in self.tokens:
                self.session.headers.update({
                    "Authorization": f"Bearer {self.tokens[username]}"
                })
                
                try:
                    response = self.session.get(f"{API_BASE}/calendar/dashboard")
                    
                    if response.status_code == 200:
                        dashboard = response.json()
                        self.log_test(
                            f"Get dashboard for {username}",
                            True,
                            f"Today's events: {dashboard.get('total_events_today', 0)}, "
                            f"Family events: {dashboard.get('total_family_events_upcoming', 0)}"
                        )
                    else:
                        self.log_test(
                            f"Get dashboard for {username}",
                            False,
                            f"Status: {response.status_code}"
                        )
                except Exception as e:
                    self.log_test(
                        f"Get dashboard for {username}",
                        False,
                        f"Error: {str(e)}"
                    )
    
    def test_event_types_and_suggestions(self):
        """Test event types and suggestions"""
        print("\n💡 Testing Event Types and Suggestions...")
        
        self.session.headers.update({
            "Authorization": f"Bearer {self.tokens['parent1']}"
        })
        
        try:
            response = self.session.get(f"{API_BASE}/calendar/event-types")
            
            if response.status_code == 200:
                event_types = response.json()
                self.log_test(
                    "Get event types",
                    True,
                    f"Found {len(event_types.get('event_types', []))} event types"
                )
            else:
                self.log_test(
                    "Get event types",
                    False,
                    f"Status: {response.status_code}"
                )
        except Exception as e:
            self.log_test(
                "Get event types",
                False,
                f"Error: {str(e)}"
            )
        
        # Test event suggestions
        try:
            response = self.session.get(f"{API_BASE}/calendar/suggestions?event_type=family")
            
            if response.status_code == 200:
                suggestions = response.json()
                self.log_test(
                    "Get event suggestions",
                    True,
                    f"Found {len(suggestions.get('suggestions', []))} suggestions"
                )
            else:
                self.log_test(
                    "Get event suggestions",
                    False,
                    f"Status: {response.status_code}"
                )
        except Exception as e:
            self.log_test(
                "Get event suggestions",
                False,
                f"Error: {str(e)}"
            )
    
    def run_all_tests(self):
        """Run all tests"""
        print("🚀 Starting Family/Group System Tests")
        print("=" * 50)
        
        start_time = time.time()
        
        # Run all test suites
        self.test_authentication()
        self.test_family_creation()
        self.test_family_membership()
        self.test_event_creation()
        self.test_event_permissions()
        self.test_unified_calendar()
        self.test_dashboard()
        self.test_event_types_and_suggestions()
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Print summary
        print("\n" + "=" * 50)
        print("📊 TEST SUMMARY")
        print("=" * 50)
        
        total_tests = len(self.test_results)
        passed_tests = sum(1 for result in self.test_results if result["success"])
        failed_tests = total_tests - passed_tests
        
        print(f"Total Tests: {total_tests}")
        print(f"Passed: {passed_tests} ✅")
        print(f"Failed: {failed_tests} ❌")
        print(f"Success Rate: {(passed_tests/total_tests)*100:.1f}%")
        print(f"Duration: {duration:.2f} seconds")
        
        # Print failed tests
        if failed_tests > 0:
            print("\n❌ FAILED TESTS:")
            for result in self.test_results:
                if not result["success"]:
                    print(f"  - {result['test']}: {result['message']}")
        
        # Save detailed results
        with open("family_system_test_results.json", "w") as f:
            json.dump({
                "summary": {
                    "total_tests": total_tests,
                    "passed_tests": passed_tests,
                    "failed_tests": failed_tests,
                    "success_rate": (passed_tests/total_tests)*100,
                    "duration": duration
                },
                "test_results": self.test_results
            }, f, indent=2)
        
        print(f"\n📄 Detailed results saved to: family_system_test_results.json")
        
        return passed_tests == total_tests

def main():
    """Main test runner"""
    print("Zoe Family/Group System Test Suite")
    print("Inspired by Skylight Calendar")
    print("=" * 50)
    
    # Check if API is running
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code != 200:
            print("❌ API is not running. Please start Zoe first.")
            sys.exit(1)
    except requests.exceptions.RequestException:
        print("❌ Cannot connect to API. Please start Zoe first.")
        sys.exit(1)
    
    # Run tests
    tester = FamilySystemTester()
    success = tester.run_all_tests()
    
    if success:
        print("\n🎉 All tests passed! Family/Group system is working correctly.")
        sys.exit(0)
    else:
        print("\n⚠️  Some tests failed. Please check the results above.")
        sys.exit(1)

if __name__ == "__main__":
    main()


