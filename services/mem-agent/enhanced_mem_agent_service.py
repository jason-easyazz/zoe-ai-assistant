"""
Enhanced MEM Agent: Multi-Expert Model with Action Execution
===========================================================

Transforms the basic memory search service into a sophisticated
Multi-Expert Model that can both search memories AND execute actions.

Expert Specialists:
- ListExpert: Manages shopping lists, tasks, and items
- CalendarExpert: Creates and manages calendar events
- MemoryExpert: Semantic memory search and retrieval
- PlanningExpert: Goal decomposition and task planning
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import logging
import httpx
import json
import re
from datetime import datetime, timedelta
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Enhanced MEM Agent", version="2.0")

# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class EnhancedRequest(BaseModel):
    query: str
    user_id: str
    max_results: int = 10  # Increased for better context
    include_graph: bool = True  # Enable by default for better intelligence
    execute_actions: bool = True  # Enable action execution
    context_window: int = 2048  # Larger context for Samantha-level intelligence
    temperature: float = 0.7  # Higher creativity for better responses
    use_semantic_search: bool = True  # Enable semantic search
    include_relationships: bool = True  # Include relationship context

class ExpertResponse(BaseModel):
    expert: str
    intent: str
    confidence: float
    result: Dict[str, Any]
    action_taken: bool = False
    message: str

class EnhancedResponse(BaseModel):
    experts: List[ExpertResponse]
    primary_expert: str
    actions_executed: int
    total_confidence: float
    execution_summary: str

# ============================================================================
# EXPERT SPECIALISTS
# ============================================================================

class ListExpert:
    """Expert for list management: shopping, tasks, items"""
    
    def __init__(self):
        self.api_base = "http://zoe-core-test:8000/api/lists"
        self.intent_patterns = [
            r"add.*to.*list|add.*shopping|add.*task",
            r"create.*list|new.*list",
            r"show.*list|what.*list|list.*items",
            r"remove.*from.*list|delete.*list.*item"
        ]
    
    def can_handle(self, query: str) -> float:
        """Return confidence score for handling this query"""
        query_lower = query.lower()
        for pattern in self.intent_patterns:
            if re.search(pattern, query_lower):
                return 0.9
        return 0.0
    
    async def execute(self, query: str, user_id: str) -> Dict[str, Any]:
        """Execute list-related actions"""
        query_lower = query.lower()
        
        # Parse intent and extract information
        if "add" in query_lower and "list" in query_lower:
            return await self._add_to_list(query, user_id)
        elif "show" in query_lower or "what" in query_lower:
            return await self._get_list_items(query, user_id)
        elif "create" in query_lower:
            return await self._create_list(query, user_id)
        else:
            return {"error": "Could not parse list action", "suggestion": "Try: 'add bread to shopping list'"}

    async def _add_to_list(self, query: str, user_id: str) -> Dict[str, Any]:
        """Add item to list"""
        try:
            # Extract item name from query
            item = self._extract_item_name(query)
            list_name = self._extract_list_name(query, default="Shopping")
            
            # Call the working API
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/tasks",
                    json={
                        "name": list_name,
                        "text": item,
                        "list_category": "shopping",
                        "priority": "medium"
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return {
                        "success": True,
                        "action": "add_to_list",
                        "item": item,
                        "list": list_name,
                        "message": f"✅ Added '{item}' to {list_name} list",
                        "api_response": result
                    }
                else:
                    return {
                        "success": False,
                        "error": f"API error: {response.status_code}",
                        "message": "❌ Failed to add item to list"
                    }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": "❌ Error adding item to list"
            }

    async def _get_list_items(self, query: str, user_id: str) -> Dict[str, Any]:
        """Get items from list"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.api_base}/tasks")
                
                if response.status_code == 200:
                    data = response.json()
                    lists = data.get("lists", [])
                    
                    if lists:
                        items = []
                        for list_data in lists:
                            for item in list_data.get("items", []):
                                items.append({
                                    "text": item.get("text"),
                                    "list": list_data.get("name"),
                                    "priority": item.get("priority")
                                })
                        
                        return {
                            "success": True,
                            "action": "get_list_items",
                            "items": items,
                            "message": f"📋 Found {len(items)} items across {len(lists)} lists"
                        }
                    else:
                        return {
                            "success": True,
                            "action": "get_list_items",
                            "items": [],
                            "message": "📋 No items found in any lists"
                        }
                else:
                    return {
                        "success": False,
                        "error": f"API error: {response.status_code}",
                        "message": "❌ Failed to retrieve list items"
                    }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": "❌ Error retrieving list items"
            }

    async def _create_list(self, query: str, user_id: str) -> Dict[str, Any]:
        """Create new list"""
        try:
            list_name = self._extract_list_name(query, default="New List")
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/tasks",
                    json={
                        "name": list_name,
                        "text": "Sample item",
                        "list_category": "personal",
                        "priority": "medium"
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return {
                        "success": True,
                        "action": "create_list",
                        "list_name": list_name,
                        "message": f"✅ Created new list: {list_name}",
                        "api_response": result
                    }
                else:
                    return {
                        "success": False,
                        "error": f"API error: {response.status_code}",
                        "message": "❌ Failed to create list"
                    }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": "❌ Error creating list"
            }

    def _extract_item_name(self, query: str) -> str:
        """Extract item name from query"""
        # Simple extraction - look for words after "add"
        words = query.lower().split()
        try:
            add_index = words.index("add")
            if add_index + 1 < len(words):
                # Get words after "add" until "to" or end
                item_words = []
                for i in range(add_index + 1, len(words)):
                    if words[i] in ["to", "list", "shopping"]:
                        break
                    item_words.append(words[i])
                return " ".join(item_words).title()
        except ValueError:
            pass
        return "Item"

    def _extract_list_name(self, query: str, default: str = "Shopping") -> str:
        """Extract list name from query"""
        query_lower = query.lower()
        if "shopping" in query_lower:
            return "Shopping"
        elif "task" in query_lower:
            return "Tasks"
        elif "work" in query_lower:
            return "Work"
        elif "personal" in query_lower:
            return "Personal"
        return default

class CalendarExpert:
    """Expert for calendar management: events, scheduling, reminders"""
    
    def __init__(self):
        self.api_base = "http://zoe-core-test:8000/api/calendar"
        self.intent_patterns = [
            r"calendar|event|schedule|meeting|appointment",
            r"create.*event|add.*event|schedule.*event",
            r"tomorrow|today|next.*week|this.*week",
            r"birthday|anniversary|reminder"
        ]
    
    def can_handle(self, query: str) -> float:
        """Return confidence score for handling this query"""
        query_lower = query.lower()
        for pattern in self.intent_patterns:
            if re.search(pattern, query_lower):
                return 0.9
        return 0.0
    
    async def execute(self, query: str, user_id: str) -> Dict[str, Any]:
        """Execute calendar-related actions"""
        query_lower = query.lower()
        
        if "create" in query_lower or "add" in query_lower or "schedule" in query_lower:
            return await self._create_event(query, user_id)
        elif "show" in query_lower or "what" in query_lower or "list" in query_lower:
            return await self._get_events(query, user_id)
        else:
            return {"error": "Could not parse calendar action", "suggestion": "Try: 'create calendar event for Dad's birthday tomorrow at 7pm'"}

    async def _create_event(self, query: str, user_id: str) -> Dict[str, Any]:
        """Create calendar event"""
        try:
            # Parse event details from query
            event_details = self._parse_event_details(query)
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/events",
                    json={
                        "title": event_details["title"],
                        "start_date": event_details["date"],
                        "start_time": event_details["time"],
                        "end_time": event_details["end_time"]
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return {
                        "success": True,
                        "action": "create_event",
                        "event": event_details,
                        "message": f"✅ Created event: {event_details['title']} on {event_details['date']} at {event_details['time']}",
                        "api_response": result
                    }
                else:
                    return {
                        "success": False,
                        "error": f"API error: {response.status_code}",
                        "message": "❌ Failed to create calendar event"
                    }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": "❌ Error creating calendar event"
            }

    async def _get_events(self, query: str, user_id: str) -> Dict[str, Any]:
        """Get calendar events"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.api_base}/events")
                
                if response.status_code == 200:
                    data = response.json()
                    events = data.get("events", [])
                    
                    return {
                        "success": True,
                        "action": "get_events",
                        "events": events,
                        "message": f"📅 Found {len(events)} calendar events"
                    }
                else:
                    return {
                        "success": False,
                        "error": f"API error: {response.status_code}",
                        "message": "❌ Failed to retrieve calendar events"
                    }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": "❌ Error retrieving calendar events"
            }

    def _parse_event_details(self, query: str) -> Dict[str, str]:
        """Parse event details from natural language"""
        query_lower = query.lower()
        
        # Extract title using improved logic
        title = self._extract_event_title(query)
        
        # Extract date
        date = self._extract_date(query_lower)
        
        # Extract time
        time = self._extract_time(query_lower)
        
        return {
            "title": title,
            "date": date,
            "time": time,
            "end_time": self._calculate_end_time(time)
        }

    def _extract_event_title(self, query: str) -> str:
        """Extract event title from natural language query"""
        query_lower = query.lower()
        
        # Pattern 1: "Add [TITLE] to calendar" - most common
        add_pattern = r"add\s+(.+?)\s+to\s+(?:the\s+)?calendar"
        add_match = re.search(add_pattern, query_lower)
        if add_match:
            title = add_match.group(1).strip()
            # Remove time/date words that might be included
            title = re.sub(r"\s+(?:tomorrow|today|at\s+\d+|pm|am)\s*$", "", title)
            if title and len(title) > 1:
                return title.title()
        
        # Pattern 2: "Create calendar event for [TITLE]"
        create_pattern = r"create\s+(?:calendar\s+)?event\s+for\s+(.+?)(?:\s+tomorrow|\s+at|\s*$)"
        create_match = re.search(create_pattern, query_lower)
        if create_match:
            title = create_match.group(1).strip()
            if title and len(title) > 1:
                return title.title()
        
        # Pattern 3: "Schedule [TITLE] for tomorrow"
        schedule_pattern = r"schedule\s+(.+?)\s+for\s+(?:tomorrow|today)"
        schedule_match = re.search(schedule_pattern, query_lower)
        if schedule_match:
            title = schedule_match.group(1).strip()
            if title and len(title) > 1:
                return title.title()
        
        # Fallback: Look for specific event types
        if "birthday" in query_lower:
            # Try to extract whose birthday
            birthday_match = re.search(r"(.+?)\s+birthday", query_lower)
            if birthday_match:
                name = birthday_match.group(1).strip()
                if name and len(name) > 1 and name not in ["add", "create", "schedule"]:
                    return f"{name.title()}'s Birthday"
            return "Birthday"
        elif "meeting" in query_lower:
            return "Meeting"
        elif "appointment" in query_lower:
            return "Appointment"
        
        return "Event"

    def _extract_date(self, query_lower: str) -> str:
        """Extract date from query"""
        today = datetime.now()
        
        if "tomorrow" in query_lower:
            return (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif "today" in query_lower:
            return today.strftime("%Y-%m-%d")
        elif "next week" in query_lower:
            return (today + timedelta(weeks=1)).strftime("%Y-%m-%d")
        else:
            # Default to tomorrow for events
            return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    def _extract_time(self, query_lower: str) -> str:
        """Extract time from query"""
        # Look for time patterns
        time_patterns = [
            r"(\d{1,2}):?(\d{2})?\s*(am|pm)?",
            r"(\d{1,2})\s*(am|pm)"
        ]
        
        for pattern in time_patterns:
            match = re.search(pattern, query_lower)
            if match:
                hour = int(match.group(1))
                minute = match.group(2) if match.group(2) else "00"
                period = match.group(3) if len(match.groups()) > 2 else None
                
                # Convert to 24-hour format
                if period == "pm" and hour != 12:
                    hour += 12
                elif period == "am" and hour == 12:
                    hour = 0
                
                return f"{hour:02d}:{minute}"
        
        # Default time
        return "19:00"

    def _calculate_end_time(self, start_time: str) -> str:
        """Calculate end time based on start time"""
        try:
            start_hour = int(start_time.split(":")[0])
            end_hour = (start_hour + 2) % 24  # 2 hours duration
            return f"{end_hour:02d}:00"
        except:
            return "21:00"

class MemoryExpert:
    """Expert for semantic memory search and retrieval"""
    
    def __init__(self):
        self.intent_patterns = [
            r"remember|recall|who is|what did|when did",
            r"search.*memory|find.*memory",
            r"tell me about|information about"
        ]
    
    def can_handle(self, query: str) -> float:
        """Return confidence score for handling this query"""
        query_lower = query.lower()
        for pattern in self.intent_patterns:
            if re.search(pattern, query_lower):
                return 0.9
        return 0.0
    
    async def execute(self, query: str, user_id: str) -> Dict[str, Any]:
        """Execute memory search"""
        # This would integrate with the existing memory search
        return {
            "success": True,
            "action": "memory_search",
            "query": query,
            "message": f"🔍 Searching memories for: {query}",
            "results": []  # Would be populated by actual memory search
        }

class PlanningExpert:
    """Expert for goal decomposition and task planning"""
    
    def __init__(self):
        self.api_base = "http://zoe-core-test:8000/api/agent"
        self.intent_patterns = [
            r"plan|organize|help me.*plan",
            r"goal|objective|task.*planning",
            r"break.*down|decompose|steps"
        ]
    
    def can_handle(self, query: str) -> float:
        """Return confidence score for handling this query"""
        query_lower = query.lower()
        for pattern in self.intent_patterns:
            if re.search(pattern, query_lower):
                return 0.9
        return 0.0
    
    async def execute(self, query: str, user_id: str) -> Dict[str, Any]:
        """Execute planning actions"""
        try:
            # Create a goal using the agent planning system
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/goals",
                    json={
                        "title": f"Plan: {query[:50]}",
                        "objective": query,
                        "priority": "medium"
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    goal_id = result.get("id")
                    
                    # Generate plan
                    plan_response = await client.post(f"{self.api_base}/goals/{goal_id}/plan")
                    
                    if plan_response.status_code == 200:
                        plan_data = plan_response.json()
                        return {
                            "success": True,
                            "action": "create_plan",
                            "goal_id": goal_id,
                            "plan": plan_data,
                            "message": f"✅ Created plan with {len(plan_data.get('steps', []))} steps",
                            "api_response": plan_data
                        }
                    else:
                        return {
                            "success": False,
                            "error": "Failed to generate plan",
                            "message": "❌ Created goal but failed to generate plan"
                        }
                else:
                    return {
                        "success": False,
                        "error": f"API error: {response.status_code}",
                        "message": "❌ Failed to create planning goal"
                    }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": "❌ Error in planning execution"
            }

# ============================================================================
# ENHANCED MEM AGENT SERVICE
# ============================================================================

class EnhancedMemAgent:
    """Enhanced MEM Agent with Multi-Expert Model"""
    
    def __init__(self):
        self.experts = {
            "list": ListExpert(),
            "calendar": CalendarExpert(),
            "memory": MemoryExpert(),
            "planning": PlanningExpert()
        }
        
        # Load additional experts from separate files
        try:
            from journal_expert import JournalExpert
            self.experts["journal"] = JournalExpert()
            logger.info("✅ JournalExpert loaded")
        except Exception as e:
            logger.warning(f"⚠️  JournalExpert not available: {e}")
        
        try:
            from reminder_expert import ReminderExpert
            self.experts["reminder"] = ReminderExpert()
            logger.info("✅ ReminderExpert loaded")
        except Exception as e:
            logger.warning(f"⚠️  ReminderExpert not available: {e}")
        
        try:
            from homeassistant_expert import HomeAssistantExpert
            self.experts["homeassistant"] = HomeAssistantExpert()
            logger.info("✅ HomeAssistantExpert loaded")
        except Exception as e:
            logger.warning(f"⚠️  HomeAssistantExpert not available: {e}")
        
        try:
            from improved_birthday_expert import ImprovedBirthdayExpert
            self.experts["birthday_setup"] = ImprovedBirthdayExpert()
            logger.info("✅ BirthdayExpert loaded")
        except Exception as e:
            logger.warning(f"⚠️  BirthdayExpert not available: {e}")
        
        logger.info(f"🎯 Total experts loaded: {len(self.experts)} - {list(self.experts.keys())}")
    
    async def process_request(self, request: EnhancedRequest) -> EnhancedResponse:
        """Process request using appropriate expert(s)"""
        
        # Route to experts
        expert_responses = []
        actions_executed = 0
        
        for expert_name, expert in self.experts.items():
            confidence = expert.can_handle(request.query)
            
            if confidence > 0.5:  # Expert can handle this
                try:
                    result = await expert.execute(request.query, request.user_id)
                    
                    expert_response = ExpertResponse(
                        expert=expert_name,
                        intent=self._classify_intent(request.query, expert_name),
                        confidence=confidence,
                        result=result,
                        action_taken=result.get("success", False),
                        message=result.get("message", f"Processed by {expert_name} expert")
                    )
                    
                    expert_responses.append(expert_response)
                    
                    if result.get("success") and result.get("action"):
                        actions_executed += 1
                        
                except Exception as e:
                    logger.error(f"Expert {expert_name} failed: {e}")
                    expert_responses.append(ExpertResponse(
                        expert=expert_name,
                        intent="error",
                        confidence=0.0,
                        result={"error": str(e)},
                        action_taken=False,
                        message=f"❌ {expert_name} expert failed: {e}"
                    ))
        
        # Determine primary expert
        primary_expert = "memory"  # Default
        if expert_responses:
            primary_expert = max(expert_responses, key=lambda x: x.confidence).expert
        
        # Calculate total confidence
        total_confidence = max([r.confidence for r in expert_responses]) if expert_responses else 0.0
        
        # Create execution summary
        execution_summary = self._create_execution_summary(expert_responses, actions_executed)
        
        return EnhancedResponse(
            experts=expert_responses,
            primary_expert=primary_expert,
            actions_executed=actions_executed,
            total_confidence=total_confidence,
            execution_summary=execution_summary
        )
    
    def _classify_intent(self, query: str, expert: str) -> str:
        """Classify intent for the query"""
        query_lower = query.lower()
        
        if expert == "list":
            if "add" in query_lower:
                return "add_to_list"
            elif "show" in query_lower or "what" in query_lower:
                return "get_list_items"
            elif "create" in query_lower:
                return "create_list"
            else:
                return "list_management"
        
        elif expert == "calendar":
            if "create" in query_lower or "add" in query_lower:
                return "create_event"
            elif "show" in query_lower or "list" in query_lower:
                return "get_events"
            else:
                return "calendar_management"
        
        elif expert == "memory":
            return "memory_search"
        
        elif expert == "planning":
            return "goal_planning"
        
        return "unknown"
    
    def _create_execution_summary(self, expert_responses: List[ExpertResponse], actions_executed: int) -> str:
        """Create human-readable execution summary"""
        if actions_executed == 0:
            return "No actions were executed"
        
        successful_experts = [r for r in expert_responses if r.action_taken]
        expert_names = [r.expert for r in successful_experts]
        
        if len(expert_names) == 1:
            return f"✅ Action executed by {expert_names[0]} expert"
        else:
            return f"✅ {actions_executed} actions executed by {', '.join(expert_names)} experts"

# Initialize enhanced MEM agent
enhanced_mem_agent = EnhancedMemAgent()

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy", 
        "service": "enhanced-mem-agent",
        "version": "2.0",
        "experts": list(enhanced_mem_agent.experts.keys())
    }

@app.post("/search", response_model=EnhancedResponse)
async def enhanced_search(request: EnhancedRequest):
    """
    Enhanced semantic search with action execution
    """
    try:
        logger.info(f"Enhanced search request: {request.query[:50]}...")
        
        result = await enhanced_mem_agent.process_request(request)
        
        logger.info(f"Processed by {len(result.experts)} experts, {result.actions_executed} actions executed")
        
        return result
        
    except Exception as e:
        logger.error(f"Enhanced search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/experts/{expert_name}")
async def call_expert_directly(expert_name: str, request: EnhancedRequest):
    """Call a specific expert directly"""
    if expert_name not in enhanced_mem_agent.experts:
        raise HTTPException(status_code=404, detail=f"Expert '{expert_name}' not found")
    
    expert = enhanced_mem_agent.experts[expert_name]
    result = await expert.execute(request.query, request.user_id)
    
    return {
        "expert": expert_name,
        "result": result,
        "action_taken": result.get("success", False)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=11435)
