#!/usr/bin/env python3
"""
Comprehensive Natural Language E2E Tests
=========================================

Tests what REAL USERS would actually ask Zoe:
- Daily life queries
- Common requests
- Natural conversational patterns
- Edge cases and variations

Goal: 100% coverage of user scenarios with natural language
"""

import requests
import json
import time
from datetime import datetime, timedelta

API_BASE = "http://localhost:8000"
TEST_USER = "natural_test_user"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'

class NaturalLanguageTester:
    def __init__(self):
        self.results = []
        self.passed = 0
        self.failed = 0
    
    def chat(self, message, description="", expect_action=False):
        """Send chat message and evaluate response"""
        print(f"\n{Colors.BLUE}{'─'*70}{Colors.RESET}")
        print(f"{Colors.MAGENTA}{description}{Colors.RESET}")
        print(f"{Colors.BLUE}User: \"{message}\"{Colors.RESET}")
        
        try:
            response = requests.post(
                f"{API_BASE}/api/chat",
                json={"message": message, "user_id": TEST_USER},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                reply = data.get("response", "")
                actions = data.get("actions_executed", 0)
                
                print(f"{Colors.GREEN}Zoe: {reply[:150]}{'...' if len(reply) > 150 else ''}{Colors.RESET}")
                
                if expect_action and actions > 0:
                    print(f"{Colors.GREEN}  ✓ Actions executed: {actions}{Colors.RESET}")
                    self.passed += 1
                    self.results.append((description, "PASS", reply[:100]))
                    return True
                elif not expect_action and len(reply) > 10:
                    print(f"{Colors.GREEN}  ✓ Response received{Colors.RESET}")
                    self.passed += 1
                    self.results.append((description, "PASS", reply[:100]))
                    return True
                else:
                    print(f"{Colors.YELLOW}  ⚠ Unexpected result{Colors.RESET}")
                    self.failed += 1
                    self.results.append((description, "PARTIAL", reply[:100]))
                    return False
            else:
                print(f"{Colors.RED}  ✗ HTTP {response.status_code}{Colors.RESET}")
                self.failed += 1
                self.results.append((description, "FAIL", f"HTTP {response.status_code}"))
                return False
        except Exception as e:
            print(f"{Colors.RED}  ✗ Error: {e}{Colors.RESET}")
            self.failed += 1
            self.results.append((description, "FAIL", str(e)))
            return False
    
    def run_all_tests(self):
        """Run comprehensive natural language test suite"""
        
        print(f"\n{Colors.MAGENTA}{'='*70}{Colors.RESET}")
        print(f"{Colors.MAGENTA}🧪 COMPREHENSIVE NATURAL LANGUAGE E2E TESTS{Colors.RESET}")
        print(f"{Colors.MAGENTA}Testing what REAL USERS would actually ask{Colors.RESET}")
        print(f"{Colors.MAGENTA}{'='*70}{Colors.RESET}\n")
        
        # === CATEGORY 1: DAILY LIFE ORGANIZATION ===
        print(f"\n{Colors.BLUE}📅 CATEGORY: Daily Life & Organization{Colors.RESET}")
        
        self.chat("Add milk and eggs to my shopping list", 
                 "Shopping - Basic items", expect_action=True)
        
        self.chat("Remind me to call mom tomorrow at 3pm",
                 "Reminder - Specific time", expect_action=True)
        
        self.chat("Schedule dentist appointment for next Wednesday at 10am",
                 "Calendar - Future appointment", expect_action=True)
        
        self.chat("What do I need to buy at the store?",
                 "Shopping - List retrieval", expect_action=False)
        
        self.chat("What's on my calendar for tomorrow?",
                 "Calendar - Query future", expect_action=False)
        
        # === CATEGORY 2: PEOPLE & RELATIONSHIPS ===
        print(f"\n{Colors.BLUE}👥 CATEGORY: People & Relationships{Colors.RESET}")
        
        self.chat("Remember a person named Sarah who is my sister and loves painting",
                 "People - Create with details", expect_action=True)
        
        self.chat("Who is Sarah?",
                 "People - Query by name", expect_action=False)
        
        self.chat("Tell me about my family members",
                 "People - Query by relationship", expect_action=False)
        
        self.chat("My colleague Mike loves coffee and works in marketing",
                 "People - Implicit create", expect_action=True)
        
        # === CATEGORY 3: JOURNALING & REFLECTION ===
        print(f"\n{Colors.BLUE}📖 CATEGORY: Journal & Reflection{Colors.RESET}")
        
        self.chat("Journal: Had a great day today, finished the project and celebrated with the team",
                 "Journal - Create entry", expect_action=True)
        
        self.chat("How was I feeling last week?",
                 "Journal - Query mood", expect_action=False)
        
        self.chat("What did I write about yesterday?",
                 "Journal - Recent entries", expect_action=False)
        
        # === CATEGORY 4: SMART HOME ===
        print(f"\n{Colors.BLUE}🏠 CATEGORY: Smart Home Control{Colors.RESET}")
        
        self.chat("Turn on the living room lights",
                 "Home - Control device", expect_action=True)
        
        self.chat("Set the temperature to 72 degrees",
                 "Home - Thermostat control", expect_action=True)
        
        self.chat("Is the garage door closed?",
                 "Home - Status query", expect_action=False)
        
        # === CATEGORY 5: CONVERSATIONAL & COMPLEX ===
        print(f"\n{Colors.BLUE}💬 CATEGORY: Conversation & Intelligence{Colors.RESET}")
        
        self.chat("What can you help me with?",
                 "Meta - Capabilities", expect_action=False)
        
        self.chat("What did we just talk about?",
                 "Context - Recent conversation", expect_action=False)
        
        self.chat("Plan my morning: workout, breakfast, then work meeting",
                 "Planning - Multi-step", expect_action=True)
        
        self.chat("How are you today?",
                 "Social - Casual chat", expect_action=False)
        
        # === CATEGORY 6: MIXED COMMANDS ===
        print(f"\n{Colors.BLUE}🎯 CATEGORY: Complex Multi-Action{Colors.RESET}")
        
        self.chat("Add coffee to shopping list and remind me to buy it tomorrow",
                 "Mixed - Shopping + Reminder", expect_action=True)
        
        self.chat("Schedule lunch with Sarah next Friday and add it to my work calendar",
                 "Mixed - Event + Person reference", expect_action=True)
        
        self.chat("Journal: Met with Sarah today, she gave great advice about the project. Remind me to follow up next week",
                 "Mixed - Journal + Reminder", expect_action=True)
        
        # === CATEGORY 7: VARIATIONS & EDGE CASES ===
        print(f"\n{Colors.BLUE}🔄 CATEGORY: Natural Language Variations{Colors.RESET}")
        
        self.chat("I need to remember to pick up bread",
                 "Variation - Informal reminder", expect_action=True)
        
        self.chat("Don't let me forget about the team meeting tomorrow",
                 "Variation - Casual reminder", expect_action=True)
        
        self.chat("Can you help me remember John's birthday is April 15th?",
                 "Variation - Question format", expect_action=True)
        
        self.chat("What's the weather like today?",
                 "Weather - Current conditions", expect_action=False)
        
        self.chat("Should I bring an umbrella tomorrow?",
                 "Weather - Contextual query", expect_action=False)
        
        # === CATEGORY 8: TIME & SCHEDULING ===
        print(f"\n{Colors.BLUE}⏰ CATEGORY: Time & Scheduling{Colors.RESET}")
        
        self.chat("What do I have scheduled for this week?",
                 "Calendar - Week view", expect_action=False)
        
        self.chat("Am I free on Thursday afternoon?",
                 "Calendar - Availability check", expect_action=False)
        
        self.chat("Move my 2pm meeting to 3pm",
                 "Calendar - Reschedule", expect_action=True)
        
        # === CATEGORY 9: INFORMATION RETRIEVAL ===
        print(f"\n{Colors.BLUE}🔍 CATEGORY: Search & Retrieval{Colors.RESET}")
        
        self.chat("Find all my notes about the project",
                 "Search - Notes by topic", expect_action=False)
        
        self.chat("Show me everything related to work this month",
                 "Search - Broad query", expect_action=False)
        
        self.chat("What tasks are still incomplete?",
                 "Lists - Query status", expect_action=False)
        
        # === SUMMARY ===
        self.print_summary()
    
    def print_summary(self):
        """Print test results summary"""
        total = self.passed + self.failed
        percentage = (self.passed / total * 100) if total > 0 else 0
        
        print(f"\n{Colors.MAGENTA}{'='*70}{Colors.RESET}")
        print(f"{Colors.MAGENTA}📊 NATURAL LANGUAGE E2E TEST RESULTS{Colors.RESET}")
        print(f"{Colors.MAGENTA}{'='*70}{Colors.RESET}\n")
        
        print(f"Total Tests: {total}")
        print(f"Passed: {Colors.GREEN}{self.passed}{Colors.RESET}")
        print(f"Failed: {Colors.RED}{self.failed}{Colors.RESET}")
        print(f"Success Rate: {Colors.GREEN if percentage >= 80 else Colors.YELLOW}{percentage:.1f}%{Colors.RESET}\n")
        
        if self.failed > 0:
            print(f"{Colors.RED}❌ FAILED/PARTIAL TESTS:{Colors.RESET}")
            for desc, status, msg in self.results:
                if status in ["FAIL", "PARTIAL"]:
                    print(f"  • {desc}: {status}")
        
        print(f"\n{Colors.MAGENTA}{'='*70}{Colors.RESET}")
        if percentage >= 90:
            print(f"{Colors.GREEN}🎉 EXCELLENT! Natural language understanding is working!{Colors.RESET}")
        elif percentage >= 70:
            print(f"{Colors.YELLOW}✅ GOOD! Most natural language working, some improvements needed{Colors.RESET}")
        else:
            print(f"{Colors.RED}⚠️  NEEDS WORK - Many natural language patterns failing{Colors.RESET}")
        print(f"{Colors.MAGENTA}{'='*70}{Colors.RESET}\n")
        
        # Save report
        report = {
            "test_date": datetime.now().isoformat(),
            "total_tests": total,
            "passed": self.passed,
            "failed": self.failed,
            "success_rate": percentage,
            "results": [
                {"description": desc, "status": status, "response": msg}
                for desc, status, msg in self.results
            ]
        }
        
        with open("/home/pi/zoe/tests/e2e/natural_language_test_report.json", "w") as f:
            json.dump(report, f, indent=2)
        
        print(f"📄 Report saved to: tests/e2e/natural_language_test_report.json\n")
        
        return percentage >= 80

if __name__ == "__main__":
    print(f"\n{Colors.MAGENTA}{'='*70}{Colors.RESET}")
    print(f"{Colors.MAGENTA}🎯 NATURAL LANGUAGE E2E TEST SUITE{Colors.RESET}")
    print(f"{Colors.MAGENTA}Testing REAL user queries in natural language{Colors.RESET}")
    print(f"{Colors.MAGENTA}{'='*70}{Colors.RESET}\n")
    print("This test covers:")
    print("  • Daily life organization (shopping, calendar, reminders)")
    print("  • People & relationships")
    print("  • Journaling & reflection")
    print("  • Smart home control")
    print("  • Conversational AI")
    print("  • Complex multi-action requests")
    print("  • Natural language variations")
    print("  • Time & scheduling")
    print("  • Information retrieval")
    print(f"\n{Colors.YELLOW}🚀 Starting automated testing...{Colors.RESET}")
    time.sleep(2)
    
    tester = NaturalLanguageTester()
    success = tester.run_all_tests()
    
    import sys
    sys.exit(0 if success else 1)

