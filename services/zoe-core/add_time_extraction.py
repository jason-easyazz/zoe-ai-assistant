import re

# Read main.py
with open('main.py', 'r') as f:
    content = f.read()

# Find our database saving code and enhance it with time extraction
old_db_code = '''await db.execute("""
                    INSERT INTO events (title, start_date, source, user_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    "Appointment",
                    datetime.strptime(detected_event["date"], "%d/%m/%Y").date(),
                    "chat_detection",
                    "default", 
                    datetime.now()
                ))'''

new_db_code = '''# Extract time from message
                time_str = None
                import re
                time_match = re.search(r'(\d{1,2}(?::\d{2})?)\s*(pm|am)', message_lower)
                if time_match:
                    time_part = time_match.group(1)
                    period = time_match.group(2).lower()
                    hour = int(time_part.split(':')[0]) if ':' in time_part else int(time_part)
                    minute = int(time_part.split(':')[1]) if ':' in time_part else 0
                    if period == 'pm' and hour != 12:
                        hour += 12
                    elif period == 'am' and hour == 12:
                        hour = 0
                    time_str = f"{hour:02d}:{minute:02d}"
                
                await db.execute("""
                    INSERT INTO events (title, start_date, start_time, source, user_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    "Appointment",
                    datetime.strptime(detected_event["date"], "%d/%m/%Y").date(),
                    time_str,
                    "chat_detection",
                    "default", 
                    datetime.now()
                ))'''

content = content.replace(old_db_code, new_db_code)

with open('main.py', 'w') as f:
    f.write(content)

print("✅ Added time extraction to database saving")
