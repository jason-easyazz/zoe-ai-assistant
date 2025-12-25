#!/usr/bin/env python3
"""
Comprehensive Zoe User Memory & Features Test Suite
Creates a demo user and tests all systems end-to-end
"""

import asyncio
import httpx
import json
import sqlite3
from datetime import datetime

# Test configuration
BASE_URL = "http://localhost:8000"
TEST_USER_ID = "demo_test_user"
TEST_USER_NAME = "Alex Thompson"

class ZoeTestSuite:
    def __init__(self):
        self.client = None
        self.results = {
            "passed": [],
            "failed": [],
            "warnings": []
        }
    
    async def setup_demo_user(self):
        """Create a demo user with comprehensive profile"""
        print("=" * 60)
        print("🔧 SETUP: Creating Demo User")
        print("=" * 60)
        
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        # Create user
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO users (user_id, username, email, role, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (TEST_USER_ID, TEST_USER_NAME, f"{TEST_USER_ID}@example.com", "user", datetime.now().isoformat()))
            print(f"✅ Created user: {TEST_USER_NAME} ({TEST_USER_ID})")
        except Exception as e:
            print(f"⚠️  User creation: {e}")
        
        # Add some baseline data
        test_data = {
            "people": [
                ("Sarah Johnson", json.dumps({"relationship": "friend", "notes": "College roommate"})),
                ("Dr. Smith", json.dumps({"relationship": "doctor", "notes": "Family physician"})),
            ],
            "projects": [
                ("Home Renovation", "active"),
                ("Learn Python", "active"),
            ],
            "self_facts": [
                ("name", TEST_USER_NAME),
                ("location", "San Francisco"),
                ("occupation", "Software Engineer"),
                ("favorite_food", "Sushi"),
                ("favorite_color", "Blue"),
                ("pet", "Golden Retriever named Max"),
                ("hobby", "Photography"),
            ]
        }
        
        # Add people
        for name, profile in test_data["people"]:
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO people (user_id, name, profile, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (TEST_USER_ID, name, profile, datetime.now().isoformat(), datetime.now().isoformat()))
            except Exception as e:
                print(f"⚠️  Adding person {name}: {e}")
        
        # Add projects
        for name, status in test_data["projects"]:
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO projects (user_id, name, status, created_at)
                    VALUES (?, ?, ?, ?)
                """, (TEST_USER_ID, name, status, datetime.now().isoformat()))
            except Exception as e:
                print(f"⚠️  Adding project {name}: {e}")
        
        # Add self-facts
        for key, value in test_data["self_facts"]:
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO self_facts (user_id, fact_key, fact_value, confidence, source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (TEST_USER_ID, key, value, 1.0, "test_setup", datetime.now().isoformat(), datetime.now().isoformat()))
            except Exception as e:
                print(f"⚠️  Adding fact {key}: {e}")
        
        conn.commit()
        conn.close()
        
        print(f"✅ Added {len(test_data['people'])} people")
        print(f"✅ Added {len(test_data['projects'])} projects")
        print(f"✅ Added {len(test_data['self_facts'])} self-facts")
        print()
    
    async def chat(self, message: str, expected_in_response: str = None):
        """Send a chat message and validate response"""
        try:
            response = await self.client.post(
                f"{BASE_URL}/api/chat/?user_id={TEST_USER_ID}&stream=false",
                json={"message": message, "context": {}},
                timeout=30.0
            )
            
            if response.status_code != 200:
                self.results["failed"].append({
                    "test": message,
                    "error": f"HTTP {response.status_code}"
                })
                return None
            
            data = response.json()
            response_text = data.get("response", "")
            
            # Validate expected content
            if expected_in_response:
                if expected_in_response.lower() in response_text.lower():
                    self.results["passed"].append({
                        "test": message,
                        "expected": expected_in_response,
                        "got": response_text[:100]
                    })
                    return True, response_text
                else:
                    self.results["failed"].append({
                        "test": message,
                        "expected": expected_in_response,
                        "got": response_text[:100]
                    })
                    return False, response_text
            
            return True, response_text
        
        except Exception as e:
            self.results["failed"].append({
                "test": message,
                "error": str(e)
            })
            return None, str(e)
    
    async def test_user_identity(self):
        """Test if Zoe knows the user's name"""
        print("=" * 60)
        print("🧪 TEST 1: User Identity")
        print("=" * 60)
        
        success, response = await self.chat(
            "What is my name?",
            expected_in_response=TEST_USER_NAME
        )
        
        if success:
            print(f"✅ PASS: Zoe correctly identified user as {TEST_USER_NAME}")
            print(f"   Response: {response[:150]}...")
        else:
            print(f"❌ FAIL: Expected '{TEST_USER_NAME}' in response")
            print(f"   Got: {response[:150]}...")
        print()
    
    async def test_self_facts_recall(self):
        """Test if Zoe can recall stored facts"""
        print("=" * 60)
        print("🧪 TEST 2: Self-Facts Recall")
        print("=" * 60)
        
        tests = [
            ("What's my favorite food?", "Sushi"),
            ("What color do I like?", "Blue"),
            ("What's my pet's name?", "Max"),
            ("Where do I live?", "San Francisco"),
            ("What do I do for work?", "Software Engineer"),
        ]
        
        for question, expected in tests:
            success, response = await self.chat(question, expected_in_response=expected)
            if success:
                print(f"✅ {question} → Found '{expected}'")
            else:
                print(f"❌ {question} → Expected '{expected}', got: {response[:100]}")
        print()
    
    async def test_self_facts_extraction(self):
        """Test if new facts are extracted and stored"""
        print("=" * 60)
        print("🧪 TEST 3: Self-Facts Extraction")
        print("=" * 60)
        
        # Say something new
        await self.chat("My favorite movie is Inception")
        await asyncio.sleep(1)
        
        # Check if it was stored
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        cursor.execute("""
            SELECT fact_value FROM self_facts 
            WHERE user_id = ? AND fact_key LIKE '%movie%'
        """, (TEST_USER_ID,))
        result = cursor.fetchone()
        conn.close()
        
        if result and "inception" in result[0].lower():
            print("✅ PASS: New fact extracted and stored")
            self.results["passed"].append({
                "test": "Self-fact extraction",
                "stored": result[0]
            })
        else:
            print("❌ FAIL: Fact not stored in database")
            self.results["failed"].append({
                "test": "Self-fact extraction",
                "expected": "Inception",
                "got": result[0] if result else "Nothing"
            })
        print()
    
    async def test_people_recall(self):
        """Test if Zoe remembers people"""
        print("=" * 60)
        print("🧪 TEST 4: People/Relationships Recall")
        print("=" * 60)
        
        success, response = await self.chat(
            "Who is Sarah Johnson?",
            expected_in_response="Sarah"
        )
        
        if success:
            print("✅ PASS: Zoe recalled information about Sarah")
        else:
            print("❌ FAIL: Zoe didn't recall Sarah")
        print()
    
    async def test_calendar_natural_language(self):
        """Test natural language calendar additions (Andrew's issue)"""
        print("=" * 60)
        print("🧪 TEST 5: Calendar Natural Language (Andrew's Issue)")
        print("=" * 60)
        
        calendar_tests = [
            "Add dentist appointment tomorrow at 3pm",
            "Remind me to call mom on Friday at 2pm",
            "Schedule team meeting next Monday at 10am",
            "I have a doctor's appointment on December 15th at 9am",
        ]
        
        for test_input in calendar_tests:
            print(f"\n📅 Testing: {test_input}")
            success, response = await self.chat(test_input)
            
            # Check if event was created
            await asyncio.sleep(1)
            conn = sqlite3.connect('/app/data/zoe.db')
            cursor = conn.cursor()
            cursor.execute("""
                SELECT title, start_date, start_time FROM events 
                WHERE user_id = ? 
                ORDER BY created_at DESC LIMIT 1
            """, (TEST_USER_ID,))
            event = cursor.fetchone()
            conn.close()
            
            if event:
                print(f"   ✅ Event created: {event[0]} on {event[1]} at {event[2]}")
                self.results["passed"].append({
                    "test": f"Calendar: {test_input}",
                    "created": f"{event[0]} on {event[1]}"
                })
            else:
                print(f"   ❌ No event created!")
                print(f"   Response: {response[:150]}")
                self.results["failed"].append({
                    "test": f"Calendar: {test_input}",
                    "error": "Event not created in database"
                })
        print()
    
    async def test_list_natural_language(self):
        """Test adding items to lists via natural language"""
        print("=" * 60)
        print("🧪 TEST 6: Shopping List Natural Language")
        print("=" * 60)
        
        list_tests = [
            "Add milk to shopping list",
            "Add eggs and bread to my shopping list",
            "I need to buy coffee",
        ]
        
        for test_input in list_tests:
            print(f"\n📝 Testing: {test_input}")
            success, response = await self.chat(test_input)
            print(f"   Response: {response[:100]}")
        print()
    
    async def test_conversation_memory(self):
        """Test if Zoe remembers earlier in the conversation"""
        print("=" * 60)
        print("🧪 TEST 7: Conversation Memory")
        print("=" * 60)
        
        # Say something
        await self.chat("I'm planning a trip to Japan next month")
        await asyncio.sleep(1)
        
        # Reference it later
        success, response = await self.chat(
            "Where am I planning to travel?",
            expected_in_response="Japan"
        )
        
        if success:
            print("✅ PASS: Zoe remembered the conversation")
        else:
            print("❌ FAIL: Zoe didn't remember")
            print(f"   Response: {response[:150]}")
        print()
    
    async def run_all_tests(self):
        """Run the complete test suite"""
        print("\n")
        print("╔" + "=" * 58 + "╗")
        print("║" + " " * 10 + "ZOE COMPREHENSIVE TEST SUITE" + " " * 20 + "║")
        print("╚" + "=" * 58 + "╝")
        print()
        
        async with httpx.AsyncClient() as client:
            self.client = client
            
            # Setup
            await self.setup_demo_user()
            
            # Run tests
            await self.test_user_identity()
            await self.test_self_facts_recall()
            await self.test_self_facts_extraction()
            await self.test_people_recall()
            await self.test_calendar_natural_language()
            await self.test_list_natural_language()
            await self.test_conversation_memory()
        
        # Summary
        self.print_summary()
    
    def print_summary(self):
        """Print test results summary"""
        print("\n")
        print("╔" + "=" * 58 + "╗")
        print("║" + " " * 20 + "TEST SUMMARY" + " " * 26 + "║")
        print("╚" + "=" * 58 + "╝")
        print()
        
        total = len(self.results["passed"]) + len(self.results["failed"])
        passed = len(self.results["passed"])
        failed = len(self.results["failed"])
        
        print(f"Total Tests: {total}")
        print(f"✅ Passed: {passed} ({passed/total*100:.1f}%)" if total > 0 else "✅ Passed: 0")
        print(f"❌ Failed: {failed} ({failed/total*100:.1f}%)" if total > 0 else "❌ Failed: 0")
        print()
        
        if self.results["failed"]:
            print("FAILED TESTS:")
            print("-" * 60)
            for failure in self.results["failed"]:
                print(f"❌ {failure.get('test', 'Unknown')}")
                if 'expected' in failure:
                    print(f"   Expected: {failure['expected']}")
                    print(f"   Got: {failure.get('got', 'N/A')}")
                if 'error' in failure:
                    print(f"   Error: {failure['error']}")
                print()
        
        # Save results to file
        with open('/app/TEST_RESULTS_COMPREHENSIVE.json', 'w') as f:
            json.dump(self.results, f, indent=2)
        print("📊 Detailed results saved to: TEST_RESULTS_COMPREHENSIVE.json")
        print()

if __name__ == "__main__":
    suite = ZoeTestSuite()
    asyncio.run(suite.run_all_tests())

