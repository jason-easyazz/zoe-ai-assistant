"""
Intelligent Suggestion Engine
Generates context-aware suggestions after action execution
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime
import sqlite3
import json

logger = logging.getLogger(__name__)

class SuggestionEngine:
    """Generate intelligent suggestions based on actions and context"""
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        self.db_path = db_path
        
        # Define suggestion rules per tool type
        self.suggestion_rules = {
            "add_to_list": self._suggest_list_additions,
            "create_calendar_event": self._suggest_calendar_related,
            "add_person": self._suggest_person_related,
            "create_note": self._suggest_note_related,
            "control_device": self._suggest_automation,
        }
    
    async def generate_post_action_suggestions(
        self, 
        tool_name: str, 
        params: Dict, 
        result: Dict,
        user_id: str,
        context: Dict
    ) -> Dict[str, any]:
        """
        Generate intelligent suggestions after successful action execution
        
        Returns:
            {
                "suggestions": [
                    {
                        "type": "related_action",
                        "action": "Would you like to set a reminder for this?",
                        "confidence": 0.8,
                        "tool": "create_reminder",
                        "params": {...}
                    },
                    ...
                ],
                "alternatives": [
                    {
                        "type": "better_way",
                        "suggestion": "You could also create a recurring event...",
                        "why": "More efficient for weekly meetings"
                    }
                ],
                "insights": [
                    "You've added 5 items to shopping list this week - want to schedule a shopping trip?"
                ]
            }
        """
        
        suggestions = []
        alternatives = []
        insights = []
        
        try:
            # Get context-aware suggestions based on tool type
            if tool_name in self.suggestion_rules:
                tool_suggestions = await self.suggestion_rules[tool_name](
                    params, result, user_id, context
                )
                suggestions.extend(tool_suggestions.get("suggestions", []))
                alternatives.extend(tool_suggestions.get("alternatives", []))
            
            # Get pattern-based insights
            pattern_insights = await self._get_pattern_insights(tool_name, user_id)
            insights.extend(pattern_insights)
            
            # Get time-based suggestions
            temporal_suggestions = await self._get_temporal_suggestions(user_id, context)
            suggestions.extend(temporal_suggestions)
            
        except Exception as e:
            logger.error(f"Error generating suggestions: {e}")
        
        return {
            "suggestions": suggestions[:3],  # Top 3 suggestions
            "alternatives": alternatives[:2],  # Top 2 alternatives
            "insights": insights[:2]  # Top 2 insights
        }
    
    async def _suggest_list_additions(
        self, 
        params: Dict, 
        result: Dict, 
        user_id: str,
        context: Dict
    ) -> Dict:
        """Suggest related items when adding to shopping list"""
        suggestions = []
        alternatives = []
        
        item_added = params.get("item", "")
        list_type = params.get("list_type", "shopping")
        
        # Learn patterns from user's actual behavior (no hardcoding!)
        try:
            from unified_learner import unified_learner
            frequent_together = await unified_learner.get_frequently_bought_together(
                user_id, item_added, min_confidence=0.5
            )
            for item in frequent_together[:2]:
                if not await self._item_on_list(user_id, item, list_type):
                    suggestions.append({
                        "type": "frequent_together",
                        "action": f"You usually buy {item} with {item_added}. Add it?",
                        "confidence": 0.85,
                        "tool": "add_to_list",
                        "params": {"item": item, "list_type": list_type}
                    })
        except Exception as e:
            logger.warning(f"Could not get learned patterns: {e}")
        
        # Suggest scheduling shopping trip if many items
        item_count = await self._get_list_item_count(user_id, list_type)
        if item_count >= 6:
            suggestions.append({
                "type": "action_trigger",
                "action": f"You have {item_count} items now. Schedule a shopping trip?",
                "confidence": 0.7,
                "tool": "create_calendar_event",
                "params": {
                    "title": "Shopping Trip",
                    "description": f"Buy {item_count} items from shopping list"
                }
            })
        
        return {"suggestions": suggestions, "alternatives": alternatives}
    
    async def _suggest_calendar_related(
        self, 
        params: Dict, 
        result: Dict, 
        user_id: str,
        context: Dict
    ) -> Dict:
        """Suggest related actions when creating calendar events"""
        suggestions = []
        alternatives = []
        
        event_title = params.get("title", "").lower()
        event_time = params.get("start_time", "")
        message = context.get("message", "").lower()
        
        # Suggest reminder
        suggestions.append({
            "type": "reminder",
            "action": "Set a reminder for this event?",
            "confidence": 0.9,
            "tool": "create_reminder",
            "params": {
                "title": f"Reminder: {params.get('title')}",
                "time": event_time
            }
        })
        
        # Detect recurring patterns from message
        recurring_keywords = ["weekly", "every week", "daily", "every day", 
                            "monthly", "every month", "recurring", "each"]
        is_recurring_mentioned = any(kw in message for kw in recurring_keywords)
        
        # Meeting-specific suggestions
        if any(word in event_title for word in ["meeting", "call", "interview", "standup", "sync"]):
            suggestions.append({
                "type": "preparation",
                "action": "Create a prep checklist for this meeting?",
                "confidence": 0.75,
                "tool": "create_list",
                "params": {
                    "name": f"{params.get('title')} - Prep",
                    "list_type": "tasks"
                }
            })
            
            # Alternative: recurring meeting (if recurring words detected)
            if is_recurring_mentioned:
                alternatives.append({
                    "type": "better_way",
                    "suggestion": "Make this a recurring event?",
                    "why": "Saves you from creating it each time",
                    "tool": "create_calendar_event",
                    "params": {**params, "recurring": "weekly"}
                })
        
        # Appointment-specific
        if any(word in event_title for word in ["doctor", "dentist", "appointment", "checkup", "visit"]):
            suggestions.append({
                "type": "related",
                "action": "Add travel time before this appointment?",
                "confidence": 0.8,
                "tool": "create_calendar_event",
                "params": {
                    "title": f"Travel to {params.get('title')}",
                    "duration": "30 minutes"
                }
            })
            
            # Suggest setting recurring for regular checkups
            if any(word in message for word in ["checkup", "regular", "annual", "yearly"]):
                alternatives.append({
                    "type": "better_way",
                    "suggestion": "Set this as an annual recurring appointment?",
                    "why": "Never forget your regular checkups",
                    "tool": "create_calendar_event",
                    "params": {**params, "recurring": "yearly"}
                })
        
        # Workout/exercise specific
        if any(word in event_title for word in ["workout", "gym", "exercise", "yoga", "run"]):
            if is_recurring_mentioned:
                alternatives.append({
                    "type": "better_way",
                    "suggestion": "Make this a recurring workout schedule?",
                    "why": "Helps build consistent habits",
                    "tool": "create_calendar_event",
                    "params": {**params, "recurring": "weekly"}
                })
        
        return {"suggestions": suggestions, "alternatives": alternatives}
    
    async def _suggest_person_related(
        self, 
        params: Dict, 
        result: Dict, 
        user_id: str,
        context: Dict
    ) -> Dict:
        """Suggest actions when adding a person"""
        suggestions = []
        
        person_name = params.get("name", "")
        relationship = params.get("relationship", "")
        
        # Suggest setting up reminder to stay in touch
        if relationship in ["friend", "family", "colleague"]:
            suggestions.append({
                "type": "relationship_maintenance",
                "action": f"Set a reminder to check in with {person_name}?",
                "confidence": 0.7,
                "tool": "create_reminder",
                "params": {
                    "title": f"Check in with {person_name}",
                    "recurring": "monthly"
                }
            })
        
        # Suggest adding birthday if close relationship
        if relationship in ["friend", "family", "spouse", "partner"]:
            suggestions.append({
                "type": "important_date",
                "action": f"Would you like to add {person_name}'s birthday?",
                "confidence": 0.85,
                "tool": "update_person",
                "params": {
                    "name": person_name,
                    "field": "birthday"
                }
            })
        
        return {"suggestions": suggestions, "alternatives": []}
    
    async def _suggest_note_related(
        self, 
        params: Dict, 
        result: Dict, 
        user_id: str,
        context: Dict
    ) -> Dict:
        """Suggest actions when creating notes"""
        suggestions = []
        alternatives = []
        
        note_content = params.get("content", "").lower()
        
        # Detect if note contains action items
        action_words = ["todo", "need to", "must", "should", "remember to"]
        if any(word in note_content for word in action_words):
            suggestions.append({
                "type": "convert_to_task",
                "action": "This looks like a task. Convert to your task list?",
                "confidence": 0.8,
                "tool": "add_to_list",
                "params": {"list_type": "tasks"}
            })
        
        # Detect if note is meeting notes
        if "meeting" in note_content or "discussed" in note_content:
            suggestions.append({
                "type": "follow_up",
                "action": "Create follow-up tasks from these meeting notes?",
                "confidence": 0.75,
                "tool": "extract_tasks_from_note",
                "params": {"note_id": result.get("note_id")}
            })
        
        return {"suggestions": suggestions, "alternatives": alternatives}
    
    async def _suggest_automation(
        self, 
        params: Dict, 
        result: Dict, 
        user_id: str,
        context: Dict
    ) -> Dict:
        """Suggest automations when controlling devices"""
        suggestions = []
        alternatives = []
        
        device_name = params.get("device", "")
        action = params.get("action", "")
        
        # Suggest creating automation/scene
        suggestions.append({
            "type": "automation",
            "action": f"Create an automation for {device_name}?",
            "confidence": 0.6,
            "why": "You can trigger this automatically based on time or other conditions",
            "tool": "create_automation"
        })
        
        # Suggest scene if turning on multiple things
        recent_controls = await self._get_recent_device_controls(user_id, minutes=5)
        if len(recent_controls) >= 2:
            alternatives.append({
                "type": "better_way",
                "suggestion": "Create a scene to control multiple devices at once",
                "why": "One command instead of multiple",
                "devices": [d["device"] for d in recent_controls]
            })
        
        return {"suggestions": suggestions, "alternatives": alternatives}
    
    async def _get_pattern_insights(self, tool_name: str, user_id: str) -> List[str]:
        """Generate insights based on usage patterns"""
        insights = []
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Count recent usage of this tool
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM action_logs
                WHERE user_id = ? AND tool_name = ?
                AND timestamp > datetime('now', '-7 days')
            """, (user_id, tool_name))
            
            result = cursor.fetchone()
            count = result[0] if result else 0
            
            if count >= 5:
                insights.append(
                    f"You've used this feature {count} times this week - "
                    f"you're really staying organized!"
                )
            
            conn.close()
        except Exception as e:
            logger.error(f"Error getting pattern insights: {e}")
        
        return insights
    
    async def _get_temporal_suggestions(
        self, 
        user_id: str, 
        context: Dict
    ) -> List[Dict]:
        """Get time-based suggestions"""
        suggestions = []
        hour = datetime.now().hour
        day = datetime.now().strftime("%A")
        
        # Evening planning reminder
        if hour >= 18 and day == "Sunday":
            suggestions.append({
                "type": "proactive",
                "action": "Would you like to plan your week?",
                "confidence": 0.6,
                "tool": "orchestrate",
                "params": {"task": "weekly_planning"}
            })
        
        # Morning routine check
        if 6 <= hour <= 9:
            suggestions.append({
                "type": "proactive",
                "action": "Check today's schedule?",
                "confidence": 0.5,
                "tool": "get_calendar_events",
                "params": {"period": "today"}
            })
        
        return suggestions
    
    async def log_suggestions_shown(
        self,
        user_id: str,
        tool_name: str,
        suggestions: List[Dict]
    ) -> None:
        """Log which suggestions were shown to the user"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Find the most recent action log for this user/tool
            cursor.execute("""
                UPDATE action_logs
                SET suggestions_shown = ?
                WHERE user_id = ? AND tool_name = ?
                  AND id = (
                      SELECT id FROM action_logs
                      WHERE user_id = ? AND tool_name = ?
                      ORDER BY timestamp DESC
                      LIMIT 1
                  )
            """, (
                json.dumps([s["action"] for s in suggestions]),
                user_id,
                tool_name,
                user_id,
                tool_name
            ))
            
            conn.commit()
            conn.close()
            logger.debug(f"Logged {len(suggestions)} suggestions for {tool_name}")
        except Exception as e:
            logger.warning(f"Failed to log suggestions: {e}")
    
    async def log_suggestion_accepted(
        self,
        user_id: str,
        tool_name: str,
        accepted_suggestion: str
    ) -> None:
        """Log when a user accepts a suggestion"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE action_logs
                SET suggestion_accepted = ?,
                    suggestion_accepted_at = datetime('now')
                WHERE user_id = ? AND tool_name = ?
                  AND suggestions_shown IS NOT NULL
                  AND suggestion_accepted IS NULL
                ORDER BY timestamp DESC
                LIMIT 1
            """, (accepted_suggestion, user_id, tool_name))
            
            conn.commit()
            conn.close()
            logger.info(f"✅ User accepted suggestion: {accepted_suggestion}")
        except Exception as e:
            logger.warning(f"Failed to log accepted suggestion: {e}")
    
    async def get_suggestion_acceptance_rate(
        self,
        user_id: str,
        tool_name: Optional[str] = None
    ) -> Dict:
        """Get suggestion acceptance statistics"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if tool_name:
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total_shown,
                        SUM(CASE WHEN suggestion_accepted IS NOT NULL THEN 1 ELSE 0 END) as accepted
                    FROM action_logs
                    WHERE user_id = ? AND tool_name = ?
                      AND suggestions_shown IS NOT NULL
                """, (user_id, tool_name))
            else:
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total_shown,
                        SUM(CASE WHEN suggestion_accepted IS NOT NULL THEN 1 ELSE 0 END) as accepted
                    FROM action_logs
                    WHERE user_id = ?
                      AND suggestions_shown IS NOT NULL
                """, (user_id,))
            
            result = cursor.fetchone()
            conn.close()
            
            total = result[0] if result else 0
            accepted = result[1] if result else 0
            rate = (accepted / total * 100) if total > 0 else 0
            
            return {
                "total_suggestions_shown": total,
                "total_accepted": accepted,
                "acceptance_rate": round(rate, 2)
            }
        except Exception as e:
            logger.error(f"Error getting acceptance rate: {e}")
            return {"total_suggestions_shown": 0, "total_accepted": 0, "acceptance_rate": 0}
    
    # Helper methods
    async def _item_on_list(
        self, 
        user_id: str, 
        item: str, 
        list_type: str
    ) -> bool:
        """Check if item is already on list"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 1 FROM list_items li
                JOIN lists l ON li.list_id = l.id
                WHERE l.user_id = ? AND l.list_type = ?
                AND li.task_text LIKE ?
                AND li.completed = 0
                LIMIT 1
            """, (user_id, list_type, f"%{item}%"))
            result = cursor.fetchone() is not None
            conn.close()
            return result
        except Exception as e:
            logger.error(f"Error checking if item on list: {e}")
            return False
    
    async def _get_list_item_count(
        self, 
        user_id: str, 
        list_type: str
    ) -> int:
        """Count items on a list - use action_logs for immediate counts"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # First try list_items table
            cursor.execute("""
                SELECT COUNT(*) FROM list_items li
                JOIN lists l ON li.list_id = l.id
                WHERE l.user_id = ? AND l.list_type = ?
                AND li.completed = 0
            """, (user_id, list_type))
            result1 = cursor.fetchone()
            list_items_count = result1[0] if result1 else 0
            
            # Also count from recent action_logs (last 24 hours) for immediate feedback
            cursor.execute("""
                SELECT COUNT(DISTINCT json_extract(tool_params, '$.item'))
                FROM action_logs
                WHERE user_id = ?
                  AND tool_name = 'add_to_list'
                  AND json_extract(tool_params, '$.list_type') = ?
                  AND timestamp > datetime('now', '-24 hours')
            """, (user_id, list_type))
            result2 = cursor.fetchone()
            action_count = result2[0] if result2 else 0
            
            conn.close()
            
            # Use the higher count (action_logs is more immediate)
            final_count = max(list_items_count, action_count)
            logger.debug(f"📊 Item count for {user_id}/{list_type}: list_items={list_items_count}, action_logs={action_count}, final={final_count}")
            return final_count
        except Exception as e:
            logger.error(f"Error counting list items: {e}")
            return 0
    
    async def _get_recent_device_controls(
        self, 
        user_id: str, 
        minutes: int = 5
    ) -> List[Dict]:
        """Get recent device control actions"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT tool_params FROM action_logs
                WHERE user_id = ? AND tool_name = 'control_device'
                AND timestamp > datetime('now', '-' || ? || ' minutes')
                ORDER BY timestamp DESC
            """, (user_id, minutes))
            rows = cursor.fetchall()
            conn.close()
            return [json.loads(row[0]) for row in rows]
        except Exception as e:
            logger.error(f"Error getting recent device controls: {e}")
            return []

# Global instance
suggestion_engine = SuggestionEngine()

