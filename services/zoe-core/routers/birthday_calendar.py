"""
Birthday Calendar Service
=========================

Pulls birthday information from people in memories and displays them in the calendar.
This is better than recurring calendar events because:
1. Single source of truth (people in memories)
2. Person details can control reminder preferences
3. No duplicate birthday data
4. Secure access to personal information
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import sqlite3
import json
import os
from auth_integration import validate_session, AuthenticatedSession

router = APIRouter(prefix="/api/birthdays", tags=["birthdays"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

class BirthdayCalendarService:
    """Service for managing birthday display in calendar from people in memories"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
    
    def get_upcoming_birthdays(self, user_id: str, days_ahead: int = 30) -> List[Dict[str, Any]]:
        """Get upcoming birthdays from people in memories"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get people with birthdays from memories
            cursor.execute("""
                SELECT id, name, relationship, birthday, metadata
                FROM people 
                WHERE user_id = ? AND birthday IS NOT NULL
                ORDER BY name
            """, (user_id,))
            
            people = cursor.fetchall()
            conn.close()
            
            upcoming_birthdays = []
            today = datetime.now().date()
            end_date = today + timedelta(days=days_ahead)
            
            for person in people:
                person_id, name, relationship, birthday, metadata = person
                
                if not birthday:
                    continue
                
                # Parse birthday
                try:
                    birth_date = datetime.strptime(birthday, "%Y-%m-%d").date()
                    
                    # Calculate this year's birthday
                    current_year = today.year
                    this_year_birthday = birth_date.replace(year=current_year)
                    
                    # If birthday has passed this year, use next year
                    if this_year_birthday < today:
                        this_year_birthday = birth_date.replace(year=current_year + 1)
                    
                    # Check if birthday is within the specified range
                    if today <= this_year_birthday <= end_date:
                        # Parse metadata for reminder preferences
                        reminder_enabled = True
                        reminder_days = 14
                        reminder_time = "09:00"
                        
                        if metadata:
                            try:
                                meta = json.loads(metadata)
                                reminder_enabled = meta.get("birthday_reminder_enabled", True)
                                reminder_days = meta.get("reminder_days_before", 14)
                                reminder_time = meta.get("reminder_time", "09:00")
                            except:
                                pass
                        
                        # Calculate age
                        age = current_year - birth_date.year
                        if this_year_birthday > today:
                            age = current_year - birth_date.year
                        else:
                            age = current_year - birth_date.year + 1
                        
                        upcoming_birthdays.append({
                            "person_id": person_id,
                            "name": name,
                            "relationship": relationship,
                            "birthday": birthday,
                            "this_year_birthday": this_year_birthday.isoformat(),
                            "age": age,
                            "days_until": (this_year_birthday - today).days,
                            "reminder_enabled": reminder_enabled,
                            "reminder_days_before": reminder_days,
                            "reminder_time": reminder_time,
                            "reminder_date": (this_year_birthday - timedelta(days=reminder_days)).isoformat() if reminder_enabled else None
                        })
                        
                except ValueError:
                    # Invalid date format, skip
                    continue
            
            # Sort by days until birthday
            upcoming_birthdays.sort(key=lambda x: x["days_until"])
            
            return upcoming_birthdays
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error getting upcoming birthdays: {str(e)}")
    
    def get_birthday_calendar_events(self, user_id: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Get birthday events for calendar display (not stored as calendar events)"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get people with birthdays
            cursor.execute("""
                SELECT id, name, relationship, birthday, metadata
                FROM people 
                WHERE user_id = ? AND birthday IS NOT NULL
                ORDER BY name
            """, (user_id,))
            
            people = cursor.fetchall()
            conn.close()
            
            calendar_events = []
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
            
            for person in people:
                person_id, name, relationship, birthday, metadata = person
                
                if not birthday:
                    continue
                
                try:
                    birth_date = datetime.strptime(birthday, "%Y-%m-%d").date()
                    
                    # Check birthdays in the date range for current year
                    current_year = start_dt.year
                    this_year_birthday = birth_date.replace(year=current_year)
                    
                    if start_dt <= this_year_birthday <= end_dt:
                        # Parse metadata for reminder preferences
                        reminder_enabled = True
                        reminder_days = 14
                        
                        if metadata:
                            try:
                                meta = json.loads(metadata)
                                reminder_enabled = meta.get("birthday_reminder_enabled", True)
                                reminder_days = meta.get("reminder_days_before", 14)
                            except:
                                pass
                        
                        # Calculate age
                        age = current_year - birth_date.year
                        
                        calendar_events.append({
                            "id": f"birthday_{person_id}",
                            "title": f"🎂 {name}'s Birthday",
                            "description": f"{name} turns {age} today! {relationship}",
                            "start_date": this_year_birthday.isoformat(),
                            "start_time": "12:00",
                            "end_time": "23:59",
                            "all_day": True,
                            "category": "birthday",
                            "source": "memories",
                            "person_id": person_id,
                            "reminder_enabled": reminder_enabled,
                            "reminder_days_before": reminder_days
                        })
                        
                        # Add reminder event if enabled and within range
                        if reminder_enabled:
                            reminder_date = this_year_birthday - timedelta(days=reminder_days)
                            if start_dt <= reminder_date <= end_dt:
                                calendar_events.append({
                                    "id": f"birthday_reminder_{person_id}",
                                    "title": f"🎁 {name}'s Birthday Reminder",
                                    "description": f"Reminder: {name}'s birthday is in {reminder_days} days! Time to plan and get gifts.",
                                    "start_date": reminder_date.isoformat(),
                                    "start_time": "09:00",
                                    "end_time": "09:30",
                                    "all_day": False,
                                    "category": "reminder",
                                    "source": "memories",
                                    "person_id": person_id,
                                    "is_reminder": True
                                })
                    
                except ValueError:
                    continue
            
            # Sort by date
            calendar_events.sort(key=lambda x: x["start_date"])
            
            return calendar_events
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error getting birthday calendar events: {str(e)}")
    
    def update_birthday_reminder_preferences(self, person_id: int, user_id: str, 
                                           reminder_enabled: bool, 
                                           reminder_days_before: int = 14,
                                           reminder_time: str = "09:00") -> Dict[str, Any]:
        """Update birthday reminder preferences for a person"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get current metadata
            cursor.execute("""
                SELECT metadata FROM people 
                WHERE id = ? AND user_id = ?
            """, (person_id, user_id))
            
            result = cursor.fetchone()
            if not result:
                conn.close()
                raise HTTPException(status_code=404, detail="Person not found")
            
            # Update metadata with reminder preferences
            current_metadata = {}
            if result[0]:
                try:
                    current_metadata = json.loads(result[0])
                except:
                    pass
            
            current_metadata.update({
                "birthday_reminder_enabled": reminder_enabled,
                "reminder_days_before": reminder_days_before,
                "reminder_time": reminder_time
            })
            
            cursor.execute("""
                UPDATE people SET metadata = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
            """, (json.dumps(current_metadata), person_id, user_id))
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "message": "Birthday reminder preferences updated successfully",
                "preferences": {
                    "reminder_enabled": reminder_enabled,
                    "reminder_days_before": reminder_days_before,
                    "reminder_time": reminder_time
                }
            }
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error updating reminder preferences: {str(e)}")

# Global service instance
birthday_service = BirthdayCalendarService()

# API Endpoints
@router.get("/upcoming")
async def get_upcoming_birthdays(
    days_ahead: int = Query(30, description="Number of days to look ahead"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get upcoming birthdays from people in memories"""
    return {
        "birthdays": birthday_service.get_upcoming_birthdays(session.user_id, days_ahead)
    }

@router.get("/calendar-events")
async def get_birthday_calendar_events(
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get birthday events for calendar display (pulled from memories, not stored as calendar events)"""
    return {
        "events": birthday_service.get_birthday_calendar_events(session.user_id, start_date, end_date)
    }

@router.put("/{person_id}/reminder-preferences")
async def update_reminder_preferences(
    person_id: int,
    reminder_enabled: bool = Query(..., description="Enable birthday reminders"),
    reminder_days_before: int = Query(14, description="Days before birthday for reminder"),
    reminder_time: str = Query("09:00", description="Time for reminder"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update birthday reminder preferences for a person"""
    return birthday_service.update_birthday_reminder_preferences(
        person_id, session.user_id, reminder_enabled, reminder_days_before, reminder_time
    )
