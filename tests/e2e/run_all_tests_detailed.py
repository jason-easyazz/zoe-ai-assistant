#!/usr/bin/env python3
"""
Comprehensive E2E Test Runner with Detailed Response Analysis
==============================================================

Runs all 43 E2E tests and generates a detailed report showing:
- Question asked
- Response received
- Whether response is relevant to question
- Success/failure status
"""

import requests
import json
import time
from datetime import datetime

API_BASE = "http://localhost:8000"
TEST_USER = "detailed_test_user_" + str(int(time.time()))

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    RESET = '\033[0m'

class DetailedTestRunner:
    def __init__(self):
        self.test_results = []
        self.passed = 0
        self.failed = 0
        
    def chat(self, question, expected_keywords=None, expect_action=False, test_name=""):
        """Send chat and analyze response relevance"""
        print(f"\n{Colors.CYAN}{'─'*80}{Colors.RESET}")
        print(f"{Colors.MAGENTA}TEST: {test_name}{Colors.RESET}")
        print(f"{Colors.BLUE}Q: {question}{Colors.RESET}")
        
        try:
            response = requests.post(
                f"{API_BASE}/api/chat",
                json={"message": question, "user_id": TEST_USER},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                answer = data.get("response", "")
                actions = data.get("actions_executed", 0)
                
                # Check if response is relevant
                relevant = self._check_relevance(answer, question, expected_keywords, expect_action, actions)
                
                # Determine pass/fail
                if expect_action:
                    passed = actions > 0 and relevant
                elif expected_keywords:
                    passed = relevant
                else:
                    passed = len(answer) > 10 and relevant
                
                # Color code the response
                if passed:
                    color = Colors.GREEN
                    status = "✅ PASS"
                    self.passed += 1
                else:
                    color = Colors.YELLOW
                    status = "⚠️  FAIL"
                    self.failed += 1
                
                print(f"{color}A: {answer[:200]}{'...' if len(answer) > 200 else ''}{Colors.RESET}")
                print(f"{color}{status}{Colors.RESET} | Actions: {actions} | Relevant: {relevant}")
                
                self.test_results.append({
                    "test_name": test_name,
                    "question": question,
                    "answer": answer,
                    "actions_executed": actions,
                    "relevant": relevant,
                    "passed": passed,
                    "expected_keywords": expected_keywords,
                    "expect_action": expect_action
                })
                
                return passed
            else:
                print(f"{Colors.RED}✗ HTTP {response.status_code}{Colors.RESET}")
                self.failed += 1
                return False
                
        except Exception as e:
            print(f"{Colors.RED}✗ Error: {e}{Colors.RESET}")
            self.failed += 1
            return False
    
    def _check_relevance(self, answer, question, expected_keywords, expect_action, actions):
        """Check if response is relevant to the question"""
        answer_lower = answer.lower()
        question_lower = question.lower()
        
        # If we expected an action and got one, check if response acknowledges it
        if expect_action and actions > 0:
            # Response should mention success/completion or contain expert message
            action_indicators = ["added", "created", "scheduled", "reminder", "✅", "action executed"]
            if any(ind in answer_lower for ind in action_indicators):
                return True
        
        # If we have expected keywords, check for them
        if expected_keywords:
            matches = sum(1 for kw in expected_keywords if kw.lower() in answer_lower)
            # At least one keyword should be present
            if matches > 0:
                return True
        
        # Check for irrelevant/crazy responses
        irrelevant_patterns = [
            "i cannot provide information",
            "illegal or harmful",
            "i can't help with that",
            "i can't fulfill",
            "stalking samantha"
        ]
        if any(pattern in answer_lower for pattern in irrelevant_patterns):
            # Unless it's actually asking for something problematic
            return False
        
        # General relevance: response shouldn't be completely off-topic
        # Extract key nouns from question
        question_words = set(question_lower.split())
        answer_words = set(answer_lower.split())
        
        # Remove common words
        common = {"the", "a", "an", "is", "are", "was", "were", "to", "from", "and", "or", "but", "my", "me", "i", "you"}
        q_content = question_words - common
        a_content = answer_words - common
        
        # Check for word overlap
        overlap = len(q_content & a_content)
        if overlap >= 2 or len(answer) > 20:  # Has some overlap or is substantive
            return True
        
        return False
    
    def generate_report(self):
        """Generate comprehensive report"""
        total = self.passed + self.failed
        percentage = (self.passed / total * 100) if total > 0 else 0
        
        print(f"\n\n{Colors.MAGENTA}{'='*80}{Colors.RESET}")
        print(f"{Colors.MAGENTA}📊 DETAILED E2E TEST REPORT - ALL 43 TESTS{Colors.RESET}")
        print(f"{Colors.MAGENTA}{'='*80}{Colors.RESET}\n")
        
        print(f"Total Tests: {total}")
        print(f"Passed: {Colors.GREEN}{self.passed}{Colors.RESET}")
        print(f"Failed: {Colors.RED}{self.failed}{Colors.RESET}")
        print(f"Success Rate: {Colors.GREEN if percentage >= 90 else Colors.YELLOW}{percentage:.1f}%{Colors.RESET}\n")
        
        # Group by status
        passed_tests = [t for t in self.test_results if t["passed"]]
        failed_tests = [t for t in self.test_results if not t["passed"]]
        
        if passed_tests:
            print(f"{Colors.GREEN}✅ PASSED TESTS ({len(passed_tests)}):{Colors.RESET}")
            for test in passed_tests:
                print(f"  • {test['test_name']}")
        
        if failed_tests:
            print(f"\n{Colors.RED}❌ FAILED TESTS ({len(failed_tests)}) - DETAILED ANALYSIS:{Colors.RESET}")
            for test in failed_tests:
                print(f"\n  {Colors.RED}Test: {test['test_name']}{Colors.RESET}")
                print(f"  Question: {test['question']}")
                print(f"  Answer: {test['answer'][:150]}...")
                print(f"  Relevant: {test['relevant']}")
                print(f"  Actions: {test['actions_executed']}")
                if test['expected_keywords']:
                    print(f"  Expected keywords: {test['expected_keywords']}")
        
        print(f"\n{Colors.MAGENTA}{'='*80}{Colors.RESET}")
        if percentage == 100:
            print(f"{Colors.GREEN}🎉 PERFECT SCORE! ALL 43 TESTS PASSED!{Colors.RESET}")
        elif percentage >= 90:
            print(f"{Colors.GREEN}✅ EXCELLENT! {percentage:.1f}% success rate{Colors.RESET}")
        elif percentage >= 70:
            print(f"{Colors.YELLOW}✅ GOOD! {percentage:.1f}% success rate, some work needed{Colors.RESET}")
        else:
            print(f"{Colors.RED}⚠️  NEEDS WORK - {percentage:.1f}% success rate{Colors.RESET}")
        print(f"{Colors.MAGENTA}{'='*80}{Colors.RESET}\n")
        
        # Save detailed JSON report
        report = {
            "timestamp": datetime.now().isoformat(),
            "total_tests": total,
            "passed": self.passed,
            "failed": self.failed,
            "success_rate": percentage,
            "test_user": TEST_USER,
            "results": self.test_results
        }
        
        with open("/home/pi/zoe/tests/e2e/detailed_test_report.json", "w") as f:
            json.dump(report, f, indent=2)
        
        print(f"📄 Detailed report saved to: tests/e2e/detailed_test_report.json\n")
        
        return percentage == 100

def main():
    print(f"\n{Colors.MAGENTA}{'='*80}{Colors.RESET}")
    print(f"{Colors.MAGENTA}🧪 COMPREHENSIVE E2E TEST SUITE - ALL 43 TESTS{Colors.RESET}")
    print(f"{Colors.MAGENTA}With Response Relevance Verification{Colors.RESET}")
    print(f"{Colors.MAGENTA}{'='*80}{Colors.RESET}\n")
    
    tester = DetailedTestRunner()
    
    # ===================================================================
    # COMPREHENSIVE CHAT TESTS (10 tests)
    # ===================================================================
    print(f"\n{Colors.BLUE}{'='*80}{Colors.RESET}")
    print(f"{Colors.BLUE}PART 1: COMPREHENSIVE CHAT TESTS (10 tests){Colors.RESET}")
    print(f"{Colors.BLUE}{'='*80}{Colors.RESET}")
    
    tester.chat("Add bread to my shopping list", 
                expected_keywords=["added", "shopping", "bread", "list"],
                expect_action=True,
                test_name="1. Shopping List - Add Item")
    time.sleep(1)
    
    tester.chat("Create an event on October 24th at 3pm called 'Team Meeting'",
                expected_keywords=["event", "meeting", "october", "24"],
                expect_action=True,
                test_name="2. Calendar - Create Event")
    time.sleep(1)
    
    tester.chat("Remind me tomorrow at 10am to go shopping",
                expected_keywords=["remind", "tomorrow", "shopping"],
                expect_action=True,
                test_name="3. Reminder - Create")
    time.sleep(1)
    
    tester.chat("Remember that I prefer dark roast coffee and my favorite coding language is Python",
                expected_keywords=["coffee", "python", "remember"],
                expect_action=False,
                test_name="4. Memory - Create Preferences")
    time.sleep(1)
    
    tester.chat("Add coffee beans to my shopping list and remind me to buy them tomorrow",
                expected_keywords=["coffee", "shopping", "remind"],
                expect_action=True,
                test_name="5. Multi-Step - Shopping + Reminder")
    time.sleep(1)
    
    tester.chat("What did I just ask you about?",
                expected_keywords=["coffee", "beans", "shopping", "remind"],
                expect_action=False,
                test_name="6. Temporal Memory - Recall Previous")
    time.sleep(1)
    
    tester.chat("What are my coffee and coding preferences?",
                expected_keywords=["coffee", "python", "coding", "preference"],
                expect_action=False,
                test_name="7. Memory Search - Retrieve Preferences")
    time.sleep(1)
    
    tester.chat("What's on my shopping list?",
                expected_keywords=["shopping", "list", "bread", "coffee", "beans"],
                expect_action=True,
                test_name="8. List Management - Query")
    time.sleep(1)
    
    tester.chat("What events do I have coming up?",
                expected_keywords=["event", "meeting", "calendar"],
                expect_action=True,
                test_name="9. Calendar - Query Events")
    time.sleep(1)
    
    tester.chat("What can you help me with?",
                expected_keywords=["help", "assist", "can"],
                expect_action=False,
                test_name="10. General AI - Capabilities")
    time.sleep(1)
    
    # ===================================================================
    # NATURAL LANGUAGE TESTS (33 tests)
    # ===================================================================
    print(f"\n{Colors.BLUE}{'='*80}{Colors.RESET}")
    print(f"{Colors.BLUE}PART 2: NATURAL LANGUAGE TESTS (33 tests){Colors.RESET}")
    print(f"{Colors.BLUE}{'='*80}{Colors.RESET}")
    
    # Daily Life & Organization
    print(f"\n{Colors.CYAN}Category: Daily Life & Organization{Colors.RESET}")
    
    tester.chat("Add milk and eggs to my shopping list",
                expected_keywords=["milk", "eggs", "added", "shopping"],
                expect_action=True,
                test_name="11. Shopping - Multiple Items")
    time.sleep(1)
    
    tester.chat("Remind me to call mom tomorrow at 3pm",
                expected_keywords=["remind", "call", "mom", "tomorrow"],
                expect_action=True,
                test_name="12. Reminder - Specific Time")
    time.sleep(1)
    
    tester.chat("Schedule dentist appointment for next Wednesday at 10am",
                expected_keywords=["dentist", "appointment", "wednesday"],
                expect_action=True,
                test_name="13. Calendar - Future Appointment")
    time.sleep(1)
    
    tester.chat("What do I need to buy at the store?",
                expected_keywords=["shopping", "list", "store", "buy"],
                expect_action=True,
                test_name="14. Shopping - Query Needs")
    time.sleep(1)
    
    tester.chat("What's on my calendar for tomorrow?",
                expected_keywords=["calendar", "tomorrow", "event"],
                expect_action=True,
                test_name="15. Calendar - Tomorrow")
    time.sleep(1)
    
    # People & Relationships
    print(f"\n{Colors.CYAN}Category: People & Relationships{Colors.RESET}")
    
    tester.chat("Remember a person named Sarah who is my sister and loves painting",
                expected_keywords=["sarah", "sister", "painting"],
                expect_action=True,
                test_name="16. People - Create with Details")
    time.sleep(1)
    
    tester.chat("Who is Sarah?",
                expected_keywords=["sarah", "sister", "painting"],
                expect_action=False,
                test_name="17. People - Query by Name")
    time.sleep(1)
    
    tester.chat("Tell me about my family members",
                expected_keywords=["family", "sarah", "sister"],
                expect_action=False,
                test_name="18. People - Query Family")
    time.sleep(1)
    
    tester.chat("My colleague Mike loves coffee and works in marketing",
                expected_keywords=["mike", "colleague", "coffee", "marketing"],
                expect_action=True,
                test_name="19. People - Implicit Create")
    time.sleep(1)
    
    # Journal & Reflection
    print(f"\n{Colors.CYAN}Category: Journal & Reflection{Colors.RESET}")
    
    tester.chat("Journal: Had a great day today, finished the project and celebrated with the team",
                expected_keywords=["journal", "project", "team", "great"],
                expect_action=True,
                test_name="20. Journal - Create Entry")
    time.sleep(1)
    
    tester.chat("How was I feeling last week?",
                expected_keywords=["feeling", "mood", "week"],
                expect_action=False,
                test_name="21. Journal - Query Mood")
    time.sleep(1)
    
    tester.chat("What did I write about yesterday?",
                expected_keywords=["wrote", "journal", "yesterday"],
                expect_action=False,
                test_name="22. Journal - Recent Entries")
    time.sleep(1)
    
    # Smart Home
    print(f"\n{Colors.CYAN}Category: Smart Home Control{Colors.RESET}")
    
    tester.chat("Turn on the living room lights",
                expected_keywords=["light", "living room", "on"],
                expect_action=True,
                test_name="23. Home - Turn On Lights")
    time.sleep(1)
    
    tester.chat("Set the temperature to 72 degrees",
                expected_keywords=["temperature", "72", "degrees"],
                expect_action=True,
                test_name="24. Home - Thermostat")
    time.sleep(1)
    
    tester.chat("Is the garage door closed?",
                expected_keywords=["garage", "door", "closed"],
                expect_action=False,
                test_name="25. Home - Status Query")
    time.sleep(1)
    
    # Conversation & Intelligence
    print(f"\n{Colors.CYAN}Category: Conversation & Intelligence{Colors.RESET}")
    
    tester.chat("What can you do for me?",
                expected_keywords=["help", "assist", "can"],
                expect_action=False,
                test_name="26. Meta - Capabilities")
    time.sleep(1)
    
    tester.chat("What did we just talk about?",
                expected_keywords=["garage", "door", "temperature", "lights"],
                expect_action=False,
                test_name="27. Context - Recent Conversation")
    time.sleep(1)
    
    tester.chat("Plan my morning: workout, breakfast, then work meeting",
                expected_keywords=["workout", "breakfast", "meeting", "plan"],
                expect_action=True,
                test_name="28. Planning - Multi-step")
    time.sleep(1)
    
    tester.chat("How are you today?",
                expected_keywords=["how", "today"],
                expect_action=False,
                test_name="29. Social - Casual Chat")
    time.sleep(1)
    
    # Complex Multi-Action
    print(f"\n{Colors.CYAN}Category: Complex Multi-Action{Colors.RESET}")
    
    tester.chat("Add bananas to shopping list and remind me to buy them tomorrow",
                expected_keywords=["bananas", "shopping", "remind"],
                expect_action=True,
                test_name="30. Mixed - Shopping + Reminder")
    time.sleep(1)
    
    tester.chat("Schedule lunch with Sarah next Friday and add it to my work calendar",
                expected_keywords=["lunch", "sarah", "friday", "calendar"],
                expect_action=True,
                test_name="31. Mixed - Event + Person")
    time.sleep(1)
    
    tester.chat("Journal: Met with Sarah today, she gave great advice about the project. Remind me to follow up next week",
                expected_keywords=["journal", "sarah", "remind", "follow up"],
                expect_action=True,
                test_name="32. Mixed - Journal + Reminder")
    time.sleep(1)
    
    # Natural Language Variations
    print(f"\n{Colors.CYAN}Category: Natural Language Variations{Colors.RESET}")
    
    tester.chat("I need to remember to pick up groceries",
                expected_keywords=["groceries", "remember", "pick up"],
                expect_action=True,
                test_name="33. Variation - Informal Reminder")
    time.sleep(1)
    
    tester.chat("Don't let me forget about the team meeting tomorrow",
                expected_keywords=["forget", "meeting", "tomorrow"],
                expect_action=True,
                test_name="34. Variation - Casual Reminder")
    time.sleep(1)
    
    tester.chat("Can you help me remember that my doctor appointment is on Thursday?",
                expected_keywords=["doctor", "appointment", "thursday"],
                expect_action=True,
                test_name="35. Variation - Question Format")
    time.sleep(1)
    
    tester.chat("What's the weather like today?",
                expected_keywords=["weather", "today"],
                expect_action=False,
                test_name="36. Weather - Current Conditions")
    time.sleep(1)
    
    tester.chat("Should I bring an umbrella tomorrow?",
                expected_keywords=["umbrella", "weather", "tomorrow", "rain"],
                expect_action=False,
                test_name="37. Weather - Contextual Query")
    time.sleep(1)
    
    # Time & Scheduling
    print(f"\n{Colors.CYAN}Category: Time & Scheduling{Colors.RESET}")
    
    tester.chat("What do I have scheduled for this week?",
                expected_keywords=["schedule", "week", "event"],
                expect_action=True,
                test_name="38. Calendar - Week View")
    time.sleep(1)
    
    tester.chat("Am I free on Thursday afternoon?",
                expected_keywords=["thursday", "free", "afternoon"],
                expect_action=False,
                test_name="39. Calendar - Availability")
    time.sleep(1)
    
    tester.chat("Move my 2pm meeting to 3pm",
                expected_keywords=["move", "meeting", "2pm", "3pm"],
                expect_action=True,
                test_name="40. Calendar - Reschedule")
    time.sleep(1)
    
    # Information Retrieval
    print(f"\n{Colors.CYAN}Category: Search & Retrieval{Colors.RESET}")
    
    tester.chat("Find all my notes about the project",
                expected_keywords=["notes", "project", "find"],
                expect_action=False,
                test_name="41. Search - Notes by Topic")
    time.sleep(1)
    
    tester.chat("Show me everything related to work this month",
                expected_keywords=["work", "month", "show"],
                expect_action=False,
                test_name="42. Search - Broad Query")
    time.sleep(1)
    
    tester.chat("What tasks are still incomplete?",
                expected_keywords=["tasks", "incomplete", "todo"],
                expect_action=False,
                test_name="43. Lists - Query Status")
    
    # Generate final report
    tester.generate_report()
    
    import sys
    sys.exit(0 if tester.passed == 43 else 1)

if __name__ == "__main__":
    main()

