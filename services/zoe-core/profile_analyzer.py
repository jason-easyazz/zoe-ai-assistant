"""
Profile Analyzer Service
Analyzes user data from multiple sources to build and update user profile
Inspired by Second Me's Hierarchical Memory Modeling approach
"""
import sqlite3
import json
import os
from typing import Dict, Any, List, Optional
from datetime import datetime

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

class ProfileAnalyzer:
    """Analyzes user behavior across multiple data sources to build comprehensive profile"""
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        
    def analyze_all_sources(self) -> Dict[str, Any]:
        """
        Main analysis method that pulls from all available data sources
        Returns structured profile updates with confidence scores
        """
        insights = {}
        
        # 1. Analyze chat conversations
        chat_insights = self.analyze_conversations()
        insights['chat'] = chat_insights
        
        # 2. Analyze journal entries
        journal_insights = self.analyze_journal_entries()
        insights['journal'] = journal_insights
        
        # 3. Analyze calendar events
        calendar_insights = self.analyze_calendar_events()
        insights['calendar'] = calendar_insights
        
        # 4. Analyze people relationships
        people_insights = self.analyze_people_relationships()
        insights['people'] = people_insights
        
        # Combine and generate profile updates
        profile_updates = self.combine_insights(insights)
        
        return profile_updates
    
    def analyze_conversations(self) -> Dict[str, Any]:
        """Extract personality, interests, values from chat history"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get recent conversations from temporal memory
        cursor.execute("""
            SELECT content, metadata
            FROM temporal_memories
            WHERE user_id = ? AND role = 'user'
            ORDER BY created_at DESC
            LIMIT 100
        """, (self.user_id,))
        
        conversations = cursor.fetchall()
        conn.close()
        
        if not conversations:
            return {"confidence": 0.0, "insights": []}
        
        # Extract insights from conversation content
        # This is a placeholder - would use LLM to analyze in production
        insights = []
        
        for conv in conversations:
            content = conv['content'] if conv else ''
            # TODO: Use LLM to extract personality traits, interests, values from content
            # For now, return structure
            
        return {
            "confidence": 0.7,
            "insights": insights,
            "sources": len(conversations),
            "timeframe": "Recent 100 messages"
        }
    
    def analyze_journal_entries(self) -> Dict[str, Any]:
        """Extract mood patterns, values, goals from journal"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get journal entries
        cursor.execute("""
            SELECT content, mood, tags, created_at
            FROM journal_entries
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 50
        """, (self.user_id,))
        
        entries = cursor.fetchall()
        conn.close()
        
        if not entries:
            return {"confidence": 0.0, "insights": []}
        
        # Analyze mood patterns, topics, values
        mood_patterns = {}
        topics = {}
        
        for entry in entries:
            if entry['mood']:
                mood_patterns[entry['mood']] = mood_patterns.get(entry['mood'], 0) + 1
            
            # Extract topics from content (simplified)
            if entry['content']:
                words = entry['content'].split()
                for word in words:
                    if len(word) > 4:
                        topics[word] = topics.get(word, 0) + 1
        
        top_moods = sorted(mood_patterns.items(), key=lambda x: x[1], reverse=True)[:3]
        top_topics = sorted(topics.items(), key=lambda x: x[1], reverse=True)[:10]
        
        return {
            "confidence": 0.8,
            "insights": [
                {"type": "mood_patterns", "data": top_moods},
                {"type": "topics", "data": top_topics}
            ],
            "sources": len(entries)
        }
    
    def analyze_calendar_events(self) -> Dict[str, Any]:
        """Extract activity patterns and interests from calendar"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get calendar events
        cursor.execute("""
            SELECT title, description, category, start_time
            FROM calendar_events
            WHERE user_id = ?
            AND start_time > datetime('now', '-3 months')
            ORDER BY start_time DESC
        """, (self.user_id,))
        
        events = cursor.fetchall()
        conn.close()
        
        if not events:
            return {"confidence": 0.0, "insights": []}
        
        # Analyze activity patterns
        categories = {}
        activity_types = {}
        
        for event in events:
            if event['category']:
                categories[event['category']] = categories.get(event['category'], 0) + 1
            
            # Extract activity type from title
            title_lower = event['title'].lower() if event['title'] else ''
            if any(word in title_lower for word in ['coffee', 'lunch', 'dinner', 'meeting', 'workout']):
                activity_types['social_activities'] = activity_types.get('social_activities', 0) + 1
            if any(word in title_lower for word in ['meeting', 'call', 'conference']):
                activity_types['professional'] = activity_types.get('professional', 0) + 1
        
        return {
            "confidence": 0.75,
            "insights": [
                {"type": "categories", "data": categories},
                {"type": "activity_patterns", "data": activity_types}
            ],
            "sources": len(events)
        }
    
    def analyze_people_relationships(self) -> Dict[str, Any]:
        """Analyze relationship patterns and social preferences"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get people and their relationship types
        cursor.execute("""
            SELECT relationship, metadata
            FROM people
            WHERE user_id = ?
        """, (self.user_id,))
        
        people = cursor.fetchall()
        conn.close()
        
        if not people:
            return {"confidence": 0.0, "insights": []}
        
        # Analyze relationship distribution
        relationship_types = {}
        for person in people:
            rel_type = person['relationship']
            relationship_types[rel_type] = relationship_types.get(rel_type, 0) + 1
        
        return {
            "confidence": 0.85,
            "insights": [
                {"type": "relationship_distribution", "data": relationship_types}
            ],
            "sources": len(people)
        }
    
    def combine_insights(self, sources: Dict[str, Any]) -> Dict[str, Any]:
        """
        Combine insights from all sources and generate profile updates
        This is where Hierarchical Memory Modeling happens
        """
        profile_updates = {
            "personality_traits": {},
            "values_priority": {},
            "interests": [],
            "life_goals": [],
            "confidence_score": 0.0,
            "observed_patterns": [],
            "last_analyzed": datetime.now().isoformat()
        }
        
        # Combine all confidence scores
        total_confidence = 0
        count = 0
        
        for source, insights in sources.items():
            if insights.get('confidence', 0) > 0:
                total_confidence += insights['confidence']
                count += 1
        
        if count > 0:
            profile_updates['confidence_score'] = total_confidence / count
        
        # Extract observed patterns
        for source, insights in sources.items():
            if 'insights' in insights:
                for insight in insights['insights']:
                    if 'type' in insight:
                        profile_updates['observed_patterns'].append({
                            "source": source,
                            "type": insight['type'],
                            "data": insight.get('data', {})
                        })
        
        return profile_updates
    
    def update_profile(self) -> Dict[str, Any]:
        """
        Run complete analysis and update user profile in database
        Returns updated profile with new insights
        """
        profile_updates = self.analyze_all_sources()
        
        # Update database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get existing profile
        cursor.execute("""
            SELECT personality_traits, values_priority, interests, life_goals, 
                   ai_insights, observed_patterns, confidence_score
            FROM user_profiles WHERE user_id = ?
        """, (self.user_id,))
        
        existing = cursor.fetchone()
        
        if existing:
            # Merge with existing data
            existing_traits = json.loads(existing[0]) if existing[0] else {}
            existing_insights = json.loads(existing[4]) if existing[4] else {}
            existing_patterns = json.loads(existing[5]) if existing[5] else []
            
            # Merge updates
            cursor.execute("""
                UPDATE user_profiles
                SET personality_traits = COALESCE(?, personality_traits),
                    values_priority = COALESCE(?, values_priority),
                    interests = COALESCE(?, interests),
                    life_goals = COALESCE(?, life_goals),
                    ai_insights = ?,
                    observed_patterns = ?,
                    confidence_score = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (
                json.dumps(profile_updates.get('personality_traits')),
                json.dumps(profile_updates.get('values_priority')),
                json.dumps(profile_updates.get('interests')),
                json.dumps(profile_updates.get('life_goals')),
                json.dumps(profile_updates),
                json.dumps(profile_updates.get('observed_patterns')),
                profile_updates.get('confidence_score'),
                self.user_id
            ))
        else:
            # Create new profile
            cursor.execute("""
                INSERT INTO user_profiles (user_id, personality_traits, values_priority, 
                                         interests, life_goals, ai_insights, observed_patterns, 
                                         confidence_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.user_id,
                json.dumps(profile_updates.get('personality_traits')),
                json.dumps(profile_updates.get('values_priority')),
                json.dumps(profile_updates.get('interests')),
                json.dumps(profile_updates.get('life_goals')),
                json.dumps(profile_updates),
                json.dumps(profile_updates.get('observed_patterns')),
                profile_updates.get('confidence_score')
            ))
        
        conn.commit()
        conn.close()
        
        return profile_updates

def analyze_user_profile(user_id: str) -> Dict[str, Any]:
    """Convenience function to analyze a user's profile"""
    analyzer = ProfileAnalyzer(user_id)
    return analyzer.update_profile()



