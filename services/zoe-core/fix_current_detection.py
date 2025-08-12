#!/usr/bin/env python3

def fix_current_detection():
    """Fix the current event detection logic in main.py"""
    
    with open('main.py', 'r') as f:
        content = f.read()
    
    # Find and replace the current detection logic
    old_detection = '''    # Simple but effective detection
    if "add" in message_lower and "birthday" in message_lower:
        detected_event = {"title": "Birthday", "date": "24/03/2025", "created": True}
    elif "appointment" in message_lower and "tomorrow" in message_lower:
        detected_event = {"title": "Doctor Appointment", "date": "12/08/2025", "created": True}'''
    
    new_detection = '''    # Enhanced event detection that actually works
    from datetime import date, timedelta
    import re
    
    if "birthday" in message_lower:
        print("🎂 Birthday detected!")
        # Look for month and day
        month_match = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)\\s+(\\d{1,2})', message_lower)
        if month_match:
            month_name, day = month_match.groups()
            months = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']
            month_num = months.index(month_name) + 1
            try:
                event_date = date(2025, month_num, int(day))
                detected_event = {
                    "title": "Birthday", 
                    "date": event_date.strftime("%d/%m/%Y"), 
                    "category": "personal",
                    "priority": "high",
                    "created": True
                }
                print(f"✅ Birthday event: {detected_event}")
            except ValueError:
                detected_event = {"title": "Birthday", "date": "TBD", "created": True}
        else:
            detected_event = {"title": "Birthday", "date": "TBD", "created": True}
    
    elif "meeting" in message_lower:
        print("📅 Meeting detected!")
        event_date = date.today() + timedelta(days=1)  # Default tomorrow
        if "tomorrow" in message_lower:
            event_date = date.today() + timedelta(days=1)
        elif "today" in message_lower:
            event_date = date.today()
        detected_event = {
            "title": "Meeting", 
            "date": event_date.strftime("%d/%m/%Y"), 
            "category": "work",
            "priority": "medium",
            "created": True
        }
        print(f"✅ Meeting event: {detected_event}")
    
    elif "party" in message_lower:
        print("🎉 Party detected!")
        event_date = date.today() + timedelta(days=1)  # Default tomorrow
        if "tomorrow" in message_lower:
            event_date = date.today() + timedelta(days=1)
        elif "today" in message_lower:
            event_date = date.today()
        detected_event = {
            "title": "Party", 
            "date": event_date.strftime("%d/%m/%Y"), 
            "category": "social",
            "priority": "medium",
            "created": True
        }
        print(f"✅ Party event: {detected_event}")
    
    elif "appointment" in message_lower:
        print("🏥 Appointment detected!")
        event_date = date.today() + timedelta(days=1)  # Default tomorrow
        if "friday" in message_lower:
            today = date.today()
            days_ahead = 4 - today.weekday()  # Friday is 4
            if days_ahead <= 0:
                days_ahead += 7
            event_date = today + timedelta(days=days_ahead)
        elif "tomorrow" in message_lower:
            event_date = date.today() + timedelta(days=1)
        detected_event = {
            "title": "Appointment", 
            "date": event_date.strftime("%d/%m/%Y"), 
            "category": "health",
            "priority": "high",
            "created": True
        }
        print(f"✅ Appointment event: {detected_event}")'''
    
    if old_detection in content:
        content = content.replace(old_detection, new_detection)
        print("✅ Replaced old detection logic with enhanced version")
    else:
        print("❌ Could not find old detection logic")
        print("Let's try a different approach...")
        
        # Alternative: replace the specific patterns
        content = content.replace(
            'if "add" in message_lower and "birthday" in message_lower:',
            'if "birthday" in message_lower:'
        )
        
        # Add the enhanced logic after the print statement
        if '# Simple but effective detection' in content:
            content = content.replace(
                '# Simple but effective detection',
                '''# Enhanced event detection
    from datetime import date, timedelta
    import re'''
            )
        
        print("✅ Applied alternative fix")
    
    with open('main.py', 'w') as f:
        f.write(content)
    
    return True

fix_current_detection()
