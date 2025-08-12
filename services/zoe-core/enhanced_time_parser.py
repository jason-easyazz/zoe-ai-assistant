import re
from datetime import datetime, timedelta, time
from typing import Optional

def parse_time_from_text(text: str) -> Optional[time]:
    """Extract time from natural language text"""
    text = text.lower().strip()
    
    # Time patterns
    time_patterns = [
        # 2pm, 3:30pm, 14:00
        r'(\d{1,2}):?(\d{0,2})\s*(pm|am)',
        # 24 hour format  
        r'(\d{1,2}):(\d{2})',
        # Simple hour format "at 2", "around 5"
        r'(?:at|around)\s+(\d{1,2})\s*(?:pm|am)?',
    ]
    
    for pattern in time_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                groups = match.groups()
                hour = int(groups[0])
                minute = int(groups[1]) if groups[1] else 0
                
                # Handle AM/PM
                if len(groups) > 2 and groups[2]:
                    if groups[2] == 'pm' and hour != 12:
                        hour += 12
                    elif groups[2] == 'am' and hour == 12:
                        hour = 0
                
                # Validate time
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return time(hour, minute)
            except (ValueError, IndexError):
                continue
    
    return None

def parse_date_from_text(text: str) -> Optional[str]:
    """Extract date from natural language text"""
    text = text.lower().strip()
    today = datetime.now().date()
    
    # Relative date patterns
    if 'tomorrow' in text:
        return (today + timedelta(days=1)).strftime('%Y-%m-%d')
    elif 'today' in text:
        return today.strftime('%Y-%m-%d')
    elif 'friday' in text:
        days_ahead = 4 - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        return (today + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
    
    return None
