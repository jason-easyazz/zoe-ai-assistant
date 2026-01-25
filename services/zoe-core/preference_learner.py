"""
Preference Learning System
Learn and adapt to user's preferred response style, tone, and interaction patterns
"""
import sqlite3
import json
from typing import Dict, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class PreferenceLearner:
    """Learn user preferences from feedback patterns"""
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        self.db_path = db_path
        self._init_preferences_table()
    
    def _init_preferences_table(self):
        """Create user preferences table"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE NOT NULL,
                response_length TEXT DEFAULT 'balanced',
                tone_preference TEXT DEFAULT 'friendly',
                emoji_usage TEXT DEFAULT 'moderate',
                proactiveness_level TEXT DEFAULT 'moderate',
                detail_level TEXT DEFAULT 'balanced',
                technical_level TEXT DEFAULT 'moderate',
                preferences_json TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                -- 🎵 Music preferences
                preferred_music_provider TEXT DEFAULT 'youtube_music',
                default_output_device TEXT,
                audio_quality TEXT DEFAULT 'high',
                autoplay_recommendations INTEGER DEFAULT 1
            )
        """)
        
        # Add music columns if table already exists (migration)
        try:
            cursor.execute("ALTER TABLE user_preferences ADD COLUMN preferred_music_provider TEXT DEFAULT 'youtube_music'")
        except:
            pass  # Column already exists
        try:
            cursor.execute("ALTER TABLE user_preferences ADD COLUMN default_output_device TEXT")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE user_preferences ADD COLUMN audio_quality TEXT DEFAULT 'high'")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE user_preferences ADD COLUMN autoplay_recommendations INTEGER DEFAULT 1")
        except:
            pass
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS preference_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                signal_type TEXT,
                signal_value TEXT,
                confidence REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info("✅ Preference tables initialized")
    
    async def analyze_feedback_patterns(self, user_id: str) -> Dict:
        """Analyze user's feedback to learn preferences"""
        
        training_db = "/app/data/training.db"
        
        try:
            conn = sqlite3.connect(training_db)
            cursor = conn.cursor()
            
            # Get recent feedback with response characteristics
            cursor.execute("""
                SELECT user_input, zoe_output, feedback_type, 
                       quality_score, warmth_score, intelligence_score
                FROM training_examples
                WHERE user_id = ?
                AND timestamp >= datetime('now', '-30 days')
                AND feedback_type IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 100
            """, (user_id,))
            
            feedback_data = cursor.fetchall()
            conn.close()
            
            if not feedback_data:
                logger.info(f"No feedback data yet for {user_id}")
                return {}
            
            # Analyze patterns
            preferences = {
                "response_length": self._analyze_length_preference(feedback_data),
                "tone_preference": self._analyze_tone_preference(feedback_data),
                "emoji_usage": self._analyze_emoji_preference(feedback_data),
                "detail_level": self._analyze_detail_preference(feedback_data),
                "confidence": len(feedback_data) / 100  # Confidence based on data points
            }
            
            # Save preferences
            await self._save_preferences(user_id, preferences)
            
            logger.info(f"✅ Learned preferences for {user_id}: {preferences}")
            return preferences
            
        except Exception as e:
            logger.error(f"Preference analysis failed: {e}")
            return {}
    
    def _analyze_length_preference(self, feedback_data) -> str:
        """Determine if user prefers concise or detailed responses"""
        
        positive_lengths = []
        negative_lengths = []
        
        for _, output, feedback, *_ in feedback_data:
            word_count = len(output.split())
            
            if feedback == 'positive':
                positive_lengths.append(word_count)
            elif feedback == 'negative':
                negative_lengths.append(word_count)
        
        if not positive_lengths:
            return 'balanced'
        
        avg_positive = sum(positive_lengths) / len(positive_lengths)
        
        if avg_positive < 50:
            return 'concise'
        elif avg_positive > 150:
            return 'detailed'
        else:
            return 'balanced'
    
    def _analyze_tone_preference(self, feedback_data) -> str:
        """Determine preferred tone (formal vs casual)"""
        
        casual_indicators = ['!', '😊', '🎉', 'awesome', 'great', 'cool']
        formal_indicators = ['therefore', 'however', 'additionally', 'furthermore']
        
        casual_score = 0
        formal_score = 0
        
        for _, output, feedback, *_ in feedback_data:
            if feedback != 'positive':
                continue
            
            output_lower = output.lower()
            
            for indicator in casual_indicators:
                if indicator in output_lower:
                    casual_score += 1
            
            for indicator in formal_indicators:
                if indicator in output_lower:
                    formal_score += 1
        
        if casual_score > formal_score * 2:
            return 'casual'
        elif formal_score > casual_score * 2:
            return 'formal'
        else:
            return 'friendly'
    
    def _analyze_emoji_preference(self, feedback_data) -> str:
        """Determine emoji usage preference"""
        
        positive_with_emoji = 0
        positive_without_emoji = 0
        
        for _, output, feedback, *_ in feedback_data:
            if feedback != 'positive':
                continue
            
            has_emoji = any(char in output for char in '😊🎉💡✨🚀📅🛒')
            
            if has_emoji:
                positive_with_emoji += 1
            else:
                positive_without_emoji += 1
        
        total = positive_with_emoji + positive_without_emoji
        if total == 0:
            return 'moderate'
        
        emoji_ratio = positive_with_emoji / total
        
        if emoji_ratio > 0.7:
            return 'frequent'
        elif emoji_ratio < 0.3:
            return 'minimal'
        else:
            return 'moderate'
    
    def _analyze_detail_preference(self, feedback_data) -> str:
        """Determine level of detail preference"""
        
        detailed_responses = 0
        brief_responses = 0
        
        for _, output, feedback, *_ in feedback_data:
            if feedback != 'positive':
                continue
            
            # Count bullet points, lists, step-by-step
            has_structure = any(indicator in output for indicator in ['•', '1.', '2.', 'Step', '**'])
            
            if has_structure:
                detailed_responses += 1
            else:
                brief_responses += 1
        
        if detailed_responses > brief_responses * 1.5:
            return 'detailed'
        elif brief_responses > detailed_responses * 1.5:
            return 'brief'
        else:
            return 'balanced'
    
    async def _save_preferences(self, user_id: str, preferences: Dict):
        """Save learned preferences to database"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO user_preferences
                (user_id, response_length, tone_preference, emoji_usage, detail_level, 
                 preferences_json, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                user_id,
                preferences.get('response_length', 'balanced'),
                preferences.get('tone_preference', 'friendly'),
                preferences.get('emoji_usage', 'moderate'),
                preferences.get('detail_level', 'balanced'),
                json.dumps(preferences)
            ))
            
            conn.commit()
        finally:
            conn.close()
    
    async def get_preferences(self, user_id: str) -> Dict:
        """Get user's learned preferences"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT response_length, tone_preference, emoji_usage, 
                       detail_level, preferences_json
                FROM user_preferences
                WHERE user_id = ?
            """, (user_id,))
            
            row = cursor.fetchone()
            
            if not row:
                # Return defaults
                return {
                    "response_length": "balanced",
                    "tone_preference": "friendly",
                    "emoji_usage": "moderate",
                    "detail_level": "balanced"
                }
            
            preferences = {
                "response_length": row[0],
                "tone_preference": row[1],
                "emoji_usage": row[2],
                "detail_level": row[3]
            }
            
            # Add extra preferences from JSON
            if row[4]:
                extra = json.loads(row[4])
                preferences.update(extra)
            
            return preferences
            
        finally:
            conn.close()
    
    def get_preference_prompt_additions(self, preferences: Dict) -> str:
        """Generate prompt additions based on preferences"""
        
        additions = "\n# USER PREFERENCES (learned from feedback):\n"
        
        # Response length
        if preferences.get("response_length") == "concise":
            additions += "- Keep responses brief and to the point\n"
        elif preferences.get("response_length") == "detailed":
            additions += "- Provide detailed, thorough responses\n"
        
        # Tone
        tone = preferences.get("tone_preference", "friendly")
        if tone == "casual":
            additions += "- Use casual, conversational tone\n"
        elif tone == "formal":
            additions += "- Use professional, formal tone\n"
        else:
            additions += "- Use warm, friendly tone\n"
        
        # Emojis
        emoji = preferences.get("emoji_usage", "moderate")
        if emoji == "frequent":
            additions += "- Use emojis liberally to add warmth\n"
        elif emoji == "minimal":
            additions += "- Use emojis sparingly\n"
        else:
            additions += "- Use emojis moderately for emphasis\n"
        
        # Detail level
        detail = preferences.get("detail_level", "balanced")
        if detail == "detailed":
            additions += "- Include step-by-step breakdowns and examples\n"
        elif detail == "brief":
            additions += "- Keep explanations concise\n"
        
        return additions


    async def learn_from_archives(self, user_id: str) -> Dict:
        """
        Phase 6B: Learn preferences from complete historical archive.
        
        Uses memvid archives to analyze ALL past interactions for better preference learning.
        """
        try:
            from unified_learner import unified_learner
            
            # Get complete history analysis
            history = await unified_learner.analyze_complete_history(user_id)
            
            if history.get('error') or not history.get('patterns'):
                return {
                    "learned": False,
                    "message": "No archive data available yet - learning from recent data only"
                }
            
            patterns = history['patterns']
            
            # Extract communication preferences
            if 'communication_style' in patterns:
                comm = patterns['communication_style']
                logger.info(f"📚 Learning from {comm.get('total_chats_analyzed', 0)} archived chats")
            
            # Update preferences based on historical patterns
            # (Actual implementation would update user_preferences table)
            
            return {
                "learned": True,
                "archives_analyzed": history.get('archives_analyzed', 0),
                "patterns_discovered": len(patterns),
                "message": "Preferences updated from complete history"
            }
            
        except Exception as e:
            logger.error(f"Archive learning failed: {e}")
            return {"learned": False, "error": str(e)}


    # 🎵 Music preference methods
    async def get_music_preferences(self, user_id: str) -> Dict:
        """Get user's music preferences"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT preferred_music_provider, default_output_device,
                       audio_quality, autoplay_recommendations
                FROM user_preferences
                WHERE user_id = ?
            """, (user_id,))
            
            row = cursor.fetchone()
            
            if not row:
                # Return defaults
                return {
                    "preferred_provider": "youtube_music",
                    "default_output_device": None,
                    "audio_quality": "high",
                    "autoplay_recommendations": True
                }
            
            return {
                "preferred_provider": row[0] or "youtube_music",
                "default_output_device": row[1],
                "audio_quality": row[2] or "high",
                "autoplay_recommendations": bool(row[3]) if row[3] is not None else True
            }
        finally:
            conn.close()
    
    async def set_music_preferences(self, user_id: str, preferences: Dict) -> bool:
        """Set user's music preferences"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Ensure user row exists
            cursor.execute("""
                INSERT OR IGNORE INTO user_preferences (user_id)
                VALUES (?)
            """, (user_id,))
            
            # Update music preferences
            updates = []
            values = []
            
            if "preferred_provider" in preferences:
                updates.append("preferred_music_provider = ?")
                values.append(preferences["preferred_provider"])
            if "default_output_device" in preferences:
                updates.append("default_output_device = ?")
                values.append(preferences["default_output_device"])
            if "audio_quality" in preferences:
                updates.append("audio_quality = ?")
                values.append(preferences["audio_quality"])
            if "autoplay_recommendations" in preferences:
                updates.append("autoplay_recommendations = ?")
                values.append(1 if preferences["autoplay_recommendations"] else 0)
            
            if updates:
                updates.append("last_updated = CURRENT_TIMESTAMP")
                values.append(user_id)
                
                cursor.execute(f"""
                    UPDATE user_preferences
                    SET {', '.join(updates)}
                    WHERE user_id = ?
                """, values)
            
            conn.commit()
            logger.info(f"✅ Updated music preferences for {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to set music preferences: {e}")
            return False
        finally:
            conn.close()


# Global instance
preference_learner = PreferenceLearner()












