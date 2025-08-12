import re

# Read main.py
with open('main.py', 'r') as f:
    content = f.read()

# Find the old extract_entities_advanced function
old_function_pattern = r'async def extract_entities_advanced\(text: str\) -> Dict:.*?return entities'
old_function = re.search(old_function_pattern, content, re.DOTALL)

if old_function:
    # Replace with enhanced version
    new_function = '''async def extract_entities_advanced(text: str) -> Dict:
    """Advanced entity extraction for tasks and events with time parsing"""
    entities = {"tasks": [], "events": []}
    
    # Enhanced task patterns
    task_patterns = [
        r"(?:need to|have to|should|must|remember to|don't forget to) (.+?)(?:\.|$|,)",
        r"(?:task|todo|action item): (.+?)(?:\.|$)",
        r"(?:buy|get|pick up|call|email|text|contact|schedule|book) (.+?)(?:\.|$|tomorrow|today|this week)",
        r"I (?:will|gonna|plan to|want to) (.+?)(?:\.|$|tomorrow|today|this week)"
    ]
    
    for pattern in task_patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            task_text = match.group(1).strip()
            if 3 < len(task_text) < 100 and not any(skip in task_text.lower() for skip in ["i think", "maybe", "perhaps"]):
                entities["tasks"].append({
                    "title": task_text,
                    "confidence": 0.8,
                    "description": f"Detected from conversation"
                })
    
    # Enhanced event patterns with time extraction
    event_patterns = [
        r"(meeting|appointment|call|dinner|lunch|event) (?:at|on|with) (.+?) (?:on|at) (.+?)(?:\.|$)",
        r"my (.+?) (?:is|at) (.+?)(?:\.|$)",
        r"I have (?:a |an )?(.+?) (?:tomorrow|today|friday|monday|tuesday|wednesday|thursday|saturday|sunday) (?:at )?(.+?)(?:\.|$)",
        r"(.+?) (?:tomorrow|today|friday|monday|tuesday|wednesday|thursday|saturday|sunday) at (.+?)(?:\.|$)"
    ]
    
    for pattern in event_patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            # Extract event title (first meaningful group)
            event_title = match.group(1).strip()
            # Remove leading connecting words
            event_title = re.sub(r'^(on|at|my|going to|visiting|I have a|I have an|I have)\s+', '', event_title, flags=re.IGNORECASE)
            
            # Parse time and date from the full match
            full_match = match.group(0)
            parsed_time = parse_time_from_text(full_match)
            parsed_date = parse_date_from_text(full_match)
            
            entities["events"].append({
                "title": event_title,
                "confidence": 0.8,
                "date": parsed_date or (datetime.now().date() + timedelta(days=1)).strftime('%Y-%m-%d'),
                "time": parsed_time.strftime('%H:%M') if parsed_time else None,
                "raw_text": full_match
            })
    
    return entities'''
    
    content = content.replace(old_function.group(0), new_function)
    
    # Write back
    with open('main.py', 'w') as f:
        f.write(content)
    
    print("✅ Updated extract_entities_advanced function with time parsing")
else:
    print("❌ Could not find extract_entities_advanced function")
