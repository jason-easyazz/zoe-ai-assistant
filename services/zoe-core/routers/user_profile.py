"""
User Profile Router
Manages user profile data including basic info and compatibility profile
Designed to be network-ready for future Zoe-to-Zoe communication features
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime
import sqlite3
import json
import os
import sys
sys.path.append('/app')
from auth_integration import validate_session, AuthenticatedSession

router = APIRouter(prefix="/api/user/profile", tags=["user-profile"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def init_user_profiles_db():
    """Initialize user profiles table"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT UNIQUE NOT NULL,
            name TEXT,
            bio TEXT,
            location TEXT,
            timezone TEXT,
            birthday DATE,
            avatar_url TEXT,
            age_range TEXT,
            personality_traits JSON,
            values_priority JSON,
            interests JSON,
            life_goals JSON,
            communication_styles JSON,
            social_energy TEXT,
            current_life_phase TEXT,
            daily_routine_type TEXT,
            profile_completeness REAL DEFAULT 0.0,
            confidence_score REAL DEFAULT 0.0,
            ai_insights JSON,
            observed_patterns JSON,
            onboarding_completed BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id ON user_profiles(user_id)")
    
    conn.commit()
    conn.close()

# Initialize on import
init_user_profiles_db()

# Request/Response models
class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None
    location: Optional[str] = None
    timezone: Optional[str] = None
    birthday: Optional[str] = None
    avatar_url: Optional[str] = None
    age_range: Optional[str] = None
    personality_traits: Optional[Dict[str, Any]] = None
    values_priority: Optional[Dict[str, Any]] = None
    interests: Optional[List[Dict[str, Any]]] = None
    life_goals: Optional[List[Dict[str, Any]]] = None
    communication_styles: Optional[List[str]] = None
    social_energy: Optional[str] = None
    current_life_phase: Optional[str] = None
    daily_routine_type: Optional[str] = None
    onboarding_completed: Optional[bool] = None

@router.get("")
async def get_profile(session: AuthenticatedSession = Depends(validate_session)):
    """Get user profile for authenticated user"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM user_profiles WHERE user_id = ?
    """, (user_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        # Return empty profile with defaults
        return {
            "user_id": user_id,
            "onboarding_completed": False,
            "profile_completeness": 0.0,
            "confidence_score": 0.0
        }
    
    profile = dict(row)
    
    # Parse JSON fields
    json_fields = ['personality_traits', 'values_priority', 'interests', 'life_goals', 
                   'communication_styles', 'ai_insights', 'observed_patterns']
    
    for field in json_fields:
        if profile.get(field):
            try:
                profile[field] = json.loads(profile[field])
            except:
                profile[field] = None
    
    return profile

@router.put("")
async def update_profile(
    profile_data: ProfileUpdate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update user profile"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if profile exists
    cursor.execute("SELECT id FROM user_profiles WHERE user_id = ?", (user_id,))
    exists = cursor.fetchone()
    
    # Prepare update data
    update_data = profile_data.model_dump(exclude_unset=True)
    
    # Serialize JSON fields
    json_fields = ['personality_traits', 'values_priority', 'interests', 'life_goals',
                   'communication_styles', 'ai_insights', 'observed_patterns']
    
    for field in json_fields:
        if field in update_data and update_data[field] is not None:
            update_data[field] = json.dumps(update_data[field])
    
    # Calculate completeness (simple implementation)
    if exists:
        # Update existing
        set_clauses = [f"{k} = ?" for k in update_data.keys()]
        set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        
        cursor.execute(f"""
            UPDATE user_profiles
            SET {', '.join(set_clauses)}
            WHERE user_id = ?
        """, list(update_data.values()) + [user_id])
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Profile not found")
    else:
        # Create new
        keys = list(update_data.keys()) + ['user_id']
        values = list(update_data.values()) + [user_id]
        placeholders = ', '.join(['?'] * len(values))
        
        cursor.execute(f"""
            INSERT INTO user_profiles ({', '.join(keys)})
            VALUES ({placeholders})
        """, values)
    
    conn.commit()
    conn.close()
    
    return {"message": "Profile updated successfully"}

@router.post("/analyze")
async def analyze_profile(session: AuthenticatedSession = Depends(validate_session)):
    """Trigger AI analysis of user profile from conversations/journal/calendar"""
    user_id = session.user_id
    
    # This will call the profile_analyzer service
    # For now, return placeholder
    return {
        "message": "Analysis triggered",
        "status": "in_progress",
        "estimated_time": "2-5 minutes"
    }

@router.get("/insights")
async def get_profile_insights(session: AuthenticatedSession = Depends(validate_session)):
    """Get AI-generated insights with confidence scores"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT ai_insights, confidence_score, observed_patterns
        FROM user_profiles WHERE user_id = ?
    """, (user_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row or not row['ai_insights']:
        return {"insights": [], "confidence": 0.0, "patterns": []}
    
    insights = json.loads(row['ai_insights']) if row['ai_insights'] else []
    patterns = json.loads(row['observed_patterns']) if row['observed_patterns'] else []
    
    return {
        "insights": insights,
        "confidence": row['confidence_score'],
        "patterns": patterns
    }

@router.post("/extract")
async def extract_profile_from_chat(session: AuthenticatedSession = Depends(validate_session)):
    """Extract profile data from conversation history"""
    user_id = session.user_id
    
    # This will be implemented by analyzing chat history
    # Returns confidence scores per extracted field
    return {
        "extracted_fields": [],
        "confidence_scores": {},
        "message": "Extraction will be implemented in profile_analyzer.py"
    }



