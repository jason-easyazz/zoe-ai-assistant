#!/usr/bin/env python3
import re

def fix_chat_endpoint():
    """Fix the chat endpoint to return event_created"""
    
    with open('main.py', 'r') as f:
        content = f.read()
    
    # Add enhanced calendar import at the top if not present
    if 'from enhanced_calendar import' not in content:
        # Find the imports section and add our import
        import_pos = content.find('from datetime import')
        if import_pos != -1:
            # Insert after datetime import
            insert_pos = content.find('\n', import_pos) + 1
            enhanced_import = '''
# Enhanced Calendar System
try:
    from enhanced_calendar import (
        EnhancedCalendarSystem, 
        UserFormatPreferences, 
        DateFormat, 
        TimeFormat
    )
    ENHANCED_CALENDAR_AVAILABLE = True
    print("✅ Enhanced calendar system imported")
except ImportError as e:
    print(f"⚠️ Enhanced calendar system not available: {e}")
    ENHANCED_CALENDAR_AVAILABLE = False

'''
            content = content[:insert_pos] + enhanced_import + content[insert_pos:]
            print("✅ Added enhanced calendar imports")
    
    # Find the chat endpoint and modify its return
    chat_pattern = r'(@app\.post\("/api/chat"\).*?async def chat_message.*?)(return \{.*?"response": ai_response.*?\})'
    
    def replace_chat_return(match):
        function_start = match.group(1)
        
        # New return logic with event detection
        new_return = '''
        # Check for events in the response and add event_created field
        event_created = None
        try:
            # Simple event detection for now
            message_text = user_message.lower()
            ai_text = ai_response.lower()
            combined_text = f"{message_text} {ai_text}"
            
            # Look for event keywords and dates
            if any(keyword in combined_text for keyword in ['birthday', 'meeting', 'appointment', 'party', 'dinner', 'lunch']):
                # Extract basic event info
                title = "Event"
                date_str = "TBD"
                
                if 'birthday' in combined_text:
                    title = "Birthday"
                    # Look for date patterns
                    import re
                    date_match = re.search(r'(march|april|may|june|july|august|september|october|november|december)\\s+(\\d{1,2})', combined_text)
                    if date_match:
                        month, day = date_match.groups()
                        date_str = f"{day.zfill(2)}/{['january','february','march','april','may','june','july','august','september','october','november','december'].index(month)+1:02d}/2025"
                
                elif 'meeting' in combined_text:
                    title = "Meeting"
                    if 'tomorrow' in combined_text:
                        from datetime import date, timedelta
                        tomorrow = date.today() + timedelta(days=1)
                        date_str = tomorrow.strftime("%d/%m/%Y")
                
                elif 'appointment' in combined_text:
                    title = "Appointment"
                    if 'friday' in combined_text:
                        from datetime import date, timedelta
                        today = date.today()
                        days_ahead = 4 - today.weekday()  # Friday is 4
                        if days_ahead <= 0:
                            days_ahead += 7
                        friday = today + timedelta(days=days_ahead)
                        date_str = friday.strftime("%d/%m/%Y")
                
                # Look for time
                time_str = None
                time_match = re.search(r'(\\d{1,2}):(\\d{2})\\s*(pm|am)', combined_text)
                if not time_match:
                    time_match = re.search(r'(\\d{1,2})\\s*(pm|am)', combined_text)
                if time_match:
                    hour = time_match.group(1)
                    minute = time_match.group(2) if len(time_match.groups()) > 2 else "00"
                    ampm = time_match.groups()[-1]
                    time_str = f"{hour}:{minute} {ampm.upper()}"
                
                event_created = {
                    "title": title,
                    "date": date_str,
                    "time": time_str,
                    "category": "general",
                    "priority": "medium",
                    "notifications": [],
                    "tasks": []
                }
        except Exception as e:
            print(f"Event detection error: {e}")
        
        response_data = {
            "response": ai_response,
            "conversation_id": conversation_id,
            "timestamp": datetime.now().isoformat()
        }
        
        if event_created:
            response_data["event_created"] = event_created
        
        return response_data'''
        
        return function_start + new_return
    
    # Apply the replacement
    new_content = re.sub(chat_pattern, replace_chat_return, content, flags=re.DOTALL)
    
    if new_content != content:
        with open('main.py', 'w') as f:
            f.write(new_content)
        print("✅ Chat endpoint updated successfully")
        return True
    else:
        print("❌ Could not find chat endpoint pattern")
        return False

if __name__ == "__main__":
    fix_chat_endpoint()
