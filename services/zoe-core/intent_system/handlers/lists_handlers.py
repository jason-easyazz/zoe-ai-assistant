"""
List Intent Handlers
====================

Handlers for list-related intents (shopping, todo, work, etc).

Features:
- Smart list type inference from item content
- Personalized responses with user's name
- Clarifying questions when context is ambiguous

Intents handled:
- ListAdd: Add item to list
- ListRemove: Remove item from list
- ListShow: Show list contents
- ListClear: Clear all items from list
- ListComplete: Mark item as complete
"""

import logging
import sqlite3
import os
import re
from typing import Dict, Any, Optional, Tuple

from intent_system.classifiers import ZoeIntent

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# Keywords for smart list type inference
WORK_KEYWORDS = [
    "meeting", "client", "project", "deadline", "report", "presentation",
    "email", "call with", "review", "submit", "invoice", "proposal",
    "conference", "standup", "sprint", "task", "deliverable", "stakeholder",
    "budget", "schedule meeting", "follow up with", "prepare"
]

SHOPPING_KEYWORDS = [
    "milk", "eggs", "bread", "butter", "cheese", "meat", "chicken", "beef",
    "vegetables", "fruit", "apples", "bananas", "groceries", "cereal",
    "coffee", "tea", "sugar", "flour", "rice", "pasta", "toilet paper",
    "soap", "shampoo", "detergent", "paper towels", "snacks", "juice"
]

PERSONAL_KEYWORDS = [
    "call mom", "call dad", "pick up kids", "doctor appointment", "dentist",
    "birthday", "anniversary", "gift", "exercise", "gym", "haircut",
    "renew", "pay bills", "clean", "organize", "pack", "book"
]


def get_user_name(user_id: str) -> str:
    """Get user's first name for personalized responses."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Try users table display_name
        cursor.execute("""
            SELECT display_name FROM users WHERE id = ?
        """, (user_id,))
        row = cursor.fetchone()
        
        if row and row[0]:
            conn.close()
            # Get first name from display name
            return row[0].split()[0]
        
        # Try username as fallback
        cursor.execute("""
            SELECT username FROM users WHERE id = ?
        """, (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0]:
            # Capitalize username as name
            return row[0].split()[0].title()
            
    except Exception as e:
        logger.warning(f"Could not get user name: {e}")
    
    return ""


def infer_list_type(item: str, explicit_list: Optional[str] = None) -> Tuple[str, float]:
    """
    Infer the most appropriate list type from item content.
    
    Returns:
        Tuple of (list_type, confidence)
        - confidence >= 0.8: High confidence, auto-assign
        - confidence < 0.8: Ask for clarification
    """
    if explicit_list:
        return (explicit_list, 1.0)
    
    item_lower = item.lower()
    
    # Check for work keywords
    for keyword in WORK_KEYWORDS:
        if keyword in item_lower:
            return ("work_todos", 0.9)
    
    # Check for shopping keywords
    for keyword in SHOPPING_KEYWORDS:
        if keyword in item_lower:
            return ("shopping", 0.95)
    
    # Check for personal keywords
    for keyword in PERSONAL_KEYWORDS:
        if keyword in item_lower:
            return ("personal_todos", 0.85)
    
    # Default to shopping with low confidence (will prompt)
    return ("shopping", 0.5)


def get_friendly_list_name(list_type: str) -> str:
    """Convert list type to friendly name."""
    names = {
        "shopping": "shopping list",
        "work_todos": "work list",
        "personal_todos": "personal to-do list",
        "todo": "to-do list",
        "bucket": "bucket list",
    }
    return names.get(list_type, f"{list_type} list")


async def handle_list_add(
    intent: ZoeIntent,
    user_id: str,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle ListAdd intent with smart inference and personalization.
    
    Features:
    - Infers list type from item content (work, shopping, personal)
    - Uses user's name in confirmations
    - Asks clarifying questions when unsure
    """
    item = intent.slots.get("item")
    explicit_list = intent.slots.get("list")
    priority = intent.slots.get("priority", "medium")
    
    # Get user's name for personalization
    user_name = get_user_name(user_id)
    name_prefix = f"No worries {user_name}, " if user_name else ""
    logger.info(f"🧑 User lookup for {user_id}: name='{user_name}', prefix='{name_prefix}'")
    
    if not item:
        if user_name:
            return {
                "success": False,
                "message": f"Hey {user_name}, what would you like me to add?",
                "needs_clarification": True,
                "clarification_type": "item"
            }
        return {
            "success": False,
            "message": "What would you like me to add?",
            "needs_clarification": True,
            "clarification_type": "item"
        }
    
    # Smart list type inference
    list_type, confidence = infer_list_type(item, explicit_list)
    
    # If low confidence and no explicit list, ask for clarification
    if confidence < 0.7 and not explicit_list:
        friendly_name = get_friendly_list_name(list_type)
        return {
            "success": False,
            "message": f"I'd like to add \"{item}\" - should I put this on your shopping list, work list, or personal to-do list?",
            "needs_clarification": True,
            "clarification_type": "list_type",
            "pending_item": item,
            "data": {"item": item, "suggested_list": list_type}
        }
    
    # Format item to title case
    item = item.title()
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get or create list
        cursor.execute("""
            SELECT id FROM lists 
            WHERE user_id = ? AND list_type = ? 
            LIMIT 1
        """, (user_id, list_type))
        
        row = cursor.fetchone()
        if row:
            list_id = row[0]
        else:
            cursor.execute("""
                INSERT INTO lists (user_id, list_type, list_category, name)
                VALUES (?, ?, ?, ?)
            """, (user_id, list_type, list_type, f"{list_type.title()} List"))
            list_id = cursor.lastrowid
        
        # Add item
        cursor.execute("""
            INSERT INTO list_items (list_id, task_text, priority, completed)
            VALUES (?, ?, ?, 0)
        """, (list_id, item, priority))
        
        conn.commit()
        conn.close()
        
        logger.info(f"⚡ Added '{item}' to {list_type} list for user {user_id}")
        
        # Broadcast WebSocket update
        try:
            from routers.lists import lists_ws_manager
            await lists_ws_manager.broadcast_to_user(user_id, {
                "type": "item_added",
                "list_type": list_type,
                "item": item
            })
        except Exception as e:
            logger.warning(f"WebSocket broadcast failed: {e}")
        
        # Build personalized response based on list type
        friendly_name = get_friendly_list_name(list_type)
        
        if list_type == "work_todos":
            message = f"{name_prefix}I've added \"{item}\" to your work list. 💼"
        elif list_type == "shopping":
            message = f"{name_prefix}I've added {item} to your shopping list. 🛒"
        elif list_type == "personal_todos":
            message = f"{name_prefix}I've added \"{item}\" to your personal to-do list. ✅"
        else:
            message = f"{name_prefix}Added {item} to your {friendly_name}!"
        
        return {
            "success": True,
            "message": message,
            "data": {
                "item": item,
                "list": list_type,
                "list_id": list_id,
                "inferred": not explicit_list,
                "confidence": confidence
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to add item to list: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't add that right now. Can you try again?"
        }


async def handle_list_remove(
    intent: ZoeIntent,
    user_id: str,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle ListRemove intent.
    """
    item = intent.slots.get("item")
    list_type = intent.slots.get("list", context.get("last_list", "shopping"))
    
    user_name = get_user_name(user_id)
    
    if not item:
        # Check if we can use context
        last_items = context.get("last_items", [])
        if last_items:
            return {
                "success": False,
                "message": f"Which item would you like to remove? The last ones mentioned were: {', '.join(last_items[:3])}",
                "needs_clarification": True
            }
        return {
            "success": False,
            "message": "What would you like to remove?"
        }
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Find and remove item (fuzzy match)
        cursor.execute("""
            SELECT li.id, li.task_text, l.list_type
            FROM list_items li
            JOIN lists l ON li.list_id = l.id
            WHERE l.user_id = ? AND LOWER(li.task_text) LIKE ?
            AND li.completed = 0
            LIMIT 1
        """, (user_id, f"%{item.lower()}%"))
        
        row = cursor.fetchone()
        if row:
            item_id, actual_item, actual_list = row
            cursor.execute("DELETE FROM list_items WHERE id = ?", (item_id,))
            conn.commit()
            conn.close()
            
            friendly_name = get_friendly_list_name(actual_list)
            if user_name:
                return {
                    "success": True,
                    "message": f"Done {user_name}! Removed {actual_item} from your {friendly_name}. ✓",
                    "data": {"item": actual_item, "list": actual_list}
                }
            return {
                "success": True,
                "message": f"Removed {actual_item} from your {friendly_name}. ✓",
                "data": {"item": actual_item, "list": actual_list}
            }
        else:
            conn.close()
            return {
                "success": False,
                "message": f"I couldn't find \"{item}\" on any of your lists. Would you like me to check a specific list?"
            }
            
    except Exception as e:
        logger.error(f"Failed to remove item: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't remove that item."
        }


async def handle_list_show(
    intent: ZoeIntent,
    user_id: str,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle ListShow intent.
    """
    list_type = intent.slots.get("list", context.get("last_list", "shopping"))
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT li.task_text, li.completed
            FROM list_items li
            JOIN lists l ON li.list_id = l.id
            WHERE l.user_id = ? AND l.list_type = ?
            ORDER BY li.completed ASC, li.id DESC
            LIMIT 20
        """, (user_id, list_type))
        
        items = cursor.fetchall()
        conn.close()
        
        friendly_name = get_friendly_list_name(list_type)
        
        if not items:
            user_name = get_user_name(user_id)
            if user_name:
                return {
                    "success": True,
                    "message": f"Your {friendly_name} is empty, {user_name}. Would you like to add something?",
                    "data": {"items": [], "list": list_type}
                }
            return {
                "success": True,
                "message": f"Your {friendly_name} is empty.",
                "data": {"items": [], "list": list_type}
            }
        
        # Format items nicely
        active_items = [item for item, completed in items if not completed]
        completed_items = [item for item, completed in items if completed]
        
        lines = [f"Here's your {friendly_name}:"]
        for item in active_items:
            lines.append(f"• {item}")
        
        if completed_items:
            lines.append(f"\n✓ Completed ({len(completed_items)}):")
            for item in completed_items[:3]:
                lines.append(f"  ~~{item}~~")
        
        return {
            "success": True,
            "message": "\n".join(lines),
            "data": {
                "items": active_items,
                "completed": completed_items,
                "list": list_type
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to show list: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't retrieve your list."
        }


async def handle_list_clear(
    intent: ZoeIntent,
    user_id: str,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle ListClear intent with confirmation.
    """
    list_type = intent.slots.get("list", context.get("last_list", "shopping"))
    confirmed = intent.slots.get("confirmed", False)
    
    friendly_name = get_friendly_list_name(list_type)
    user_name = get_user_name(user_id)
    
    # Ask for confirmation before clearing
    if not confirmed:
        return {
            "success": False,
            "message": f"Are you sure you want to clear your entire {friendly_name}? Say 'yes, clear it' to confirm.",
            "needs_clarification": True,
            "clarification_type": "confirm_clear",
            "data": {"list": list_type}
        }
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            DELETE FROM list_items 
            WHERE list_id IN (
                SELECT id FROM lists WHERE user_id = ? AND list_type = ?
            )
        """, (user_id, list_type))
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted_count > 0:
            if user_name:
                return {
                    "success": True,
                    "message": f"Done {user_name}! Cleared {deleted_count} items from your {friendly_name}. 🧹",
                    "data": {"cleared": deleted_count, "list": list_type}
                }
            return {
                "success": True,
                "message": f"Cleared {deleted_count} items from your {friendly_name}. 🧹",
                "data": {"cleared": deleted_count, "list": list_type}
            }
        else:
            return {
                "success": True,
                "message": f"Your {friendly_name} was already empty!",
                "data": {"cleared": 0, "list": list_type}
            }
            
    except Exception as e:
        logger.error(f"Failed to clear list: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't clear the list."
        }


async def handle_list_complete(
    intent: ZoeIntent,
    user_id: str,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle ListComplete intent.
    """
    item = intent.slots.get("item")
    list_type = intent.slots.get("list", context.get("last_list"))
    
    user_name = get_user_name(user_id)
    
    if not item:
        last_items = context.get("last_items", [])
        if last_items:
            return {
                "success": False,
                "message": f"Which one did you complete? {', '.join(last_items[:3])}?",
                "needs_clarification": True
            }
        return {
            "success": False,
            "message": "Which item did you complete?"
        }
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Find and mark item as complete
        cursor.execute("""
            UPDATE list_items 
            SET completed = 1
            WHERE id IN (
                SELECT li.id FROM list_items li
                JOIN lists l ON li.list_id = l.id
                WHERE l.user_id = ? AND LOWER(li.task_text) LIKE ?
                AND li.completed = 0
                LIMIT 1
            )
        """, (user_id, f"%{item.lower()}%"))
        
        if cursor.rowcount > 0:
            conn.commit()
            conn.close()
            
            responses = [
                f"Nice work{', ' + user_name if user_name else ''}! ✓ Marked {item} as complete.",
                f"Done! ✓ {item} is checked off.",
                f"Great job{', ' + user_name if user_name else ''}! {item} is complete. 🎉",
            ]
            import random
            return {
                "success": True,
                "message": random.choice(responses),
                "data": {"item": item, "completed": True}
            }
        else:
            conn.close()
            return {
                "success": False,
                "message": f"I couldn't find \"{item}\" in your active items."
            }
            
    except Exception as e:
        logger.error(f"Failed to complete item: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't mark that as complete."
        }
