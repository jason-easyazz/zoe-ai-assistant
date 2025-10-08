"""
ReminderExpert - Dedicated Reminder Management
==============================================
"""
import httpx
import re
from typing import Dict, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class ReminderExpert:
    """Expert for creating and managing reminders"""
    
    def __init__(self):
        self.api_base = "http://zoe-core-test:8000/api"
        self.intent_patterns = [
            r"remind me|reminder|don't forget",
            r"alert me|notify me",
            r"what.*reminders|show.*reminders"
        ]
    
    def can_handle(self, query: str) -> float:
        """Return confidence score for handling this query"""
        query_lower = query.lower()
        
        # High confidence for explicit reminder commands
        if re.search(r"remind me|reminder", query_lower):
            return 0.95
        
        # Medium confidence for other patterns
        for pattern in self.intent_patterns:
            if re.search(pattern, query_lower):
                return 0.8
        
        return 0.0
    
    async def execute(self, query: str, user_id: str) -> Dict[str, Any]:
        """Execute reminder actions"""
        query_lower = query.lower()
        
        # Detect action type
        if "remind" in query_lower or "don't forget" in query_lower:
            return await self._create_reminder(query, user_id)
        elif "what" in query_lower or "show" in query_lower:
            return await self._get_reminders(query, user_id)
        else:
            return await self._create_reminder(query, user_id)
    
    async def _create_reminder(self, query: str, user_id: str) -> Dict[str, Any]:
        """Create a reminder from natural language"""
        try:
            # Extract title (what to remind about)
            # "Remind me tomorrow at 10am to go shopping"
            title_match = re.search(r"to (.+?)(?:\s+at\s+|\s+tomorrow|\s+on\s+|$)", query, re.IGNORECASE)
            if not title_match:
                # Try "remind me about X"
                title_match = re.search(r"about (.+)", query, re.IGNORECASE)
            
            if not title_match:
                # Try extracting everything after "remind me" or "don't forget"
                fallback = re.search(r"(?:remind me|don't forget)(?: about| to)? (.+)", query, re.IGNORECASE)
                title = fallback.group(1).strip() if fallback else "Reminder"
            else:
                title = title_match.group(1).strip()
            
            # Extract time
            time_match = re.search(r"at (\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", query, re.IGNORECASE)
            reminder_time = time_match.group(1).strip() if time_match else "09:00"
            
            # Normalize time format
            if reminder_time and not any(x in reminder_time for x in [':', 'am', 'pm']):
                reminder_time = f"{reminder_time}:00"
            
            # Extract date (tomorrow, next week, specific date)
            date_str = None
            if "tomorrow" in query.lower():
                date_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            elif "next week" in query.lower():
                date_str = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            else:
                # Default to tomorrow
                date_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            
            logger.info(f"ReminderExpert: Creating reminder - title='{title}', date={date_str}, time={reminder_time}")
            
            # Call reminders API
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    f"{self.api_base}/reminders/",
                    headers={"X-Service-Token": "zoe_internal_2025"},
                    json={
                        "title": title,
                        "reminder_time": reminder_time,
                        "reminder_date": date_str,
                        "user_id": user_id,
                        "reminder_type": "once",
                        "category": "personal"
                    }
                )
                
                logger.info(f"ReminderExpert: API response status={response.status_code}")
                
                if response.status_code == 200:
                    logger.info(f"ReminderExpert: ✅ Successfully created reminder")
                    return {
                        "success": True,
                        "action": "create_reminder",
                        "message": f"✅ I'll remind you: {title} at {reminder_time} on {date_str}"
                    }
                else:
                    logger.error(f"ReminderExpert: ❌ API error {response.status_code}: {response.text}")
                    return {
                        "success": False,
                        "error": f"API returned {response.status_code}",
                        "message": f"❌ Couldn't create reminder"
                    }
        except Exception as e:
            logger.error(f"Reminder creation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"❌ Error creating reminder: {e}"
            }
    
    async def _get_reminders(self, query: str, user_id: str) -> Dict[str, Any]:
        """Get active reminders"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_base}/reminders/",
                    params={"user_id": user_id},
                    headers={"X-Service-Token": "zoe_internal_2025"},
                    timeout=3.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    reminders = data.get("reminders", [])
                    
                    if reminders:
                        return {
                            "success": True,
                            "action": "get_reminders",
                            "results": reminders,
                            "message": f"⏰ You have {len(reminders)} active reminders"
                        }
                    else:
                        return {
                            "success": True,
                            "action": "get_reminders",
                            "results": [],
                            "message": "⏰ No active reminders"
                        }
                else:
                    return {
                        "success": False,
                        "error": f"API returned {response.status_code}",
                        "message": "❌ Couldn't retrieve reminders"
                    }
        except Exception as e:
            logger.error(f"Reminder retrieval failed: {e}")
            return {
                "success": True,
                "action": "get_reminders",
                "message": "⏰ Checking reminders...",
                "results": []
            }

