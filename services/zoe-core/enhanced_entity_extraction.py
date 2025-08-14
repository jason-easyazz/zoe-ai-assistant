# Enhanced entity extraction with names
import re

with open('main.py', 'r') as f:
    content = f.read()

# Enhanced entity detection function
enhanced_detection = '''@app.post("/api/chat/enhanced")
async def enhanced_chat_working(request: dict):
    """Enhanced chat - smart name detection"""
    try:
        message = request.get("message", "")
        
        # Enhanced person detection with names
        person = None
        person_name = None
        relationship = None
        
        # Pattern 1: "my [relationship] [name]" - e.g., "my sister Sarah"
        relationship_name_pattern = r"my (mum|mom|mother|dad|father|sister|brother|wife|husband|partner|friend)\s+([A-Z][a-z]+)"
        match = re.search(relationship_name_pattern, message, re.IGNORECASE)
        if match:
            relationship = match.group(1).lower().replace("mom", "mum")
            person_name = match.group(2).capitalize()
            person = person_name
        
        # Pattern 2: "[Name] is my [relationship]" - e.g., "Sarah is my sister"
        elif re.search(r"([A-Z][a-z]+)\s+is\s+my\s+(sister|brother|mum|dad|mother|father|friend)", message, re.IGNORECASE):
            match = re.search(r"([A-Z][a-z]+)\s+is\s+my\s+(sister|brother|mum|dad|mother|father|friend)", message, re.IGNORECASE)
            person_name = match.group(1).capitalize()
            relationship = match.group(2).lower().replace("mom", "mum")
            person = person_name
        
        # Pattern 3: Possessive names - "Sarah's birthday", "John's car"
        elif re.search(r"([A-Z][a-z]+)'s\s+", message):
            match = re.search(r"([A-Z][a-z]+)'s\s+", message)
            person_name = match.group(1).capitalize()
            person = person_name
            relationship = "friend"  # Default to friend, will be updated if we know more
        
        # Pattern 4: Generic relationships without names (fallback to current system)
        elif any(rel in message.lower() for rel in ["my mum", "my mom", "my dad", "my sister", "my brother"]):
            for rel in ["mum", "mom", "dad", "father", "sister", "brother"]:
                if f"my {rel}" in message.lower():
                    relationship = rel.replace("mom", "mum")
                    person = relationship
                    break
        
        # Store person in database if detected
        person_stored = False
        needs_clarification = False
        
        if person:
            try:
                db_path = "/app/data/zoe.db"
                async with aiosqlite.connect(db_path) as db:
                    # Check if this exact person exists
                    cursor = await db.execute(
                        "SELECT id, mention_count, relationship FROM people WHERE name = ? AND user_id = ?", 
                        (person, "default")
                    )
                    existing = await cursor.fetchone()
                    
                    if existing:
                        # Update existing person
                        new_count = existing[1] + 1
                        await db.execute("""
                            UPDATE people SET 
                                mention_count = ?,
                                last_mentioned = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (new_count, existing[0]))
                        person_stored = True
                        print(f"Updated {person}: mention count now {new_count}")
                    else:
                        # Check if we have multiple people with the same relationship
                        if relationship and relationship in ["sister", "brother"] and not person_name:
                            cursor = await db.execute(
                                "SELECT name FROM people WHERE relationship = ? AND user_id = ?", 
                                (relationship, "default")
                            )
                            existing_relations = await cursor.fetchall()
                            if existing_relations:
                                needs_clarification = True
                                person_stored = False
                                print(f"Multiple {relationship}s found: {[r[0] for r in existing_relations]}")
                            else:
                                # First person with this relationship
                                await db.execute("""
                                    INSERT INTO people (user_id, name, relationship, mention_count, avatar_emoji)
                                    VALUES (?, ?, ?, ?, ?)
                                """, ("default", person, "family", 1, "👤"))
                                person_stored = True
                        else:
                            # Insert new person with name or unique relationship
                            rel_category = "family" if relationship in ["mum", "dad", "sister", "brother", "mother", "father"] else "friend"
                            await db.execute("""
                                INSERT INTO people (user_id, name, relationship, mention_count, avatar_emoji)
                                VALUES (?, ?, ?, ?, ?)
                            """, ("default", person, rel_category, 1, "👤"))
                            person_stored = True
                            print(f"Created new person: {person} ({rel_category})")
                    
                    await db.commit()
                    
            except Exception as e:
                print(f"Storage error: {e}")
                person_stored = False
        
        # Generate appropriate response
        if needs_clarification:
            existing_names = []
            async with aiosqlite.connect("/app/data/zoe.db") as db:
                cursor = await db.execute(
                    "SELECT name FROM people WHERE relationship = ? AND user_id = ?", 
                    (relationship, "default")
                )
                existing_names = [r[0] for r in await cursor.fetchall()]
            
            response_text = f"I understand you mentioned your {relationship}! I already know about: {', '.join(existing_names)}. What's your {relationship}'s name so I can keep track of them separately?"
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
            "error": "Enhanced chat failed",
            "response": "Sorry, I had an issue processing that.",
            "entities_detected": {"person": None, "needs_clarification": False},
            "profile_updates": {"memory_stored": False}
        }'''

# Replace the enhanced chat function
pattern = r'@app\.post\("/api/chat/enhanced"\)[^@]*?(?=@app\.|$)'
content = re.sub(pattern, enhanced_detection + '\n\n', content, flags=re.DOTALL)

with open('main.py', 'w') as f:
    f.write(content)

print("✅ Enhanced entity extraction with names implemented")
