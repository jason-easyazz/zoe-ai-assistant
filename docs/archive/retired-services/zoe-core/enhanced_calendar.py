"""
Enhanced Calendar System with Event Clusters
Implements prep tasks, notifications, and intelligent scheduling
"""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import re

class EventClusterSystem:
    def __init__(self, db_path="/data/zoe.db"):
        self.db_path = db_path
        self.init_database()
        
    def init_database(self):
        """Initialize event cluster tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Event clusters table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_clusters (
                cluster_id TEXT PRIMARY KEY,
                primary_event_id INTEGER,
                event_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (primary_event_id) REFERENCES events(id)
            )
        ''')
        
        # Prep tasks table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_prep_tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id TEXT,
                task_text TEXT NOT NULL,
                days_before INTEGER DEFAULT 0,
                time_of_day TEXT,
                completed BOOLEAN DEFAULT 0,
                completed_at TIMESTAMP,
                FOREIGN KEY (cluster_id) REFERENCES event_clusters(cluster_id)
            )
        ''')
        
        # Notifications table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_notifications (
                notif_id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id TEXT,
                trigger_time TIMESTAMP,
                message TEXT,
                delivered BOOLEAN DEFAULT 0,
                delivered_at TIMESTAMP,
                FOREIGN KEY (cluster_id) REFERENCES event_clusters(cluster_id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def create_event_cluster(self, event_data: Dict) -> Dict:
        """Create an event with associated prep tasks and notifications"""
        
        # Define prep task templates by event type
        prep_templates = {
            "birthday": [
                {"task": "Buy gift for {name}", "days_before": 14},
                {"task": "Get birthday card", "days_before": 7},
                {"task": "Wrap present", "days_before": 1},
                {"task": "Call {name} to wish happy birthday", "days_before": 0, "time": "09:00"}
            ],
            "meeting": [
                {"task": "Prepare agenda", "days_before": 1},
                {"task": "Review previous meeting notes", "days_before": 1},
                {"task": "Send reminder to attendees", "days_before": 2},
                {"task": "Set up meeting room/link", "days_before": 0, "time": "30min_before"}
            ],
            "appointment": [
                {"task": "Confirm appointment", "days_before": 1},
                {"task": "Prepare documents", "days_before": 1},
                {"task": "Plan route/transport", "days_before": 0, "time": "1hr_before"}
            ],
            "deadline": [
                {"task": "Final review", "days_before": 1},
                {"task": "Complete first draft", "days_before": 7},
                {"task": "Start research", "days_before": 14}
            ],
            "travel": [
                {"task": "Check in online", "days_before": 1},
                {"task": "Pack luggage", "days_before": 1},
                {"task": "Confirm bookings", "days_before": 3},
                {"task": "Arrange transport to airport", "days_before": 7}
            ]
        }
        
        # Detect event type from title
        event_type = self._detect_event_type(event_data.get("title", ""))
        
        # Generate cluster ID
        cluster_id = f"cluster_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{event_type}"
        
        # Create the main event
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Insert main event
        cursor.execute('''
            INSERT INTO events (title, start_date, start_time, end_date, end_time, 
                              description, location, event_type, cluster_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            event_data.get("title"),
            event_data.get("start_date"),
            event_data.get("start_time"),
            event_data.get("end_date"),
            event_data.get("end_time"),
            event_data.get("description"),
            event_data.get("location"),
            event_type,
            cluster_id
        ))
        
        event_id = cursor.lastrowid
        
        # Create cluster entry
        cursor.execute('''
            INSERT INTO event_clusters (cluster_id, primary_event_id, event_type)
            VALUES (?, ?, ?)
        ''', (cluster_id, event_id, event_type))
        
        # Generate prep tasks based on template
        if event_type in prep_templates:
            for task_template in prep_templates[event_type]:
                task_text = task_template["task"].format(
                    name=event_data.get("title", "").replace("'s birthday", "")
                )
                cursor.execute('''
                    INSERT INTO event_prep_tasks (cluster_id, task_text, days_before, time_of_day)
                    VALUES (?, ?, ?, ?)
                ''', (
                    cluster_id,
                    task_text,
                    task_template["days_before"],
                    task_template.get("time")
                ))
        
        # Generate notifications
        event_date = datetime.strptime(event_data.get("start_date"), "%Y-%m-%d")
        
        # Standard notification schedule
        notification_schedule = [
            (14, "📅 {title} in 2 weeks"),
            (7, "📅 {title} in 1 week"),
            (1, "⏰ {title} tomorrow!"),
            (0, "🔔 {title} today!")
        ]
        
        for days_before, message_template in notification_schedule:
            trigger_time = event_date - timedelta(days=days_before)
            if trigger_time >= datetime.now():
                cursor.execute('''
                    INSERT INTO event_notifications (cluster_id, trigger_time, message)
                    VALUES (?, ?, ?)
                ''', (
                    cluster_id,
                    trigger_time.isoformat(),
                    message_template.format(title=event_data.get("title"))
                ))
        
        conn.commit()
        conn.close()
        
        return {
            "cluster_id": cluster_id,
            "event_id": event_id,
            "event_type": event_type,
            "prep_tasks_created": len(prep_templates.get(event_type, [])),
            "notifications_scheduled": len(notification_schedule)
        }
    
    def _detect_event_type(self, title: str) -> str:
        """Detect event type from title keywords"""
        title_lower = title.lower()
        
        type_keywords = {
            "birthday": ["birthday", "bday", "born"],
            "meeting": ["meeting", "meet", "conference", "call", "sync"],
            "appointment": ["appointment", "doctor", "dentist", "checkup"],
            "deadline": ["deadline", "due", "submit", "deliverable"],
            "travel": ["flight", "travel", "trip", "vacation", "holiday"]
        }
        
        for event_type, keywords in type_keywords.items():
            if any(keyword in title_lower for keyword in keywords):
                return event_type
        
        return "general"
    
    def get_upcoming_tasks(self, days_ahead: int = 7) -> List[Dict]:
        """Get prep tasks for the next N days"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                t.task_id,
                t.task_text,
                t.days_before,
                t.cluster_id,
                e.title as event_title,
                e.start_date as event_date
            FROM event_prep_tasks t
            JOIN event_clusters c ON t.cluster_id = c.cluster_id
            JOIN events e ON c.primary_event_id = e.id
            WHERE t.completed = 0
                AND date(e.start_date, '-' || t.days_before || ' days') <= date('now', '+' || ? || ' days')
                AND date(e.start_date, '-' || t.days_before || ' days') >= date('now')
            ORDER BY date(e.start_date, '-' || t.days_before || ' days')
        ''', (days_ahead,))
        
        tasks = []
        for row in cursor.fetchall():
            tasks.append({
                "task_id": row[0],
                "task_text": row[1],
                "days_before": row[2],
                "cluster_id": row[3],
                "event_title": row[4],
                "event_date": row[5]
            })
        
        conn.close()
        return tasks

# Integration with main calendar
calendar_system = EventClusterSystem()
