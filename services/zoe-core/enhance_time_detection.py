import re

# Read main.py
with open('main.py', 'r') as f:
    content = f.read()

# Add our enhanced time parsing function near the top (after imports)
time_function = '''
def extract_time_from_text(text):
    """Extract time from text and return in HH:MM format"""
    text = text.lower()
    
    # Pattern for time like "2pm", "3:30pm", "14:00"
    time_patterns = [
        r'(\d{1,2}):?(\d{0,2})\s*(pm|am)',
        r'(\d{1,2}):(\d{2})(?!\s*(?:pm|am))',  # 24-hour format
    ]
    
    for pattern in time_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                hour = int(match.group(1))
                minute = int(match.group(2)) if match.group(2) else 0
                period = match.group(3) if len(match.groups()) > 2 else None
                
                if period:
                    if period == 'pm' and hour != 12:
                        hour += 12
                    elif period == 'am' and hour == 12:
                        hour = 0
                
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return f"{hour:02d}:{minute:02d}"
            except:
                continue
    
    return None

'''

# Insert the function after the imports section
import_end = content.find('app = FastAPI()')
if import_end > 0:
    content = content[:import_end] + time_function + '\n' + content[import_end:]
    print("✅ Added enhanced time extraction function")

# Now update the time extraction in appointment detection to use our function
old_time_code = '''if "at" in message_lower:
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
                            time_str = f"{hour:02d}:{minute:02d}"'''

new_time_code = '''time_str = extract_time_from_text(message_lower)'''

content = content.replace(old_time_code, new_time_code)

# Write back
with open('main.py', 'w') as f:
    f.write(content)

print("✅ Enhanced time detection logic")
