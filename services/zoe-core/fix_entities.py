#!/usr/bin/env python3

def fix_extract_entities():
    """Fix the extract_entities_advanced function"""
    
    with open('main.py', 'r') as f:
        content = f.read()
    
    # Find and replace the extract_entities_advanced function
    # First, let's see if we can find it
    if 'async def extract_entities_advanced(' in content:
        print("✅ Found extract_entities_advanced function")
        
        # Replace the entire function with a working version
        start_pattern = r'async def extract_entities_advanced\(text: str\) -> Dict:'
        
        # Find the start of the function
        import re
        match = re.search(start_pattern, content)
        if match:
            start_pos = match.start()
            
            # Find the end of the function (next function or class definition)
            end_patterns = [
                r'\nasync def ',
                r'\ndef ',
                r'\nclass ',
                r'\n@app\.'
            ]
            
            end_pos = len(content)
            for pattern in end_patterns:
                next_match = re.search(pattern, content[start_pos + 100:])  # Skip a bit to avoid matching itself
                if next_match:
                    potential_end = start_pos + 100 + next_match.start()
                    if potential_end < end_pos:
                        end_pos = potential_end
            
            # New working function
            new_function = '''async def extract_entities_advanced(text: str) -> Dict:
    """Enhanced entity extraction with working event detection"""
    entities = {"tasks": [], "events": []}
    
    print(f"🔍 extract_entities_advanced called with: {text}")
    
    # Enhanced event patterns that actually work
    text_lower = text.lower()
    
    # Birthday detection
    if 'birthday' in text_lower:
        print("🎂 Birthday detected!")
        # Look for dates
        import re
        from datetime import date, timedelta
        
        # Look for month and day
        month_match = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)\\s+(\\d{1,2})', text_lower)
        if month_match:
            month_name, day = month_match.groups()
            months = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']
            month_num = months.index(month_name) + 1
            try:
                event_date = date(2025, month_num, int(day))
                entities["events"].append({
                    "title": "Birthday",
                    "date": event_date,
                    "confidence": 0.9,
                    "category": "personal",
                    "priority": "high"
                })
                print(f"✅ Birthday event created: {event_date}")
            except ValueError:
                pass
        else:
            # Default to tomorrow if no specific date
            entities["events"].append({
                "title": "Birthday",
                "date": date.today() + timedelta(days=1),
                "confidence": 0.7,
                "category": "personal",
                "priority": "high"
            })
    
    # Meeting detection
    elif 'meeting' in text_lower:
        print("📅 Meeting detected!")
        event_date = date.today() + timedelta(days=1)  # Default to tomorrow
        if 'tomorrow' in text_lower:
            event_date = date.today() + timedelta(days=1)
        elif 'today' in text_lower:
            event_date = date.today()
        
        entities["events"].append({
            "title": "Meeting",
            "date": event_date,
            "confidence": 0.8,
            "category": "work",
            "priority": "medium"
        })
        print(f"✅ Meeting event created: {event_date}")
    
    # Party detection
    elif 'party' in text_lower:
        print("🎉 Party detected!")
        event_date = date.today() + timedelta(days=1)  # Default to tomorrow
        if 'tomorrow' in text_lower:
            event_date = date.today() + timedelta(days=1)
        elif 'today' in text_lower:
            event_date = date.today()
        
        entities["events"].append({
            "title": "Party",
            "date": event_date,
            "confidence": 0.8,
            "category": "social",
            "priority": "medium"
        })
        print(f"✅ Party event created: {event_date}")
    
    # Appointment detection
    elif 'appointment' in text_lower:
        print("🏥 Appointment detected!")
        event_date = date.today() + timedelta(days=1)  # Default to tomorrow
        if 'friday' in text_lower:
            # Calculate next Friday
            today = date.today()
            days_ahead = 4 - today.weekday()  # Friday is 4
            if days_ahead <= 0:
                days_ahead += 7
            event_date = today + timedelta(days=days_ahead)
        
        entities["events"].append({
            "title": "Appointment",
            "date": event_date,
            "confidence": 0.8,
            "category": "health",
            "priority": "high"
        })
        print(f"✅ Appointment event created: {event_date}")
    
    # Task detection (existing logic)
    task_patterns = [
        r"(?:need to|have to|should|must|remember to|don't forget to) (.+?)(?:\\.|$|,)",
        r"(?:task|todo|action item): (.+?)(?:\\.|$)",
        r"(?:buy|get|pick up|call|email|text|contact|schedule|book) (.+?)(?:\\.|$|tomorrow|today|this week)"
    ]
    
    for pattern in task_patterns:
        import re
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            task_text = match.group(1).strip()
            if 3 < len(task_text) < 100:
                entities["tasks"].append({
                    "title": task_text,
                    "confidence": 0.8,
                    "description": f"Detected from conversation"
                })
    
    print(f"🔍 Final entities: {entities}")
    return entities'''
            
            # Replace the function
            new_content = content[:start_pos] + new_function + content[end_pos:]
            
            with open('main.py', 'w') as f:
                f.write(new_content)
            
            print("✅ Successfully replaced extract_entities_advanced function")
            return True
    
    print("❌ Could not find extract_entities_advanced function")
    return False

fix_extract_entities()
