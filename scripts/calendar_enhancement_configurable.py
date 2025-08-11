#!/usr/bin/env python3
"""
Zoe Calendar Enhancement Script - Multi-Format Support
Configurable date formats: Australian, American, ISO, European
"""

import re
from datetime import datetime, date, timedelta
from typing import Optional, Tuple
import calendar

def parse_natural_date(text: str, reference_date: date = None, date_format: str = "AU") -> Optional[date]:
    """
    Parse natural language dates into proper date objects
    Supports multiple date formats based on user preference
    
    Args:
        text: Natural language date text
        reference_date: Base date for relative calculations
        date_format: "AU" (DD/MM/YYYY), "US" (MM/DD/YYYY), "ISO" (YYYY-MM-DD)
    
    Examples: "24/03/2025", "March 24th", "tomorrow", "next Friday"
    """
    if reference_date is None:
        reference_date = date.today()
    
    text = text.lower().strip()
    
    # Today/Tomorrow/Yesterday
    if "today" in text:
        return reference_date
    elif "tomorrow" in text:
        return reference_date + timedelta(days=1)
    elif "yesterday" in text:
        return reference_date - timedelta(days=1)
    
    # Next/This + day of week
    weekdays = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6,
        'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6
    }
    
    for day_name, day_num in weekdays.items():
        if day_name in text:
            days_ahead = day_num - reference_date.weekday()
            if "next" in text:
                if days_ahead <= 0:  # Target day already passed this week
                    days_ahead += 7
            elif days_ahead <= 0:  # "this friday" but friday passed = next friday
                days_ahead += 7
            return reference_date + timedelta(days_ahead)
    
    # Month names
    months = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 
        'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    
    # "March 24th" or "24th March"
    for month_name, month_num in months.items():
        if month_name in text:
            day_match = re.search(r'(\d{1,2})(?:st|nd|rd|th)?', text)
            if day_match:
                day = int(day_match.group(1))
                year = reference_date.year
                
                try:
                    try_date = date(year, month_num, day)
                    if try_date < reference_date:
                        try_date = date(year + 1, month_num, day)
                    return try_date
                except ValueError:
                    pass
    
    # Numeric date formats based on user preference
    slash_match = re.search(r'(\d{1,2})/(\d{1,2})(?:/(\d{4}))?', text)
    if slash_match:
        num1, num2 = int(slash_match.group(1)), int(slash_match.group(2))
        year = int(slash_match.group(3)) if slash_match.group(3) else reference_date.year
        
        if date_format == "AU":  # Australian: DD/MM/YYYY
            day, month = num1, num2
        elif date_format == "US":  # American: MM/DD/YYYY
            month, day = num1, num2
        else:  # Default to AU format
            day, month = num1, num2
            
        if 1 <= day <= 31 and 1 <= month <= 12:
            try:
                return date(year, month, day)
            except ValueError:
                pass
    
    # DD.MM.YYYY format (European/Australian style)
    if date_format in ["AU", "EU"]:
        dot_match = re.search(r'(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?', text)
        if dot_match:
            day, month = int(dot_match.group(1)), int(dot_match.group(2))
            year = int(dot_match.group(3)) if dot_match.group(3) else reference_date.year
            if 1 <= day <= 31 and 1 <= month <= 12:
                try:
                    return date(year, month, day)
                except ValueError:
                    pass
    
    # ISO format dates (YYYY-MM-DD)
    iso_match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', text)
    if iso_match:
        year, month, day = map(int, iso_match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            pass
    
    return None

def extract_event_from_text(text: str, date_format: str = "AU") -> Optional[Tuple[str, date, Optional[str]]]:
    """Extract event details from natural language text"""
    text = text.strip()
    
    patterns = [
        r'(?:add|create|schedule|plan|book)\s+(.+?)\s+(?:on|for)\s+(.+?)(?:\s|$)',
        r'(?:my|the)\s+(.+?)\s+(?:is|on)\s+(.+?)(?:\s|$)',
        r'(.+?)\s+(?:on|is on)\s+(.+?)(?:\s|$)',
        r'remind me (?:about|of)\s+(.+?)\s+(?:on|for)\s+(.+?)(?:\s|$)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            date_text = match.group(2).strip()
            
            title = re.sub(r'^(my|the|a|an)\s+', '', title, flags=re.IGNORECASE)
            title = title.replace("'s", "").strip()
            
            event_date = parse_natural_date(date_text, date_format=date_format)
            if event_date:
                return (title.title(), event_date, None)
    
    return None

def format_date_display(event_date: date, format_preference: str = "AU") -> str:
    """Format date for display based on user preference"""
    if format_preference == "AU":
        return event_date.strftime("%d/%m/%Y")
    elif format_preference == "US":
        return event_date.strftime("%m/%d/%Y")
    elif format_preference == "ISO":
        return event_date.strftime("%Y-%m-%d")
    else:
        return event_date.strftime("%d/%m/%Y")

if __name__ == "__main__":
    test_cases = [
        "add my birthday on March 24th",
        "doctor appointment tomorrow",
        "meeting next Friday", 
        "Christmas is on December 25",
        "vacation on 15/07/2025",
        "dentist on 15.03.2025",
        "bbq on 25/12",
        "Melbourne Cup on 5th November",
        "Australia Day on 26th January",
        "ANZAC Day on 25/04",
        "book dentist for 3/4/2025"
    ]
    
    print("🌏 Testing Multi-Format Date Parser")
    print("=" * 50)
    
    formats = ["AU", "US", "ISO"]
    
    for fmt in formats:
        print(f"\n📅 Testing {fmt} format:")
        print("-" * 25)
        
        for test in test_cases:
            result = extract_event_from_text(test, date_format=fmt)
            if result:
                title, event_date, desc = result
                display_date = format_date_display(event_date, fmt)
                print(f"✅ '{test}' → {title} on {display_date}")
            else:
                print(f"❌ '{test}' → Could not parse")
    
    print("\n🚀 Ready to integrate into Zoe!")
