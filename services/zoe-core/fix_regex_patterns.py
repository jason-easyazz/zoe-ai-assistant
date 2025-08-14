# Fix regex patterns for better name detection
import re

with open('main.py', 'r') as f:
    content = f.read()

# Improved enhanced chat with better regex
improved_enhanced_chat = '''@app.post("/api/chat/enhanced")
async def enhanced_chat_working(request: dict):
    """Enhanced chat - improved regex patterns"""
    try:
        message = request.get("message", "")
        
        # Enhanced detection logic with better patterns
        person = None
        person_name = None
        relationship = None
        needs_clarification = False
        
        # Pattern 1: "my [relationship] [Name]" - e.g., "my sister Sarah"
        # More precise: only match if Name is at word boundary and capitalized
        pattern1 = re.search(r"\\bmy\\s+(sister|brother|mum|mom|dad|father|friend)\\s+([A-Z][a-z]{2,})\\b", message, re.IGNORECASE)
        if pattern1:
            relationship = pattern1.group(1).lower()
            person_name = pattern1.group(2).capitalize()
            person = person_name
            print(f"Pattern 1 matched: {relationship} {person_name}")
        
        # Pattern 2: "[Name] is my [relationship]" - more precise
        elif re.search(r"\\b([A-Z][a-z]{2,})\\s+is\\s+my\\s+(sister|brother|mum|mom|dad|father|friend)\\b", message, re.IGNORECASE):
            pattern2 = re.search(r"\\b([A-Z][a-z]{2,})\\s+is\\s+my\\s+(sister|brother|mum|mom|dad|father|friend)\\b", message, re.IGNORECASE)
            person_name = pattern2.group(1).capitalize()
            relationship = pattern2.group(2).lower()
            person = person_name
            print(f"Pattern 2 matched: {person_name} is {relationship}")
        
        # Pattern 3: Possessive "[Name]'s" - more restrictive
        elif re.search(r"\\b([A-Z][a-z]{2,})'s\\s+(birthday|anniversary|car|house|job|phone)", message):
            pattern3 = re.search(r"\\b([A-Z][a-z]{2,})'s\\s+(birthday|anniversary|car|house|job|phone)", message)
            person_name = pattern3.group(1).capitalize()
            person = person_name
            relationship = "friend"  # Default
            print(f"Pattern 3 matched: {person_name}")
        
        # Pattern 4: Generic "my [relationship]" (fallback) - only if no name detected
        elif re.search(r"\\bmy\\s+(sister|brother|mum|mom|dad|father)\\b", message, re.IGNORECASE):
            pattern4 = re.search(r"\\bmy\\s+(sister|brother|mum|mom|dad|father)\\b", message, re.IGNORECASE)
            relationship = pattern4.group(1).lower()
            person = relationship
            print(f"Pattern 4 matched: {relationship}")
        
        # Store in database
        person_stored = False
        clarification_message = ""
        
        if person:
            try:
                db_path = "/app/data/zoe.db"
                async with aiosqlite.connect(db_path) as db:
                    # Check if person exists
                    cursor = await db.execute(
                        "SELECT id, mention_count FROM people WHERE name = ? AND user_id = ?", 
                        (person, "default")
                    )
                    existing = await cursor.fetchone()
                    
                    if existing:
                        # Update existing
                        new_count = existing[1] + 1
                        await db.execute(
                            "UPDATE people SET mention_count = ?, last_mentioned = CURRENT_TIMESTAMP WHERE id = ?", 
                            (new_count, existing[0])
                        )
                        person_stored = True
                        print(f"Updated {person}: count now {new_count}")
                    else:
                        # Check for multiple relationships only for generic terms
                        if relationship and not person_name and relationship in ["sister", "brother"]:
                            cursor = await db.execute(
                                "SELECT name FROM people WHERE relationship LIKE ? AND user_id = ? AND name != ?", 
                                (f"%{relationship}%", "default", relationship)
                            )
                            existing_relations = await cursor.fetchall()
                            if existing_relations:
                                needs_clarification = True
                                existing_names = [r[0] for r in existing_relations if r[0] != relationship]
                                if existing_names:
                                    clarification_message = f"I already know about: {', '.join(existing_names)}. What's this {relationship}'s name?"
                        
                        if not needs_clarification:
                            # Create new person
                            rel_type = "family" if relationship in ["sister", "brother", "mum", "mom", "dad", "father"] else "friend"
                            await db.execute("""
                                INSERT INTO people (user_id, name, relationship, mention_count, avatar_emoji)
                                VALUES (?, ?, ?, ?, ?)
                            """, ("default", person, rel_type, 1, "👤"))
                            person_stored = True
                            print(f"Created {person} ({rel_type})")
                    
                    await db.commit()
                    
            except Exception as e:
                print(f"Database error: {e}")
        
        # Generate response
        if needs_clarification:
            response_text = f"I understand you mentioned your {relationship}! {clarification_message}"
        elif person_stored and person_name:
            response_text = f"Got it! I've noted that about {person_name}. I'll remember they're your {relationship}! 😊"
        elif person_stored:
            response_text = f"I understand! I've noted that about your {person}! 😊"
        else:
            response_text = f"I understand! {message}"
        
        return {
            "response": response_text,
            "entities_detected": {
                "person": person,
                "person_name": person_name,
                "relationship": relationship,
                "needs_clarification": needs_clarification,
                "memory_type": "general",
                "requires_storage": person is not None
            },
            "profile_updates": {
                "memory_stored": person_stored
            }
        }
        
    except Exception as e:
        print(f"Enhanced chat error: {e}")
        return {
            "response": "I understand!",
            "entities_detected": {
                "person": None,
                "person_name": None,
                "relationship": None,
                "needs_clarification": False
            },
            "profile_updates": {"memory_stored": False}
        }'''

# Replace the enhanced chat function
lines = content.split('\n')
new_lines = []
skip_until_next_app = False

for line in lines:
    if '@app.post("/api/chat/enhanced")' in line:
        skip_until_next_app = True
        new_lines.append(improved_enhanced_chat)
        continue
    elif skip_until_next_app and line.startswith('@app.'):
        skip_until_next_app = False
        new_lines.append(line)
    elif not skip_until_next_app:
        new_lines.append(line)

with open('main.py', 'w') as f:
    f.write('\n'.join(new_lines))

print("✅ Improved regex patterns applied")
