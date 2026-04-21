"""
Predictive Intelligence System (Phase 6C)
Predict user needs based on complete historical patterns from memvid archives
"""

import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from pathlib import Path

from memvid.retriever import MemvidRetriever

logger = logging.getLogger(__name__)


class PredictiveIntelligence:
    """Predict user needs based on complete historical patterns"""
    
    def __init__(self, archive_dir: str = "/app/data/learning-archives"):
        self.archive_dir = Path(archive_dir)
    
    async def predict_next_action(self, user_id: str, current_context: Dict) -> Dict[str, Any]:
        """
        Predict what user likely needs next based on similar historical contexts.
        
        Args:
            user_id: User ID
            current_context: Current situation (time, day, recent activity, mood, etc.)
            
        Returns:
            Predictions with confidence scores
        """
        if not self.archive_dir.exists() or not list(self.archive_dir.glob("*.mp4")):
            return {
                "predictions": [],
                "note": "No archives yet - predictions available after data accumulates"
            }
        
        # Find similar historical contexts
        similar_contexts = await self._find_similar_contexts(user_id, current_context)
        
        if not similar_contexts:
            return {
                "predictions": [],
                "confidence": "low",
                "note": "No similar historical contexts found"
            }
        
        # Analyze what happened next in similar situations
        predictions = {
            "likely_next_request": await self._predict_next_request(similar_contexts),
            "suggested_proactive_actions": await self._generate_proactive_suggestions(similar_contexts),
            "optimal_response_style": await self._predict_response_style(similar_contexts),
            "confidence": self._calculate_confidence(similar_contexts)
        }
        
        return predictions
    
    async def enable_proactive_support(self, user_id: str) -> Dict[str, Any]:
        """
        Proactively offer assistance based on time/day patterns.
        
        Example: User typically plans week on Sunday evening
        → Zoe proactively offers: "Want to plan your week?"
        """
        current_time = datetime.now()
        current_day = current_time.strftime("%A")
        current_hour = current_time.hour
        
        # Search archives for what user typically does now
        temporal_query = f"day:{current_day} hour:{current_hour}"
        
        historical_pattern = await self._get_temporal_patterns(
            user_id=user_id,
            day_of_week=current_day,
            hour_of_day=current_hour
        )
        
        if not historical_pattern:
            return {
                "proactive_suggestions": [],
                "note": "Learning patterns - proactive assistance improves over time"
            }
        
        return {
            "typical_activity_now": historical_pattern.get("common_activities", []),
            "proactive_suggestions": historical_pattern.get("suggestions", []),
            "confidence": historical_pattern.get("confidence", 0)
        }
    
    async def predict_mood_from_context(self, user_id: str, context: Dict) -> Dict:
        """
        Predict likely mood based on current context and historical patterns.
        
        Uses: time of day, recent tasks, calendar events, etc.
        """
        # Correlate current context with past journal moods
        mood_patterns = await self._correlate_context_with_mood(user_id, context)
        
        return {
            "predicted_mood": mood_patterns.get("most_likely_mood"),
            "confidence": mood_patterns.get("confidence", 0),
            "factors": mood_patterns.get("influencing_factors", [])
        }
    
    async def _find_similar_contexts(self, user_id: str, current_context: Dict) -> List[Dict]:
        """Find similar historical contexts from archives"""
        # Simplified implementation - would use semantic similarity in production
        return []
    
    async def _predict_next_request(self, similar_contexts: List[Dict]) -> str:
        """Predict most likely next user request"""
        if not similar_contexts:
            return "Unable to predict"
        
        # Analyze what happened next in similar situations
        return "Prediction based on patterns (placeholder)"
    
    async def _generate_proactive_suggestions(self, similar_contexts: List[Dict]) -> List[str]:
        """Generate helpful proactive suggestions"""
        if not similar_contexts:
            return []
        
        return ["Proactive suggestions based on learned patterns"]
    
    async def _predict_response_style(self, similar_contexts: List[Dict]) -> str:
        """Predict optimal response style for current context"""
        return "adaptive"  # Would analyze successful past responses
    
    async def _calculate_confidence(self, similar_contexts: List[Dict]) -> float:
        """Calculate prediction confidence based on data available"""
        if not similar_contexts:
            return 0.0
        
        return min(0.95, len(similar_contexts) / 10.0)  # More data = higher confidence
    
    async def _get_temporal_patterns(self, user_id: str, day_of_week: str, hour_of_day: int) -> Dict:
        """Get patterns for specific time/day"""
        # Would search archives for this temporal pattern
        return {}
    
    async def _correlate_context_with_mood(self, user_id: str, context: Dict) -> Dict:
        """Correlate current context with historical moods"""
        # Would search journal archives and correlate
        return {
            "most_likely_mood": "neutral",
            "confidence": 0.5,
            "influencing_factors": []
        }

    async def generate_post_action_suggestions(
        self,
        tool_name: str,
        params: dict,
        result: dict,
        user_id: str,
        context: dict
    ) -> dict:
        """
        Generate intelligent suggestions after tool execution.
        Uses existing learning systems - no hardcoded rules where possible.
        """
        import sqlite3
        from typing import List, Optional
        
        suggestions = []
        alternatives = []
        insights = []
        
        try:
            # Use unified_learner for learned patterns
            from unified_learner import unified_learner
            
            # Shopping list suggestions (learned from behavior)
            if tool_name == "add_to_list":
                item = params.get("item", "")
                list_type = params.get("list_type", "shopping")
                
                if list_type == "shopping" and item:
                    # Get items user frequently buys together
                    related = await unified_learner.get_frequently_bought_together(
                        user_id, item, min_confidence=0.5
                    )
                    
                    for related_item in related[:3]:  # Max 3 suggestions
                        suggestions.append({
                            "type": "frequent_together",
                            "action": f"You usually buy {related_item} with {item}. Add it?",
                            "confidence": 0.85,
                            "mcp_tool": "add_to_list",
                            "params": {"item": related_item, "list_type": list_type}
                        })
            
            # Check pattern thresholds
            pattern_insights = await self._check_pattern_thresholds(
                user_id, tool_name, params
            )
            insights.extend(pattern_insights)
            
            # Suggest better approaches
            better = await self._suggest_better_approach(
                tool_name, params, context
            )
            if better:
                alternatives.append(better)
            
            # Get related actions from learning
            related_actions = await unified_learner.get_related_actions(
                user_id, tool_name, params
            )
            for action in related_actions[:2]:  # Max 2 related actions
                suggestions.append({
                    "type": "related_action",
                    "action": f"Based on your patterns, you might want to {action['tool_name']}?",
                    "confidence": 0.6,
                    "mcp_tool": action["tool_name"],
                    "params": action.get("params", {})
                })
        
        except Exception as e:
            logger.warning(f"Suggestion generation failed: {e}")
        
        return {
            "suggestions": suggestions,
            "alternatives": alternatives,
            "insights": insights
        }

    async def _check_pattern_thresholds(
        self, user_id: str, tool_name: str, params: dict
    ) -> List[str]:
        """Check if action triggers threshold-based insights"""
        import sqlite3
        
        insights = []
        
        if tool_name == "add_to_list":
            list_type = params.get("list_type", "shopping")
            
            # Count items using existing table
            conn = sqlite3.connect("/app/data/zoe.db")
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM list_items li
                JOIN lists l ON li.list_id = l.id
                WHERE l.user_id = ?
                  AND l.list_type = ?
                  AND li.completed = 0
            """, (user_id, list_type))
            
            result = cursor.fetchone()
            count = result[0] if result else 0
            conn.close()
            
            if count >= 6:
                insights.append(f"You have {count} items now. Schedule a shopping trip?")
        
        return insights

    async def _suggest_better_approach(
        self, tool_name: str, params: dict, context: dict
    ) -> Optional[dict]:
        """Suggest alternative approaches that might be better"""
        
        if tool_name == "create_calendar_event":
            message = context.get("message", "").lower()
            recurring_keywords = ["weekly", "daily", "every", "recurring", "each"]
            
            if any(kw in message for kw in recurring_keywords):
                return {
                    "type": "better_way",
                    "suggestion": "Make this a recurring event?",
                    "why": "Saves you from creating it each time",
                    "mcp_tool": "update_event_recurrence"
                }
        
        elif tool_name == "add_to_list" and params.get("list_type") == "tasks":
            # Suggest calendar event for time-sensitive tasks
            item_text = params.get("item", "").lower()
            time_words = ["tomorrow", "today", "monday", "tuesday", "wednesday", 
                         "thursday", "friday", "saturday", "sunday", "next week"]
            
            if any(word in item_text for word in time_words):
                return {
                    "type": "better_way",
                    "suggestion": "Add this to your calendar instead?",
                    "why": "Time-sensitive tasks work better as calendar events",
                    "mcp_tool": "create_calendar_event"
                }
        
        return None


# Global instance
predictive_intelligence = PredictiveIntelligence()




