import re

# Read main.py
with open('main.py', 'r') as f:
    content = f.read()

# Find the database insertion for events in process_message_integrations
insertion_pattern = r'await db\.execute\(\s*\"\"\"\s*INSERT INTO events \(title, start_date, source, integration_id, created_at, user_id\)\s*VALUES \(\?, \?, \?, \?, \?, \?\)\s*\"\"\",.*?\)'

insertion_match = re.search(insertion_pattern, content, re.DOTALL)

if insertion_match:
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
    
    content = content.replace(insertion_match.group(0), new_insertion)
    
    # Write back
    with open('main.py', 'w') as f:
        f.write(content)
    
    print("✅ Fixed database insertion to include time and proper user_id")
else:
    print("❌ Could not find database insertion code")
