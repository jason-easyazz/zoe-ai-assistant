"""
User Profile Schema Tests
=========================
Tests for the compatibility profile system
"""

import pytest
import sys
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
from datetime import datetime

sys.path.insert(0, str(PROJECT_ROOT / "services/zoe-core"))

from user_profile_schema import (
    UserCompatibilityProfile,
    PersonalityTraits,
    ValuesPriority,
    InterestCategory,
    LifeGoal,
    CommunicationStyle,
    SocialEnergy,
    CompatibilityAnalysis,
    calculate_values_alignment,
    calculate_personality_compatibility,
    calculate_interest_overlap
)


class TestUserCompatibilityProfile:
    """Tests for user profile creation and management"""
    
    def test_create_minimal_profile(self):
        """Test creating profile with minimal data"""
        profile = UserCompatibilityProfile(user_id="test_user")
        
        assert profile.user_id == "test_user"
        assert profile.profile_completeness == 0.0
        assert profile.interaction_count == 0
    
    def test_create_rich_profile(self):
        """Test creating profile with comprehensive data"""
        profile = UserCompatibilityProfile(
            user_id="test_user",
            display_name="Test User",
            age_range="26-35",
            communication_styles=[CommunicationStyle.CASUAL, CommunicationStyle.HUMOROUS],
            social_energy=SocialEnergy.AMBIVERT,
            personality_traits=PersonalityTraits(
                openness=0.8,
                extraversion=0.6
            ),
            values_priority=ValuesPriority(
                personal_growth=9,
                family=8,
                helping_others=7
            )
        )
        
        assert profile.display_name == "Test User"
        assert CommunicationStyle.CASUAL in profile.communication_styles
        assert profile.personality_traits.openness == 0.8
    
    def test_calculate_profile_completeness(self):
        """Test completeness calculation"""
        # Empty profile
        profile1 = UserCompatibilityProfile(user_id="user1")
        assert profile1.calculate_completeness() == 0.0
        
        # Partially filled profile
        profile2 = UserCompatibilityProfile(
            user_id="user2",
            display_name="User 2",
            communication_styles=[CommunicationStyle.DIRECT],
            social_energy=SocialEnergy.EXTROVERTED
        )
        completeness = profile2.calculate_completeness()
        assert 0.0 < completeness < 1.0
    
    def test_get_top_values(self):
        """Test extracting top values"""
        values = ValuesPriority(
            personal_growth=10,
            family=8,
            career_success=6
        )
        
        profile = UserCompatibilityProfile(
            user_id="test",
            values_priority=values
        )
        
        top_values = profile.get_top_values(n=2)
        assert len(top_values) <= 2
        assert top_values[0][0] == "personal_growth"
        assert top_values[0][1] == 10
    
    def test_personality_summary(self):
        """Test personality summary generation"""
        traits = PersonalityTraits(
            openness=0.9,  # Very open
            extraversion=0.2,  # Introverted
            conscientiousness=0.5  # Balanced
        )
        
        profile = UserCompatibilityProfile(
            user_id="test",
            personality_traits=traits
        )
        
        summary = profile.get_personality_summary()
        
        assert "openness" in summary
        assert "open" in summary["openness"].lower()
        assert "introverted" in summary["extraversion"].lower()


class TestPersonalityTraits:
    """Tests for personality traits"""
    
    def test_default_traits(self):
        """Test default trait values"""
        traits = PersonalityTraits()
        
        assert traits.openness == 0.5
        assert traits.extraversion == 0.5
        assert 0.0 <= traits.empathy <= 1.0
    
    def test_trait_bounds(self):
        """Test that traits stay within bounds"""
        with pytest.raises(Exception):  # Pydantic validation error
            PersonalityTraits(openness=1.5)  # Above max
        
        with pytest.raises(Exception):
            PersonalityTraits(extraversion=-0.1)  # Below min


class TestValuesPriority:
    """Tests for values priority"""
    
    def test_default_values(self):
        """Test default value priorities"""
        values = ValuesPriority()
        
        assert values.family == 5
        assert values.career_success == 5
    
    def test_to_dict(self):
        """Test conversion to dictionary"""
        values = ValuesPriority(family=10, career_success=8)
        values_dict = values.to_dict()
        
        assert isinstance(values_dict, dict)
        assert values_dict["family"] == 10
        assert values_dict["career_success"] == 8


class TestInterestCategory:
    """Tests for interest categories"""
    
    def test_create_interest(self):
        """Test creating an interest"""
        interest = InterestCategory(
            name="Rock Climbing",
            category="sports",
            intensity=0.8,
            skill_level="intermediate",
            frequency="weekly"
        )
        
        assert interest.name == "Rock Climbing"
        assert interest.intensity == 0.8
        assert interest.frequency == "weekly"


class TestLifeGoal:
    """Tests for life goals"""
    
    def test_create_goal(self):
        """Test creating a life goal"""
        goal = LifeGoal(
            goal="Start my own business",
            category="career",
            timeframe="medium_term",
            priority=9,
            why_important="Financial independence and creative freedom"
        )
        
        assert goal.category == "career"
        assert goal.priority == 9
        assert "independence" in goal.why_important


class TestCompatibilityAnalysis:
    """Tests for compatibility analysis"""
    
    def test_create_analysis(self):
        """Test creating compatibility analysis"""
        analysis = CompatibilityAnalysis(
            user1_id="user1",
            user2_id="user2",
            overall_compatibility=0.75,
            values_alignment=0.8,
            personality_compatibility=0.7,
            interest_overlap=0.6
        )
        
        assert analysis.overall_compatibility == 0.75
        assert analysis.user1_id == "user1"
    
    def test_compatibility_level_labels(self):
        """Test compatibility level descriptions"""
        excellent = CompatibilityAnalysis(user1_id="1", user2_id="2", overall_compatibility=0.95)
        good = CompatibilityAnalysis(user1_id="1", user2_id="2", overall_compatibility=0.70)
        low = CompatibilityAnalysis(user1_id="1", user2_id="2", overall_compatibility=0.25)
        
        assert "Excellent" in excellent.get_compatibility_level()
        # 0.70 falls into "Good Potential" range
        assert good.get_compatibility_level() in ["Very Compatible", "Good Potential"]
        assert "Limited" in low.get_compatibility_level()
    
    def test_dimension_breakdown(self):
        """Test dimension breakdown"""
        analysis = CompatibilityAnalysis(
            user1_id="1",
            user2_id="2",
            overall_compatibility=0.75,
            values_alignment=0.9,
            personality_compatibility=0.6,
            interest_overlap=0.4
        )
        
        breakdown = analysis.get_dimension_breakdown()
        
        assert "Values Alignment" in breakdown
        assert breakdown["Values Alignment"][0] == 0.9
        assert breakdown["Values Alignment"][1] == "Excellent"


class TestCompatibilityCalculations:
    """Tests for compatibility calculation functions"""
    
    def test_values_alignment_calculation(self):
        """Test values alignment calculation"""
        profile1 = UserCompatibilityProfile(
            user_id="user1",
            values_priority=ValuesPriority(family=10, career_success=5, personal_growth=8)
        )
        
        profile2 = UserCompatibilityProfile(
            user_id="user2",
            values_priority=ValuesPriority(family=9, career_success=6, personal_growth=8)
        )
        
        alignment = calculate_values_alignment(profile1, profile2)
        
        assert 0.0 <= alignment <= 1.0
        assert alignment > 0.7  # Should be high alignment
    
    def test_personality_compatibility_calculation(self):
        """Test personality compatibility calculation"""
        profile1 = UserCompatibilityProfile(
            user_id="user1",
            personality_traits=PersonalityTraits(
                openness=0.8,
                extraversion=0.6,
                empathy=0.7
            )
        )
        
        profile2 = UserCompatibilityProfile(
            user_id="user2",
            personality_traits=PersonalityTraits(
                openness=0.75,
                extraversion=0.5,
                empathy=0.8
            )
        )
        
        compatibility = calculate_personality_compatibility(profile1, profile2)
        
        assert 0.0 <= compatibility <= 1.0
    
    def test_interest_overlap_calculation(self):
        """Test interest overlap calculation"""
        profile1 = UserCompatibilityProfile(
            user_id="user1",
            interests=[
                InterestCategory(name="hiking", category="outdoors", intensity=0.8),
                InterestCategory(name="reading", category="intellectual", intensity=0.7),
                InterestCategory(name="cooking", category="creative", intensity=0.6)
            ]
        )
        
        profile2 = UserCompatibilityProfile(
            user_id="user2",
            interests=[
                InterestCategory(name="hiking", category="outdoors", intensity=0.9),
                InterestCategory(name="photography", category="creative", intensity=0.7),
                InterestCategory(name="reading", category="intellectual", intensity=0.6)
            ]
        )
        
        overlap = calculate_interest_overlap(profile1, profile2)
        
        assert 0.0 <= overlap <= 1.0
        assert overlap > 0.0  # Should have some overlap (hiking, reading)
    
    def test_empty_profiles_return_neutral(self):
        """Test that empty profiles return neutral scores"""
        profile1 = UserCompatibilityProfile(user_id="user1")
        profile2 = UserCompatibilityProfile(user_id="user2")
        
        # Should return neutral/default scores
        values_score = calculate_values_alignment(profile1, profile2)
        personality_score = calculate_personality_compatibility(profile1, profile2)
        interest_score = calculate_interest_overlap(profile1, profile2)
        
        assert values_score == 0.5  # Neutral
        assert personality_score == 0.5  # Neutral
        assert interest_score == 0.0  # No interests


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

