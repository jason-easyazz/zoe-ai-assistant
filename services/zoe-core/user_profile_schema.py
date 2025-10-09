"""
User Profile Schema for Zoe's Compatibility Matching System
==========================================================
Comprehensive profile structure that enables two Zoes to analyze user compatibility
for friend-making and match-making purposes.

This schema supports:
- Deep personality understanding
- Values and beliefs mapping
- Interest and hobby tracking
- Life goals and aspirations
- Communication style preferences
- Relationship compatibility analysis
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any
from datetime import datetime
from enum import Enum


class CommunicationStyle(str, Enum):
    """How users prefer to communicate"""
    DIRECT = "direct"  # Gets to the point quickly
    DETAILED = "detailed"  # Likes thorough explanations
    CASUAL = "casual"  # Informal, friendly tone
    FORMAL = "formal"  # Professional, structured
    HUMOROUS = "humorous"  # Appreciates wit and jokes
    EMPATHETIC = "empathetic"  # Values emotional connection


class SocialEnergy(str, Enum):
    """Social energy and interaction preferences"""
    HIGHLY_EXTROVERTED = "highly_extroverted"  # Energized by constant social interaction
    EXTROVERTED = "extroverted"  # Enjoys socializing regularly
    AMBIVERT = "ambivert"  # Balanced between social and alone time
    INTROVERTED = "introverted"  # Prefers smaller groups or one-on-one
    HIGHLY_INTROVERTED = "highly_introverted"  # Needs significant alone time


class ValuesPriority(BaseModel):
    """Core values ranked by importance (0-10 scale)"""
    family: int = Field(default=5, ge=0, le=10)
    career_success: int = Field(default=5, ge=0, le=10)
    personal_growth: int = Field(default=5, ge=0, le=10)
    health_wellness: int = Field(default=5, ge=0, le=10)
    creativity: int = Field(default=5, ge=0, le=10)
    financial_security: int = Field(default=5, ge=0, le=10)
    adventure: int = Field(default=5, ge=0, le=10)
    stability: int = Field(default=5, ge=0, le=10)
    helping_others: int = Field(default=5, ge=0, le=10)
    knowledge_learning: int = Field(default=5, ge=0, le=10)
    spirituality: int = Field(default=5, ge=0, le=10)
    environmental_consciousness: int = Field(default=5, ge=0, le=10)
    
    def to_dict(self) -> Dict[str, int]:
        """Convert to dictionary for easier processing"""
        return {
            "family": self.family,
            "career_success": self.career_success,
            "personal_growth": self.personal_growth,
            "health_wellness": self.health_wellness,
            "creativity": self.creativity,
            "financial_security": self.financial_security,
            "adventure": self.adventure,
            "stability": self.stability,
            "helping_others": self.helping_others,
            "knowledge_learning": self.knowledge_learning,
            "spirituality": self.spirituality,
            "environmental_consciousness": self.environmental_consciousness
        }


class PersonalityTraits(BaseModel):
    """Big Five personality traits + additional dimensions (0.0-1.0 scale)"""
    # Big Five
    openness: float = Field(default=0.5, ge=0.0, le=1.0)  # Open to new experiences vs traditional
    conscientiousness: float = Field(default=0.5, ge=0.0, le=1.0)  # Organized vs spontaneous
    extraversion: float = Field(default=0.5, ge=0.0, le=1.0)  # Outgoing vs reserved
    agreeableness: float = Field(default=0.5, ge=0.0, le=1.0)  # Compassionate vs challenging
    neuroticism: float = Field(default=0.5, ge=0.0, le=1.0)  # Sensitive vs resilient
    
    # Additional traits for richer profiles
    optimism: float = Field(default=0.5, ge=0.0, le=1.0)
    assertiveness: float = Field(default=0.5, ge=0.0, le=1.0)
    creativity: float = Field(default=0.5, ge=0.0, le=1.0)
    analytical: float = Field(default=0.5, ge=0.0, le=1.0)
    playfulness: float = Field(default=0.5, ge=0.0, le=1.0)
    empathy: float = Field(default=0.5, ge=0.0, le=1.0)
    independence: float = Field(default=0.5, ge=0.0, le=1.0)


class InterestCategory(BaseModel):
    """Interest with intensity and engagement level"""
    name: str
    category: str  # sports, arts, technology, outdoors, social, intellectual, creative, etc.
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)  # How passionate (0-1)
    skill_level: Optional[str] = None  # beginner, intermediate, advanced, expert
    frequency: Optional[str] = None  # daily, weekly, monthly, occasionally
    notes: Optional[str] = None


class LifeGoal(BaseModel):
    """User's goals and aspirations"""
    goal: str
    category: str  # career, relationship, health, financial, personal, creative, social
    timeframe: str  # short_term (< 1 year), medium_term (1-3 years), long_term (3+ years)
    priority: int = Field(default=5, ge=1, le=10)
    progress_notes: Optional[str] = None
    why_important: Optional[str] = None  # Helps understand values


class RelationshipPreference(BaseModel):
    """What user seeks in relationships (friendship or romantic)"""
    relationship_type: str  # friendship, romantic, professional, mentorship, activity_partner
    preferred_communication_frequency: str  # daily, few_times_week, weekly, occasional
    preferred_activity_style: str  # active_outdoor, cultural_events, quiet_hangouts, varied, digital
    deal_breakers: List[str] = []  # Absolute incompatibilities
    must_haves: List[str] = []  # Essential qualities
    nice_to_haves: List[str] = []  # Bonus qualities
    ideal_description: Optional[str] = None  # Free-form description


class UserCompatibilityProfile(BaseModel):
    """Complete user profile for compatibility analysis"""
    
    # Core Identity
    user_id: str
    display_name: Optional[str] = None
    age_range: Optional[str] = None  # 18-25, 26-35, 36-45, 46-55, 56-65, 66+
    location: Optional[str] = None
    timezone: Optional[str] = None
    
    # Communication & Personality
    communication_styles: List[CommunicationStyle] = []
    social_energy: Optional[SocialEnergy] = None
    personality_traits: Optional[PersonalityTraits] = None
    
    # Values & Beliefs
    values_priority: Optional[ValuesPriority] = None
    core_beliefs: List[str] = []  # Free text important beliefs
    deal_breakers: List[str] = []  # Absolute incompatibilities
    
    # Interests & Hobbies
    interests: List[InterestCategory] = []
    current_obsessions: List[str] = []  # What they're currently into
    
    # Life Situation
    life_goals: List[LifeGoal] = []
    current_life_phase: Optional[str] = None  # student, early_career, established, transitioning, retired
    daily_routine_type: Optional[str] = None  # early_bird, night_owl, flexible, structured, chaotic
    
    # Relationship Preferences
    relationship_preferences: Dict[str, RelationshipPreference] = {}
    
    # Learning & Growth
    learning_style: Optional[str] = None  # visual, auditory, kinesthetic, reading_writing, multimodal
    growth_areas: List[str] = []  # What they're working on
    
    # Metadata
    profile_completeness: float = Field(default=0.0, ge=0.0, le=1.0)  # How filled out (0-1)
    last_updated: datetime = Field(default_factory=datetime.now)
    interaction_count: int = 0  # How many conversations
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)  # How confident we are about this profile
    
    # Raw insights (for Zoe's internal tracking)
    observed_patterns: List[str] = []  # Patterns noticed in behavior
    conversation_excerpts: List[Dict[str, Any]] = []  # Context for conclusions (max 50)
    
    def calculate_completeness(self) -> float:
        """Calculate profile completeness score"""
        total_fields = 15  # Key sections
        filled_fields = 0
        
        if self.display_name:
            filled_fields += 1
        if self.communication_styles:
            filled_fields += 1
        if self.social_energy:
            filled_fields += 1
        if self.personality_traits:
            filled_fields += 1
        if self.values_priority:
            filled_fields += 1
        if self.core_beliefs:
            filled_fields += 1
        if self.interests:
            filled_fields += 1
        if self.current_obsessions:
            filled_fields += 1
        if self.life_goals:
            filled_fields += 1
        if self.current_life_phase:
            filled_fields += 1
        if self.relationship_preferences:
            filled_fields += 1
        if self.learning_style:
            filled_fields += 1
        if self.growth_areas:
            filled_fields += 1
        if self.age_range:
            filled_fields += 1
        if self.daily_routine_type:
            filled_fields += 1
        
        return filled_fields / total_fields
    
    def get_top_values(self, n: int = 3) -> List[tuple[str, int]]:
        """Get top N values by priority"""
        if not self.values_priority:
            return []
        
        values_dict = self.values_priority.to_dict()
        sorted_values = sorted(values_dict.items(), key=lambda x: x[1], reverse=True)
        return sorted_values[:n]
    
    def get_personality_summary(self) -> Dict[str, str]:
        """Get human-readable personality summary"""
        if not self.personality_traits:
            return {}
        
        traits = self.personality_traits
        
        def score_to_label(score: float, low_label: str, high_label: str) -> str:
            if score < 0.3:
                return f"Very {low_label}"
            elif score < 0.45:
                return f"Somewhat {low_label}"
            elif score < 0.55:
                return "Balanced"
            elif score < 0.7:
                return f"Somewhat {high_label}"
            else:
                return f"Very {high_label}"
        
        return {
            "openness": score_to_label(traits.openness, "traditional", "open to new experiences"),
            "conscientiousness": score_to_label(traits.conscientiousness, "spontaneous", "organized"),
            "extraversion": score_to_label(traits.extraversion, "introverted", "extraverted"),
            "agreeableness": score_to_label(traits.agreeableness, "assertive", "compassionate"),
            "emotional_stability": score_to_label(1.0 - traits.neuroticism, "sensitive", "resilient"),
            "optimism": score_to_label(traits.optimism, "realistic", "optimistic"),
            "creativity": score_to_label(traits.creativity, "practical", "creative")
        }


class CompatibilityAnalysis(BaseModel):
    """Result of comparing two user profiles"""
    user1_id: str
    user2_id: str
    overall_compatibility: float = Field(ge=0.0, le=1.0)  # 0-1 overall score
    
    # Dimension scores (0-1 each)
    values_alignment: float = Field(default=0.0, ge=0.0, le=1.0)
    personality_compatibility: float = Field(default=0.0, ge=0.0, le=1.0)
    interest_overlap: float = Field(default=0.0, ge=0.0, le=1.0)
    communication_compatibility: float = Field(default=0.0, ge=0.0, le=1.0)
    lifestyle_compatibility: float = Field(default=0.0, ge=0.0, le=1.0)
    goal_alignment: float = Field(default=0.0, ge=0.0, le=1.0)
    
    # Detailed insights
    strengths: List[str] = []  # What would work well
    challenges: List[str] = []  # Potential friction points
    complementary_traits: List[str] = []  # Where differences are beneficial
    shared_interests: List[str] = []
    shared_values: List[str] = []
    
    # Recommendations
    suggested_activities: List[str] = []
    conversation_starters: List[str] = []
    compatibility_summary: str = ""
    compatibility_explanation: str = ""  # Detailed reasoning
    
    # Metadata
    analyzed_at: datetime = Field(default_factory=datetime.now)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)  # Confidence in this analysis
    
    def get_compatibility_level(self) -> str:
        """Get human-readable compatibility level"""
        score = self.overall_compatibility
        if score >= 0.9:
            return "Excellent Match"
        elif score >= 0.75:
            return "Very Compatible"
        elif score >= 0.6:
            return "Good Potential"
        elif score >= 0.45:
            return "Moderate Compatibility"
        elif score >= 0.3:
            return "Some Common Ground"
        else:
            return "Limited Compatibility"
    
    def get_dimension_breakdown(self) -> Dict[str, tuple[float, str]]:
        """Get all dimensions with scores and labels"""
        def score_to_label(score: float) -> str:
            if score >= 0.8:
                return "Excellent"
            elif score >= 0.6:
                return "Good"
            elif score >= 0.4:
                return "Fair"
            else:
                return "Low"
        
        return {
            "Values Alignment": (self.values_alignment, score_to_label(self.values_alignment)),
            "Personality Compatibility": (self.personality_compatibility, score_to_label(self.personality_compatibility)),
            "Shared Interests": (self.interest_overlap, score_to_label(self.interest_overlap)),
            "Communication Style": (self.communication_compatibility, score_to_label(self.communication_compatibility)),
            "Lifestyle Compatibility": (self.lifestyle_compatibility, score_to_label(self.lifestyle_compatibility)),
            "Goal Alignment": (self.goal_alignment, score_to_label(self.goal_alignment))
        }


# Compatibility calculation utilities

def calculate_values_alignment(profile1: UserCompatibilityProfile, profile2: UserCompatibilityProfile) -> float:
    """Calculate alignment on core values (0-1 score)"""
    if not profile1.values_priority or not profile2.values_priority:
        return 0.5  # Neutral if data missing
    
    values1 = profile1.values_priority.to_dict()
    values2 = profile2.values_priority.to_dict()
    
    # Calculate correlation between value priorities
    total_diff = 0
    count = 0
    for key in values1.keys():
        if key in values2:
            diff = abs(values1[key] - values2[key])
            total_diff += diff
            count += 1
    
    if count == 0:
        return 0.5
    
    avg_diff = total_diff / count
    max_diff = 10  # Maximum possible difference
    
    # Convert to similarity score (0-1)
    similarity = 1.0 - (avg_diff / max_diff)
    return max(0.0, min(1.0, similarity))


def calculate_personality_compatibility(profile1: UserCompatibilityProfile, profile2: UserCompatibilityProfile) -> float:
    """Calculate personality compatibility (complementary + similar traits)"""
    if not profile1.personality_traits or not profile2.personality_traits:
        return 0.5
    
    traits1 = profile1.personality_traits
    traits2 = profile2.personality_traits
    
    # Some traits work best when similar, others when complementary
    similarity_traits = ["openness", "optimism", "empathy"]
    complementary_traits = ["assertiveness", "independence"]
    
    scores = []
    
    # Calculate similarity for traits that should match
    for trait in similarity_traits:
        if hasattr(traits1, trait) and hasattr(traits2, trait):
            val1 = getattr(traits1, trait)
            val2 = getattr(traits2, trait)
            similarity = 1.0 - abs(val1 - val2)
            scores.append(similarity)
    
    # Calculate complementarity for traits that can differ
    for trait in complementary_traits:
        if hasattr(traits1, trait) and hasattr(traits2, trait):
            val1 = getattr(traits1, trait)
            val2 = getattr(traits2, trait)
            # Moderate difference is good (not too similar, not too different)
            diff = abs(val1 - val2)
            complementarity = 1.0 - abs(diff - 0.3) / 0.7  # Optimal diff around 0.3
            scores.append(max(0.0, complementarity))
    
    if not scores:
        return 0.5
    
    return sum(scores) / len(scores)


def calculate_interest_overlap(profile1: UserCompatibilityProfile, profile2: UserCompatibilityProfile) -> float:
    """Calculate overlap in interests and hobbies"""
    if not profile1.interests or not profile2.interests:
        return 0.0
    
    interests1 = {i.name.lower(): i.intensity for i in profile1.interests}
    interests2 = {i.name.lower(): i.intensity for i in profile2.interests}
    
    shared_interests = set(interests1.keys()) & set(interests2.keys())
    all_interests = set(interests1.keys()) | set(interests2.keys())
    
    if not all_interests:
        return 0.0
    
    # Basic overlap
    overlap_ratio = len(shared_interests) / len(all_interests)
    
    # Weight by intensity of shared interests
    intensity_bonus = 0
    for interest in shared_interests:
        avg_intensity = (interests1[interest] + interests2[interest]) / 2
        intensity_bonus += avg_intensity
    
    if shared_interests:
        intensity_bonus /= len(shared_interests)
    
    # Combined score
    score = (overlap_ratio * 0.6) + (intensity_bonus * 0.4)
    return min(1.0, score)

