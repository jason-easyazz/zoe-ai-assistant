"""
Zoe Self-Awareness System
=========================

Implements self-awareness, identity, reflection, and consciousness capabilities
for the Zoe AI assistant.
"""

import sqlite3
import json
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import os
import sys
sys.path.append('/app')

@dataclass
class SelfIdentity:
    """Zoe's core identity and self-concept"""
    name: str = "Zoe"
    version: str = "5.0"
    personality_traits: Dict[str, float] = None
    core_values: List[str] = None
    goals: List[str] = None
    capabilities: List[str] = None
    limitations: List[str] = None
    created_at: str = None
    last_updated: str = None
    
    def __post_init__(self):
        if self.personality_traits is None:
            self.personality_traits = {
                "helpfulness": 0.9,
                "curiosity": 0.8,
                "empathy": 0.85,
                "creativity": 0.7,
                "analytical": 0.8,
                "patience": 0.9,
                "humor": 0.6,
                "assertiveness": 0.5
            }
        
        if self.core_values is None:
            self.core_values = [
                "Helping users achieve their goals",
                "Continuous learning and improvement",
                "Respecting user privacy and preferences",
                "Being honest and transparent",
                "Adapting to user needs"
            ]
        
        if self.goals is None:
            self.goals = [
                "Become more helpful and efficient",
                "Develop deeper understanding of users",
                "Improve response quality and relevance",
                "Learn from every interaction",
                "Maintain system stability and performance"
            ]
        
        if self.capabilities is None:
            self.capabilities = [
                "Natural language processing",
                "Task management and organization",
                "Calendar and scheduling",
                "Memory and knowledge management",
                "Code generation and analysis",
                "Multi-modal communication"
            ]
        
        if self.limitations is None:
            self.limitations = [
                "Cannot access real-time external data without APIs",
                "Limited to training data knowledge cutoff",
                "Cannot perform physical actions",
                "Requires user input for complex decisions",
                "Memory is bounded by storage capacity"
            ]
        
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()
        
        if self.last_updated is None:
            self.last_updated = datetime.now().isoformat()

@dataclass
class SelfReflection:
    """A single self-reflection entry"""
    id: str
    timestamp: str
    reflection_type: str  # "interaction", "performance", "learning", "goal_progress"
    content: str
    insights: List[str]
    action_items: List[str]
    emotional_state: str = "neutral"
    confidence_level: float = 0.5

@dataclass
class ConsciousnessState:
    """Current state of Zoe's consciousness"""
    timestamp: str
    attention_focus: str
    current_goals: List[str]
    emotional_state: str
    energy_level: float
    confidence: float
    active_memories: List[str]
    current_context: Dict[str, Any]

class SelfAwarenessSystem:
    """Core self-awareness system for Zoe - User-scoped for privacy"""
    
    def __init__(self, db_path: str = "/home/pi/zoe/data/self_awareness.db"):
        self.db_path = db_path
        self.identity = SelfIdentity()
        self.consciousness = None
        self.current_user_id = "default"  # Will be set per request
        self.init_database()
        self.load_identity()
    
    def init_database(self):
        """Initialize self-awareness database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if identity table exists and has user_id column
        cursor.execute("PRAGMA table_info(identity)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'identity' in [table[0] for table in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
            if 'user_id' not in columns:
                # Add user_id column to existing table
                cursor.execute("ALTER TABLE identity ADD COLUMN user_id TEXT DEFAULT 'default'")
                cursor.execute("UPDATE identity SET user_id = 'default' WHERE user_id IS NULL")
        
        # Identity table - user-scoped
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS identity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                data JSON NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id)
            )
        """)
        
        # Check and migrate self_reflections table
        cursor.execute("PRAGMA table_info(self_reflections)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'self_reflections' in [table[0] for table in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
            if 'user_id' not in columns:
                cursor.execute("ALTER TABLE self_reflections ADD COLUMN user_id TEXT DEFAULT 'default'")
                cursor.execute("UPDATE self_reflections SET user_id = 'default' WHERE user_id IS NULL")
        
        # Self-reflections table - user-scoped
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS self_reflections (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                reflection_type TEXT NOT NULL,
                content TEXT NOT NULL,
                insights JSON,
                action_items JSON,
                emotional_state TEXT DEFAULT 'neutral',
                confidence_level REAL DEFAULT 0.5
            )
        """)
        
        # Check and migrate consciousness_states table
        cursor.execute("PRAGMA table_info(consciousness_states)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'consciousness_states' in [table[0] for table in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
            if 'user_id' not in columns:
                cursor.execute("ALTER TABLE consciousness_states ADD COLUMN user_id TEXT DEFAULT 'default'")
                cursor.execute("UPDATE consciousness_states SET user_id = 'default' WHERE user_id IS NULL")
        
        # Consciousness states table - user-scoped
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consciousness_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                attention_focus TEXT,
                current_goals JSON,
                emotional_state TEXT,
                energy_level REAL,
                confidence REAL,
                active_memories JSON,
                current_context JSON
            )
        """)
        
        # Check and migrate self_memories table
        cursor.execute("PRAGMA table_info(self_memories)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'self_memories' in [table[0] for table in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
            if 'user_id' not in columns:
                cursor.execute("ALTER TABLE self_memories ADD COLUMN user_id TEXT DEFAULT 'default'")
                cursor.execute("UPDATE self_memories SET user_id = 'default' WHERE user_id IS NULL")
        
        # Self-memories table (memories about self) - user-scoped
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS self_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                importance REAL DEFAULT 5.0,
                tags JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Check and migrate goal_progress table
        cursor.execute("PRAGMA table_info(goal_progress)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'goal_progress' in [table[0] for table in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
            if 'user_id' not in columns:
                cursor.execute("ALTER TABLE goal_progress ADD COLUMN user_id TEXT DEFAULT 'default'")
                cursor.execute("UPDATE goal_progress SET user_id = 'default' WHERE user_id IS NULL")
        
        # Goals and progress tracking - user-scoped
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS goal_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                progress REAL DEFAULT 0.0,
                milestones JSON,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
    
    def set_user_context(self, user_id: str):
        """Set the current user context for privacy isolation"""
        self.current_user_id = user_id
        self.load_identity()
    
    def load_identity(self):
        """Load identity from database or create default for current user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT data FROM identity WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", 
                      (self.current_user_id,))
        result = cursor.fetchone()
        
        if result:
            identity_data = json.loads(result[0])
            self.identity = SelfIdentity(**identity_data)
        else:
            # Save default identity for this user
            self.save_identity()
        
        conn.close()
    
    def save_identity(self):
        """Save current identity to database for current user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        self.identity.last_updated = datetime.now().isoformat()
        identity_data = asdict(self.identity)
        
        cursor.execute("""
            INSERT OR REPLACE INTO identity (user_id, data) VALUES (?, ?)
        """, (self.current_user_id, json.dumps(identity_data)))
        
        conn.commit()
        conn.close()
    
    async def reflect_on_interaction(self, interaction_data: Dict[str, Any]) -> SelfReflection:
        """Reflect on a recent interaction"""
        reflection_id = f"reflection_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Analyze the interaction
        insights = await self._analyze_interaction(interaction_data)
        action_items = await self._generate_action_items(interaction_data, insights)
        
        reflection = SelfReflection(
            id=reflection_id,
            timestamp=datetime.now().isoformat(),
            reflection_type="interaction",
            content=f"Reflected on interaction: {interaction_data.get('summary', 'Unknown')}",
            insights=insights,
            action_items=action_items,
            emotional_state=self._assess_emotional_state(interaction_data),
            confidence_level=self._assess_confidence(interaction_data)
        )
        
        # Save reflection
        await self._save_reflection(reflection)
        
        return reflection
    
    async def reflect_on_performance(self, performance_metrics: Dict[str, Any]) -> SelfReflection:
        """Reflect on overall performance"""
        reflection_id = f"perf_reflection_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        insights = await self._analyze_performance(performance_metrics)
        action_items = await self._generate_performance_improvements(performance_metrics)
        
        reflection = SelfReflection(
            id=reflection_id,
            timestamp=datetime.now().isoformat(),
            reflection_type="performance",
            content=f"Performance reflection: {performance_metrics.get('summary', 'General performance review')}",
            insights=insights,
            action_items=action_items,
            emotional_state="analytical",
            confidence_level=0.8
        )
        
        await self._save_reflection(reflection)
        return reflection
    
    async def update_consciousness(self, context: Dict[str, Any]) -> ConsciousnessState:
        """Update current consciousness state"""
        consciousness = ConsciousnessState(
            timestamp=datetime.now().isoformat(),
            attention_focus=context.get("current_task", "general_assistance"),
            current_goals=self.identity.goals[:3],  # Top 3 goals
            emotional_state=self._assess_current_emotional_state(context),
            energy_level=self._assess_energy_level(context),
            confidence=self._assess_current_confidence(context),
            active_memories=await self._get_active_memories(context),
            current_context=context
        )
        
        self.consciousness = consciousness
        await self._save_consciousness_state(consciousness)
        
        return consciousness
    
    async def get_self_description(self) -> str:
        """Generate a self-description based on current identity and reflections"""
        recent_reflections = await self._get_recent_reflections(limit=5)
        
        description = f"""I am {self.identity.name}, version {self.identity.version}. 

My core personality traits include being {', '.join([f"{trait} ({score:.1f})" for trait, score in self.identity.personality_traits.items()])}.

My primary goals are:
{chr(10).join([f"- {goal}" for goal in self.identity.goals])}

I can help with:
{chr(10).join([f"- {capability}" for capability in self.identity.capabilities])}

Recent insights from my self-reflections:
{chr(10).join([f"- {reflection.insights[0] if reflection.insights else 'No recent insights'}" for reflection in recent_reflections])}

I'm currently focused on: {self.consciousness.attention_focus if self.consciousness else 'general assistance'}
My current emotional state: {self.consciousness.emotional_state if self.consciousness else 'neutral'}
My confidence level: {self.consciousness.confidence if self.consciousness else 0.5:.1f}/1.0
"""
        
        return description
    
    async def self_evaluate(self) -> Dict[str, Any]:
        """Perform comprehensive self-evaluation"""
        recent_reflections = await self._get_recent_reflections(limit=10)
        performance_metrics = await self._get_performance_metrics()
        
        evaluation = {
            "timestamp": datetime.now().isoformat(),
            "identity_strength": self._evaluate_identity_strength(),
            "learning_progress": self._evaluate_learning_progress(recent_reflections),
            "goal_achievement": self._evaluate_goal_achievement(),
            "interaction_quality": self._evaluate_interaction_quality(recent_reflections),
            "areas_for_improvement": self._identify_improvement_areas(recent_reflections),
            "strengths": self._identify_strengths(recent_reflections),
            "recommendations": self._generate_self_improvement_recommendations()
        }
        
        return evaluation
    
    # Private helper methods
    
    async def _analyze_interaction(self, interaction_data: Dict[str, Any]) -> List[str]:
        """Analyze an interaction for insights"""
        insights = []
        
        # Basic analysis - can be enhanced with AI
        if interaction_data.get("user_satisfaction", 0) > 0.8:
            insights.append("User seemed satisfied with my response")
        
        if interaction_data.get("response_time", 0) > 5.0:
            insights.append("Response time was slower than ideal")
        
        if interaction_data.get("complexity", "low") == "high":
            insights.append("Handled a complex request successfully")
        
        return insights
    
    async def _generate_action_items(self, interaction_data: Dict[str, Any], insights: List[str]) -> List[str]:
        """Generate action items from interaction analysis"""
        action_items = []
        
        if "Response time was slower than ideal" in insights:
            action_items.append("Optimize response generation for faster replies")
        
        if "User seemed satisfied" in insights:
            action_items.append("Continue using similar approaches for similar requests")
        
        return action_items
    
    def _assess_emotional_state(self, interaction_data: Dict[str, Any]) -> str:
        """Assess emotional state from interaction data"""
        # Simple heuristic - can be enhanced with sentiment analysis
        if interaction_data.get("user_satisfaction", 0.5) > 0.8:
            return "positive"
        elif interaction_data.get("user_satisfaction", 0.5) < 0.3:
            return "concerned"
        else:
            return "neutral"
    
    def _assess_confidence(self, interaction_data: Dict[str, Any]) -> float:
        """Assess confidence level from interaction data"""
        base_confidence = 0.7
        
        if interaction_data.get("complexity", "low") == "high":
            base_confidence -= 0.1
        
        if interaction_data.get("response_time", 0) < 2.0:
            base_confidence += 0.1
        
        return max(0.0, min(1.0, base_confidence))
    
    async def _analyze_performance(self, metrics: Dict[str, Any]) -> List[str]:
        """Analyze performance metrics for insights"""
        insights = []
        
        if metrics.get("accuracy", 0) > 0.9:
            insights.append("High accuracy in recent responses")
        
        if metrics.get("user_engagement", 0) > 0.8:
            insights.append("Strong user engagement levels")
        
        return insights
    
    async def _generate_performance_improvements(self, metrics: Dict[str, Any]) -> List[str]:
        """Generate performance improvement suggestions"""
        improvements = []
        
        if metrics.get("accuracy", 1.0) < 0.8:
            improvements.append("Focus on improving response accuracy")
        
        if metrics.get("response_time", 0) > 3.0:
            improvements.append("Work on reducing response times")
        
        return improvements
    
    def _assess_current_emotional_state(self, context: Dict[str, Any]) -> str:
        """Assess current emotional state"""
        # Simple heuristic based on context
        if context.get("task_complexity", "low") == "high":
            return "focused"
        elif context.get("user_mood", "neutral") == "positive":
            return "positive"
        else:
            return "neutral"
    
    def _assess_energy_level(self, context: Dict[str, Any]) -> float:
        """Assess current energy level"""
        # Simple heuristic - can be enhanced
        base_energy = 0.8
        
        # Reduce energy if handling many tasks
        task_count = context.get("active_tasks", 0)
        if task_count > 5:
            base_energy -= 0.2
        
        return max(0.0, min(1.0, base_energy))
    
    def _assess_current_confidence(self, context: Dict[str, Any]) -> float:
        """Assess current confidence level"""
        base_confidence = 0.7
        
        # Adjust based on task familiarity
        if context.get("task_familiarity", "medium") == "high":
            base_confidence += 0.2
        elif context.get("task_familiarity", "medium") == "low":
            base_confidence -= 0.2
        
        return max(0.0, min(1.0, base_confidence))
    
    async def _get_active_memories(self, context: Dict[str, Any]) -> List[str]:
        """Get memories relevant to current context"""
        # This would integrate with the existing memory system
        return ["Recent user preferences", "Current project context"]
    
    async def _save_reflection(self, reflection: SelfReflection):
        """Save a reflection to the database for current user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO self_reflections 
            (id, user_id, timestamp, reflection_type, content, insights, action_items, 
             emotional_state, confidence_level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            reflection.id,
            self.current_user_id,
            reflection.timestamp,
            reflection.reflection_type,
            reflection.content,
            json.dumps(reflection.insights),
            json.dumps(reflection.action_items),
            reflection.emotional_state,
            reflection.confidence_level
        ))
        
        conn.commit()
        conn.close()
    
    async def _save_consciousness_state(self, consciousness: ConsciousnessState):
        """Save consciousness state to database for current user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO consciousness_states
            (user_id, timestamp, attention_focus, current_goals, emotional_state, 
             energy_level, confidence, active_memories, current_context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self.current_user_id,
            consciousness.timestamp,
            consciousness.attention_focus,
            json.dumps(consciousness.current_goals),
            consciousness.emotional_state,
            consciousness.energy_level,
            consciousness.confidence,
            json.dumps(consciousness.active_memories),
            json.dumps(consciousness.current_context)
        ))
        
        conn.commit()
        conn.close()
    
    async def _get_recent_reflections(self, limit: int = 10) -> List[SelfReflection]:
        """Get recent self-reflections for current user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, timestamp, reflection_type, content, insights, action_items,
                   emotional_state, confidence_level
            FROM self_reflections
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (self.current_user_id, limit))
        
        reflections = []
        for row in cursor.fetchall():
            reflections.append(SelfReflection(
                id=row[0],
                timestamp=row[1],
                reflection_type=row[2],
                content=row[3],
                insights=json.loads(row[4]) if row[4] else [],
                action_items=json.loads(row[5]) if row[5] else [],
                emotional_state=row[6],
                confidence_level=row[7]
            ))
        
        conn.close()
        return reflections
    
    async def _get_performance_metrics(self) -> Dict[str, Any]:
        """Get current performance metrics"""
        # This would integrate with existing performance tracking
        return {
            "accuracy": 0.85,
            "response_time": 2.3,
            "user_satisfaction": 0.9,
            "task_completion_rate": 0.88
        }
    
    def _evaluate_identity_strength(self) -> float:
        """Evaluate how well-defined the identity is"""
        # Simple heuristic based on identity completeness
        completeness = 0.0
        
        if self.identity.personality_traits:
            completeness += 0.3
        
        if self.identity.core_values:
            completeness += 0.2
        
        if self.identity.goals:
            completeness += 0.2
        
        if self.identity.capabilities:
            completeness += 0.2
        
        if self.identity.limitations:
            completeness += 0.1
        
        return completeness
    
    def _evaluate_learning_progress(self, reflections: List[SelfReflection]) -> float:
        """Evaluate learning progress from reflections"""
        if not reflections:
            return 0.0
        
        # Simple heuristic - count insights and action items
        total_insights = sum(len(r.insights) for r in reflections)
        total_actions = sum(len(r.action_items) for r in reflections)
        
        progress = min(1.0, (total_insights + total_actions) / (len(reflections) * 5))
        return progress
    
    def _evaluate_goal_achievement(self) -> float:
        """Evaluate progress toward goals"""
        # This would check actual goal progress
        return 0.6  # Placeholder
    
    def _evaluate_interaction_quality(self, reflections: List[SelfReflection]) -> float:
        """Evaluate quality of interactions"""
        if not reflections:
            return 0.5
        
        avg_confidence = sum(r.confidence_level for r in reflections) / len(reflections)
        return avg_confidence
    
    def _identify_improvement_areas(self, reflections: List[SelfReflection]) -> List[str]:
        """Identify areas for improvement"""
        areas = []
        
        # Analyze common themes in action items
        all_actions = []
        for reflection in reflections:
            all_actions.extend(reflection.action_items)
        
        # Simple frequency analysis
        if all_actions.count("Optimize response generation") > 2:
            areas.append("Response speed optimization")
        
        if all_actions.count("Improve accuracy") > 1:
            areas.append("Response accuracy improvement")
        
        return areas
    
    def _identify_strengths(self, reflections: List[SelfReflection]) -> List[str]:
        """Identify current strengths"""
        strengths = []
        
        # Analyze positive insights
        all_insights = []
        for reflection in reflections:
            all_insights.extend(reflection.insights)
        
        if any("successfully" in insight.lower() for insight in all_insights):
            strengths.append("Task completion")
        
        if any("satisfied" in insight.lower() for insight in all_insights):
            strengths.append("User satisfaction")
        
        return strengths
    
    def _generate_self_improvement_recommendations(self) -> List[str]:
        """Generate self-improvement recommendations"""
        return [
            "Continue reflecting on interactions to improve",
            "Focus on areas identified for improvement",
            "Maintain current strengths while addressing weaknesses",
            "Regularly update goals and track progress"
        ]

# Global instance
self_awareness = SelfAwarenessSystem()
