"""Action Executor - Enables Zoe to take real actions"""
import httpx
import logging
from datetime import datetime, timedelta
import re

logger = logging.getLogger(__name__)

async def detect_and_execute_action(message: str, context: dict) -> dict:
    """
    Detect user intent for actions and execute them.
    Returns action result or None if no action detected.
    """
    message_lower = message.lower()
    user_id = context.get("user_id", "default")
    
    # Detect reminder creation
    reminder_match = detect_reminder_request(message_lower)
    if reminder_match:
        return await create_reminder_action(reminder_match, message, user_id, context)
    
    # Detect event creation
    event_match = detect_event_request(message_lower)
    if event_match:
        return await create_event_action(event_match, message, user_id)
    
    # Detect task creation
    task_match = detect_task_request(message_lower)
    if task_match:
        return await create_task_action(task_match, message, user_id)
    
    # Detect list item addition
    list_item_match = detect_list_item_request(message_lower, message)
    if list_item_match:
        return await add_to_list_action(list_item_match, message, user_id)
    
    return None

def detect_reminder_request(message: str) -> dict:
    """Detect if user wants to create a reminder"""
    # Patterns like "remind me in 30 minutes", "reminder in 1 hour", etc.
    time_patterns = [
        (r'remind.*in (\d+) (minute|minutes|min|mins)', 'minutes'),
        (r'remind.*in (\d+) (hour|hours|hr|hrs)', 'hours'),
        (r'remind.*in (\d+) (day|days)', 'days'),
        (r'reminder.*in (\d+) (minute|minutes|min|mins)', 'minutes'),
        (r'set.*reminder.*(\d+) (minute|minutes|min|mins)', 'minutes'),
    ]
    
    for pattern, unit in time_patterns:
        match = re.search(pattern, message)
        if match:
            amount = int(match.group(1))
            return {
                'amount': amount,
                'unit': unit,
                'about': extract_reminder_subject(message)
            }
    
    return None

def extract_reminder_subject(message: str) -> str:
    """Extract what the reminder is about from the message"""
    # Look for context before "remind me"
    parts = message.split('remind me')
    if len(parts) > 1:
        before = parts[0].strip()
        # Remove common prefixes
        before = before.replace('yes', '').replace('ok', '').replace('sure', '').strip()
        if before:
            return before
    
    # Look for "about" or "to"
    if 'about' in message:
        about_parts = message.split('about')
        if len(about_parts) > 1:
            return about_parts[1].split('in')[0].strip()
    
    if ' to ' in message:
        to_parts = message.split(' to ')
        if len(to_parts) > 1:
            return to_parts[1].split('in')[0].strip()
    
    # Default: use previous context
    return "this"

def detect_event_request(message: str) -> dict:
    """Detect calendar event creation"""
    # Patterns like "create event", "schedule meeting", etc.
    patterns = [
        r'create.*event',
        r'schedule.*meeting',
        r'add.*to.*calendar',
        r'book.*appointment'
    ]
    
    if any(re.search(p, message) for p in patterns):
        return {'type': 'event', 'message': message}
    
    return None

def detect_task_request(message: str) -> dict:
    """Detect task creation"""
    patterns = [
        r'add.*task',
        r'create.*task',
        r'remind.*to do'
    ]
    
    if any(re.search(p, message) for p in patterns):
        return {'type': 'task', 'message': message}
    
    return None

def detect_list_item_request(message: str, original_message: str) -> dict:
    """Detect adding item to a list"""
    # Patterns: "add X to shopping list", "add X to the list", "put X on shopping list"
    patterns = [
        r'add (.*?) to (?:the |my )?(shopping|grocery|groceries) list',
        r'add (.*?) to (?:the |my )?list',
        r'put (.*?) on (?:the |my )?(shopping|grocery|groceries) list',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            item_name = match.group(1).strip()
            list_type = match.group(2) if len(match.groups()) > 1 else 'shopping'
            return {
                'item': item_name,
                'list_type': 'shopping' if list_type in ['shopping', 'grocery', 'groceries'] else 'personal',
                'original': original_message
            }
    
    return None

async def create_reminder_action(match: dict, message: str, user_id: str, context: dict) -> dict:
    """Create a reminder via API"""
    try:
        # Calculate reminder time
        amount = match['amount']
        unit = match['unit']
        
        now = datetime.now()
        if unit == 'minutes':
            remind_time = now + timedelta(minutes=amount)
        elif unit == 'hours':
            remind_time = now + timedelta(hours=amount)
        elif unit == 'days':
            remind_time = now + timedelta(days=amount)
        else:
            remind_time = now + timedelta(minutes=30)  # Default
        
        # Determine what to remind about
        about = match.get('about', 'this')
        
        # Get context from conversation history
        conversation = context.get('conversation_history', [])
        if about == 'this' and len(conversation) > 1:
            # Look at previous message for context
            prev_msg = conversation[-2] if len(conversation) >= 2 else {}
            if 'content' in prev_msg:
                about = prev_msg['content'][:100]  # First 100 chars
        
        # Create reminder title
        title = f"Reminder: {about}" if about != 'this' else "Reminder"
        
        # Call reminder API using reminder_time field
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"http://localhost:8000/api/reminders/",
                json={
                    "title": title,
                    "description": f"User requested reminder: {about}",
                    "reminder_time": remind_time.isoformat(),
                    "reminder_type": "once",
                    "category": "personal",
                    "priority": "medium",
                    "user_id": user_id
                }
            )
            
            if response.status_code in [200, 201]:
                result = response.json()
                return {
                    'success': True,
                    'action': 'reminder_created',
                    'title': title,
                    'time': remind_time.strftime('%I:%M %p'),
                    'reminder_id': result.get('id')
                }
            else:
                logger.error(f"Failed to create reminder: {response.status_code}")
                return {
                    'success': False,
                    'action': 'reminder_failed',
                    'error': f"API error: {response.status_code}"
                }
                
    except Exception as e:
        logger.error(f"Reminder creation error: {e}")
        return {
            'success': False,
            'action': 'reminder_failed',
            'error': str(e)
        }

async def create_event_action(match: dict, message: str, user_id: str) -> dict:
    """Create a calendar event"""
    # Placeholder for event creation
    return {
        'success': False,
        'action': 'event_creation',
        'message': 'Event creation not yet implemented'
    }

async def create_task_action(match: dict, message: str, user_id: str) -> dict:
    """Create a task"""
    # Placeholder for task creation
    return {
        'success': False,
        'action': 'task_creation',
        'message': 'Task creation not yet implemented'
    }

async def add_to_list_action(match: dict, message: str, user_id: str) -> dict:
    """Add item to an existing list"""
    try:
        item_name = match['item']
        list_type = match['list_type']
        
        # First, find existing list of this type
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Get all lists (correct endpoint)
            response = await client.get(f"http://localhost:8000/api/lists")
            
            if response.status_code == 200:
                data = response.json()
                lists = data.get('lists', [])
                
                # Find a shopping list (prefer one with items, then newest)
                shopping_list = None
                for lst in lists:
                    if lst.get('name', '').lower() in ['shopping', 'groceries', 'grocery']:
                        if not shopping_list or len(lst.get('items', [])) > 0:
                            shopping_list = lst
                
                # If no shopping list found, use the first list of this type
                if not shopping_list and lists:
                    shopping_list = lists[0]
                
                if shopping_list:
                    list_id = shopping_list['id']
                    current_items = shopping_list.get('items', [])
                    
                    # Add new item to items array
                    new_item = {
                        "text": item_name,
                        "priority": "medium",
                        "completed": False
                    }
                    current_items.append(new_item)
                    
                    # Update the list with new items
                    update_response = await client.put(
                        f"http://localhost:8000/api/lists/{list_type}/{list_id}",
                        json={
                            "items": current_items
                        }
                    )
                    
                    if update_response.status_code in [200, 201]:
                        return {
                            'success': True,
                            'action': 'list_item_added',
                            'item': item_name,
                            'list_name': shopping_list['name'],
                            'list_id': list_id
                        }
                
                # No suitable list found - would need to create one
                return {
                    'success': False,
                    'action': 'list_item_failed',
                    'error': 'No shopping list found'
                }
                
            else:
                logger.error(f"Failed to fetch lists: {response.status_code}")
                return {
                    'success': False,
                    'action': 'list_item_failed',
                    'error': f"API error: {response.status_code}"
                }
                
    except Exception as e:
        logger.error(f"List item addition error: {e}")
        return {
            'success': False,
            'action': 'list_item_failed',
            'error': str(e)
        }

