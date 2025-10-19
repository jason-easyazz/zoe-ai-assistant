from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import sqlite3
import json
from datetime import datetime, timedelta, time
import asyncio
import httpx

router = APIRouter(prefix="/api/smart-scheduling")

class TimeSlot(BaseModel):
    start_time: str
    end_time: str
    duration_minutes: int
    energy_level: str  # high, medium, low
    task_type: str  # focus, admin, creative, physical
    priority: int  # 1-5
    available: bool = True
    score: Optional[float] = None  # For internal scoring

class ScheduleRequest(BaseModel):
    task_id: str
    estimated_duration_minutes: int
    task_type: str = "focus"
    priority: int = 3
    preferred_times: Optional[List[str]] = None  # ["morning", "afternoon", "evening"]
    deadline: Optional[str] = None
    energy_requirement: str = "medium"  # high, medium, low

class ScheduleResponse(BaseModel):
    task_id: str
    suggested_slots: List[TimeSlot]
    best_slot: Optional[TimeSlot] = None
    reasoning: str
    confidence: float  # 0-1

class UserPatterns(BaseModel):
    user_id: str
    morning_energy: float  # 0-1
    afternoon_energy: float  # 0-1
    evening_energy: float  # 0-1
    focus_time_preference: str  # morning, afternoon, evening
    break_frequency_minutes: int
    work_session_length_minutes: int
    preferred_work_days: List[str]  # ["monday", "tuesday", ...]
    preferred_work_hours: Dict[str, Any]  # {"start": "09:00", "end": "17:00"}

# Initialize database for scheduling
def init_scheduling_db():
    conn = sqlite3.connect('/app/data/zoe.db')
    cursor = conn.cursor()
    
    # Create user patterns table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_patterns (
            user_id TEXT PRIMARY KEY,
            morning_energy REAL DEFAULT 0.8,
            afternoon_energy REAL DEFAULT 0.6,
            evening_energy REAL DEFAULT 0.4,
            focus_time_preference TEXT DEFAULT 'morning',
            break_frequency_minutes INTEGER DEFAULT 25,
            work_session_length_minutes INTEGER DEFAULT 50,
            preferred_work_days TEXT DEFAULT '["monday", "tuesday", "wednesday", "thursday", "friday"]',
            preferred_work_hours TEXT DEFAULT '{"start": "09:00", "end": "17:00"}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create scheduled_tasks table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            scheduled_start TEXT NOT NULL,
            scheduled_end TEXT NOT NULL,
            actual_start TEXT,
            actual_end TEXT,
            status TEXT DEFAULT 'scheduled',  -- scheduled, in_progress, completed, cancelled
            energy_level TEXT DEFAULT 'medium',
            task_type TEXT DEFAULT 'focus',
            priority INTEGER DEFAULT 3,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create time_slots table for available slots
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS time_slots (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            energy_level TEXT NOT NULL,
            task_type TEXT NOT NULL,
            priority INTEGER NOT NULL,
            available BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_scheduling_db()

@router.get("/patterns/{user_id}")
async def get_user_patterns(user_id: str):
    """Get user scheduling patterns and preferences"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM user_patterns WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        conn.close()
        
        if result:
            patterns = dict(result)
            patterns['preferred_work_days'] = json.loads(patterns['preferred_work_days'])
            patterns['preferred_work_hours'] = json.loads(patterns['preferred_work_hours'])
            return patterns
        else:
            # Return default patterns
            return {
                "user_id": user_id,
                "morning_energy": 0.8,
                "afternoon_energy": 0.6,
                "evening_energy": 0.4,
                "focus_time_preference": "morning",
                "break_frequency_minutes": 25,
                "work_session_length_minutes": 50,
                "preferred_work_days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
                "preferred_work_hours": {"start": "09:00", "end": "17:00"}
            }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching user patterns: {str(e)}")

@router.put("/patterns/{user_id}")
async def update_user_patterns(user_id: str, patterns: UserPatterns):
    """Update user scheduling patterns"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO user_patterns 
            (user_id, morning_energy, afternoon_energy, evening_energy, 
             focus_time_preference, break_frequency_minutes, work_session_length_minutes,
             preferred_work_days, preferred_work_hours, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, patterns.morning_energy, patterns.afternoon_energy, patterns.evening_energy,
            patterns.focus_time_preference, patterns.break_frequency_minutes, patterns.work_session_length_minutes,
            json.dumps(patterns.preferred_work_days), json.dumps(patterns.preferred_work_hours),
            datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()
        
        return {"message": "User patterns updated successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating user patterns: {str(e)}")

@router.post("/schedule")
async def schedule_task(request: ScheduleRequest, user_id: str = "default"):
    """Schedule a task using smart scheduling algorithm"""
    try:
        # Get user patterns
        patterns = await get_user_patterns(user_id)
        
        # Generate available time slots
        available_slots = await generate_time_slots(user_id, patterns, request)
        
        # Score and rank slots
        scored_slots = score_time_slots(available_slots, request, patterns)
        
        # Select best slot
        best_slot = scored_slots[0] if scored_slots else None
        
        # Generate reasoning
        reasoning = generate_scheduling_reasoning(best_slot, request, patterns)
        
        # Calculate confidence
        confidence = calculate_scheduling_confidence(scored_slots, request, patterns)
        
        response = ScheduleResponse(
            task_id=request.task_id,
            suggested_slots=scored_slots[:5],  # Top 5 suggestions
            best_slot=best_slot,
            reasoning=reasoning,
            confidence=confidence
        )
        
        return response
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error scheduling task: {str(e)}")

async def generate_time_slots(user_id: str, patterns: Dict, request: ScheduleRequest) -> List[TimeSlot]:
    """Generate available time slots based on user patterns and task requirements"""
    slots = []
    
    # Get current time and work hours
    now = datetime.now()
    work_start = time.fromisoformat(patterns['preferred_work_hours']['start'])
    work_end = time.fromisoformat(patterns['preferred_work_hours']['end'])
    
    # Generate slots for the next 7 days
    for day_offset in range(7):
        current_date = now.date() + timedelta(days=day_offset)
        day_name = current_date.strftime('%A').lower()
        
        # Skip non-work days
        if day_name not in patterns['preferred_work_days']:
            continue
        
        # Generate slots for this day
        day_slots = generate_day_slots(
            current_date, work_start, work_end, 
            patterns, request, user_id
        )
        slots.extend(day_slots)
    
    return slots

def generate_day_slots(date, work_start, work_end, patterns, request, user_id):
    """Generate time slots for a specific day"""
    slots = []
    
    # Create datetime objects for start and end of work day
    work_start_dt = datetime.combine(date, work_start)
    work_end_dt = datetime.combine(date, work_end)
    
    # Generate slots in 30-minute intervals
    current_time = work_start_dt
    while current_time + timedelta(minutes=request.estimated_duration_minutes) <= work_end_dt:
        slot_end = current_time + timedelta(minutes=request.estimated_duration_minutes)
        
        # Determine energy level based on time of day
        hour = current_time.hour
        if 6 <= hour < 12:
            energy_level = "high" if patterns['morning_energy'] > 0.7 else "medium"
        elif 12 <= hour < 18:
            energy_level = "high" if patterns['afternoon_energy'] > 0.7 else "medium"
        else:
            energy_level = "low"
        
        # Determine task type compatibility
        task_type = determine_task_type(current_time, patterns)
        
        slot = TimeSlot(
            start_time=current_time.isoformat(),
            end_time=slot_end.isoformat(),
            duration_minutes=request.estimated_duration_minutes,
            energy_level=energy_level,
            task_type=task_type,
            priority=request.priority,
            available=True
        )
        
        slots.append(slot)
        current_time += timedelta(minutes=30)  # 30-minute intervals
    
    return slots

def determine_task_type(time_slot, patterns):
    """Determine the best task type for a given time slot"""
    hour = time_slot.hour
    
    if 6 <= hour < 10:
        return "focus"  # Morning focus time
    elif 10 <= hour < 12:
        return "creative"  # Late morning creativity
    elif 12 <= hour < 14:
        return "admin"  # Afternoon admin tasks
    elif 14 <= hour < 16:
        return "focus"  # Afternoon focus time
    else:
        return "admin"  # Evening admin tasks

def score_time_slots(slots: List[TimeSlot], request: ScheduleRequest, patterns: Dict) -> List[TimeSlot]:
    """Score and rank time slots based on various factors"""
    scored_slots = []
    
    for slot in slots:
        score = 0
        
        # Energy level matching
        if request.energy_requirement == "high" and slot.energy_level == "high":
            score += 30
        elif request.energy_requirement == "medium" and slot.energy_level in ["high", "medium"]:
            score += 20
        elif request.energy_requirement == "low":
            score += 10
        
        # Task type matching
        if slot.task_type == request.task_type:
            score += 25
        
        # Time preference matching
        hour = datetime.fromisoformat(slot.start_time).hour
        if request.preferred_times:
            if "morning" in request.preferred_times and 6 <= hour < 12:
                score += 20
            elif "afternoon" in request.preferred_times and 12 <= hour < 18:
                score += 20
            elif "evening" in request.preferred_times and 18 <= hour < 22:
                score += 20
        
        # Priority matching
        if slot.priority >= request.priority:
            score += 15
        
        # Recency bonus (sooner is better)
        time_diff = datetime.fromisoformat(slot.start_time) - datetime.now()
        if time_diff.days == 0:
            score += 10
        elif time_diff.days == 1:
            score += 5
        
        # Add score to slot
        slot.score = score
        scored_slots.append(slot)
    
    # Sort by score (highest first)
    scored_slots.sort(key=lambda x: x.score, reverse=True)
    
    return scored_slots

def generate_scheduling_reasoning(best_slot: Optional[TimeSlot], request: ScheduleRequest, patterns: Dict) -> str:
    """Generate human-readable reasoning for the scheduling decision"""
    if not best_slot:
        return "No suitable time slots found. Consider adjusting task requirements or extending deadline."
    
    hour = datetime.fromisoformat(best_slot.start_time).hour
    day_name = datetime.fromisoformat(best_slot.start_time).strftime('%A')
    
    reasoning_parts = []
    
    # Time of day reasoning
    if 6 <= hour < 12:
        reasoning_parts.append(f"Morning slot on {day_name} - optimal for high-energy tasks")
    elif 12 <= hour < 18:
        reasoning_parts.append(f"Afternoon slot on {day_name} - good for sustained work")
    else:
        reasoning_parts.append(f"Evening slot on {day_name} - suitable for lighter tasks")
    
    # Energy level reasoning
    if best_slot.energy_level == "high":
        reasoning_parts.append("High energy period - perfect for demanding tasks")
    elif best_slot.energy_level == "medium":
        reasoning_parts.append("Medium energy period - good for most tasks")
    else:
        reasoning_parts.append("Lower energy period - suitable for routine tasks")
    
    # Task type reasoning
    if best_slot.task_type == request.task_type:
        reasoning_parts.append(f"Matches your preferred {request.task_type} work style")
    
    return ". ".join(reasoning_parts) + "."

def calculate_scheduling_confidence(scored_slots: List[TimeSlot], request: ScheduleRequest, patterns: Dict) -> float:
    """Calculate confidence score for the scheduling recommendation"""
    if not scored_slots:
        return 0.0
    
    best_score = scored_slots[0].score
    max_possible_score = 100  # Adjust based on scoring system
    
    # Base confidence from score
    confidence = min(best_score / max_possible_score, 1.0)
    
    # Boost confidence if multiple good options
    if len(scored_slots) > 1 and scored_slots[1].score > best_score * 0.8:
        confidence += 0.1
    
    # Boost confidence if matches user preferences well
    if request.preferred_times and any(
        "morning" in request.preferred_times and 6 <= datetime.fromisoformat(slot.start_time).hour < 12
        for slot in scored_slots[:3]
    ):
        confidence += 0.1
    
    return min(confidence, 1.0)

@router.get("/schedule/{user_id}")
async def get_user_schedule(user_id: str, days: int = 7):
    """Get user's scheduled tasks for the next N days"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get scheduled tasks for the next N days
        start_date = datetime.now().date()
        end_date = start_date + timedelta(days=days)
        
        cursor.execute("""
            SELECT * FROM scheduled_tasks 
            WHERE user_id = ? AND DATE(scheduled_start) BETWEEN ? AND ?
            ORDER BY scheduled_start
        """, (user_id, start_date.isoformat(), end_date.isoformat()))
        
        tasks = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return {
            "user_id": user_id,
            "period": f"{start_date} to {end_date}",
            "scheduled_tasks": tasks,
            "total_tasks": len(tasks)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching schedule: {str(e)}")

@router.post("/schedule/{user_id}/confirm")
async def confirm_schedule(user_id: str, task_id: str, slot: TimeSlot):
    """Confirm a scheduled task"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        # Insert scheduled task
        scheduled_id = f"{user_id}_{task_id}_{int(datetime.now().timestamp())}"
        cursor.execute("""
            INSERT INTO scheduled_tasks 
            (id, task_id, user_id, scheduled_start, scheduled_end, 
             energy_level, task_type, priority, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            scheduled_id, task_id, user_id, slot.start_time, slot.end_time,
            slot.energy_level, slot.task_type, slot.priority, 'scheduled'
        ))
        
        conn.commit()
        conn.close()
        
        return {"message": "Task scheduled successfully", "scheduled_id": scheduled_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error confirming schedule: {str(e)}")

@router.get("/analytics/{user_id}")
async def get_scheduling_analytics(user_id: str):
    """Get scheduling analytics and insights"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get completed tasks for analytics
        cursor.execute("""
            SELECT * FROM scheduled_tasks 
            WHERE user_id = ? AND status = 'completed'
            ORDER BY scheduled_start DESC
            LIMIT 100
        """, (user_id,))
        
        completed_tasks = [dict(row) for row in cursor.fetchall()]
        
        # Calculate analytics
        analytics = {
            "total_completed": len(completed_tasks),
            "energy_distribution": {},
            "task_type_distribution": {},
            "time_preference_accuracy": 0.0,
            "average_duration_accuracy": 0.0
        }
        
        if completed_tasks:
            # Energy distribution
            energy_counts = {}
            for task in completed_tasks:
                energy = task['energy_level']
                energy_counts[energy] = energy_counts.get(energy, 0) + 1
            
            total = len(completed_tasks)
            analytics["energy_distribution"] = {
                energy: count / total for energy, count in energy_counts.items()
            }
            
            # Task type distribution
            type_counts = {}
            for task in completed_tasks:
                task_type = task['task_type']
                type_counts[task_type] = type_counts.get(task_type, 0) + 1
            
            analytics["task_type_distribution"] = {
                task_type: count / total for task_type, count in type_counts.items()
            }
        
        conn.close()
        
        return analytics
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching analytics: {str(e)}")
