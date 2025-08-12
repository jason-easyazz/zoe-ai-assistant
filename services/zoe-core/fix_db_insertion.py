import re

with open('main.py', 'r') as f:
    content = f.read()

# Find and update the events insertion in process_message_integrations
# Look for the insertion pattern
pattern = r'(await db\.execute\(\s*\"\"\"\s*INSERT INTO events \([^)]+\)\s*VALUES \([^)]+\)\s*\"\"\",\s*\([^)]+\),?\s*\))'

matches = list(re.finditer(pattern, content, re.DOTALL))

for match in matches:
    old_insertion = match.group(0)
    
    # Check if this is the events insertion (should contain event["title"])
    if 'event["title"]' in old_insertion:
        new_insertion = '''await db.execute(
                    """
                    INSERT INTO events (title, start_date, start_time, source, integration_id, created_at, user_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["title"],
                        event.get("date", datetime.now().date()),
                        event.get("time"),
                        "chat_detection",
                        f"conv_{conversation_id}",
                        datetime.now(),
                        "default",
                    ),
                )'''
        
        content = content.replace(old_insertion, new_insertion)
        print("✅ Updated events database insertion")
        break

with open('main.py', 'w') as f:
    f.write(content)
