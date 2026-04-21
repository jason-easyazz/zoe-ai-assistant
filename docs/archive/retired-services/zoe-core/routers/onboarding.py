"""
User Onboarding System for Zoe
===============================
Conversational questionnaire that builds comprehensive user profiles
for personalization and compatibility matching.

This system learns about users through natural conversation to enable:
- Personalized assistant experience
- Friend-making and match-making through compatibility analysis
- Two Zoes talking to determine if their users are compatible
"""

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends, Header
import os
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import sqlite3
import json
import logging
import sys

sys.path.append('/app')

# Import authentication
from auth_integration import validate_session, AuthenticatedSession

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class OnboardingResponse(BaseModel):
    question_id: str
    response: str
    metadata: Optional[Dict[str, Any]] = None


class OnboardingProgress(BaseModel):
    user_id: str
    current_phase: str
    questions_answered: int
    total_questions: int
    profile_completeness: float
    next_question: Optional[Dict[str, Any]] = None


# ============================================================================
# ONBOARDING QUESTIONS - Conversational & Natural
# ============================================================================

ONBOARDING_PHASES = {
    "intro": {
        "name": "Getting Started",
        "description": "Let's get to know each other",
        "questions": [
            {
                "id": "intro_1",
                "question": "Hi! I'm Zoe - your best friend and best personal assistant combined into one. What should I call you?",
                "field": "display_name",
                "type": "text",
                "help_text": "Just your first name is fine!"
            },
            {
                "id": "intro_2",
                "question": "Nice to meet you{name}! I'm excited to get to know you better. How do you like people to communicate with you?",
                "field": "communication_style",
                "type": "multiple_choice",
                "options": [
                    {"value": "direct", "label": "Direct and concise - get to the point"},
                    {"value": "detailed", "label": "Detailed and thorough - I like context"},
                    {"value": "casual", "label": "Casual and friendly - keep it relaxed"},
                    {"value": "humorous", "label": "Fun and humorous - make me laugh"},
                    {"value": "empathetic", "label": "Warm and empathetic - understand my feelings"}
                ]
            },
            {
                "id": "intro_3",
                "question": "When are you usually most active during the day? This helps me know when to check in with you versus when to give you space.",
                "field": "daily_routine",
                "type": "multiple_choice",
                "options": [
                    {"value": "early_bird", "label": "Early bird 🌅 (most active mornings)"},
                    {"value": "night_owl", "label": "Night owl 🦉 (most active evenings/night)"},
                    {"value": "flexible", "label": "Flexible - no strong pattern"},
                    {"value": "structured", "label": "Consistent schedule (like 9-5)"}
                ]
            }
        ]
    },
    
    "personality": {
        "name": "Understanding You",
        "description": "Your personality and social style",
        "questions": [
            {
                "id": "personality_1",
                "question": "After a long day, what sounds better to you - going out with friends or having quiet time alone?",
                "field": "social_energy",
                "type": "scale",
                "scale": {
                    "min": 1,
                    "max": 5,
                    "min_label": "Definitely alone time",
                    "max_label": "Definitely social time",
                    "mid_label": "Depends on my mood"
                }
            },
            {
                "id": "personality_2",
                "question": "When you have free time, do you prefer sticking to what you know and love, or trying something completely new?",
                "field": "openness",
                "type": "scale",
                "scale": {
                    "min": 1,
                    "max": 5,
                    "min_label": "Stick with what I know",
                    "max_label": "Always trying new things"
                }
            },
            {
                "id": "personality_3",
                "question": "Are you more of a planner who likes things organized, or spontaneous and go-with-the-flow?",
                "field": "conscientiousness",
                "type": "scale",
                "scale": {
                    "min": 1,
                    "max": 5,
                    "min_label": "Spontaneous & flexible",
                    "max_label": "Organized & planned"
                }
            },
            {
                "id": "personality_4",
                "question": "How would you describe your general outlook - more 'glass half full' or 'glass half empty'?",
                "field": "optimism",
                "type": "scale",
                "scale": {
                    "min": 1,
                    "max": 5,
                    "min_label": "Realistic/cautious",
                    "max_label": "Optimistic/positive"
                }
            }
        ]
    },
    
    "values": {
        "name": "What Matters Most",
        "description": "Your core values and beliefs",
        "questions": [
            {
                "id": "values_1",
                "question": "What matters most to you in life right now? Pick your top 3:",
                "field": "top_values",
                "type": "multi_select",
                "options": [
                    "Family & close relationships",
                    "Career success & achievement",
                    "Personal growth & self-improvement",
                    "Health & fitness",
                    "Financial security",
                    "Creativity & self-expression",
                    "Helping others & making a difference",
                    "Learning & knowledge",
                    "Adventure & new experiences",
                    "Stability & security"
                ],
                "max_selections": 3
            },
            {
                "id": "values_2",
                "question": "Are there any dealbreakers for you in relationships (any kind - friendship, work, romantic)? Things you just can't compromise on?",
                "field": "dealbreakers",
                "type": "text",
                "multiline": True,
                "optional": True,
                "help_text": "For example: dishonesty, lack of respect, etc."
            }
        ]
    },
    
    "interests": {
        "name": "What You Love",
        "description": "Your interests and passions",
        "questions": [
            {
                "id": "interests_1",
                "question": "What do you love doing in your free time? Tell me about your hobbies and interests!",
                "field": "interests_text",
                "type": "text",
                "multiline": True,
                "help_text": "The more details, the better I can help you connect with compatible people"
            },
            {
                "id": "interests_2",
                "question": "What are you currently obsessed with or really into right now?",
                "field": "current_obsessions",
                "type": "text",
                "multiline": True,
                "optional": True,
                "help_text": "Could be a show, project, new hobby, anything!"
            },
            {
                "id": "interests_3",
                "question": "How do you prefer to spend time with people? Pick what sounds most appealing (up to 4):",
                "field": "social_activities",
                "type": "multi_select",
                "options": [
                    "Outdoor activities & sports",
                    "Coffee shops & cafes",
                    "Cultural events (museums, concerts, theater)",
                    "Quiet hangouts at home",
                    "Parties & social events",
                    "Working on projects together",
                    "Deep conversations over dinner",
                    "Gaming or watching shows together"
                ],
                "max_selections": 4
            }
        ]
    },
    
    "goals": {
        "name": "Your Aspirations",
        "description": "Where you're headed",
        "questions": [
            {
                "id": "goals_1",
                "question": "What are you working towards right now? What are your goals for the next year or two?",
                "field": "short_term_goals",
                "type": "text",
                "multiline": True
            },
            {
                "id": "goals_2",
                "question": "Dream big for a moment - if everything goes your way, where do you see yourself in 5-10 years?",
                "field": "long_term_vision",
                "type": "text",
                "multiline": True,
                "optional": True
            },
            {
                "id": "goals_3",
                "question": "What's something you've been wanting to learn or get better at?",
                "field": "growth_areas",
                "type": "text",
                "multiline": True,
                "optional": True
            }
        ]
    },
    
    "relationships": {
        "name": "Connections",
        "description": "What you're looking for",
        "questions": [
            {
                "id": "relationships_1",
                "question": "What are you hoping to get out of using Zoe?",
                "field": "usage_intent",
                "type": "multi_select",
                "options": [
                    "Personal organization & productivity",
                    "Finding friends with similar interests",
                    "Professional networking",
                    "Activity partners (sports, hobbies, etc.)",
                    "Just having a smart assistant"
                ],
                "max_selections": 5
            },
            {
                "id": "relationships_2",
                "question": "When you're looking for new friends or connections, what qualities matter most to you?",
                "field": "relationship_qualities",
                "type": "text",
                "multiline": True,
                "optional": True,
                "help_text": "For example: honesty, sense of humor, shared interests, etc."
            }
        ]
    },
    
    "wrap_up": {
        "name": "Final Touches",
        "description": "Just a few more things",
        "questions": [
            {
                "id": "wrap_1",
                "question": "Is there anything else you'd like me to know about you that I haven't asked about?",
                "field": "additional_info",
                "type": "text",
                "multiline": True,
                "optional": True
            },
            {
                "id": "wrap_2",
                "question": "Should I send you proactive insights when I notice patterns or opportunities? (Like 'you haven't talked to Sarah in a while' or 'this event seems perfect for you')",
                "field": "proactive_insights_enabled",
                "type": "yes_no"
            }
        ]
    }
}


# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def init_onboarding_db():
    """Initialize onboarding tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_onboarding (
            user_id TEXT PRIMARY KEY,
            current_phase TEXT DEFAULT 'intro',
            phase_progress TEXT DEFAULT '{}',
            responses TEXT DEFAULT '{}',
            profile_data TEXT DEFAULT '{}',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_complete BOOLEAN DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_compatibility_profiles (
            user_id TEXT PRIMARY KEY,
            profile_data TEXT NOT NULL,
            profile_completeness REAL DEFAULT 0.0,
            confidence_score REAL DEFAULT 0.0,
            interaction_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()


# Initialize on import
try:
    init_onboarding_db()
    logger.info("✅ Onboarding database initialized")
except Exception as e:
    logger.error(f"❌ Failed to initialize onboarding database: {e}")


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.post("/start")
async def start_onboarding(session: AuthenticatedSession = Depends(validate_session)):
    """Start onboarding process for a new user"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if already exists
        cursor.execute("SELECT user_id, is_complete FROM user_onboarding WHERE user_id = ?", (user_id,))
        existing = cursor.fetchone()
        
        if existing:
            if existing[1]:  # is_complete
                conn.close()
                return {
                    "status": "already_complete",
                    "message": "You've already completed onboarding! I already know you well."
                }
            else:
                # Resume existing onboarding
                cursor.execute("""
                    SELECT current_phase, phase_progress
                    FROM user_onboarding WHERE user_id = ?
                """, (user_id,))
                phase, progress_json = cursor.fetchone()
                progress = json.loads(progress_json)
                conn.close()
                
                # Find next question
                next_q = get_next_question_in_phase(phase, progress.get(phase, {}).get("last_question"))
                
                return {
                    "status": "resume",
                    "message": "Welcome back! Let's continue where we left off.",
                    "current_phase": phase,
                    "next_question": next_q
                }
        
        # Create new onboarding record
        cursor.execute("""
            INSERT INTO user_onboarding (user_id, current_phase)
            VALUES (?, 'intro')
        """, (user_id,))
        
        conn.commit()
        conn.close()
        
        # Return first question
        first_question = ONBOARDING_PHASES["intro"]["questions"][0]
        
        total_questions = sum(len(phase["questions"]) for phase in ONBOARDING_PHASES.values())
        
        return {
            "status": "started",
            "user_id": user_id,
            "current_phase": "intro",
            "next_question": first_question,
            "progress": {
                "phase": "intro",
                "phase_name": ONBOARDING_PHASES["intro"]["name"],
                "questions_answered": 0,
                "total_questions": total_questions,
                "percentage": 0
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to start onboarding: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/answer")
async def submit_answer(
    response: OnboardingResponse,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Submit an answer to an onboarding question"""
    user_id = session.user_id
    question_id = response.question_id
    answer = response.response
    metadata = response.metadata or {}
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get current onboarding state
        cursor.execute("""
            SELECT current_phase, responses, phase_progress
            FROM user_onboarding WHERE user_id = ?
        """, (user_id,))
        
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Onboarding not found. Please start onboarding first.")
        
        current_phase, responses_json, progress_json = row
        responses = json.loads(responses_json) if responses_json else {}
        progress = json.loads(progress_json) if progress_json else {}
        
        # Store the response
        responses[question_id] = {
            "response": answer,
            "metadata": metadata,
            "answered_at": datetime.now().isoformat()
        }
        
        # Update progress
        if current_phase not in progress:
            progress[current_phase] = {"answered": 0, "last_question": None}
        progress[current_phase]["answered"] += 1
        progress[current_phase]["last_question"] = question_id
        
        # Determine next question
        next_question = get_next_question(current_phase, question_id)
        
        if next_question is None:
            # Move to next phase or complete
            next_phase = get_next_phase(current_phase)
            if next_phase:
                cursor.execute("""
                    UPDATE user_onboarding 
                    SET current_phase = ?, responses = ?, phase_progress = ?,
                        last_interaction = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                """, (next_phase, json.dumps(responses), json.dumps(progress), user_id))
                conn.commit()
                conn.close()
                
                # Get first question of next phase
                first_q = ONBOARDING_PHASES[next_phase]["questions"][0]
                total_answered = sum(p.get("answered", 0) for p in progress.values())
                total_questions = sum(len(phase["questions"]) for phase in ONBOARDING_PHASES.values())
                
                return {
                    "status": "phase_complete",
                    "phase_completed": current_phase,
                    "next_phase": next_phase,
                    "next_phase_name": ONBOARDING_PHASES[next_phase]["name"],
                    "next_question": first_q,
                    "progress": {
                        "questions_answered": total_answered,
                        "total_questions": total_questions,
                        "percentage": round((total_answered / total_questions) * 100)
                    }
                }
            else:
                # Onboarding complete!
                cursor.execute("""
                    UPDATE user_onboarding 
                    SET is_complete = 1, completed_at = CURRENT_TIMESTAMP,
                        responses = ?, phase_progress = ?
                    WHERE user_id = ?
                """, (json.dumps(responses), json.dumps(progress), user_id))
                
                # Build compatibility profile
                build_compatibility_profile(user_id, responses, cursor)
                
                conn.commit()
                conn.close()
                
                return {
                    "status": "complete",
                    "message": "That's it! I've got a great understanding of you now. I'll keep learning more as we interact. Ready to get started?",
                    "profile_ready": True
                }
        else:
            # Continue in current phase
            cursor.execute("""
                UPDATE user_onboarding 
                SET responses = ?, phase_progress = ?, last_interaction = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (json.dumps(responses), json.dumps(progress), user_id))
            conn.commit()
            
            total_answered = sum(p.get("answered", 0) for p in progress.values())
            total_questions = sum(len(phase["questions"]) for phase in ONBOARDING_PHASES.values())
            
            conn.close()
            
            return {
                "status": "continue",
                "next_question": next_question,
                "progress": {
                    "questions_answered": total_answered,
                    "total_questions": total_questions,
                    "percentage": round((total_answered / total_questions) * 100)
                }
            }
        
    except Exception as e:
        logger.error(f"Failed to submit answer: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/progress")
async def get_progress(session: AuthenticatedSession = Depends(validate_session)):
    """Get onboarding progress for a user"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT current_phase, phase_progress, responses, is_complete
            FROM user_onboarding WHERE user_id = ?
        """, (user_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return {"status": "not_started"}
        
        current_phase, progress_json, responses_json, is_complete = row
        progress = json.loads(progress_json) if progress_json else {}
        responses = json.loads(responses_json) if responses_json else {}
        
        total_questions = sum(len(phase["questions"]) for phase in ONBOARDING_PHASES.values())
        questions_answered = len(responses)
        
        return {
            "status": "complete" if is_complete else "in_progress",
            "current_phase": current_phase,
            "phase_name": ONBOARDING_PHASES.get(current_phase, {}).get("name", current_phase),
            "questions_answered": questions_answered,
            "total_questions": total_questions,
            "progress_percentage": round((questions_answered / total_questions) * 100),
            "phase_progress": progress
        }
        
    except Exception as e:
        logger.error(f"Failed to get progress: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/profile")
async def get_user_profile(session: AuthenticatedSession = Depends(validate_session)):
    """Get user's compatibility profile"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT profile_data, profile_completeness, confidence_score, last_updated
            FROM user_compatibility_profiles WHERE user_id = ?
        """, (user_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return {
                "status": "not_found",
                "message": "No profile found. Please complete onboarding first."
            }
        
        profile_data, completeness, confidence, last_updated = row
        
        return {
            "status": "found",
            "user_id": user_id,
            "profile": json.loads(profile_data),
            "completeness": completeness,
            "confidence": confidence,
            "last_updated": last_updated
        }
        
    except Exception as e:
        logger.error(f"Failed to get profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_next_question(current_phase: str, current_question_id: str) -> Optional[Dict]:
    """Get the next question in the current phase"""
    if current_phase not in ONBOARDING_PHASES:
        return None
    
    phase_questions = ONBOARDING_PHASES[current_phase]["questions"]
    current_index = next((i for i, q in enumerate(phase_questions) if q["id"] == current_question_id), -1)
    
    if current_index >= 0 and current_index < len(phase_questions) - 1:
        return phase_questions[current_index + 1]
    
    return None


def get_next_question_in_phase(phase: str, last_question_id: Optional[str]) -> Optional[Dict]:
    """Get next question in a phase given the last answered question"""
    if phase not in ONBOARDING_PHASES:
        return None
    
    phase_questions = ONBOARDING_PHASES[phase]["questions"]
    
    if not last_question_id:
        return phase_questions[0] if phase_questions else None
    
    return get_next_question(phase, last_question_id)


def get_next_phase(current_phase: str) -> Optional[str]:
    """Get the next phase after current"""
    phases = list(ONBOARDING_PHASES.keys())
    try:
        current_index = phases.index(current_phase)
        if current_index < len(phases) - 1:
            return phases[current_index + 1]
    except ValueError:
        pass
    
    return None


def build_compatibility_profile(user_id: str, responses: Dict, cursor):
    """Build a compatibility profile from onboarding responses"""
    # Build a structured profile from responses
    profile_data = {
        "user_id": user_id,
        "responses": responses,
        "profile_version": "1.0",
        "created_from": "onboarding",
        "created_at": datetime.now().isoformat()
    }
    
    # Calculate initial completeness (60% from onboarding, will grow with usage)
    completeness = 0.6
    
    cursor.execute("""
        INSERT OR REPLACE INTO user_compatibility_profiles
        (user_id, profile_data, profile_completeness, created_at, last_updated)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (user_id, json.dumps(profile_data), completeness))
    
    logger.info(f"✅ Built compatibility profile for user {user_id}")

