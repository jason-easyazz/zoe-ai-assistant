"""
Ultimate Life Orchestrator - Zoe's ALL-KNOWING Intelligence System
Enhances existing systems without breaking anything
"""
import httpx
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import logging
import json

logger = logging.getLogger(__name__)

class LifeOrchestrator:
    """The ultimate life orchestrator that knows everything about your life"""
    
    def __init__(self):
        self.base_url = "http://localhost:8000/api"
    
    async def _api_get(self, endpoint: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Helper to make GET requests to internal APIs."""
        try:
            async with httpx.AsyncClient() as client:
                headers = {"X-User-ID": user_id} # Assuming user_id is passed in headers
                response = await client.get(f"{self.base_url}{endpoint}", headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            logger.error(f"API request failed for {endpoint}: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from {endpoint}: {e}")
        return None

    async def analyze_everything(self, user_id: str, context: Dict) -> Dict[str, Any]:
        """Analyze ALL aspects of the user's life and generate intelligent suggestions"""
        
        life_analysis = {
            "people_insights": {},
            "task_insights": {},
            "calendar_insights": {},
            "relationship_insights": {},
            "collections_insights": {},
            "reminders_insights": {},
            "journal_insights": {},
            "productivity_insights": {},
            "memory_insights": {},
            "urgent_actions": [],
            "opportunity_actions": [],
            "smart_suggestions": [],
            "free_time_suggestions": []
        }
        
        # Run all analysis concurrently
        results = await asyncio.gather(
            self._analyze_people_intelligence(user_id),
            self._analyze_task_intelligence(user_id),
            self._analyze_calendar_intelligence(user_id),
            self._analyze_relationship_intelligence(user_id),
            self._analyze_collections_intelligence(user_id),
            self._analyze_reminders_intelligence(user_id),
            self._analyze_journal_intelligence(user_id),
            self._analyze_productivity_intelligence(user_id),
            self._analyze_memory_intelligence(user_id),
            return_exceptions=True # Allow other tasks to complete even if one fails
        )

        keys = ["people_insights", "task_insights", "calendar_insights", "relationship_insights",
                "collections_insights", "reminders_insights", "journal_insights",
                "productivity_insights", "memory_insights"]
        for i, key in enumerate(keys):
            if not isinstance(results[i], Exception):
                life_analysis[key] = results[i]
            else:
                logger.error(f"Error analyzing {key}: {results[i]}")
                life_analysis[key] = {"error": str(results[i])}
        
        life_analysis["urgent_actions"] = await self._generate_urgent_actions(life_analysis)
        life_analysis["opportunity_actions"] = await self._generate_opportunity_actions(life_analysis)
        life_analysis["smart_suggestions"] = await self._generate_smart_suggestions(life_analysis)
        life_analysis["free_time_suggestions"] = await self._generate_free_time_suggestions(life_analysis)
        
        return life_analysis

    async def _analyze_people_intelligence(self, user_id: str) -> Dict:
        people = await self._api_get("/people/", user_id)
        if not people or not isinstance(people, list):
            return {"error": "Could not fetch people data"}
        
        insights = {"total": len(people), "needs_contact": []}
        for person in people:
            if self._needs_contact(person):
                insights["needs_contact"].append(person)
        return insights

    def _needs_contact(self, person: Dict) -> bool:
        # Placeholder logic
        last_contact = person.get("last_contact")
        if last_contact:
            # e.g. if last contact was more than 3 months ago
            return datetime.now() - datetime.fromisoformat(last_contact) > timedelta(days=90)
        return False # Default to false if no contact date

    async def _analyze_task_intelligence(self, user_id: str) -> Dict:
        tasks = await self._api_get("/lists/all", user_id) # Assuming an endpoint for all tasks
        if not tasks or not isinstance(tasks.get("tasks"), list):
             return {"error": "Could not fetch task data"}
        
        overdue = [t for t in tasks["tasks"] if t.get("due_date") and datetime.fromisoformat(t["due_date"]) < datetime.now()]
        return {"total": len(tasks["tasks"]), "overdue_count": len(overdue), "overdue_tasks": overdue}

    async def _analyze_calendar_intelligence(self, user_id: str) -> Dict:
        # Assuming an endpoint that gets events for the next 7 days
        events = await self._api_get("/calendar/events?days=7", user_id)
        if not events:
            return {"error": "Could not fetch calendar data"}
            
        today = datetime.now().date()
        upcoming_events = [e for e in events if datetime.fromisoformat(e['start']['dateTime']).date() >= today]
        free_slots = self._find_free_time_slots(events)
        
        return {"upcoming_events_count": len(upcoming_events), "free_slots": free_slots}

    def _find_free_time_slots(self, events: List[Dict]) -> List:
        # Placeholder for complex free-time calculation logic
        return [{"start": "2025-10-07T14:00:00", "end": "2025-10-07T15:00:00"}]

    async def _analyze_relationship_intelligence(self, user_id: str) -> Dict:
        # Placeholder - needs a dedicated relationship tracking system
        return {"status": "Not Implemented"}

    async def _analyze_collections_intelligence(self, user_id: str) -> Dict:
        collections = await self._api_get("/collections/all", user_id)
        if not collections:
            return {"error": "Could not fetch collections"}
        return {"total": len(collections), "summary": [c.get('name') for c in collections]}

    async def _analyze_reminders_intelligence(self, user_id: str) -> Dict:
        reminders = await self._api_get("/reminders/", user_id)
        if not reminders:
            return {"error": "Could not fetch reminders"}
        pending = [r for r in reminders if not r.get("completed")]
        return {"total": len(reminders), "pending_count": len(pending)}

    async def _analyze_journal_intelligence(self, user_id: str) -> Dict:
        # Placeholder - needs journal API endpoint
        return {"status": "Not Implemented"}

    async def _analyze_productivity_intelligence(self, user_id: str) -> Dict:
        # Placeholder - needs productivity tracking data
        return {"status": "Not Implemented"}

    async def _analyze_memory_intelligence(self, user_id: str) -> Dict:
        memories = await self._api_get("/memory/query?query=", user_id) # Get all memories
        if not memories or not isinstance(memories.get('results'), list):
            return {"error": "Could not fetch memories"}
        return {"total_memories": len(memories['results'])}

    async def _generate_urgent_actions(self, analysis: Dict) -> List[str]:
        actions = []
        if analysis.get("task_insights", {}).get("overdue_count", 0) > 0:
            actions.append("You have overdue tasks. Address them now?")
        return actions

    async def _generate_opportunity_actions(self, analysis: Dict) -> List[str]:
        actions = []
        if analysis.get("people_insights", {}).get("needs_contact"):
            actions.append("Some of your contacts haven't been reached out to in a while.")
        return actions

    async def _generate_smart_suggestions(self, analysis: Dict) -> List[str]:
        suggestions = []
        if len(analysis.get("calendar_insights", {}).get("free_slots", [])) > 0:
            suggestions.append("You have some free time today. Want to plan something?")
        return suggestions
        
    async def _generate_free_time_suggestions(self, analysis: Dict) -> List[str]:
        return []

life_orchestrator = LifeOrchestrator()