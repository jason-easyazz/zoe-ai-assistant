#!/usr/bin/env python3
"""
Quick verification script for MEM agent fixes
Tests the 11 previously failing scenarios
"""

import requests
import json
import time

MEM_AGENT_URL = "http://localhost:11435"
CHAT_API_URL = "http://localhost:8000/api/chat"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

def test_mem_agent_health():
    """Verify MEM agent is running and has all experts loaded"""
    print(f"\n{Colors.BLUE}{'='*60}{Colors.RESET}")
    print(f"{Colors.BLUE}Step 1: Checking MEM Agent Health{Colors.RESET}")
    print(f"{Colors.BLUE}{'='*60}{Colors.RESET}\n")
    
    try:
        response = requests.get(f"{MEM_AGENT_URL}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            experts = data.get("experts", [])
            
            print(f"✅ MEM Agent Status: {data.get('status')}")
            print(f"✅ Version: {data.get('version')}")
            print(f"✅ Experts Loaded: {len(experts)}")
            print(f"\nExperts:")
            for expert in experts:
                print(f"  • {expert}")
            
            # Verify all required experts are present
            required = ["list", "calendar", "memory", "planning", "reminder", "homeassistant", "journal", "birthday", "person"]
            missing = [e for e in required if e not in experts]
            
            if missing:
                print(f"\n{Colors.RED}❌ Missing experts: {', '.join(missing)}{Colors.RESET}")
                return False
            else:
                print(f"\n{Colors.GREEN}✅ All 9 experts loaded successfully!{Colors.RESET}")
                return True
        else:
            print(f"{Colors.RED}❌ Health check failed: HTTP {response.status_code}{Colors.RESET}")
            return False
    except Exception as e:
        print(f"{Colors.RED}❌ Failed to connect to MEM agent: {e}{Colors.RESET}")
        print(f"\n{Colors.YELLOW}Make sure to restart the mem-agent service:{Colors.RESET}")
        print(f"  docker compose restart mem-agent")
        return False

def test_expert_directly(expert_name, query):
    """Test a specific expert directly"""
    try:
        response = requests.post(
            f"{MEM_AGENT_URL}/experts/{expert_name}",
            json={"query": query, "user_id": "test", "execute_actions": True},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            result = data.get("result", {})
            
            success = result.get("success", False)
            message = result.get("message", "No message")
            action = result.get("action", "none")
            
            if success and action != "none":
                print(f"  {Colors.GREEN}✅ {expert_name}: {message}{Colors.RESET}")
                return True
            else:
                print(f"  {Colors.RED}❌ {expert_name}: Failed - {message}{Colors.RESET}")
                return False
        else:
            print(f"  {Colors.RED}❌ {expert_name}: HTTP {response.status_code}{Colors.RESET}")
            return False
    except Exception as e:
        print(f"  {Colors.RED}❌ {expert_name}: {e}{Colors.RESET}")
        return False

def test_chat_api(query, expected_actions=0):
    """Test via the chat API"""
    try:
        response = requests.post(
            CHAT_API_URL,
            json={"message": query, "user_id": "test"},
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            actions = data.get("actions_executed", 0)
            answer = data.get("response", "")
            
            # Check for inappropriate safety responses
            inappropriate = [
                "i cannot provide information",
                "illegal or harmful",
                "i can't help with that",
                "i can't fulfill",
                "romantic relationship with a computer"
            ]
            
            is_inappropriate = any(phrase in answer.lower() for phrase in inappropriate)
            
            if actions >= expected_actions and not is_inappropriate:
                print(f"  {Colors.GREEN}✅ Actions: {actions}, Response: {answer[:80]}...{Colors.RESET}")
                return True
            else:
                print(f"  {Colors.RED}❌ Actions: {actions} (expected {expected_actions}){Colors.RESET}")
                print(f"     Response: {answer[:120]}...")
                if is_inappropriate:
                    print(f"     {Colors.YELLOW}⚠️  Inappropriate safety filter triggered{Colors.RESET}")
                return False
        else:
            print(f"  {Colors.RED}❌ HTTP {response.status_code}{Colors.RESET}")
            return False
    except Exception as e:
        print(f"  {Colors.RED}❌ {e}{Colors.RESET}")
        return False

def main():
    print(f"\n{Colors.BLUE}{'='*60}{Colors.RESET}")
    print(f"{Colors.BLUE}🧪 MEM Agent Fix Verification{Colors.RESET}")
    print(f"{Colors.BLUE}Testing 11 previously failing scenarios{Colors.RESET}")
    print(f"{Colors.BLUE}{'='*60}{Colors.RESET}")
    
    # Step 1: Health check
    if not test_mem_agent_health():
        print(f"\n{Colors.RED}⚠️  Cannot proceed without MEM agent running{Colors.RESET}\n")
        return
    
    # Step 2: Direct expert tests
    print(f"\n{Colors.BLUE}{'='*60}{Colors.RESET}")
    print(f"{Colors.BLUE}Step 2: Testing Experts Directly{Colors.RESET}")
    print(f"{Colors.BLUE}{'='*60}{Colors.RESET}\n")
    
    expert_tests = [
        ("reminder", "Remind me tomorrow at 10am to test"),
        ("homeassistant", "Turn on the living room lights"),
        ("person", "Remember a person named Sarah who is my sister"),
        ("list", "What do I need to buy at the store?"),
    ]
    
    passed = 0
    for expert, query in expert_tests:
        if test_expert_directly(expert, query):
            passed += 1
    
    print(f"\n{Colors.BLUE}Expert Tests: {passed}/{len(expert_tests)} passed{Colors.RESET}")
    
    # Step 3: End-to-end chat API tests
    print(f"\n{Colors.BLUE}{'='*60}{Colors.RESET}")
    print(f"{Colors.BLUE}Step 3: Testing via Chat API{Colors.RESET}")
    print(f"{Colors.BLUE}{'='*60}{Colors.RESET}\n")
    
    chat_tests = [
        ("Remind me tomorrow at 10am to go shopping", 1),
        ("Turn on the living room lights", 1),
        ("Remember a person named Sarah who is my sister and loves painting", 1),
        ("What do I need to buy at the store?", 1),
        ("Add bananas to shopping list and remind me to buy them tomorrow", 1),
        ("My colleague Mike loves coffee and works in marketing", 1),
    ]
    
    chat_passed = 0
    for query, expected_actions in chat_tests:
        print(f"\nTest: \"{query}\"")
        if test_chat_api(query, expected_actions):
            chat_passed += 1
        time.sleep(1)
    
    # Summary
    print(f"\n{Colors.BLUE}{'='*60}{Colors.RESET}")
    print(f"{Colors.BLUE}📊 Summary{Colors.RESET}")
    print(f"{Colors.BLUE}{'='*60}{Colors.RESET}\n")
    
    total_tests = len(expert_tests) + len(chat_tests)
    total_passed = passed + chat_passed
    percentage = (total_passed / total_tests * 100) if total_tests > 0 else 0
    
    print(f"Direct Expert Tests: {passed}/{len(expert_tests)}")
    print(f"Chat API Tests: {chat_passed}/{len(chat_tests)}")
    print(f"Total: {total_passed}/{total_tests} ({percentage:.1f}%)")
    
    if percentage == 100:
        print(f"\n{Colors.GREEN}🎉 ALL TESTS PASSED!{Colors.RESET}")
        print(f"{Colors.GREEN}The fixes are working correctly.{Colors.RESET}\n")
    elif percentage >= 70:
        print(f"\n{Colors.YELLOW}⚠️  Most tests passed, but some fixes may need adjustment{Colors.RESET}\n")
    else:
        print(f"\n{Colors.RED}❌ Many tests failed. Check service logs:{Colors.RESET}")
        print(f"  docker logs mem-agent --tail 50")
        print(f"  docker logs zoe-core-test --tail 50\n")

if __name__ == "__main__":
    main()
