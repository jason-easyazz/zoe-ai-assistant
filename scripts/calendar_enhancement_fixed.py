#!/usr/bin/env python3
"""
Zoe Calendar Enhancement Script - Fixed Patterns
Better natural language parsing for Australian dates
"""

import re
from datetime import datetime, date, timedelta
from typing import Optional, Tuple

def parse_natural_date(text: str, reference_date: date = None, date_format: str = "AU") -> Optional[date]:
    """Parse natural language dates with improved pattern matching"""
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
                if days_ahead <= 0:
                    days_ahead += 7
            elif days_ahead <= 0:
                days_ahead += 7
            return reference_date + timedelta(days_ahead)
    
    # Month names with improved matching
    months = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 
        'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    
    # Look for month + day patterns
    for month_name, month_num in months.items():
        if month_name in text:
            # Find day number anywhere in the text
            day_match = re.search(r'(\d{1,2})(?:st|nd|rd|th)?', text)
            if day_match:
                day = int(day_match.group(1))
                year = reference_date.year
                
                try:
                    try_date = date(year, month_num, day)
                    # If date already passed this year, assume next year
                    if try_date < reference_date:
                        try_date = date(year + 1, month_num, day)
                    return try_date
                except ValueError:
                    pass
            else:
                # No day specified, assume 1st of the month
                year = reference_date.year
                try:
                    try_date = date(year, month_num, 1)
                    if try_date < reference_date:
                        try_date = date(year + 1, month_num, 1)
                    return try_date
                except ValueError:
                    pass
    
    # Numeric date formats
    slash_match = re.search(r'(\d{1,2})/(\d{1,2})(?:/(\d{4}))?', text)
    if slash_match:
        num1, num2 = int(slash_match.group(1)), int(slash_match.group(2))
        year = int(slash_match.group(3)) if slash_match.group(3) else reference_date.year
        
        if date_format == "AU":
            day, month = num1, num2
        elif date_format == "US":
            month, day = num1, num2
        else:
            day, month = num1, num2
            
        if 1 <= day <= 31 and 1 <= month <= 12:
            try:
                return date(year, month, day)
            except ValueError:
                pass
    
    # DD.MM.YYYY format
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
    
    # ISO format
    iso_match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', text)
    if iso_match:
        year, month, day = map(int, iso_match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            pass
    
    return None

def extract_event_from_text(text: str, date_format: str = "AU") -> Optional[Tuple[str, date, Optional[str]]]:
    """Extract event details with improved patterns"""
    text = text.strip()
    
    # Improved patterns that are more flexible
    patterns = [
        # "add my birthday on March 24th"
        r'(?:add|create|schedule|plan|book)\s+(.+?)\s+(?:on|for)\s+(.+)',
        # "my birthday is on March 24th"  
        r'(?:my|the)\s+(.+?)\s+(?:is|on)\s+(.+)',
        # "birthday on March 24th"
        r'(.+?)\s+(?:on|is on)\s+(.+)',
        # "remind me about birthday on March 24th"
        r'remind me (?:about|of)\s+(.+?)\s+(?:on|for)\s+(.+)',
        # "doctor appointment tomorrow"
        r'(.+?)\s+(tomorrow|today|yesterday|next \w+|this \w+)',
        # "Christmas is December 25"
        r'(.+?)\s+(?:is)\s+(.+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            date_text = match.group(2).strip()
            
            # Clean up title
            title = re.sub(r'^(my|the|a|an)\s+', '', title, flags=re.IGNORECASE)
            title = title.replace("'s", "").strip()
            
            # Parse the date
            event_date = parse_natural_date(date_text, date_format=date_format)
            if event_date:
                return (title.title(), event_date, None)
    
    return None

def format_date_display(event_date: date, format_preference: str = "AU") -> str:
    """Format date for display"""
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
        "Christmas is December 25",
        "vacation on 15/07/2025",
        "dentist on 15.03.2025",
        "bbq on 25/12",
        "Melbourne Cup on 5th November",
        "Australia Day on 26th January",
        "ANZAC Day on 25/04",
        "book dentist for 3/4/2025"
    ]
    
    print("🔧 Testing Fixed Date Parser")
    print("=" * 40)
    
    # Test AU format primarily
    print("\n📅 Testing AU format:")
    print("-" * 25)
    
    for test in test_cases:
        result = extract_event_from_text(test, date_format="AU")
        if result:
            title, event_date, desc = result
            display_date = format_date_display(event_date, "AU")
            print(f"✅ '{test}' → {title} on {display_date}")
        else:
            print(f"❌ '{test}' → Could not parse")
    
    print(f"\n🎯 Key test - ambiguous dates:")
    print("-" * 30)
    ambiguous_tests = ["book dentist for 3/4/2025", "meeting on 15/7"]
    
    for fmt in ["AU", "US"]:
        print(f"\n{fmt} format:")
        for test in ambiguous_tests:
            result = extract_event_from_text(test, date_format=fmt)
            if result:
                title, event_date, desc = result
                display_date = format_date_display(event_date, fmt)
                print(f"  {test} → {display_date}")
