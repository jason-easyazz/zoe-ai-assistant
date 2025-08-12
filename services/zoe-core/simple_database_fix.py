import re

# Read main.py
with open('main.py', 'r') as f:
    content = f.read()

# Find the line where we print the appointment event and add database saving right after
target_line = 'print(f"✅ Appointment event: {detected_event}")'

if target_line in content:
    # Add database saving code right after the print statement
    database_code = '''
        
        # Save to database
        try:
            import aiosqlite
            async with aiosqlite.connect('/app/data/zoe.db') as db:
                await db.execute("""
                    INSERT INTO events (title, start_date, source, user_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    "Appointment",
                    datetime.strptime(detected_event["date"], "%d/%m/%Y").date(),
                    "chat_detection",
                    "default", 
                    datetime.now()
                ))
                await db.commit()
                print("✅ Saved appointment to database")
        except Exception as e:
            print(f"Database save error: {e}")'''
    
    content = content.replace(target_line, target_line + database_code)
    
    with open('main.py', 'w') as f:
        f.write(content)
    
    print("✅ Added simple database saving for appointments")
else:
    print("❌ Could not find target line")
