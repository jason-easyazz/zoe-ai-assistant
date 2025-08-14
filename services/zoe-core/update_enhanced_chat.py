# Update enhanced chat to store data in database
import re

# Read current main.py
with open('main.py', 'r') as f:
    content = f.read()

# New enhanced chat endpoint with database storage
new_enhanced_chat = '''@app.post("/api/chat/enhanced")
async def enhanced_chat_working(request: dict):
    """Enhanced chat - database storage version"""
    try:
        message = request.get("message", "")
        
        # Simple person detection
        person = None
        if "mum" in message.lower():
            person = "mum"
        elif "dad" in message.lower():
            person = "dad"
        elif "sister" in message.lower():
            person = "sister"
        elif "brother" in message.lower():
            person = "brother"
        
        # Store person in database if detected
        person_stored = False
        if person:
            try:
                async with aiosqlite.connect(CONFIG["database_path"]) as db:
                    # Check if person already exists
                    cursor = await db.execute("SELECT id FROM people WHERE name = ?", (person,))
                    existing = await cursor.fetchone()
                    
                    if existing:
                        # Update mention count
                        await db.execute("""
                            UPDATE people SET 
                                last_mentioned = CURRENT_TIMESTAMP,
                                mention_count = mention_count + 1
                            WHERE id = ?
                        """, (existing[0],))
                    else:
                        # Create new person
                        relationship = "family" if person in ["mum", "dad", "sister", "brother"] else "friend"
                        await db.execute("""
                            INSERT INTO people (name, relationship, last_mentioned, mention_count)
                            VALUES (?, ?, CURRENT_TIMESTAMP, 1)
                        """, (person, relationship))
                    
                    await db.commit()
                    person_stored = True
            except Exception as e:
                print(f"Database storage error: {e}")
        
        return {
            "response": f"I understand! You mentioned: {message}" + (f" I've noted that about your {person}! 😊" if person else ""),
            "entities_detected": {
                "person": person,
                "project": None,
                "memory_type": "general",
                "requires_storage": person is not None
            },
            "profile_updates": {
                "memory_stored": person_stored
            }
        }
    except Exception as e:
        print(f"Enhanced chat error: {e}")
        return {"error": "Enhanced chat failed"}'''

# Replace the enhanced chat endpoint
pattern = r'@app\.post\("/api/chat/enhanced"\)\s*async def enhanced_chat_working\(request: dict\):[^@]*?return \{"error": "Enhanced chat failed"\}'
content = re.sub(pattern, new_enhanced_chat, content, flags=re.DOTALL)

with open('main.py', 'w') as f:
    f.write(content)

print("✅ Enhanced chat updated with database storage")
