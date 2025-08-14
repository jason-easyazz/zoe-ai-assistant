import re
import json
import asyncio
import aiosqlite
from typing import Dict, List, Optional

class EntityExtractor:
    """Extract people, projects, and memory types from natural language"""
    
    def __init__(self):
        self.people_patterns = [
            r"my (mum|mom|mother|dad|father|sister|brother|wife|husband|partner)",
            r"my friend (\w+)",
            r"(\w+)'s (birthday|anniversary)",
        ]
        
        self.project_patterns = [
            r"(house build|renovation|kitchen project|garden|home improvement)",
            r"(work project|thesis|business idea|startup)",
            r"(trip to \w+|vacation|holiday)",
            r"for the (\w+(?:\s+\w+)?)",
        ]
        
        self.memory_types = {
            "preference": ["likes", "loves", "enjoys", "prefers", "favorite"],
            "dislike": ["hates", "dislikes", "can't stand", "allergic"],
            "fact": ["birthday", "anniversary", "works at", "lives in"],
            "gift_idea": ["wants", "needs", "mentioned wanting"],
            "idea": ["remember this", "save this", "for the"],
        }

    async def extract_entities(self, message: str) -> Dict:
        """Extract entities from a message"""
        message_lower = message.lower()
        
        person = await self._extract_person(message_lower)
        project = await self._extract_project(message_lower)
        memory_type = self._classify_memory_type(message_lower)
        key_info = self._extract_key_info(message, person, project)
        
        return {
            "person": person,
            "project": project, 
            "memory_type": memory_type,
            "key_info": key_info,
            "confidence": 0.8,
            "requires_storage": self._should_store(message_lower)
        }

    async def _extract_person(self, message: str) -> Optional[str]:
        """Extract person name from message"""
        # Family relationships
        family_match = re.search(r"my (mum|mom|mother|dad|father|sister|brother|wife|husband|partner)", message)
        if family_match:
            return family_match.group(1).replace("mom", "mum")
        
        # Friend mentions
        friend_match = re.search(r"my friend (\w+)", message)
        if friend_match:
            return friend_match.group(1).capitalize()
        
        # Possessive names
        name_match = re.search(r"(\w+)'s", message)
        if name_match:
            name = name_match.group(1).capitalize()
            if len(name) > 2 and name.isalpha():
                return name
        
        return None

    async def _extract_project(self, message: str) -> Optional[str]:
        """Extract project name from message"""
        for pattern in self.project_patterns:
            match = re.search(pattern, message)
            if match:
                return match.group(1).title()
        return None

    def _classify_memory_type(self, message: str) -> str:
        """Classify the type of memory being stored"""
        for mem_type, indicators in self.memory_types.items():
            if any(indicator in message for indicator in indicators):
                return mem_type
        return "general"

    def _extract_key_info(self, message: str, person: Optional[str], project: Optional[str]) -> str:
        """Extract the key information to remember"""
        clean_message = re.sub(r"(remember|save this|keep this|note this)", "", message, flags=re.IGNORECASE)
        return clean_message.strip()

    def _should_store(self, message: str) -> bool:
        """Determine if this message should be stored as a memory"""
        storage_triggers = [
            "remember", "save", "keep", "note", "birthday", "anniversary", 
            "likes", "loves", "hates", "allergic", "prefers", "wants"
        ]
        return any(trigger in message for trigger in storage_triggers)

# Profile manager
class ProfileManager:
    """Manage people profiles and project folders"""
    
    def __init__(self, db_path="/app/data/zoe.db"):
        self.db_path = db_path

    async def process_memory(self, entities: Dict, original_message: str) -> Dict:
        """Process extracted entities and store as organized memories"""
        if not entities["requires_storage"]:
            return {"memory_stored": False, "person_updated": False, "project_updated": False}
        
        async with aiosqlite.connect(self.db_path) as db:
            person_id = await self._get_person_id(entities["person"]) if entities["person"] else None
            project_id = await self._get_project_id(entities["project"]) if entities["project"] else None
            
            # Store memory
            await db.execute("""
                INSERT INTO memories (content, memory_type, person_id, project_id, source)
                VALUES (?, ?, ?, ?, 'chat')
            """, (entities["key_info"], entities["memory_type"], person_id, project_id))
            
            # Update person profile
            if person_id:
                await self._update_person_profile(person_id, entities)
            
            # Update project folder
            if project_id:
                await self._update_project_folder(project_id, entities)
            
            await db.commit()
            
            return {
                "memory_stored": True,
                "person_updated": bool(person_id),
                "project_updated": bool(project_id)
            }

    async def _get_person_id(self, person_name: str) -> Optional[int]:
        """Get or create person ID"""
        if not person_name:
            return None
            
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT id FROM people WHERE name = ?", (person_name,))
            row = await cursor.fetchone()
            
            if row:
                # Update mention count
                await db.execute("""
                    UPDATE people SET last_mentioned = CURRENT_TIMESTAMP, mention_count = mention_count + 1 
                    WHERE id = ?
                """, (row[0],))
                await db.commit()
                return row[0]
            
            # Create new person
            relationship = "family" if person_name.lower() in ["mum", "dad", "mother", "father", "sister", "brother"] else "friend"
            cursor = await db.execute("""
                INSERT INTO people (name, relationship, last_mentioned, mention_count)
                VALUES (?, ?, CURRENT_TIMESTAMP, 1)
            """, (person_name, relationship))
            await db.commit()
            return cursor.lastrowid

    async def _get_project_id(self, project_name: str) -> Optional[int]:
        """Get or create project ID"""
        if not project_name:
            return None
            
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
            row = await cursor.fetchone()
            
            if row:
                # Update activity
                await db.execute("""
                    UPDATE projects SET last_activity = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (row[0],))
                await db.commit()
                return row[0]
            
            # Create new project
            category = "home" if any(term in project_name.lower() for term in ["house", "renovation", "kitchen", "garden"]) else "personal"
            cursor = await db.execute("""
                INSERT INTO projects (name, category, last_activity)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (project_name, category))
            await db.commit()
            return cursor.lastrowid

    async def _update_person_profile(self, person_id: int, entities: Dict):
        """Update person's profile with new information"""
        category = "interests" if entities["memory_type"] == "preference" else entities["memory_type"]
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO person_attributes 
                (person_id, category, attribute_key, attribute_value)
                VALUES (?, ?, ?, ?)
            """, (person_id, category, entities["memory_type"], entities["key_info"]))
            await db.commit()

    async def _update_project_folder(self, project_id: int, entities: Dict):
        """Update project folder with new item"""
        item_type = entities["memory_type"] if entities["memory_type"] != "general" else "idea"
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO project_items (project_id, item_type, title, description)
                VALUES (?, ?, ?, ?)
            """, (project_id, item_type, entities["key_info"][:100], entities["key_info"]))
            
            # Update item count
            await db.execute("""
                UPDATE projects SET item_count = (SELECT COUNT(*) FROM project_items WHERE project_id = ?)
                WHERE id = ?
            """, (project_id, project_id))
            await db.commit()

# Global instances
entity_extractor = EntityExtractor()
profile_manager = ProfileManager()
