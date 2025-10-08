"""
JournalExpert - Natural Language Journal Entry Management
========================================================
"""
import httpx
import re
from typing import Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class JournalExpert:
    """Expert for journal entries, reflections, and mood tracking"""
    
    def __init__(self):
        self.api_base = "http://zoe-core-test:8000/api"
        self.intent_patterns = [
            r"journal:|write.*journal|today.*journal",
            r"how.*feeling|mood.*today|reflect",
            r"my.*journal|recent.*entries",
            r"what.*wrote|journal.*about"
        ]
    
    def can_handle(self, query: str) -> float:
        """Return confidence score for handling this query"""
        query_lower = query.lower()
        
        # High confidence for explicit journal commands
        if re.search(r"journal:", query_lower):
            return 0.95
        
        # Medium-high for journal queries
        if re.search(r"journal|feeling|mood|reflect|wrote", query_lower):
            return 0.85
        
        # Check other patterns
        for pattern in self.intent_patterns:
            if re.search(pattern, query_lower):
                return 0.75
        
        return 0.0
    
    async def execute(self, query: str, user_id: str) -> Dict[str, Any]:
        """Execute journal-related actions"""
        query_lower = query.lower()
        
        # Detect action type
        if "journal:" in query_lower or "write" in query_lower:
            return await self._create_entry(query, user_id)
        elif "show" in query_lower or "recent" in query_lower or "what" in query_lower:
            return await self._get_entries(query, user_id)
        else:
            return await self._get_entries(query, user_id)
    
    async def _create_entry(self, query: str, user_id: str) -> Dict[str, Any]:
        """Create a journal entry from natural language"""
        try:
            # Extract content after "journal:" or after "write"
            content_match = re.search(r"journal:\s*(.+)|write.*?journal\s+(.+)", query, re.IGNORECASE | re.DOTALL)
            content = content_match.group(1) or content_match.group(2) if content_match else query
            
            # Extract title (first sentence or first 50 chars)
            title_match = re.match(r"([^.!?]+)", content)
            title = title_match.group(1)[:50] if title_match else f"Journal {datetime.now().strftime('%b %d')}"
            
            # Call journal API
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/journal/entries",
                    headers={"X-Service-Token": "zoe_internal_2025"},
                    json={
                        "title": title.strip(),
                        "content": content.strip(),
                        "user_id": user_id,
                        "tags": []
                    },
                    timeout=5.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "success": True,
                        "action": "create_journal_entry",
                        "entry_id": data.get("id"),
                        "message": f"✅ Journal entry saved: {title[:40]}..."
                    }
                else:
                    return {
                        "success": False,
                        "error": f"API returned {response.status_code}",
                        "message": "❌ Couldn't save journal entry"
                    }
        except Exception as e:
            logger.error(f"Journal creation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"❌ Error saving journal: {e}"
            }
    
    async def _get_entries(self, query: str, user_id: str) -> Dict[str, Any]:
        """Get recent journal entries"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_base}/journal/entries",
                    params={"user_id": user_id, "limit": 5},
                    headers={"X-Service-Token": "zoe_internal_2025"},
                    timeout=3.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    entries = data.get("entries", [])
                    
                    if entries:
                        recent = entries[0]
                        return {
                            "success": True,
                            "action": "get_journal_entries",
                            "results": entries,
                            "message": f"📖 Your recent journal: {recent.get('title', '')} - {recent.get('content', '')[:100]}..."
                        }
                    else:
                        return {
                            "success": True,
                            "action": "get_journal_entries",
                            "results": [],
                            "message": "📖 No journal entries yet. Start writing!"
                        }
                else:
                    return {
                        "success": False,
                        "error": f"API returned {response.status_code}",
                        "message": "❌ Couldn't retrieve journal"
                    }
        except Exception as e:
            logger.error(f"Journal retrieval failed: {e}")
            return {
                "success": True,
                "action": "get_journal_entries",
                "message": "📖 Searching journal entries...",
                "results": []
            }

