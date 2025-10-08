"""
PersonExpert - People and Relationship Management
================================================
"""
import httpx
import re
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class PersonExpert:
    """Expert for managing people and relationships"""
    
    def __init__(self):
        self.api_base = "http://zoe-core-test:8000/api"
        self.intent_patterns = [
            r"remember.*person|remember.*people",
            r"who is|tell me about",
            r"my.*(?:sister|brother|mom|dad|friend|colleague|family)",
            r"person named|people named"
        ]
    
    def can_handle(self, query: str) -> float:
        """Return confidence score for handling this query"""
        query_lower = query.lower()
        
        # High confidence for explicit person creation
        if re.search(r"remember.*person|person named", query_lower):
            return 0.95
        
        # Medium-high for family/relationship mentions
        if re.search(r"sister|brother|mom|dad|friend|colleague", query_lower):
            return 0.85
        
        # Medium confidence for "who is" queries
        if re.search(r"who is|tell me about", query_lower):
            return 0.75
        
        return 0.0
    
    async def execute(self, query: str, user_id: str) -> Dict[str, Any]:
        """Execute person-related actions"""
        query_lower = query.lower()
        
        # Detect action type
        if "remember" in query_lower or "person named" in query_lower:
            return await self._create_person(query, user_id)
        elif "who is" in query_lower or "tell me about" in query_lower:
            return await self._get_person(query, user_id)
        else:
            return await self._create_person(query, user_id)
    
    async def _create_person(self, query: str, user_id: str) -> Dict[str, Any]:
        """Create a person record from natural language"""
        try:
            # Extract person details
            # "Remember a person named Sarah who is my sister and loves painting"
            name_match = re.search(r"(?:person named|remember)\s+(\w+)", query, re.IGNORECASE)
            name = name_match.group(1).strip() if name_match else "Unknown"
            
            # Extract relationship
            relationship = None
            rel_patterns = [
                (r"(?:is|who is)\s+my\s+(\w+)", 1),
                (r"my\s+(\w+)", 1),
                (r"(sister|brother|mom|dad|mother|father|friend|colleague|coworker)", 1)
            ]
            
            for pattern, group in rel_patterns:
                rel_match = re.search(pattern, query, re.IGNORECASE)
                if rel_match:
                    relationship = rel_match.group(group).strip()
                    break
            
            # Extract interests/notes
            notes = ""
            love_match = re.search(r"loves?\s+(.+)", query, re.IGNORECASE)
            if love_match:
                notes = f"Loves {love_match.group(1).strip()}"
            
            logger.info(f"PersonExpert: Creating person - name='{name}', relationship='{relationship}'")
            
            # Call memories API to create person
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    f"{self.api_base}/memories/?type=people",
                    headers={"X-Service-Token": "zoe_internal_2025"},
                    json={
                        "name": name,
                        "relationship": relationship,
                        "notes": notes
                    }
                )
                
                logger.info(f"PersonExpert: API response status={response.status_code}")
                
                if response.status_code == 200:
                    logger.info(f"PersonExpert: ✅ Successfully created person")
                    return {
                        "success": True,
                        "action": "create_person",
                        "person": {"name": name, "relationship": relationship},
                        "message": f"✅ I'll remember {name}" + (f" (your {relationship})" if relationship else "")
                    }
                else:
                    logger.error(f"PersonExpert: ❌ API error {response.status_code}: {response.text}")
                    return {
                        "success": False,
                        "error": f"API returned {response.status_code}",
                        "message": f"❌ Couldn't save person information"
                    }
        except Exception as e:
            logger.error(f"Person creation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"❌ Error creating person: {e}"
            }
    
    async def _get_person(self, query: str, user_id: str) -> Dict[str, Any]:
        """Get person information"""
        try:
            # Extract name from "Who is Sarah?"
            name_match = re.search(r"who is (\w+)", query, re.IGNORECASE)
            if not name_match:
                name_match = re.search(r"about (\w+)", query, re.IGNORECASE)
            
            if not name_match:
                return {
                    "success": False,
                    "error": "Could not extract person name",
                    "message": "❌ Who would you like to know about?"
                }
            
            name = name_match.group(1).strip()
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_base}/memories/?type=people",
                    headers={"X-Service-Token": "zoe_internal_2025"},
                    timeout=3.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    people = data.get("items", [])
                    
                    # Find person by name
                    person = next((p for p in people if p.get("name", "").lower() == name.lower()), None)
                    
                    if person:
                        rel = person.get("relationship", "")
                        notes = person.get("notes", "")
                        msg = f"👤 {person['name']}"
                        if rel:
                            msg += f" is your {rel}"
                        if notes:
                            msg += f". {notes}"
                        
                        return {
                            "success": True,
                            "action": "get_person",
                            "person": person,
                            "message": msg
                        }
                    else:
                        return {
                            "success": True,
                            "action": "get_person",
                            "person": None,
                            "message": f"👤 I don't have any information about {name}"
                        }
                else:
                    return {
                        "success": False,
                        "error": f"API returned {response.status_code}",
                        "message": "❌ Couldn't retrieve person information"
                    }
        except Exception as e:
            logger.error(f"Person retrieval failed: {e}")
            return {
                "success": True,
                "action": "get_person",
                "message": f"👤 Looking up {name}...",
                "person": None
            }
