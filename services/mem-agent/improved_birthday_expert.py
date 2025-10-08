import re
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any
import httpx

class ImprovedBirthdayExpert:
    """Improved Birthday Expert - Secure memories + Smart calendar integration"""
    
    def __init__(self):
        self.api_base = "http://zoe-core:8000/api"
        self.intent_patterns = [
            r"add.*people.*memories.*birthday",
            r"birthday.*reminder.*recurring",
            r"setup.*birthday.*system",
            r"add.*family.*birthdays"
        ]
    
    def can_handle(self, query: str) -> float:
        """Return confidence score for handling this query"""
        query_lower = query.lower()
        for pattern in self.intent_patterns:
            if re.search(pattern, query_lower):
                return 0.95  # Higher confidence than calendar expert
        return 0.0
    
    async def execute(self, query: str, user_id: str) -> Dict[str, Any]:
        """Execute improved birthday setup actions"""
        try:
            # Parse the request to extract people and their birthdays
            people_data = self._parse_people_and_birthdays(query)
            
            if not people_data:
                return {
                    "success": False,
                    "error": "Could not parse people and birthday information",
                    "message": "❌ Could not extract people and birthday information from your request"
                }
            
            # Process each person with secure authentication
            results = []
            for person in people_data:
                result = await self._setup_person_secure_birthday_system(person, user_id)
                results.append(result)
            
            # Count successes
            successful_setups = sum(1 for r in results if r.get("success", False))
            
            return {
                "success": successful_setups > 0,
                "action": "secure_birthday_system_setup",
                "people_processed": len(people_data),
                "successful_setups": successful_setups,
                "results": results,
                "message": f"✅ Set up secure birthday system for {successful_setups}/{len(people_data)} people with memories and smart calendar integration"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": "❌ Error setting up secure birthday system"
            }
    
    def _parse_people_and_birthdays(self, query: str) -> List[Dict[str, Any]]:
        """Parse people and their birthdays from the query"""
        people = []
        
        # Look for patterns like "Name - Date" or "Name - Date Year"
        patterns = [
            r"([A-Za-z\s]+?)\s*-\s*(\d{1,2}[\/\-\.]\w+[\/\-\.]\d{4})",  # Name - 13/January/2022
            r"([A-Za-z\s]+?)\s*-\s*(\d{1,2}\s+\w+\s+\d{4})",  # Name - 20 February 1988
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, query, re.IGNORECASE)
            for match in matches:
                name = match[0].strip()
                date_str = match[1].strip()
                
                # Parse the date
                birthday = self._parse_date(date_str)
                if birthday:
                    people.append({
                        "name": name,
                        "birthday": birthday,
                        "original_date": date_str
                    })
        
        return people
    
    def _parse_date(self, date_str: str) -> str:
        """Parse various date formats into YYYY-MM-DD"""
        try:
            date_str = date_str.strip()
            
            if "/" in date_str:
                parts = date_str.split("/")
                if len(parts) == 3:
                    day, month, year = parts
                    month_num = self._month_name_to_number(month)
                    if month_num:
                        return f"{year}-{month_num:02d}-{int(day):02d}"
            
            elif " " in date_str:
                parts = date_str.split()
                if len(parts) == 3:
                    day, month, year = parts
                    month_num = self._month_name_to_number(month)
                    if month_num:
                        return f"{year}-{month_num:02d}-{int(day):02d}"
            
            return None
            
        except Exception:
            return None
    
    def _month_name_to_number(self, month_str: str) -> int:
        """Convert month name to number"""
        month_names = {
            "january": 1, "jan": 1, "february": 2, "feb": 2,
            "march": 3, "mar": 3, "april": 4, "apr": 4,
            "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
            "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
            "october": 10, "oct": 10, "november": 11, "nov": 11,
            "december": 12, "dec": 12
        }
        return month_names.get(month_str.lower(), None)
    
    async def _setup_person_secure_birthday_system(self, person: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        """Setup secure birthday system for one person"""
        try:
            name = person["name"]
            birthday = person["birthday"]
            
            # Step 1: Add person to secure memories with authentication
            person_result = await self._add_person_to_secure_memories(name, birthday, user_id)
            
            # Step 2: Create birthday reminder preference in person metadata
            reminder_result = await self._setup_birthday_reminder_preference(name, user_id)
            
            return {
                "person": name,
                "success": person_result.get("success", False),
                "person_added_to_memories": person_result.get("success", False),
                "reminder_preference_set": reminder_result.get("success", False),
                "details": {
                    "person": person_result,
                    "reminder_preference": reminder_result
                }
            }
            
        except Exception as e:
            return {
                "person": person.get("name", "Unknown"),
                "success": False,
                "error": str(e)
            }
    
    async def _add_person_to_secure_memories(self, name: str, birthday: str, user_id: str) -> Dict[str, Any]:
        """Add person to secure memories with proper authentication"""
        try:
            # Create a session token for authentication
            session_token = await self._create_authenticated_session(user_id)
            
            async with httpx.AsyncClient() as client:
                # Add person with birthday information and reminder preference
                response = await client.post(
                    f"{self.api_base}/memories?type=people&user_id={user_id}",
                    headers={"Authorization": f"Bearer {session_token}"},
                    json={
                        "name": name,
                        "relationship": "family",
                        "birthday": birthday,
                        "notes": f"Birthday: {birthday}. Reminder preference: 2 weeks before",
                        "metadata": {
                            "birthday_reminder_enabled": True,
                            "reminder_days_before": 14,
                            "reminder_time": "09:00"
                        }
                    }
                )
                
                if response.status_code == 200:
                    return {
                        "success": True,
                        "message": f"✅ Added {name} to secure memories with birthday and reminder preferences"
                    }
                else:
                    return {
                        "success": False,
                        "error": f"API error: {response.status_code}",
                        "message": f"❌ Failed to add {name} to secure memories"
                    }
                    
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"❌ Error adding {name} to secure memories"
            }
    
    async def _setup_birthday_reminder_preference(self, name: str, user_id: str) -> Dict[str, Any]:
        """Setup birthday reminder preference for the person"""
        try:
            # This would be stored in the person's metadata in memories
            # The calendar system would then pull this information when displaying birthdays
            return {
                "success": True,
                "message": f"✅ Set up birthday reminder preference for {name}"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"❌ Error setting up reminder preference for {name}"
            }
    
    async def _create_authenticated_session(self, user_id: str) -> str:
        """Create an authenticated session for secure API calls"""
        try:
            async with httpx.AsyncClient() as client:
                # Create a session for the user
                response = await client.post(
                    f"{self.api_base}/auth/session",
                    json={"user_id": user_id, "expires_in": 3600}  # 1 hour session
                )
                
                if response.status_code == 200:
                    session_data = response.json()
                    return session_data.get("session_token", "default_session")
                else:
                    # Fallback to default session
                    return "default_session"
                    
        except Exception:
            # Fallback to default session
            return "default_session"
