import re

# Read main.py
with open('main.py', 'r') as f:
    content = f.read()

# Find the section where events are detected and add database saving
# Look for the appointment detection section
appointment_pattern = r'(elif "appointment" in message_lower:.*?print\(f"✅ Appointment event: \{detected_event\}"\))'

match = re.search(appointment_pattern, content, re.DOTALL)

if match:
    # Add database saving after the appointment detection
    old_section = match.group(1)
    
    new_section = old_section + '''
        
        # Save appointment to database
        try:
            import aiosqlite
            async with aiosqlite.connect('/app/data/zoe.db') as db:
                # Extract time if mentioned
                time_str = None
                if "at" in message_lower:
                    time_match = re.search(r'at (\d{1,2}(?::\d{2})?)\s*(pm|am)?', message_lower)
                    if time_match:
                        time_part = time_match.group(1)
                        period = time_match.group(2)
                        if period:
                            hour = int(time_part.split(':')[0]) if ':' in time_part else int(time_part)
                            minute = int(time_part.split(':')[1]) if ':' in time_part else 0
                            if period.lower() == 'pm' and hour != 12:
                                hour += 12
                            elif period.lower() == 'am' and hour == 12:
                                hour = 0
                            time_str = f"{hour:02d}:{minute:02d}"
                
                await db.execute("""
                    INSERT INTO events (title, start_date, start_time, source, user_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    detected_event["title"],
                    datetime.strptime(detected_event["date"], "%d/%m/%Y").date(),
                    time_str,
                    "chat_detection",
                    "default",
                    datetime.now()
                ))
                await db.commit()
                print(f"✅ Saved appointment to database with time: {time_str}")
        except Exception as e:
            print(f"❌ Database save error: {e}")'''
    
    content = content.replace(old_section, new_section)
    print("✅ Added database persistence for appointments")

# Also add it for birthday detection
birthday_pattern = r'(if "birthday" in message_lower:.*?print\(f"✅ Birthday event: \{detected_event\}"\))'

match = re.search(birthday_pattern, content, re.DOTALL)

if match:
    old_section = match.group(1)
    
    new_section = old_section + '''
        
        # Save birthday to database
        try:
            import aiosqlite
            async with aiosqlite.connect('/app/data/zoe.db') as db:
                await db.execute("""
                    INSERT INTO events (title, start_date, source, user_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    detected_event["title"],
                    datetime.strptime(detected_event["date"], "%d/%m/%Y").date(),
                    "chat_detection", 
                    "default",
                    datetime.now()
                ))
                await db.commit()
                print(f"✅ Saved birthday to database")
        except Exception as e:
            print(f"❌ Database save error: {e}")'''
    
    content = content.replace(old_section, new_section)
    print("✅ Added database persistence for birthdays")

# Write the updated content
with open('main.py', 'w') as f:
    f.write(content)

