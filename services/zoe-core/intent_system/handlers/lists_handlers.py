"""
List Intent Handlers
====================

Handlers for list-related intents (shopping, todo, work, etc).

Intents handled:
- ListAdd: Add item to list
- ListRemove: Remove item from list
- ListShow: Show list contents
- ListClear: Clear all items from list
- ListComplete: Mark item as complete
"""

import logging
import sqlite3
from typing import Dict, Any

from intent_system.classifiers import ZoeIntent

logger = logging.getLogger(__name__)


async def handle_list_add(
    intent: ZoeIntent,
    user_id: str,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle ListAdd intent.
    
    Slots:
        - item: Item to add (required)
        - list: List type (optional, default "shopping")
        - priority: Priority level (optional, default "medium")
    
    Args:
        intent: The classified intent
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Result dictionary with success status and message
    """
    item = intent.slots.get("item")
    list_type = intent.slots.get("list", "shopping")
    priority = intent.slots.get("priority", "medium")
    
    if not item:
        return {
            "success": False,
            "message": "I didn't catch what you want to add. Can you repeat?"
        }
    
    # Format item to title case for better readability
    item = item.title()
    
    try:
        # Direct database insert (fastest path - <10ms target)
        conn = sqlite3.connect("/app/data/zoe.db")
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
            # Create new list
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
        
        logger.info(f"⚡ DIRECT: Added '{item}' to {list_type} list for user {user_id} (no LLM!)")
        
        # Broadcast WebSocket update to refresh UI
        try:
            from routers.lists import lists_ws_manager
            await lists_ws_manager.broadcast_to_user(user_id, {
                "type": "item_added",
                "list_type": list_type,
                "item": item
            })
        except Exception as e:
            logger.warning(f"WebSocket broadcast failed: {e}")
        
        return {
            "success": True,
            "message": f"✅ Added {item} to your {list_type} list!",
            "data": {
                "item": item,
                "list": list_type,
                "list_id": list_id
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to add item to list: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't add that right now."
        }


async def handle_list_remove(
    intent: ZoeIntent,
    user_id: str,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle ListRemove intent.
    
    Slots:
        - item: Item to remove (required)
        - list: List type (optional, inferred from context)
    """
    item = intent.slots.get("item")
    list_type = intent.slots.get("list", context.get("last_list", "shopping"))
    
    if not item:
        return {
            "success": False,
            "message": "What would you like to remove?"
        }
    
    try:
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        
        # Find and delete item
        cursor.execute("""
            DELETE FROM list_items 
            WHERE id IN (
                SELECT li.id FROM list_items li
                JOIN lists l ON li.list_id = l.id
                WHERE l.user_id = ? 
                AND l.list_type = ?
                AND li.task_text = ?
                LIMIT 1
            )
        """, (user_id, list_type, item))
        
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted > 0:
            logger.info(f"Removed '{item}' from {list_type} list for user {user_id}")
            return {
                "success": True,
                "message": f"✅ Removed {item} from your {list_type} list!",
                "data": {"item": item, "list": list_type}
            }
        else:
            return {
                "success": False,
                "message": f"I couldn't find {item} on your {list_type} list."
            }
        
    except Exception as e:
        logger.error(f"Failed to remove item: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't remove that right now."
        }


async def handle_list_show(
    intent: ZoeIntent,
    user_id: str,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle ListShow intent.
    
    Slots:
        - list: List type to show (optional, default "shopping")
    """
    list_type = intent.slots.get("list", "shopping")
    
    try:
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        
        # Get list items
        cursor.execute("""
            SELECT li.task_text, li.completed, li.priority
            FROM list_items li
            JOIN lists l ON li.list_id = l.id
            WHERE l.user_id = ? AND l.list_type = ?
            ORDER BY li.id DESC
        """, (user_id, list_type))
        
        items = cursor.fetchall()
        conn.close()
        
        if not items:
            return {
                "success": True,
                "message": f"Your {list_type} list is empty.",
                "data": {"list": list_type, "items": []}
            }
        
        # Format items
        incomplete_items = [item[0] for item in items if not item[1]]
        complete_items = [item[0] for item in items if item[1]]
        
        # Build message
        message_parts = [f"Here's your {list_type} list:"]
        
        if incomplete_items:
            message_parts.append("\n".join([f"• {item}" for item in incomplete_items]))
        
        if complete_items:
            message_parts.append(f"\nCompleted: {', '.join(complete_items)}")
        
        return {
            "success": True,
            "message": "\n".join(message_parts),
            "data": {
                "list": list_type,
                "items": incomplete_items,
                "completed": complete_items,
                "total": len(items)
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to show list: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't retrieve your list right now."
        }


async def handle_list_clear(
    intent: ZoeIntent,
    user_id: str,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle ListClear intent.
    
    Slots:
        - list: List type to clear (optional, default "shopping")
    """
    list_type = intent.slots.get("list", "shopping")
    
    try:
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        
        # Delete all items from list
        cursor.execute("""
            DELETE FROM list_items 
            WHERE list_id IN (
                SELECT id FROM lists 
                WHERE user_id = ? AND list_type = ?
            )
        """, (user_id, list_type))
        
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted > 0:
            logger.info(f"Cleared {deleted} items from {list_type} list for user {user_id}")
            return {
                "success": True,
                "message": f"✅ Cleared your {list_type} list ({deleted} items removed).",
                "data": {"list": list_type, "deleted_count": deleted}
            }
        else:
            return {
                "success": True,
                "message": f"Your {list_type} list was already empty.",
                "data": {"list": list_type, "deleted_count": 0}
            }
        
    except Exception as e:
        logger.error(f"Failed to clear list: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't clear your list right now."
        }


async def handle_list_complete(
    intent: ZoeIntent,
    user_id: str,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle ListComplete intent.
    
    Slots:
        - item: Item to mark as complete (required)
        - list: List type (optional, inferred from context)
    """
    item = intent.slots.get("item")
    list_type = intent.slots.get("list", context.get("last_list", "shopping"))
    
    if not item:
        return {
            "success": False,
            "message": "What item did you complete?"
        }
    
    try:
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        
        # Mark item as complete
        cursor.execute("""
            UPDATE list_items 
            SET completed = 1
            WHERE id IN (
                SELECT li.id FROM list_items li
                JOIN lists l ON li.list_id = l.id
                WHERE l.user_id = ? 
                AND l.list_type = ?
                AND li.task_text = ?
                LIMIT 1
            )
        """, (user_id, list_type, item))
        
        updated = cursor.rowcount
        conn.commit()
        conn.close()
        
        if updated > 0:
            logger.info(f"Marked '{item}' as complete on {list_type} list for user {user_id}")
            return {
                "success": True,
                "message": f"✅ Marked {item} as complete!",
                "data": {"item": item, "list": list_type}
            }
        else:
            return {
                "success": False,
                "message": f"I couldn't find {item} on your {list_type} list."
            }
        
    except Exception as e:
        logger.error(f"Failed to complete item: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't update that item right now."
        }

