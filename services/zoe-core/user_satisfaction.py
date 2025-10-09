"""
User Satisfaction Measurement System for Zoe
============================================

Implements user satisfaction tracking and feedback collection to enable
the reflection and self-learning system.
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import uuid

logger = logging.getLogger(__name__)

class FeedbackType(Enum):
    EXPLICIT = "explicit"  # Thumbs up/down, ratings
    IMPLICIT = "implicit"  # Response time, engagement patterns
    BEHAVIORAL = "behavioral"  # Task completion, follow-up questions

class SatisfactionLevel(Enum):
    VERY_DISSATISFIED = 1
    DISSATISFIED = 2
    NEUTRAL = 3
    SATISFIED = 4
    VERY_SATISFIED = 5

@dataclass
class UserFeedback:
    """Represents user feedback on an interaction"""
    id: str
    user_id: str
    interaction_id: str
    feedback_type: FeedbackType
    satisfaction_level: Optional[SatisfactionLevel]
    explicit_rating: Optional[int]  # 1-5 scale
    implicit_signals: Dict[str, Any]
    feedback_text: Optional[str]
    context: Dict[str, Any]
    timestamp: str
    processed: bool = False

@dataclass
class SatisfactionMetrics:
    """Aggregated satisfaction metrics for a user"""
    user_id: str
    total_interactions: int
    explicit_feedback_count: int
    implicit_feedback_count: int
    average_satisfaction: float
    satisfaction_trend: List[float]  # Last 30 days
    top_positive_factors: List[str]
    top_negative_factors: List[str]
    last_updated: str

class UserSatisfactionSystem:
    """System for measuring and tracking user satisfaction"""
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        self.db_path = db_path
        self.implicit_weights = {
            "response_time": 0.3,
            "task_completion": 0.4,
            "follow_up_questions": 0.2,
            "engagement_duration": 0.1
        }
        self.init_database()
    
    def init_database(self):
        """Initialize satisfaction database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # User feedback table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_feedback (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                interaction_id TEXT NOT NULL,
                feedback_type TEXT NOT NULL,
                satisfaction_level INTEGER,
                explicit_rating INTEGER,
                implicit_signals TEXT,  -- JSON
                feedback_text TEXT,
                context TEXT,  -- JSON
                processed BOOLEAN DEFAULT FALSE,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Satisfaction metrics table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS satisfaction_metrics (
                user_id TEXT PRIMARY KEY,
                total_interactions INTEGER DEFAULT 0,
                explicit_feedback_count INTEGER DEFAULT 0,
                implicit_feedback_count INTEGER DEFAULT 0,
                average_satisfaction REAL DEFAULT 0.0,
                satisfaction_trend TEXT,  -- JSON array
                top_positive_factors TEXT,  -- JSON array
                top_negative_factors TEXT,  -- JSON array
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Interaction tracking table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interaction_tracking (
                interaction_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                request_text TEXT,
                response_text TEXT,
                response_time REAL,
                task_completed BOOLEAN DEFAULT FALSE,
                follow_up_questions INTEGER DEFAULT 0,
                engagement_duration REAL,
                context TEXT,  -- JSON
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_user ON user_feedback(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_timestamp ON user_feedback(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_interaction_user ON interaction_tracking(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_interaction_timestamp ON interaction_tracking(timestamp)")
        
        conn.commit()
        conn.close()
        logger.info("User satisfaction database initialized")
    
    def record_interaction(self, interaction_id: str, user_id: str, 
                          request_text: str, response_text: str,
                          response_time: float, context: Dict[str, Any] = None) -> bool:
        """Record an interaction for implicit satisfaction analysis"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO interaction_tracking
                (interaction_id, user_id, request_text, response_text, response_time, context)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                interaction_id,
                user_id,
                request_text,
                response_text,
                response_time,
                json.dumps(context or {})
            ))
            
            conn.commit()
            conn.close()
            
            # Trigger implicit satisfaction analysis
            self._analyze_implicit_satisfaction(interaction_id, user_id)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to record interaction: {e}")
            return False
    
    def record_explicit_feedback(self, user_id: str, interaction_id: str,
                                rating: int, feedback_text: str = None,
                                context: Dict[str, Any] = None) -> str:
        """Record explicit user feedback (thumbs up/down, ratings)"""
        try:
            feedback_id = str(uuid.uuid4())
            
            # Convert rating to satisfaction level
            satisfaction_level = self._rating_to_satisfaction_level(rating)
            
            feedback = UserFeedback(
                id=feedback_id,
                user_id=user_id,
                interaction_id=interaction_id,
                feedback_type=FeedbackType.EXPLICIT,
                satisfaction_level=satisfaction_level,
                explicit_rating=rating,
                implicit_signals={},
                feedback_text=feedback_text,
                context=context or {},
                timestamp=datetime.now().isoformat()
            )
            
            # Save to database
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO user_feedback
                (id, user_id, interaction_id, feedback_type, satisfaction_level,
                 explicit_rating, implicit_signals, feedback_text, context, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                feedback.id,
                feedback.user_id,
                feedback.interaction_id,
                feedback.feedback_type.value,
                feedback.satisfaction_level.value if feedback.satisfaction_level else None,
                feedback.explicit_rating,
                json.dumps(feedback.implicit_signals),
                feedback.feedback_text,
                json.dumps(feedback.context),
                feedback.timestamp
            ))
            
            conn.commit()
            conn.close()
            
            # Update satisfaction metrics
            self._update_satisfaction_metrics(user_id)
            
            logger.info(f"Recorded explicit feedback: {feedback_id}")
            return feedback_id
            
        except Exception as e:
            logger.error(f"Failed to record explicit feedback: {e}")
            return None
    
    def _analyze_implicit_satisfaction(self, interaction_id: str, user_id: str):
        """Analyze implicit satisfaction signals from interaction"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get interaction data
            cursor.execute("""
                SELECT response_time, task_completed, follow_up_questions, engagement_duration
                FROM interaction_tracking
                WHERE interaction_id = ?
            """, (interaction_id,))
            
            row = cursor.fetchone()
            if not row:
                conn.close()
                return
            
            response_time, task_completed, follow_up_questions, engagement_duration = row
            
            # Calculate implicit satisfaction score
            implicit_score = self._calculate_implicit_satisfaction(
                response_time, task_completed, follow_up_questions, engagement_duration
            )
            
            # Create implicit feedback record
            feedback_id = str(uuid.uuid4())
            implicit_signals = {
                "response_time": response_time,
                "task_completed": task_completed,
                "follow_up_questions": follow_up_questions,
                "engagement_duration": engagement_duration,
                "calculated_score": implicit_score
            }
            
            cursor.execute("""
                INSERT INTO user_feedback
                (id, user_id, interaction_id, feedback_type, satisfaction_level,
                 explicit_rating, implicit_signals, context, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                feedback_id,
                user_id,
                interaction_id,
                FeedbackType.IMPLICIT.value,
                self._score_to_satisfaction_level(implicit_score).value,
                None,
                json.dumps(implicit_signals),
                json.dumps({"auto_generated": True}),
                datetime.now().isoformat()
            ))
            
            conn.commit()
            conn.close()
            
            # Update satisfaction metrics
            self._update_satisfaction_metrics(user_id)
            
        except Exception as e:
            logger.error(f"Failed to analyze implicit satisfaction: {e}")
    
    def _calculate_implicit_satisfaction(self, response_time: float, task_completed: bool,
                                       follow_up_questions: int, engagement_duration: float) -> float:
        """Calculate implicit satisfaction score from interaction signals"""
        score = 0.0
        
        # Response time factor (faster is better, up to 5 seconds)
        if response_time <= 2.0:
            score += self.implicit_weights["response_time"] * 1.0
        elif response_time <= 5.0:
            score += self.implicit_weights["response_time"] * 0.8
        elif response_time <= 10.0:
            score += self.implicit_weights["response_time"] * 0.6
        else:
            score += self.implicit_weights["response_time"] * 0.3
        
        # Task completion factor
        if task_completed:
            score += self.implicit_weights["task_completion"] * 1.0
        else:
            score += self.implicit_weights["task_completion"] * 0.2
        
        # Follow-up questions factor (some follow-up is good, too many might indicate confusion)
        if follow_up_questions == 0:
            score += self.implicit_weights["follow_up_questions"] * 0.7  # No follow-up might mean satisfied
        elif follow_up_questions <= 2:
            score += self.implicit_weights["follow_up_questions"] * 1.0  # Good engagement
        elif follow_up_questions <= 5:
            score += self.implicit_weights["follow_up_questions"] * 0.8  # Some confusion
        else:
            score += self.implicit_weights["follow_up_questions"] * 0.4  # High confusion
        
        # Engagement duration factor (longer engagement might indicate satisfaction)
        if engagement_duration and engagement_duration > 0:
            if engagement_duration >= 60:  # 1 minute or more
                score += self.implicit_weights["engagement_duration"] * 1.0
            elif engagement_duration >= 30:  # 30 seconds to 1 minute
                score += self.implicit_weights["engagement_duration"] * 0.8
            elif engagement_duration >= 10:  # 10-30 seconds
                score += self.implicit_weights["engagement_duration"] * 0.6
            else:
                score += self.implicit_weights["engagement_duration"] * 0.4
        
        return min(1.0, max(0.0, score))  # Clamp between 0 and 1
    
    def _rating_to_satisfaction_level(self, rating: int) -> SatisfactionLevel:
        """Convert 1-5 rating to satisfaction level"""
        if rating <= 1:
            return SatisfactionLevel.VERY_DISSATISFIED
        elif rating == 2:
            return SatisfactionLevel.DISSATISFIED
        elif rating == 3:
            return SatisfactionLevel.NEUTRAL
        elif rating == 4:
            return SatisfactionLevel.SATISFIED
        else:
            return SatisfactionLevel.VERY_SATISFIED
    
    def _score_to_satisfaction_level(self, score: float) -> SatisfactionLevel:
        """Convert 0-1 score to satisfaction level"""
        if score <= 0.2:
            return SatisfactionLevel.VERY_DISSATISFIED
        elif score <= 0.4:
            return SatisfactionLevel.DISSATISFIED
        elif score <= 0.6:
            return SatisfactionLevel.NEUTRAL
        elif score <= 0.8:
            return SatisfactionLevel.SATISFIED
        else:
            return SatisfactionLevel.VERY_SATISFIED
    
    def _update_satisfaction_metrics(self, user_id: str):
        """Update satisfaction metrics for a user"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get all feedback for user
            cursor.execute("""
                SELECT satisfaction_level, feedback_type, timestamp
                FROM user_feedback
                WHERE user_id = ?
                ORDER BY timestamp DESC
            """, (user_id,))
            
            feedback_data = cursor.fetchall()
            
            if not feedback_data:
                conn.close()
                return
            
            # Calculate metrics
            total_interactions = len(feedback_data)
            explicit_count = len([f for f in feedback_data if f[1] == FeedbackType.EXPLICIT.value])
            implicit_count = len([f for f in feedback_data if f[1] == FeedbackType.IMPLICIT.value])
            
            # Calculate average satisfaction
            satisfaction_levels = [f[0] for f in feedback_data if f[0] is not None]
            avg_satisfaction = sum(satisfaction_levels) / len(satisfaction_levels) if satisfaction_levels else 0.0
            
            # Calculate trend (last 30 days)
            thirty_days_ago = datetime.now() - timedelta(days=30)
            recent_feedback = [
                f for f in feedback_data 
                if datetime.fromisoformat(f[2]) >= thirty_days_ago
            ]
            
            # Group by day and calculate daily averages
            daily_satisfaction = {}
            for feedback in recent_feedback:
                day = datetime.fromisoformat(feedback[2]).date()
                if day not in daily_satisfaction:
                    daily_satisfaction[day] = []
                if feedback[0] is not None:
                    daily_satisfaction[day].append(feedback[0])
            
            trend = []
            for day in sorted(daily_satisfaction.keys()):
                day_avg = sum(daily_satisfaction[day]) / len(daily_satisfaction[day])
                trend.append(day_avg)
            
            # Analyze positive and negative factors (simplified)
            positive_factors = self._analyze_positive_factors(feedback_data)
            negative_factors = self._analyze_negative_factors(feedback_data)
            
            # Update or insert metrics
            cursor.execute("""
                INSERT OR REPLACE INTO satisfaction_metrics
                (user_id, total_interactions, explicit_feedback_count, implicit_feedback_count,
                 average_satisfaction, satisfaction_trend, top_positive_factors, top_negative_factors,
                 last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                total_interactions,
                explicit_count,
                implicit_count,
                avg_satisfaction,
                json.dumps(trend),
                json.dumps(positive_factors),
                json.dumps(negative_factors),
                datetime.now().isoformat()
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to update satisfaction metrics: {e}")
    
    def _analyze_positive_factors(self, feedback_data: List[Tuple]) -> List[str]:
        """Analyze positive factors from feedback data"""
        # Simplified analysis - in real implementation would use more sophisticated NLP
        factors = []
        
        high_satisfaction = [f for f in feedback_data if f[0] and f[0] >= 4]
        
        if len(high_satisfaction) > len(feedback_data) * 0.3:  # More than 30% high satisfaction
            factors.append("Consistent high satisfaction")
        
        if len([f for f in feedback_data if f[1] == FeedbackType.EXPLICIT.value]) > 0:
            factors.append("Active user feedback")
        
        return factors[:5]  # Top 5 factors
    
    def _analyze_negative_factors(self, feedback_data: List[Tuple]) -> List[str]:
        """Analyze negative factors from feedback data"""
        # Simplified analysis
        factors = []
        
        low_satisfaction = [f for f in feedback_data if f[0] and f[0] <= 2]
        
        if len(low_satisfaction) > len(feedback_data) * 0.2:  # More than 20% low satisfaction
            factors.append("Frequent low satisfaction")
        
        return factors[:5]  # Top 5 factors
    
    def get_satisfaction_metrics(self, user_id: str) -> Optional[SatisfactionMetrics]:
        """Get satisfaction metrics for a user"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT total_interactions, explicit_feedback_count, implicit_feedback_count,
                       average_satisfaction, satisfaction_trend, top_positive_factors,
                       top_negative_factors, last_updated
                FROM satisfaction_metrics
                WHERE user_id = ?
            """, (user_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return None
            
            return SatisfactionMetrics(
                user_id=user_id,
                total_interactions=row[0],
                explicit_feedback_count=row[1],
                implicit_feedback_count=row[2],
                average_satisfaction=row[3],
                satisfaction_trend=json.loads(row[4]) if row[4] else [],
                top_positive_factors=json.loads(row[5]) if row[5] else [],
                top_negative_factors=json.loads(row[6]) if row[6] else [],
                last_updated=row[7]
            )
            
        except Exception as e:
            logger.error(f"Failed to get satisfaction metrics: {e}")
            return None
    
    def get_user_feedback_history(self, user_id: str, limit: int = 20) -> List[UserFeedback]:
        """Get feedback history for a user"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, user_id, interaction_id, feedback_type, satisfaction_level,
                       explicit_rating, implicit_signals, feedback_text, context, timestamp
                FROM user_feedback
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (user_id, limit))
            
            feedback_list = []
            for row in cursor.fetchall():
                feedback = UserFeedback(
                    id=row[0],
                    user_id=row[1],
                    interaction_id=row[2],
                    feedback_type=FeedbackType(row[3]),
                    satisfaction_level=SatisfactionLevel(row[4]) if row[4] else None,
                    explicit_rating=row[5],
                    implicit_signals=json.loads(row[6]) if row[6] else {},
                    feedback_text=row[7],
                    context=json.loads(row[8]) if row[8] else {},
                    timestamp=row[9]
                )
                feedback_list.append(feedback)
            
            conn.close()
            return feedback_list
            
        except Exception as e:
            logger.error(f"Failed to get feedback history: {e}")
            return []
    
    def get_system_satisfaction_stats(self) -> Dict[str, Any]:
        """Get system-wide satisfaction statistics"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get overall stats
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_feedback,
                    AVG(satisfaction_level) as avg_satisfaction,
                    COUNT(CASE WHEN feedback_type = 'explicit' THEN 1 END) as explicit_count,
                    COUNT(CASE WHEN feedback_type = 'implicit' THEN 1 END) as implicit_count
                FROM user_feedback
            """)
            
            stats = cursor.fetchone()
            
            # Get satisfaction distribution
            cursor.execute("""
                SELECT satisfaction_level, COUNT(*) as count
                FROM user_feedback
                WHERE satisfaction_level IS NOT NULL
                GROUP BY satisfaction_level
                ORDER BY satisfaction_level
            """)
            
            distribution = {row[0]: row[1] for row in cursor.fetchall()}
            
            conn.close()
            
            return {
                "total_feedback": stats[0],
                "average_satisfaction": stats[1] or 0.0,
                "explicit_feedback_count": stats[2],
                "implicit_feedback_count": stats[3],
                "satisfaction_distribution": distribution,
                "feedback_coverage": (stats[2] / stats[0] * 100) if stats[0] > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Failed to get system satisfaction stats: {e}")
            return {}

# Global instance
satisfaction_system = UserSatisfactionSystem()

