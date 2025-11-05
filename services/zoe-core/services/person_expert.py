"""
Person Expert - Intelligent People Management
==============================================

This expert handles all person-related queries and actions:
- Adding new people
- Updating person details
- Searching for people
- Managing relationships
- Tracking interactions, notes, conversations
- Gift ideas, important dates
- Smart relationship insights

Inspired by Monica CRM but tailored for Zoe's intelligence.
"""

import sqlite3
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, date
import os
import re

logger = logging.getLogger(__name__)

class PersonExpert:
    """Expert for managing people and relationships"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
        self.db_path = db_path
        self.name = "person"
        self.capabilities = [
            "add_person",
            "update_person",
            "search_people",
            "get_person_details",
            "add_note",
            "add_interaction",
            "add_gift_idea",
            "add_important_date",
            "track_conversation",
            "set_relationship",
            "get_relationship_insights"
        ]
    
    def get_capabilities(self) -> List[str]:
        """Return list of capabilities this expert can handle"""
        return self.capabilities
    
    async def can_handle(self, query: str, context: Dict = None) -> Dict[str, Any]:
        """Determine if this expert can handle the query"""
        query_lower = query.lower()
        
        # Keywords that indicate person-related queries
        person_keywords = [
            "person", "people", "friend", "family", "contact",
            "relationship", "birthday", "anniversary", "met",
            "know", "remember about", "remember that", "tell me about",
            "add someone", "new contact", "note about",
            "gift for", "gift idea", "call", "reach out",
            "talked to", "spoke with", "conversation with",
            "colleague", "coworker", "professional", "mom", "dad",
            "phone", "email", "address"  # Field keywords also indicate person queries
        ]
        
        # Check for person-related keywords
        confidence = 0.0
        for keyword in person_keywords:
            if keyword in query_lower:
                confidence = max(confidence, 0.7)
        
        # Check for specific actions
        if any(action in query_lower for action in ["add", "create", "new"]) and \
           any(person in query_lower for person in ["person", "friend", "contact", "someone"]):
            confidence = 0.9
        
        # Check for names (capitalized words that are likely names)
        words = query.split()
        # Exclude common question words
        exclude_words = {"I", "What", "Who", "When", "Where", "Why", "How", "Can", "Could", "Would", "Should", "Add", "Create", "New", "Tell", "Remember", "Is", "Are", "The", "A"}
        capitalized_words = [w for w in words if w[0].isupper() and w not in exclude_words]
        
        if capitalized_words and any(keyword in query_lower for keyword in person_keywords):
            confidence = max(confidence, 0.8)
        
        # Special case: "remember that X" with a name is likely about a person
        if "remember that" in query_lower and capitalized_words:
            confidence = max(confidence, 0.8)
        
        # Special case: "who is X?" queries
        if query_lower.startswith("who is") and capitalized_words:
            confidence = max(confidence, 0.9)
        
        return {
            "can_handle": confidence > 0.5,
            "confidence": confidence,
            "reasoning": f"Detected person-related query with {len(capitalized_words)} potential names"
        }
    
    async def execute(self, query: str, user_id: str, context: Dict = None) -> Dict[str, Any]:
        """Execute person-related action"""
        query_lower = query.lower()
        
        # Determine action type
        if any(word in query_lower for word in ["add", "create", "new"]):
            return await self._handle_add_person(query, user_id)
        elif "note about" in query_lower or "remember that" in query_lower:
            return await self._handle_add_note(query, user_id)
        elif "gift" in query_lower and ("idea" in query_lower or "for" in query_lower):
            return await self._handle_add_gift(query, user_id)
        elif any(word in query_lower for word in ["search", "find", "who is", "tell me about"]):
            return await self._handle_search_person(query, user_id)
        elif "birthday" in query_lower or "anniversary" in query_lower:
            return await self._handle_important_date(query, user_id)
        elif "talked to" in query_lower or "spoke with" in query_lower or "conversation" in query_lower:
            return await self._handle_log_conversation(query, user_id)
        else:
            # Default: search for person
            return await self._handle_search_person(query, user_id)
    
    async def _handle_add_person(self, query: str, user_id: str) -> Dict[str, Any]:
        """Add a new person"""
        # Extract name from query
        name = self._extract_name(query)
        if not name:
            return {
                "success": False,
                "message": "I couldn't identify a name. Please specify who you'd like to add.",
                "requires_input": True,
                "prompt": "What's the person's name?"
            }
        
        # Extract all available fields
        relationship = self._extract_relationship(query)
        notes = self._extract_notes(query)
        birthday = self._extract_date(query) if "birthday" in query.lower() or "born" in query.lower() else None
        phone = self._extract_phone(query)
        email = self._extract_email(query)
        address = self._extract_address(query)
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if person already exists
            cursor.execute("""
                SELECT id FROM people WHERE user_id = ? AND name = ?
            """, (user_id, name))
            
            if cursor.fetchone():
                conn.close()
                return {
                    "success": False,
                    "message": f"{name} is already in your contacts. Would you like to update their information instead?",
                    "person_exists": True
                }
            
            # Insert new person with all available fields
            cursor.execute("""
                INSERT INTO people (user_id, name, relationship, birthday, phone, email, address, notes, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                name,
                relationship,
                birthday,
                phone,
                email,
                address,
                notes,
                json.dumps({"added_via": "chat", "added_at": datetime.now().isoformat()})
            ))
            
            person_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            # Build detailed success message
            details = []
            if relationship:
                details.append(f"as {relationship}")
            if birthday:
                details.append(f"🎂 {birthday}")
            if phone:
                details.append(f"📞 {phone}")
            if email:
                details.append(f"✉️ {email}")
            
            detail_str = " (" + ", ".join(details) + ")" if details else ""
            
            return {
                "success": True,
                "message": f"✅ Added {name} to your people{detail_str}!",
                "person_id": person_id,
                "person_name": name,
                "relationship": relationship,
                "birthday": birthday,
                "phone": phone,
                "email": email,
                "address": address,
                "action_taken": "person_created"
            }
            
        except Exception as e:
            logger.error(f"Error adding person: {e}")
            return {
                "success": False,
                "message": f"Sorry, I couldn't add {name}. Error: {str(e)}"
            }
    
    async def _handle_search_person(self, query: str, user_id: str) -> Dict[str, Any]:
        """Search for a person"""
        # Extract search term
        search_term = self._extract_search_term(query)
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Search people by name, relationship, or notes
            pattern = f"%{search_term}%"
            cursor.execute("""
                SELECT id, name, relationship, birthday, phone, email, notes
                FROM people
                WHERE user_id = ? AND (
                    name LIKE ? OR
                    relationship LIKE ? OR
                    notes LIKE ? OR
                    email LIKE ?
                )
                LIMIT 5
            """, (user_id, pattern, pattern, pattern, pattern))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "name": row[1],
                    "relationship": row[2],
                    "birthday": row[3],
                    "phone": row[4],
                    "email": row[5],
                    "notes": row[6]
                })
            
            conn.close()
            
            if not results:
                return {
                    "success": True,
                    "message": f"I couldn't find anyone matching '{search_term}'. Would you like to add them?",
                    "results": [],
                    "suggest_add": True
                }
            
            # Format results nicely
            if len(results) == 1:
                person = results[0]
                info_parts = [f"**{person['name']}**"]
                if person['relationship']:
                    info_parts.append(f"({person['relationship']})")
                if person['birthday']:
                    info_parts.append(f"🎂 {person['birthday']}")
                if person['phone']:
                    info_parts.append(f"📞 {person['phone']}")
                if person['email']:
                    info_parts.append(f"✉️ {person['email']}")
                if person['notes']:
                    info_parts.append(f"\n\n_{person['notes']}_")
                
                message = " ".join(info_parts)
            else:
                message = f"Found {len(results)} people:\n\n"
                for person in results:
                    message += f"• **{person['name']}**"
                    if person['relationship']:
                        message += f" ({person['relationship']})"
                    message += "\n"
            
            return {
                "success": True,
                "message": message,
                "results": results,
                "count": len(results)
            }
            
        except Exception as e:
            logger.error(f"Error searching people: {e}")
            return {
                "success": False,
                "message": f"Sorry, I encountered an error while searching: {str(e)}"
            }
    
    async def _handle_add_note(self, query: str, user_id: str) -> Dict[str, Any]:
        """Add a note about a person"""
        # Extract person name and note
        name = self._extract_name(query)
        note_text = self._extract_note_text(query)
        
        if not name or not note_text:
            return {
                "success": False,
                "message": "I need both a person's name and the note content.",
                "requires_input": True
            }
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Find person
            cursor.execute("""
                SELECT id, notes FROM people WHERE user_id = ? AND name LIKE ?
            """, (user_id, f"%{name}%"))
            
            result = cursor.fetchone()
            if not result:
                conn.close()
                return {
                    "success": False,
                    "message": f"I couldn't find {name}. Would you like to add them first?"
                }
            
            person_id, existing_notes = result
            
            # Append to existing notes
            updated_notes = f"{existing_notes}\n\n{datetime.now().strftime('%Y-%m-%d')}: {note_text}" if existing_notes else note_text
            
            cursor.execute("""
                UPDATE people SET notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (updated_notes, person_id))
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "message": f"✅ Added note about {name}!",
                "person_id": person_id,
                "action_taken": "note_added"
            }
            
        except Exception as e:
            logger.error(f"Error adding note: {e}")
            return {
                "success": False,
                "message": f"Sorry, I couldn't add that note: {str(e)}"
            }
    
    async def _handle_add_gift(self, query: str, user_id: str) -> Dict[str, Any]:
        """Add a gift idea for someone"""
        name = self._extract_name(query)
        gift_item = self._extract_gift_item(query)
        
        if not name or not gift_item:
            return {
                "success": False,
                "message": "I need the person's name and the gift idea.",
                "requires_input": True
            }
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Find person
            cursor.execute("SELECT id FROM people WHERE user_id = ? AND name LIKE ?", (user_id, f"%{name}%"))
            result = cursor.fetchone()
            
            if not result:
                conn.close()
                return {
                    "success": False,
                    "message": f"I couldn't find {name}. Add them first?"
                }
            
            person_id = result[0]
            
            # Add gift idea
            cursor.execute("""
                INSERT INTO person_gifts (user_id, person_id, item, occasion, status)
                VALUES (?, ?, ?, ?, 'idea')
            """, (user_id, person_id, gift_item, "unspecified"))
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "message": f"✅ Added gift idea '{gift_item}' for {name}!",
                "person_id": person_id,
                "action_taken": "gift_idea_added"
            }
            
        except Exception as e:
            logger.error(f"Error adding gift: {e}")
            return {
                "success": False,
                "message": f"Sorry, couldn't add that gift idea: {str(e)}"
            }
    
    async def _handle_important_date(self, query: str, user_id: str) -> Dict[str, Any]:
        """Add an important date for someone"""
        name = self._extract_name(query)
        date_info = self._extract_date(query)
        
        if not name:
            return {
                "success": False,
                "message": "Whose birthday/anniversary is it?",
                "requires_input": True
            }
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Find person
            cursor.execute("SELECT id FROM people WHERE user_id = ? AND name LIKE ?", (user_id, f"%{name}%"))
            result = cursor.fetchone()
            
            if not result:
                conn.close()
                return {
                    "success": False,
                    "message": f"I couldn't find {name}. Add them first?"
                }
            
            person_id = result[0]
            
            # Update birthday if it's a birthday
            if "birthday" in query.lower():
                cursor.execute("""
                    UPDATE people SET birthday = ? WHERE id = ?
                """, (date_info, person_id))
            else:
                # Add to important dates
                cursor.execute("""
                    INSERT INTO person_important_dates (user_id, person_id, name, date)
                    VALUES (?, ?, ?, ?)
                """, (user_id, person_id, "Anniversary", date_info))
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "message": f"✅ Saved {name}'s important date: {date_info}",
                "person_id": person_id,
                "action_taken": "date_added"
            }
            
        except Exception as e:
            logger.error(f"Error adding date: {e}")
            return {
                "success": False,
                "message": f"Sorry, couldn't save that date: {str(e)}"
            }
    
    async def _handle_log_conversation(self, query: str, user_id: str) -> Dict[str, Any]:
        """Log a conversation with someone"""
        name = self._extract_name(query)
        topic = self._extract_conversation_topic(query)
        
        if not name:
            return {
                "success": False,
                "message": "Who did you talk to?",
                "requires_input": True
            }
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Find person
            cursor.execute("SELECT id FROM people WHERE user_id = ? AND name LIKE ?", (user_id, f"%{name}%"))
            result = cursor.fetchone()
            
            if not result:
                conn.close()
                return {
                    "success": False,
                    "message": f"I couldn't find {name}. Add them first?"
                }
            
            person_id = result[0]
            
            # Log conversation
            cursor.execute("""
                INSERT INTO person_conversations (user_id, person_id, topic, notes, conversation_date)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, person_id, topic or "General conversation", query, datetime.now().strftime("%Y-%m-%d")))
            
            # Update last contact in metadata
            cursor.execute("""
                UPDATE people 
                SET metadata = json_set(COALESCE(metadata, '{}'), '$.lastContact', ?)
                WHERE id = ?
            """, (datetime.now().isoformat(), person_id))
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "message": f"✅ Logged conversation with {name}!",
                "person_id": person_id,
                "action_taken": "conversation_logged"
            }
            
        except Exception as e:
            logger.error(f"Error logging conversation: {e}")
            return {
                "success": False,
                "message": f"Sorry, couldn't log that conversation: {str(e)}"
            }
    
    # Helper methods for extraction
    
    def _extract_name(self, query: str) -> Optional[str]:
        """Extract a person's name from the query"""
        # Look for capitalized words (names)
        words = query.split()
        capitalized = [w for w in words if w[0].isupper() and len(w) > 1]
        
        # Filter out common non-name words
        exclude = {"I", "What", "Who", "When", "Where", "Why", "How", "Can", "Could", "Would", "Should", "Add", "Create", "New", "Tell", "Remember"}
        names = [w for w in capitalized if w not in exclude]
        
        # Return first potential name
        return names[0] if names else None
    
    def _extract_relationship(self, query: str) -> Optional[str]:
        """Extract relationship type from query"""
        relationships = {
            "friend": "friend",
            "family": "family",
            "colleague": "colleague",
            "coworker": "colleague",
            "partner": "partner",
            "spouse": "spouse",
            "sibling": "family",
            "parent": "family",
            "child": "family"
        }
        
        query_lower = query.lower()
        for keyword, relationship in relationships.items():
            if keyword in query_lower:
                return relationship
        
        return None
    
    def _extract_notes(self, query: str) -> Optional[str]:
        """Extract notes from query"""
        # Look for text after keywords like "note:", "remember:", etc.
        patterns = [
            r"note[s]?:\s*(.+)",
            r"remember:\s*(.+)",
            r"details?:\s*(.+)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        return None
    
    def _extract_search_term(self, query: str) -> str:
        """Extract search term from query"""
        # Remove common search prefixes
        query = re.sub(r"^(search for|find|who is|tell me about)\s+", "", query, flags=re.IGNORECASE)
        return query.strip()
    
    def _extract_note_text(self, query: str) -> Optional[str]:
        """Extract note text from query"""
        # Look for text after "note about X:"or "remember that X"
        patterns = [
            r"note about .+?:\s*(.+)",
            r"remember that .+?\s+(.+)",
            r"remember:\s*(.+)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        return None
    
    def _extract_gift_item(self, query: str) -> Optional[str]:
        """Extract gift item from query"""
        # Look for text after "gift" or "get them"
        patterns = [
            r"gift idea[s]?:?\s*(.+)",
            r"get (?:them|him|her)\s+(.+)",
            r"gift:?\s*(.+)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, query, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        return None
    
    def _extract_date(self, query: str) -> Optional[str]:
        """Extract date from query"""
        # Look for date patterns
        import re
        from datetime import datetime
        
        # Try to find date patterns like "Jan 15", "January 15th", "2024-01-15", etc.
        date_patterns = [
            r"(\d{4}-\d{2}-\d{2})",  # YYYY-MM-DD
            r"(\d{1,2}/\d{1,2}/\d{4})",  # MM/DD/YYYY
            r"(\d{1,2}/\d{1,2})",  # MM/DD (assume current year)
            r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)",  # Month DD, YYYY or Month DD
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                # Try to normalize to YYYY-MM-DD format
                try:
                    # Handle different formats
                    if '-' in date_str and len(date_str.split('-')[0]) == 4:
                        return date_str  # Already YYYY-MM-DD
                    elif '/' in date_str:
                        parts = date_str.split('/')
                        if len(parts) == 3:
                            return f"{parts[2]}-{parts[0]:0>2}-{parts[1]:0>2}"
                        elif len(parts) == 2:
                            # Assume current year
                            from datetime import datetime
                            year = datetime.now().year
                            return f"{year}-{parts[0]:0>2}-{parts[1]:0>2}"
                    else:
                        # Month name format - return as is for now
                        return date_str
                except:
                    return date_str
        
        return None
    
    def _extract_phone(self, query: str) -> Optional[str]:
        """Extract phone number from query"""
        import re
        
        # Phone patterns
        phone_patterns = [
            r"phone[:\s]+([0-9-().\s]+)",
            r"call[:\s]+([0-9-().\s]+)",
            r"number[:\s]+([0-9-().\s]+)",
            r"(\d{3}[-.\s]?\d{3}[-.\s]?\d{4})",  # 555-123-4567 or 5551234567
            r"(\(\d{3}\)\s*\d{3}[-.\s]?\d{4})",  # (555) 123-4567
        ]
        
        for pattern in phone_patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                phone = match.group(1).strip()
                # Clean up phone number
                phone = re.sub(r'[^\d\-().\s]', '', phone)
                if len(phone.replace('-', '').replace('(', '').replace(')', '').replace('.', '').replace(' ', '')) >= 10:
                    return phone
        
        return None
    
    def _extract_email(self, query: str) -> Optional[str]:
        """Extract email address from query"""
        import re
        
        # Email pattern
        email_pattern = r'\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b'
        match = re.search(email_pattern, query)
        
        if match:
            return match.group(1)
        
        return None
    
    def _extract_address(self, query: str) -> Optional[str]:
        """Extract address from query"""
        import re
        
        # Address patterns
        address_patterns = [
            r"address[:\s]+(.+?)(?:,|$|\s+phone|\s+email|\s+birthday)",
            r"lives at[:\s]+(.+?)(?:,|$|\s+phone|\s+email)",
            r"located at[:\s]+(.+?)(?:,|$|\s+phone|\s+email)",
            r"(\d+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+St(?:reet)?|\s+Ave(?:nue)?|\s+Rd|\s+Dr|\s+Ln|\s+Blvd))",
        ]
        
        for pattern in address_patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                address = match.group(1).strip()
                # Remove trailing punctuation
                address = re.sub(r'[,.]$', '', address)
                return address
        
        return None
    
    def _extract_conversation_topic(self, query: str) -> Optional[str]:
        """Extract conversation topic from query"""
        # Look for "about X" patterns
        patterns = [
            r"about\s+(.+)",
            r"discussed\s+(.+)",
            r"topic[s]?:?\s*(.+)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        return "General conversation"

# Global instance
person_expert = PersonExpert()

