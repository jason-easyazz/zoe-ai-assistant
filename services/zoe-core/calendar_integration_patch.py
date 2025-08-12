#!/usr/bin/env python3
import re

def patch_main_py():
    """Patch main.py to integrate enhanced calendar system"""
    
    with open('main.py', 'r') as f:
        content = f.read()
    
    # Check if enhanced_calendar import already exists
    if 'from enhanced_calendar import' in content:
        print("✅ Enhanced calendar already imported")
        return
    
    # Add import after other imports
    import_pattern = r'(from datetime import.*?\n)'
    import_replacement = r'\1\n# Enhanced Calendar System\ntry:\n    from enhanced_calendar import (\n        EnhancedCalendarSystem, \n        UserFormatPreferences, \n        DateFormat, \n        TimeFormat,\n        CalendarEvent,\n        EventNotification,\n        PreparationTask,\n        ENHANCED_CALENDAR_SCHEMA\n    )\n    ENHANCED_CALENDAR_AVAILABLE = True\n    logger.info("✅ Enhanced calendar system imported")\nexcept ImportError as e:\n    logger.warning(f"⚠️ Enhanced calendar system not available: {e}")\n    ENHANCED_CALENDAR_AVAILABLE = False\n\n'
    
    if re.search(import_pattern, content):
        content = re.sub(import_pattern, import_replacement, content)
        print("✅ Added enhanced calendar imports")
    else:
        # Fallback: add after existing imports
        content = content.replace(
            'import logging',
            'import logging\n\n# Enhanced Calendar System\ntry:\n    from enhanced_calendar import (\n        EnhancedCalendarSystem, \n        UserFormatPreferences, \n        DateFormat, \n        TimeFormat,\n        CalendarEvent,\n        EventNotification,\n        PreparationTask,\n        ENHANCED_CALENDAR_SCHEMA\n    )\n    ENHANCED_CALENDAR_AVAILABLE = True\nexcept ImportError as e:\n    logger.warning(f"Enhanced calendar system not available: {e}")\n    ENHANCED_CALENDAR_AVAILABLE = False'
        )
    
    # Update extract_entities_advanced function
    old_extract = r'async def extract_entities_advanced\(text: str\) -> Dict:.*?return entities'
    
    new_extract = '''async def extract_entities_advanced(text: str) -> Dict:
    """Enhanced entity extraction using the new calendar system"""
    entities = {"tasks": [], "events": []}
    
    if ENHANCED_CALENDAR_AVAILABLE:
        try:
            # Initialize enhanced calendar system
            preferences = UserFormatPreferences()
            calendar_system = EnhancedCalendarSystem(preferences)
            
            # Use enhanced calendar system for event detection
            detected_events = await calendar_system.extract_events_advanced(text)
            
            for event in detected_events:
                # Convert to the format expected by the rest of the system
                event_dict = {
                    "title": event.title,
                    "date": event.date,
                    "time": event.time,
                    "confidence": 0.9,
                    "category": event.category,
                    "priority": event.priority,
                    "location": event.location,
                    "description": event.description,
                    "notifications": [
                        {
                            "type": n.type,
                            "days_before": n.days_before,
                            "message": n.message
                        }
                        for n in event.notifications
                    ],
                    "tasks": [
                        {
                            "title": t.title,
                            "due_date": t.due_date,
                            "type": t.type,
                            "priority": t.priority,
                            "description": t.description
                        }
                        for t in event.tasks
                    ]
                }
                entities["events"].append(event_dict)
                
                # Add related tasks to the tasks list
                for task in event.tasks:
                    entities["tasks"].append({
                        "title": task.title,
                        "due_date": task.due_date,
                        "description": task.description,
                        "priority": task.priority,
                        "confidence": 0.8,
                        "source": "event_preparation"
                    })
        
        except Exception as e:
            logger.error(f"Enhanced calendar extraction failed: {e}")
            # Fallback to simple detection
            pass
    
    # Enhanced task patterns (keeping existing functionality)
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
    
    # Simple event patterns as fallback
    if not entities["events"]:
        event_patterns = [
            r"(?:meeting|appointment|call|dinner|lunch|event) (?:at|on|with) (.+?) (?:on|at) (.+?)(?:\.|$)",
            r"(?:going to|visiting|traveling to) (.+?) (?:on|at|this|next) (.+?)(?:\.|$)",
            r"(?:birthday|anniversary|celebration) (?:is|on) (.+?)(?:\.|$)"
        ]
        
        for pattern in event_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                event_title = match.group(1).strip() if match.groups() else match.group(0).strip()
                event_title = re.sub(r'^(on|at)\s+', '', event_title, flags=re.IGNORECASE)
                entities["events"].append({
                    "title": event_title,
                    "confidence": 0.7,
                    "date": datetime.now().date() + timedelta(days=1)
                })
    
    return entities'''
    
    # Replace the function
    content = re.sub(old_extract, new_extract, content, flags=re.DOTALL)
    
    # Update chat endpoint to return event_created field
    chat_pattern = r'(return \{[\s\S]*?"response": ai_response,[\s\S]*?"timestamp": datetime\.now\(\)\.isoformat\(\)[\s\S]*?\})'
    
    chat_replacement = '''# Check if events were created
        event_created = None
        if entities.get("events"):
            # Format the first event for response
            event = entities["events"][0]
            if ENHANCED_CALENDAR_AVAILABLE:
                preferences = UserFormatPreferences()
                calendar_sys = EnhancedCalendarSystem(preferences)
                event_created = {
                    "title": event["title"],
                    "date": calendar_sys.format_date(event["date"]) if hasattr(event["date"], 'strftime') else str(event["date"]),
                    "time": calendar_sys.format_time(event["time"]) if event.get("time") else None,
                    "category": event.get("category", "general"),
                    "priority": event.get("priority", "medium"),
                    "notifications": event.get("notifications", []),
                    "tasks": event.get("tasks", [])
                }
            else:
                event_created = {
                    "title": event["title"],
                    "date": str(event["date"]),
                    "time": str(event["time"]) if event.get("time") else None
                }
        
        response_data = {
            "response": ai_response,
            "conversation_id": conversation_id,
            "timestamp": datetime.now().isoformat()
        }
        
        if event_created:
            response_data["event_created"] = event_created
        
        return response_data'''
    
    content = re.sub(chat_pattern, chat_replacement, content)
    
    # Write updated content
    with open('main.py', 'w') as f:
        f.write(content)
    
    print("✅ main.py patched successfully")

if __name__ == "__main__":
    patch_main_py()
