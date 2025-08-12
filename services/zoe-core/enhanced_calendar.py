# Enhanced Calendar System for Zoe v3.1
import re
import json
import logging
from datetime import datetime, date, timedelta, time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import calendar
from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

class DateFormat(Enum):
    DD_MM_YYYY = "DD/MM/YYYY"
    MM_DD_YYYY = "MM/DD/YYYY"
    DD_MM_YYYY_DOT = "DD.MM.YYYY"
    YYYY_MM_DD = "YYYY-MM-DD"

class TimeFormat(Enum):
    TWELVE_HOUR = "12h"
    TWENTY_FOUR_HOUR = "24h"

@dataclass
class UserFormatPreferences:
    date_format: DateFormat = DateFormat.DD_MM_YYYY
    time_format: TimeFormat = TimeFormat.TWELVE_HOUR
    timezone: str = "UTC"
    default_reminder_days: int = 1
    default_event_duration: int = 60

@dataclass
class EventNotification:
    type: str
    days_before: int
    message: str
    created_at: datetime = None

@dataclass
class PreparationTask:
    title: str
    due_date: date
    type: str
    priority: str = "medium"
    description: str = ""

@dataclass
class CalendarEvent:
    title: str
    date: date
    time: Optional[time] = None
    duration: int = 60
    location: str = ""
    description: str = ""
    notifications: List[EventNotification] = None
    tasks: List[PreparationTask] = None
    priority: str = "medium"
    category: str = "general"
    
    def __post_init__(self):
        if self.notifications is None:
            self.notifications = []
        if self.tasks is None:
            self.tasks = []

class EnhancedCalendarSystem:
    def __init__(self, user_preferences: UserFormatPreferences = None):
        self.preferences = user_preferences or UserFormatPreferences()
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._init_patterns()
    
    def _init_patterns(self):
        self.time_patterns = [
            r'(\d{1,2}):(\d{2})\s*(am|pm|AM|PM)',
            r'(\d{1,2})\s*(am|pm|AM|PM)',
            r'(\d{1,2}):(\d{2})',
            r'at\s+(\d{1,2}):(\d{2})\s*(am|pm|AM|PM)?',
            r'at\s+(\d{1,2})\s*(am|pm|AM|PM)',
        ]
        
        self.date_patterns = [
            r'\b(today|tomorrow|yesterday)\b',
            r'\b(next|this)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
            r'\b(next|this)\s+(week|month|year)\b',
            r'\bin\s+(\d+)\s+(days?|weeks?|months?)\b',
            r'\b(\d{1,2})[\/\.\-](\d{1,2})[\/\.\-](\d{2,4})\b',
            r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?\b',
            r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)\b',
            r'\bon\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?\b',
        ]
        
        self.reminder_patterns = [
            r'remind\s+me\s+(.*?)(?:\s+before|\s+ahead)',
            r'(day|days|week|weeks|month|months)\s+before',
            r'(\d+)\s+(day|days|week|weeks|month|months)\s+before',
            r'remind\s+me\s+(\d+)\s+(day|days|week|weeks|month|months)\s+before',
            r'during\s+the\s+(week|month)',
            r'the\s+(day|week|month)\s+before',
        ]
        
        self.task_patterns = [
            r'(?:need to|have to|should|must)\s+(.+?)\s+(?:before|for|by)',
            r'(?:buy|get|pick up|prepare|book|schedule|arrange)\s+(.+?)(?:\s+(?:before|for|by|ahead))',
            r'(?:preparation|prepare):\s*(.+?)(?:\.|$)',
            r'(?:todo|task):\s*(.+?)(?:\.|$)',
        ]
        
        self.event_patterns = [
            r'(?:meeting|appointment|call|conference)\s+(?:with\s+(.+?)\s+)?(?:at|on)\s+(.+?)(?:\s+at\s+(.+?))?(?:\.|$)',
            r'(?:doctor|dentist|medical)\s+(?:appointment|visit)\s+(?:at|on)\s+(.+?)(?:\s+at\s+(.+?))?(?:\.|$)',
            r'(?:dinner|lunch|breakfast)\s+(?:with\s+(.+?)\s+)?(?:at|on)\s+(.+?)(?:\s+at\s+(.+?))?(?:\.|$)',
            r'(?:party|celebration|gathering)\s+(?:at|on)\s+(.+?)(?:\s+at\s+(.+?))?(?:\.|$)',
            r'(?:flight|trip|travel|vacation)\s+(?:to\s+(.+?)\s+)?(?:on|at)\s+(.+?)(?:\s+at\s+(.+?))?(?:\.|$)',
            r'(?:birthday|anniversary)\s+(?:of\s+(.+?)\s+)?(?:on|at)\s+(.+?)(?:\.|$)',
            r'(?:event|activity)\s+(.+?)\s+(?:on|at)\s+(.+?)(?:\s+at\s+(.+?))?(?:\.|$)',
        ]

    async def extract_events_advanced(self, text: str) -> List[CalendarEvent]:
        events = []
        text = text.lower().strip()
        
        self.logger.debug(f"🔍 Analyzing text for events: {text}")
        
        for pattern in self.event_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    event = await self._parse_event_match(match, text)
                    if event:
                        events.append(event)
                        self.logger.debug(f"✅ Extracted event: {event.title}")
                except Exception as e:
                    self.logger.error(f"Error parsing event match: {e}")
        
        if not events:
            simple_event = await self._simple_event_detection(text)
            if simple_event:
                events.append(simple_event)
        
        return events

    async def _parse_event_match(self, match: re.Match, full_text: str) -> Optional[CalendarEvent]:
        groups = match.groups()
        
        event_title = groups[0] if groups[0] else "Event"
        date_str = groups[1] if len(groups) > 1 and groups[1] else None
        time_str = groups[2] if len(groups) > 2 and groups[2] else None
        
        event_date = await self._parse_date(date_str) if date_str else date.today() + timedelta(days=1)
        event_time = await self._parse_time(time_str) if time_str else None
        
        notifications = await self._extract_notifications(full_text)
        tasks = await self._extract_related_tasks(full_text, event_date)
        
        category = self._determine_category(event_title)
        priority = self._determine_priority(full_text, category)
        
        event = CalendarEvent(
            title=event_title.strip(),
            date=event_date,
            time=event_time,
            notifications=notifications,
            tasks=tasks,
            category=category,
            priority=priority,
            description=f"Detected from conversation"
        )
        
        return event

    async def _simple_event_detection(self, text: str) -> Optional[CalendarEvent]:
        birthday_patterns = [
            r'(?:my\s+)?birthday\s+(?:is\s+)?(?:on\s+)?(.+?)(?:\.|$)',
            r'birthday.*?(?:on|at)\s+(.+?)(?:\.|$)',
        ]
        
        for pattern in birthday_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                date_str = match.group(1).strip()
                event_date = await self._parse_date(date_str)
                
                return CalendarEvent(
                    title="Birthday",
                    date=event_date,
                    category="personal",
                    priority="high",
                    notifications=[
                        EventNotification(
                            type="reminder",
                            days_before=7,
                            message="Birthday coming up next week!"
                        )
                    ]
                )
        
        return None

    async def _parse_date(self, date_str: str) -> date:
        if not date_str:
            return date.today() + timedelta(days=1)
        
        date_str = date_str.strip().lower()
        
        if 'today' in date_str:
            return date.today()
        elif 'tomorrow' in date_str:
            return date.today() + timedelta(days=1)
        elif 'yesterday' in date_str:
            return date.today() - timedelta(days=1)
        
        weekday_match = re.search(r'(next|this)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)', date_str)
        if weekday_match:
            direction, weekday = weekday_match.groups()
            weekday_num = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'].index(weekday)
            
            today = date.today()
            days_ahead = weekday_num - today.weekday()
            
            if direction == 'next' or days_ahead <= 0:
                days_ahead += 7
            
            return today + timedelta(days=days_ahead)
        
        relative_match = re.search(r'in\s+(\d+)\s+(day|days|week|weeks|month|months)', date_str)
        if relative_match:
            num, unit = relative_match.groups()
            num = int(num)
            
            if 'day' in unit:
                return date.today() + timedelta(days=num)
            elif 'week' in unit:
                return date.today() + timedelta(weeks=num)
            elif 'month' in unit:
                return date.today() + relativedelta(months=num)
        
        date_match = re.search(r'(\d{1,2})[\/\.\-](\d{1,2})[\/\.\-](\d{2,4})', date_str)
        if date_match:
            part1, part2, year = date_match.groups()
            year = int(year)
            if year < 100:
                year += 2000
            
            if self.preferences.date_format == DateFormat.MM_DD_YYYY:
                month, day = int(part1), int(part2)
            else:
                day, month = int(part1), int(part2)
            
            try:
                return date(year, month, day)
            except ValueError:
                try:
                    return date(year, day, month)
                except ValueError:
                    pass
        
        month_match = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})', date_str)
        if month_match:
            month_name, day = month_match.groups()
            month_num = ['january', 'february', 'march', 'april', 'may', 'june', 
                        'july', 'august', 'september', 'october', 'november', 'december'].index(month_name) + 1
            year = date.today().year
            
            try:
                event_date = date(year, month_num, int(day))
                if event_date < date.today():
                    event_date = date(year + 1, month_num, int(day))
                return event_date
            except ValueError:
                pass
        
        try:
            parsed = date_parser.parse(date_str, fuzzy=True)
            return parsed.date()
        except:
            pass
        
        return date.today() + timedelta(days=1)

    async def _parse_time(self, time_str: str) -> Optional[time]:
        if not time_str:
            return None
        
        time_str = time_str.strip().lower()
        
        am_pm_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)', time_str)
        if am_pm_match:
            hour = int(am_pm_match.group(1))
            minute = int(am_pm_match.group(2)) if am_pm_match.group(2) else 0
            ampm = am_pm_match.group(3)
            
            if ampm == 'pm' and hour != 12:
                hour += 12
            elif ampm == 'am' and hour == 12:
                hour = 0
            
            try:
                return time(hour, minute)
            except ValueError:
                pass
        
        twenty_four_match = re.search(r'(\d{1,2}):(\d{2})', time_str)
        if twenty_four_match:
            hour = int(twenty_four_match.group(1))
            minute = int(twenty_four_match.group(2))
            
            try:
                return time(hour, minute)
            except ValueError:
                pass
        
        return None

    async def _extract_notifications(self, text: str) -> List[EventNotification]:
        notifications = []
        
        for pattern in self.reminder_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    notification = self._parse_notification_match(match)
                    if notification:
                        notifications.append(notification)
                except Exception as e:
                    self.logger.error(f"Error parsing notification: {e}")
        
        if not notifications:
            notifications.append(
                EventNotification(
                    type="reminder",
                    days_before=self.preferences.default_reminder_days,
                    message="Upcoming event reminder"
                )
            )
        
        return notifications

    def _parse_notification_match(self, match: re.Match) -> Optional[EventNotification]:
        text = match.group(0).lower()
        
        time_match = re.search(r'(\d+)\s+(day|days|week|weeks|month|months)', text)
        if time_match:
            num = int(time_match.group(1))
            unit = time_match.group(2)
            
            if 'day' in unit:
                days_before = num
            elif 'week' in unit:
                days_before = num * 7
            elif 'month' in unit:
                days_before = num * 30
            else:
                days_before = 1
        else:
            if 'week' in text:
                days_before = 7
            elif 'month' in text:
                days_before = 30
            else:
                days_before = 1
        
        message = f"Reminder: Event in {days_before} day{'s' if days_before != 1 else ''}"
        
        return EventNotification(
            type="reminder",
            days_before=days_before,
            message=message,
            created_at=datetime.now()
        )

    async def _extract_related_tasks(self, text: str, event_date: date) -> List[PreparationTask]:
        tasks = []
        
        for pattern in self.task_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    task = self._parse_task_match(match, event_date)
                    if task:
                        tasks.append(task)
                except Exception as e:
                    self.logger.error(f"Error parsing task: {e}")
        
        return tasks

    def _parse_task_match(self, match: re.Match, event_date: date) -> Optional[PreparationTask]:
        task_text = match.group(1).strip()
        
        due_date = event_date - timedelta(days=2)
        
        if any(word in task_text.lower() for word in ['buy', 'purchase', 'get', 'pick up']):
            due_date = event_date - timedelta(days=3)
            priority = "medium"
        elif any(word in task_text.lower() for word in ['book', 'schedule', 'reserve']):
            due_date = event_date - timedelta(days=7)
            priority = "high"
        else:
            priority = "medium"
        
        return PreparationTask(
            title=task_text,
            due_date=due_date,
            type="preparation",
            priority=priority,
            description=f"Preparation for event on {event_date.strftime('%Y-%m-%d')}"
        )

    def _determine_category(self, title: str) -> str:
        title_lower = title.lower()
        
        if any(word in title_lower for word in ['meeting', 'call', 'conference', 'work']):
            return "work"
        elif any(word in title_lower for word in ['doctor', 'dentist', 'medical', 'appointment']):
            return "health"
        elif any(word in title_lower for word in ['dinner', 'lunch', 'party', 'social']):
            return "social"
        elif any(word in title_lower for word in ['birthday', 'anniversary', 'personal']):
            return "personal"
        elif any(word in title_lower for word in ['flight', 'trip', 'travel', 'vacation']):
            return "travel"
        else:
            return "general"

    def _determine_priority(self, text: str, category: str) -> str:
        text_lower = text.lower()
        
        if any(word in text_lower for word in ['urgent', 'important', 'critical', 'asap']):
            return "high"
        elif category in ['work', 'health']:
            return "high"
        elif category in ['personal', 'social']:
            return "medium"
        else:
            return "low"

    def format_date(self, date_obj: date) -> str:
        if self.preferences.date_format == DateFormat.MM_DD_YYYY:
            return date_obj.strftime("%m/%d/%Y")
        elif self.preferences.date_format == DateFormat.DD_MM_YYYY_DOT:
            return date_obj.strftime("%d.%m.%Y")
        elif self.preferences.date_format == DateFormat.YYYY_MM_DD:
            return date_obj.strftime("%Y-%m-%d")
        else:
            return date_obj.strftime("%d/%m/%Y")

    def format_time(self, time_obj: time) -> str:
        if self.preferences.time_format == TimeFormat.TWELVE_HOUR:
            return time_obj.strftime("%I:%M %p").lstrip('0')
        else:
            return time_obj.strftime("%H:%M")

    def to_json(self, event: CalendarEvent) -> Dict[str, Any]:
        return {
            "title": event.title,
            "date": self.format_date(event.date),
            "time": self.format_time(event.time) if event.time else None,
            "duration": event.duration,
            "location": event.location,
            "description": event.description,
            "category": event.category,
            "priority": event.priority,
            "notifications": [
                {
                    "type": n.type,
                    "days_before": n.days_before,
                    "message": n.message,
                    "created_at": n.created_at.isoformat() if n.created_at else None
                }
                for n in event.notifications
            ],
            "tasks": [
                {
                    "title": t.title,
                    "due_date": self.format_date(t.due_date),
                    "type": t.type,
                    "priority": t.priority,
                    "description": t.description
                }
                for t in event.tasks
            ]
        }

ENHANCED_CALENDAR_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    start_date DATE NOT NULL,
    start_time TIME,
    duration INTEGER DEFAULT 60,
    location TEXT,
    category TEXT DEFAULT 'general',
    priority TEXT DEFAULT 'medium',
    source TEXT DEFAULT 'manual',
    integration_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id TEXT DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS event_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    days_before INTEGER NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES events (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'pending',
    priority TEXT DEFAULT 'medium',
    due_date DATE,
    task_type TEXT DEFAULT 'general',
    event_id INTEGER,
    source TEXT DEFAULT 'manual',
    integration_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id TEXT DEFAULT 'default',
    FOREIGN KEY (event_id) REFERENCES events (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    date_format TEXT DEFAULT 'DD/MM/YYYY',
    time_format TEXT DEFAULT '12h',
    timezone TEXT DEFAULT 'UTC',
    default_reminder_days INTEGER DEFAULT 1,
    default_event_duration INTEGER DEFAULT 60,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id)
);
"""
