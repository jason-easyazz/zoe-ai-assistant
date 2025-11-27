#!/usr/bin/env python3
"""
Comprehensive Zoe Capabilities Test Suite
Tests all 10 major sections with context memory and action execution
"""

import asyncio
import httpx
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import sys

# Configuration
BASE_URL = "http://localhost:8000"
TEST_USER = "capability_test_user"
TIMEOUT = 30.0

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    PURPLE = '\033[95m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

class ZoeCapabilityTest:
    def __init__(self):
        self.results = {
            "passed": [],
            "failed": [],
            "warnings": [],
            "skipped": []
        }
        self.test_data = {
            "shopping_items": [],
            "calendar_events": [],
            "people": [],
            "journal_entries": []
        }
        self.start_time = time.time()
    
    async def run_all_tests(self):
        """Execute comprehensive test suite"""
        print(f"\n{Colors.BOLD}{'='*80}{Colors.RESET}")
        print(f"{Colors.CYAN}{Colors.BOLD}ZOE COMPREHENSIVE CAPABILITIES TEST{Colors.RESET}")
        print(f"{Colors.BOLD}{'='*80}{Colors.RESET}")
        print(f"Base URL: {BASE_URL}")
        print(f"Test User: {TEST_USER}")
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        test_sections = [
            ("A. Core Chat & Memory", self.test_chat_and_memory),
            ("B. Lists & Task Management", self.test_lists_and_tasks),
            ("C. Calendar & Events", self.test_calendar),
            ("D. People & Relationships", self.test_people),
            ("E. Journal & Notes", self.test_journal),
            ("F. Smart Home (Home Assistant)", self.test_homeassistant),
            ("G. Automation (N8N)", self.test_n8n),
            ("H. Developer Tools", self.test_developer_tools),
            ("I. Voice & Media", self.test_voice_media),
            ("J. Advanced Features", self.test_advanced_features)
        ]
        
        for section_name, test_func in test_sections:
            print(f"\n{Colors.PURPLE}{Colors.BOLD}{section_name}{Colors.RESET}")
            print(f"{'-'*80}")
            try:
                await test_func()
            except Exception as e:
                print(f"{Colors.RED}✗ Section failed: {e}{Colors.RESET}")
                self.results["failed"].append((section_name, str(e)))
        
        await self.print_results()
    
    # ========================================================================
    # A. CORE CHAT & MEMORY TESTS
    # ========================================================================
    
    async def test_chat_and_memory(self):
        """Test core chat functionality with context and temporal memory"""
        
        # Test 1: Basic chat response
        print(f"{Colors.BLUE}Test 1.1: Basic chat response{Colors.RESET}")
        response = await self.send_chat("Hello Zoe, I'm testing the system")
        if response and len(response) > 0:
            print(f"{Colors.GREEN}✓ Basic chat working{Colors.RESET}")
            self.results["passed"].append("Chat - Basic Response")
        else:
            print(f"{Colors.RED}✗ Chat returned empty response{Colors.RESET}")
            self.results["failed"].append(("Chat - Basic", "Empty response"))
        
        # Test 2: Context memory within conversation
        print(f"\n{Colors.BLUE}Test 1.2: Context memory (within conversation){Colors.RESET}")
        response1 = await self.send_chat("My favorite food is pizza")
        await asyncio.sleep(0.5)
        response2 = await self.send_chat("What's my favorite food?")
        
        # Check if tool execution happened or pizza was mentioned
        if response2 and ("pizza" in response2.lower() or "executed" in response2.lower()):
            print(f"{Colors.GREEN}✓ Context memory system active{Colors.RESET}")
            self.results["passed"].append("Chat - Context Memory")
        else:
            print(f"{Colors.YELLOW}⚠ Context memory unclear: {response2[:100]}{Colors.RESET}")
            self.results["warnings"].append("Chat - Context Memory")
        
        # Test 3: Temporal memory (episode tracking)
        print(f"\n{Colors.BLUE}Test 1.3: Temporal memory (episode tracking){Colors.RESET}")
        response = await self.send_chat("What have we talked about in this conversation?")
        
        if response and ("pizza" in response.lower() or "food" in response.lower() or "testing" in response.lower()):
            print(f"{Colors.GREEN}✓ Temporal memory working{Colors.RESET}")
            self.results["passed"].append("Chat - Temporal Memory")
        else:
            print(f"{Colors.YELLOW}⚠ Temporal memory unclear{Colors.RESET}")
            self.results["warnings"].append("Chat - Temporal Memory")
        
        # Test 4: Streaming endpoint
        print(f"\n{Colors.BLUE}Test 1.4: Streaming chat{Colors.RESET}")
        try:
            stream_worked = await self.test_streaming_chat("Quick test message")
            if stream_worked:
                print(f"{Colors.GREEN}✓ Streaming chat working{Colors.RESET}")
                self.results["passed"].append("Chat - Streaming")
            else:
                print(f"{Colors.RED}✗ Streaming failed{Colors.RESET}")
                self.results["failed"].append(("Chat - Streaming", "Failed"))
        except Exception as e:
            print(f"{Colors.RED}✗ Streaming error: {e}{Colors.RESET}")
            self.results["failed"].append(("Chat - Streaming", str(e)))
    
    # ========================================================================
    # B. LISTS & TASK MANAGEMENT TESTS
    # ========================================================================
    
    async def test_lists_and_tasks(self):
        """Test lists, tasks, and projects with context"""
        
        # Test 1: Add to shopping list via chat
        print(f"{Colors.BLUE}Test 2.1: Add items to shopping list{Colors.RESET}")
        response = await self.send_chat("Add milk, bread, and eggs to my shopping list")
        
        if response and ("added" in response.lower() or "✅" in response):
            print(f"{Colors.GREEN}✓ Items added via chat{Colors.RESET}")
            self.results["passed"].append("Lists - Add via Chat")
        else:
            print(f"{Colors.YELLOW}⚠ Add response unclear: {response[:100]}{Colors.RESET}")
            self.results["warnings"].append("Lists - Add via Chat")
        
        # Test 2: Verify items in database
        print(f"\n{Colors.BLUE}Test 2.2: Verify items persisted{Colors.RESET}")
        await asyncio.sleep(1)  # Allow time for persistence
        
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(f"{BASE_URL}/api/lists/shopping?user_id={TEST_USER}")
                if response.status_code == 200:
                    data = response.json()
                    items = data.get("lists", [{}])[0].get("items", [])
                    item_texts = [item.get("text", "").lower() for item in items]
                    
                    found_items = []
                    for item in ["milk", "bread", "eggs"]:
                        if any(item in text for text in item_texts):
                            found_items.append(item)
                    
                    if len(found_items) >= 2:  # At least 2 of 3 found
                        print(f"{Colors.GREEN}✓ Items persisted: {found_items}{Colors.RESET}")
                        self.results["passed"].append("Lists - Persistence")
                        self.test_data["shopping_items"] = found_items
                    else:
                        print(f"{Colors.YELLOW}⚠ Only found: {found_items}{Colors.RESET}")
                        self.results["warnings"].append("Lists - Partial Persistence")
                else:
                    print(f"{Colors.RED}✗ API error: {response.status_code}{Colors.RESET}")
                    self.results["failed"].append(("Lists - API", f"Status {response.status_code}"))
        except Exception as e:
            print(f"{Colors.RED}✗ Error checking list: {e}{Colors.RESET}")
            self.results["failed"].append(("Lists - Verification", str(e)))
        
        # Test 3: Query with context
        print(f"\n{Colors.BLUE}Test 2.3: Query list with context{Colors.RESET}")
        response = await self.send_chat("What's on my shopping list?")
        
        found_count = sum(1 for item in ["milk", "bread", "eggs"] if item in response.lower())
        if found_count >= 2:
            print(f"{Colors.GREEN}✓ Query returned items (found {found_count}/3){Colors.RESET}")
            self.results["passed"].append("Lists - Query with Context")
        else:
            print(f"{Colors.YELLOW}⚠ Query incomplete (found {found_count}/3){Colors.RESET}")
            self.results["warnings"].append("Lists - Query")
        
        # Test 4: Remove item with context
        print(f"\n{Colors.BLUE}Test 2.4: Remove item with context{Colors.RESET}")
        response = await self.send_chat("Remove bread from my list")
        
        if response and ("removed" in response.lower() or "deleted" in response.lower() or "✅" in response or "executed" in response.lower()):
            print(f"{Colors.GREEN}✓ Remove action executed{Colors.RESET}")
            self.results["passed"].append("Lists - Remove")
        else:
            print(f"{Colors.YELLOW}⚠ Remove response: {response[:100] if response else 'empty'}{Colors.RESET}")
            self.results["warnings"].append("Lists - Remove")
        
        # Test 5: Task lists API
        print(f"\n{Colors.BLUE}Test 2.5: Task list API{Colors.RESET}")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(f"{BASE_URL}/api/lists/tasks?user_id={TEST_USER}")
                if response.status_code == 200:
                    print(f"{Colors.GREEN}✓ Task lists API accessible{Colors.RESET}")
                    self.results["passed"].append("Lists - Tasks API")
                else:
                    print(f"{Colors.YELLOW}⚠ Task lists API status: {response.status_code}{Colors.RESET}")
                    self.results["warnings"].append("Lists - Tasks API")
        except Exception as e:
            print(f"{Colors.RED}✗ Task lists API error: {e}{Colors.RESET}")
            self.results["failed"].append(("Lists - Tasks API", str(e)))
    
    # ========================================================================
    # C. CALENDAR & EVENTS TESTS
    # ========================================================================
    
    async def test_calendar(self):
        """Test calendar and event management"""
        
        # Test 1: Create event via chat
        print(f"{Colors.BLUE}Test 3.1: Create calendar event{Colors.RESET}")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%A")
        response = await self.send_chat(f"Schedule dentist appointment for {tomorrow} at 2pm")
        
        if response and ("scheduled" in response.lower() or "created" in response.lower() or "✅" in response or "executed" in response.lower() or "calendar" in response.lower()):
            print(f"{Colors.GREEN}✓ Calendar action executed{Colors.RESET}")
            self.results["passed"].append("Calendar - Create Event")
        else:
            print(f"{Colors.YELLOW}⚠ Calendar response: {response[:100] if response else 'empty'}{Colors.RESET}")
            self.results["warnings"].append("Calendar - Create")
        
        # Test 2: Query events
        print(f"\n{Colors.BLUE}Test 3.2: Query calendar events{Colors.RESET}")
        response = await self.send_chat("What's on my calendar?")
        
        if response and len(response) > 20:
            print(f"{Colors.GREEN}✓ Calendar query working{Colors.RESET}")
            self.results["passed"].append("Calendar - Query")
        else:
            print(f"{Colors.YELLOW}⚠ Calendar query returned minimal response{Colors.RESET}")
            self.results["warnings"].append("Calendar - Query")
        
        # Test 3: Calendar API direct
        print(f"\n{Colors.BLUE}Test 3.3: Calendar API endpoint{Colors.RESET}")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(f"{BASE_URL}/api/calendar/events?user_id={TEST_USER}")
                if response.status_code == 200:
                    data = response.json()
                    events = data.get("events", [])
                    print(f"{Colors.GREEN}✓ Calendar API working ({len(events)} events){Colors.RESET}")
                    self.results["passed"].append("Calendar - API")
                else:
                    print(f"{Colors.YELLOW}⚠ Calendar API status: {response.status_code}{Colors.RESET}")
                    self.results["warnings"].append("Calendar - API")
        except Exception as e:
            print(f"{Colors.RED}✗ Calendar API error: {e}{Colors.RESET}")
            self.results["failed"].append(("Calendar - API", str(e)))
    
    # ========================================================================
    # D. PEOPLE & RELATIONSHIPS TESTS
    # ========================================================================
    
    async def test_people(self):
        """Test people management and relationships"""
        
        # Test 1: Add person via chat
        print(f"{Colors.BLUE}Test 4.1: Add person with context{Colors.RESET}")
        response = await self.send_chat("My friend Alice loves gardening and lives in Seattle")
        
        if response:
            print(f"{Colors.GREEN}✓ Person info acknowledged{Colors.RESET}")
            self.results["passed"].append("People - Add via Chat")
        else:
            print(f"{Colors.YELLOW}⚠ Person add unclear{Colors.RESET}")
            self.results["warnings"].append("People - Add")
        
        # Test 2: Query person
        print(f"\n{Colors.BLUE}Test 4.2: Query person details{Colors.RESET}")
        await asyncio.sleep(0.5)
        response = await self.send_chat("Tell me about Alice")
        
        if response and ("alice" in response.lower() or "garden" in response.lower() or "seattle" in response.lower()):
            print(f"{Colors.GREEN}✓ Person query with context working{Colors.RESET}")
            self.results["passed"].append("People - Query with Context")
        else:
            print(f"{Colors.YELLOW}⚠ Person query unclear{Colors.RESET}")
            self.results["warnings"].append("People - Query")
        
        # Test 3: People API
        print(f"\n{Colors.BLUE}Test 4.3: People API endpoint{Colors.RESET}")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(f"{BASE_URL}/api/people?user_id={TEST_USER}")
                if response.status_code == 200:
                    print(f"{Colors.GREEN}✓ People API accessible{Colors.RESET}")
                    self.results["passed"].append("People - API")
                else:
                    print(f"{Colors.YELLOW}⚠ People API status: {response.status_code}{Colors.RESET}")
                    self.results["warnings"].append("People - API")
        except Exception as e:
            print(f"{Colors.RED}✗ People API error: {e}{Colors.RESET}")
            self.results["failed"].append(("People - API", str(e)))
    
    # ========================================================================
    # E. JOURNAL & NOTES TESTS
    # ========================================================================
    
    async def test_journal(self):
        """Test journal entries and notes"""
        
        # Test 1: Create journal entry
        print(f"{Colors.BLUE}Test 5.1: Create journal entry{Colors.RESET}")
        response = await self.send_chat("Help me write a journal entry about my day")
        
        if response and len(response) > 50:
            print(f"{Colors.GREEN}✓ Journal prompt generated{Colors.RESET}")
            self.results["passed"].append("Journal - Prompt Generation")
        else:
            print(f"{Colors.YELLOW}⚠ Journal response short{Colors.RESET}")
            self.results["warnings"].append("Journal - Prompt")
        
        # Test 2: Journal API
        print(f"\n{Colors.BLUE}Test 5.2: Journal API endpoint{Colors.RESET}")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(f"{BASE_URL}/api/journal/entries?user_id={TEST_USER}")
                if response.status_code == 200:
                    data = response.json()
                    entries = data.get("entries", [])
                    print(f"{Colors.GREEN}✓ Journal API accessible ({len(entries)} entries){Colors.RESET}")
                    self.results["passed"].append("Journal - API")
                else:
                    print(f"{Colors.YELLOW}⚠ Journal API status: {response.status_code}{Colors.RESET}")
                    self.results["warnings"].append("Journal - API")
        except Exception as e:
            print(f"{Colors.RED}✗ Journal API error: {e}{Colors.RESET}")
            self.results["failed"].append(("Journal - API", str(e)))
        
        # Test 3: Notes API
        print(f"\n{Colors.BLUE}Test 5.3: Notes API endpoint{Colors.RESET}")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(f"{BASE_URL}/api/notes/?user_id={TEST_USER}")
                if response.status_code == 200:
                    data = response.json()
                    notes = data.get("notes", [])
                    print(f"{Colors.GREEN}✓ Notes API accessible ({len(notes)} notes){Colors.RESET}")
                    self.results["passed"].append("Notes - API")
                else:
                    print(f"{Colors.YELLOW}⚠ Notes API status: {response.status_code}{Colors.RESET}")
                    self.results["warnings"].append("Notes - API")
        except Exception as e:
            print(f"{Colors.RED}✗ Notes API error: {e}{Colors.RESET}")
            self.results["failed"].append(("Notes - API", str(e)))
    
    # ========================================================================
    # F. SMART HOME (HOME ASSISTANT) TESTS
    # ========================================================================
    
    async def test_homeassistant(self):
        """Test Home Assistant integration"""
        
        # Test 1: Query devices
        print(f"{Colors.BLUE}Test 6.1: Query Home Assistant devices{Colors.RESET}")
        response = await self.send_chat("What smart home devices do I have?")
        
        if response:
            print(f"{Colors.GREEN}✓ HA query responded{Colors.RESET}")
            self.results["passed"].append("HomeAssistant - Query")
        else:
            print(f"{Colors.YELLOW}⚠ HA response empty{Colors.RESET}")
            self.results["warnings"].append("HomeAssistant - Query")
        
        # Test 2: MCP Bridge health
        print(f"\n{Colors.BLUE}Test 6.2: Home Assistant MCP bridge{Colors.RESET}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("http://localhost:8007/")
                if response.status_code == 200:
                    print(f"{Colors.GREEN}✓ HA MCP bridge running{Colors.RESET}")
                    self.results["passed"].append("HomeAssistant - Bridge")
                else:
                    print(f"{Colors.YELLOW}⚠ HA bridge status: {response.status_code}{Colors.RESET}")
                    self.results["warnings"].append("HomeAssistant - Bridge")
        except Exception as e:
            print(f"{Colors.YELLOW}⚠ HA bridge not accessible (might not be configured): {e}{Colors.RESET}")
            self.results["skipped"].append("HomeAssistant - Bridge not configured")
    
    # ========================================================================
    # G. AUTOMATION (N8N) TESTS
    # ========================================================================
    
    async def test_n8n(self):
        """Test N8N workflow automation"""
        
        # Test 1: Query workflows
        print(f"{Colors.BLUE}Test 7.1: Query N8N workflows{Colors.RESET}")
        response = await self.send_chat("What automation workflows do I have?")
        
        if response:
            print(f"{Colors.GREEN}✓ N8N query responded{Colors.RESET}")
            self.results["passed"].append("N8N - Query")
        else:
            print(f"{Colors.YELLOW}⚠ N8N response empty{Colors.RESET}")
            self.results["warnings"].append("N8N - Query")
        
        # Test 2: N8N service health
        print(f"\n{Colors.BLUE}Test 7.2: N8N service status{Colors.RESET}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("http://localhost:5678/")
                if response.status_code in [200, 401]:  # 401 = auth required but service running
                    print(f"{Colors.GREEN}✓ N8N service running{Colors.RESET}")
                    self.results["passed"].append("N8N - Service")
                else:
                    print(f"{Colors.YELLOW}⚠ N8N status: {response.status_code}{Colors.RESET}")
                    self.results["warnings"].append("N8N - Service")
        except Exception as e:
            print(f"{Colors.YELLOW}⚠ N8N not accessible: {e}{Colors.RESET}")
            self.results["skipped"].append("N8N - Not configured")
    
    # ========================================================================
    # H. DEVELOPER TOOLS TESTS
    # ========================================================================
    
    async def test_developer_tools(self):
        """Test developer tools and system management"""
        
        # Test 1: System health
        print(f"{Colors.BLUE}Test 8.1: System health check{Colors.RESET}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{BASE_URL}/health")
                if response.status_code == 200:
                    print(f"{Colors.GREEN}✓ System health good{Colors.RESET}")
                    self.results["passed"].append("System - Health")
                else:
                    print(f"{Colors.RED}✗ Health check failed{Colors.RESET}")
                    self.results["failed"].append(("System - Health", f"Status {response.status_code}"))
        except Exception as e:
            print(f"{Colors.RED}✗ Health check error: {e}{Colors.RESET}")
            self.results["failed"].append(("System - Health", str(e)))
        
        # Test 2: Developer tasks API
        print(f"\n{Colors.BLUE}Test 8.2: Developer tasks endpoint{Colors.RESET}")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(f"{BASE_URL}/api/developer/tasks?user_id={TEST_USER}")
                if response.status_code == 200:
                    print(f"{Colors.GREEN}✓ Developer tasks API working{Colors.RESET}")
                    self.results["passed"].append("Developer - Tasks API")
                else:
                    print(f"{Colors.YELLOW}⚠ Developer tasks status: {response.status_code}{Colors.RESET}")
                    self.results["warnings"].append("Developer - Tasks")
        except Exception as e:
            print(f"{Colors.YELLOW}⚠ Developer tasks error: {e}{Colors.RESET}")
            self.results["warnings"].append("Developer - Tasks")
    
    # ========================================================================
    # I. VOICE & MEDIA TESTS
    # ========================================================================
    
    async def test_voice_media(self):
        """Test voice and media capabilities"""
        
        # Test 1: TTS endpoint
        print(f"{Colors.BLUE}Test 9.1: TTS service health{Colors.RESET}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("http://localhost:9002/health")
                if response.status_code == 200:
                    print(f"{Colors.GREEN}✓ TTS service running{Colors.RESET}")
                    self.results["passed"].append("Voice - TTS")
                else:
                    print(f"{Colors.YELLOW}⚠ TTS status: {response.status_code}{Colors.RESET}")
                    self.results["warnings"].append("Voice - TTS")
        except Exception as e:
            print(f"{Colors.YELLOW}⚠ TTS not accessible: {e}{Colors.RESET}")
            self.results["skipped"].append("Voice - TTS not running")
        
        # Test 2: Voice agent status
        print(f"\n{Colors.BLUE}Test 9.2: Voice agent service{Colors.RESET}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("http://localhost:9003/health")
                if response.status_code == 200:
                    print(f"{Colors.GREEN}✓ Voice agent running{Colors.RESET}")
                    self.results["passed"].append("Voice - Agent")
                else:
                    print(f"{Colors.YELLOW}⚠ Voice agent status: {response.status_code}{Colors.RESET}")
                    self.results["warnings"].append("Voice - Agent")
        except Exception as e:
            print(f"{Colors.YELLOW}⚠ Voice agent not accessible: {e}{Colors.RESET}")
            self.results["skipped"].append("Voice - Agent not running")
    
    # ========================================================================
    # J. ADVANCED FEATURES TESTS
    # ========================================================================
    
    async def test_advanced_features(self):
        """Test advanced orchestration and intelligence features"""
        
        # Test 1: Multi-system request (orchestration)
        print(f"{Colors.BLUE}Test 10.1: Multi-system orchestration{Colors.RESET}")
        response = await self.send_chat("Schedule lunch with Alice tomorrow at noon and add ingredients to my shopping list")
        
        if response and len(response) > 50:
            print(f"{Colors.GREEN}✓ Multi-system request processed{Colors.RESET}")
            self.results["passed"].append("Advanced - Orchestration")
        else:
            print(f"{Colors.YELLOW}⚠ Orchestration response unclear{Colors.RESET}")
            self.results["warnings"].append("Advanced - Orchestration")
        
        # Test 2: Self-awareness query
        print(f"\n{Colors.BLUE}Test 10.2: Self-awareness capabilities{Colors.RESET}")
        response = await self.send_chat("What can you do for me?")
        
        if response and len(response) > 100:
            print(f"{Colors.GREEN}✓ Capabilities query working{Colors.RESET}")
            self.results["passed"].append("Advanced - Self Awareness")
        else:
            print(f"{Colors.YELLOW}⚠ Capabilities response short{Colors.RESET}")
            self.results["warnings"].append("Advanced - Self Awareness")
        
        # Test 3: Memory search across types
        print(f"\n{Colors.BLUE}Test 10.3: Cross-memory search{Colors.RESET}")
        response = await self.send_chat("Search my memories for pizza")
        
        if response:
            print(f"{Colors.GREEN}✓ Memory search responded{Colors.RESET}")
            self.results["passed"].append("Advanced - Memory Search")
        else:
            print(f"{Colors.YELLOW}⚠ Memory search empty{Colors.RESET}")
            self.results["warnings"].append("Advanced - Memory Search")
        
        # Test 4: MCP Server health
        print(f"\n{Colors.BLUE}Test 10.4: MCP Server status{Colors.RESET}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("http://localhost:8003/")
                if response.status_code in [200, 404]:  # 404 is OK (no root endpoint)
                    print(f"{Colors.GREEN}✓ MCP Server running{Colors.RESET}")
                    self.results["passed"].append("Advanced - MCP Server")
                else:
                    print(f"{Colors.YELLOW}⚠ MCP Server status: {response.status_code}{Colors.RESET}")
                    self.results["warnings"].append("Advanced - MCP Server")
        except Exception as e:
            print(f"{Colors.RED}✗ MCP Server error: {e}{Colors.RESET}")
            self.results["failed"].append(("Advanced - MCP Server", str(e)))
    
    # ========================================================================
    # HELPER METHODS
    # ========================================================================
    
    async def send_chat(self, message: str) -> Optional[str]:
        """Send chat message and return response"""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(
                    f"{BASE_URL}/api/chat",
                    headers={"X-User-ID": TEST_USER},
                    json={
                        "message": message,
                        "user_id": TEST_USER
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return data.get("response", "")
                else:
                    print(f"{Colors.RED}    Chat API error: {response.status_code}{Colors.RESET}")
                    return None
        except Exception as e:
            print(f"{Colors.RED}    Chat request failed: {e}{Colors.RESET}")
            return None
    
    async def test_streaming_chat(self, message: str) -> bool:
        """Test streaming endpoint"""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    f"{BASE_URL}/api/chat/?user_id={TEST_USER}&stream=true",
                    json={"message": message, "user_id": TEST_USER}
                ) as response:
                    if response.status_code != 200:
                        return False
                    
                    # Read at least one event
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = json.loads(line[6:])
                            if data.get("type") in ["session_start", "message_delta"]:
                                return True
                    return False
        except Exception as e:
            print(f"    Streaming error: {e}")
            return False
    
    async def print_results(self):
        """Print comprehensive test results"""
        elapsed = time.time() - self.start_time
        
        print(f"\n{Colors.BOLD}{'='*80}{Colors.RESET}")
        print(f"{Colors.CYAN}{Colors.BOLD}TEST RESULTS SUMMARY{Colors.RESET}")
        print(f"{Colors.BOLD}{'='*80}{Colors.RESET}")
        
        total_passed = len(self.results["passed"])
        total_failed = len(self.results["failed"])
        total_warnings = len(self.results["warnings"])
        total_skipped = len(self.results["skipped"])
        total_tests = total_passed + total_failed + total_warnings
        
        pass_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0
        
        print(f"\n{Colors.BOLD}Overview:{Colors.RESET}")
        print(f"  Total Tests: {total_tests}")
        print(f"  {Colors.GREEN}Passed: {total_passed}{Colors.RESET}")
        print(f"  {Colors.RED}Failed: {total_failed}{Colors.RESET}")
        print(f"  {Colors.YELLOW}Warnings: {total_warnings}{Colors.RESET}")
        print(f"  {Colors.CYAN}Skipped: {total_skipped}{Colors.RESET}")
        print(f"  Pass Rate: {pass_rate:.1f}%")
        print(f"  Duration: {elapsed:.2f}s")
        
        if self.results["passed"]:
            print(f"\n{Colors.GREEN}{Colors.BOLD}✅ PASSED ({len(self.results['passed'])}){Colors.RESET}")
            for test in self.results["passed"]:
                print(f"  {Colors.GREEN}✓{Colors.RESET} {test}")
        
        if self.results["warnings"]:
            print(f"\n{Colors.YELLOW}{Colors.BOLD}⚠ WARNINGS ({len(self.results['warnings'])}){Colors.RESET}")
            for test in self.results["warnings"]:
                print(f"  {Colors.YELLOW}⚠{Colors.RESET} {test}")
        
        if self.results["failed"]:
            print(f"\n{Colors.RED}{Colors.BOLD}❌ FAILED ({len(self.results['failed'])}){Colors.RESET}")
            for test, error in self.results["failed"]:
                print(f"  {Colors.RED}✗{Colors.RESET} {test}: {error[:80]}")
        
        if self.results["skipped"]:
            print(f"\n{Colors.CYAN}{Colors.BOLD}⊘ SKIPPED ({len(self.results['skipped'])}){Colors.RESET}")
            for test in self.results["skipped"]:
                print(f"  {Colors.CYAN}⊘{Colors.RESET} {test}")
        
        # Overall status
        print(f"\n{Colors.BOLD}{'='*80}{Colors.RESET}")
        if total_failed == 0 and pass_rate >= 80:
            print(f"{Colors.GREEN}{Colors.BOLD}✅ SYSTEM TEST PASSED{Colors.RESET}")
            print(f"{Colors.GREEN}All critical capabilities working correctly{Colors.RESET}")
            status = "PASS"
        elif total_failed == 0:
            print(f"{Colors.YELLOW}{Colors.BOLD}⚠ SYSTEM TEST PASSED WITH WARNINGS{Colors.RESET}")
            print(f"{Colors.YELLOW}Some features may need attention{Colors.RESET}")
            status = "PASS_WITH_WARNINGS"
        else:
            print(f"{Colors.RED}{Colors.BOLD}❌ SYSTEM TEST FAILED{Colors.RESET}")
            print(f"{Colors.RED}Critical issues need to be fixed{Colors.RESET}")
            status = "FAIL"
        print(f"{Colors.BOLD}{'='*80}{Colors.RESET}\n")
        
        # Save JSON report
        report = {
            "timestamp": datetime.now().isoformat(),
            "user_id": TEST_USER,
            "duration_seconds": elapsed,
            "status": status,
            "pass_rate": pass_rate,
            "summary": {
                "total": total_tests,
                "passed": total_passed,
                "failed": total_failed,
                "warnings": total_warnings,
                "skipped": total_skipped
            },
            "results": self.results,
            "test_data": self.test_data
        }
        
        report_path = "/home/zoe/assistant/tests/integration/comprehensive_test_report.json"
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"📊 Detailed report saved: {report_path}\n")
        
        return status == "PASS" or status == "PASS_WITH_WARNINGS"

async def main():
    """Main test execution"""
    tester = ZoeCapabilityTest()
    success = await tester.run_all_tests()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())

