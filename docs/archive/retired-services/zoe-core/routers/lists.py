from auth_integration import validate_session, AuthenticatedSession
"""
Lists Management System
Supports: Shopping, Bucket, Personal Todos, Work Todos, Custom
With Martin-inspired productivity features
"""
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Set
from datetime import datetime, date, time
import sqlite3
import json
import os
import asyncio

# Platform-specific hierarchy limits
try:
    from model_config import detect_hardware
    HARDWARE_PLATFORM = detect_hardware()
except:
    HARDWARE_PLATFORM = 'unknown'

PLATFORM_MAX_DEPTH = {
    'jetson': 5,    # Can handle more nesting
    'pi5': 3,       # Keep simpler for performance
    'unknown': 3    # Conservative default
}
MAX_HIERARCHY_DEPTH = PLATFORM_MAX_DEPTH.get(HARDWARE_PLATFORM, 3)

print(f"[Lists] Platform: {HARDWARE_PLATFORM}, Max hierarchy depth: {MAX_HIERARCHY_DEPTH}")

router = APIRouter(prefix="/api/lists", tags=["lists"])

# WebSocket Connection Manager for real-time list updates
class ListsWebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        async with self.lock:
            if user_id not in self.active_connections:
                self.active_connections[user_id] = set()
            self.active_connections[user_id].add(websocket)
        print(f"✅ WebSocket connected for user {user_id} (total: {len(self.active_connections[user_id])})")
    
    async def disconnect(self, websocket: WebSocket, user_id: str):
        async with self.lock:
            if user_id in self.active_connections and websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
                if not self.active_connections[user_id]:
                    del self.active_connections[user_id]
        print(f"❌ WebSocket disconnected for user {user_id}")
    
    async def broadcast_to_user(self, user_id: str, message: Dict[str, Any]):
        """Broadcast message to all connections for a specific user"""
        async with self.lock:
            if user_id not in self.active_connections:
                return
            connections = list(self.active_connections[user_id])
        
        dead_connections = []
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception as e:
                print(f"⚠️ Failed to send to connection: {e}")
                dead_connections.append(ws)
        
        # Clean up dead connections
        for ws in dead_connections:
            await self.disconnect(ws, user_id)

lists_ws_manager = ListsWebSocketManager()

# Martin-inspired productivity models (defined early to avoid import issues)
class FocusSession(BaseModel):
    id: Optional[int] = None
    task_id: Optional[int] = None
    task_text: str
    duration_minutes: int = 25  # Default Pomodoro
    break_duration_minutes: int = 5
    session_type: str = "focus"  # focus, break, long_break
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    completed: bool = False
    productivity_score: Optional[float] = None

class BreakReminder(BaseModel):
    id: Optional[int] = None
    user_id: str = "default"
    reminder_type: str = "break"  # break, stretch, water, eye_rest
    message: str
    interval_minutes: int = 25
    last_reminder: Optional[str] = None
    enabled: bool = True

class ProductivityAnalytics(BaseModel):
    total_focus_time: int = 0
    total_break_time: int = 0
    completed_tasks: int = 0
    average_session_length: float = 0.0
    productivity_score: float = 0.0
    most_productive_hour: Optional[str] = None
    focus_streak: int = 0
    break_compliance: float = 0.0

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")


# ==================================================================
# HIERARCHY VALIDATION FUNCTIONS
# ==================================================================

def validate_hierarchy_depth(parent_id: Optional[int], conn: sqlite3.Connection) -> tuple:
    """
    Prevent deep nesting that could cause performance issues.
    Returns (is_valid, error_message)
    
    Max depth: 3 for Pi 5, 5 for Jetson
    """
    if parent_id is None:
        return True, ""  # Top-level item is always valid
    
    cursor = conn.cursor()
    current_depth = 0
    current_id = parent_id
    visited = set()
    
    while current_id is not None and current_depth < MAX_HIERARCHY_DEPTH + 1:
        # Check for circular reference during traversal
        if current_id in visited:
            return False, "Circular reference detected in item hierarchy"
        visited.add(current_id)
        
        cursor.execute("SELECT parent_id FROM list_items WHERE id = ?", (current_id,))
        result = cursor.fetchone()
        if result is None:
            break
        current_id = result[0]
        current_depth += 1
    
    if current_depth >= MAX_HIERARCHY_DEPTH:
        return False, f"Maximum hierarchy depth ({MAX_HIERARCHY_DEPTH} levels) exceeded for this platform ({HARDWARE_PLATFORM})"
    
    return True, ""


def prevent_circular_reference(item_id: int, new_parent_id: Optional[int], conn: sqlite3.Connection) -> tuple:
    """
    Prevent item from becoming its own ancestor.
    Returns (is_valid, error_message)
    """
    if new_parent_id is None:
        return True, ""  # Moving to top level is always safe
    
    if item_id == new_parent_id:
        return False, "Item cannot be its own parent"
    
    cursor = conn.cursor()
    current_id = new_parent_id
    visited = set([item_id])
    
    # Traverse up the parent chain to see if we encounter item_id
    while current_id is not None:
        if current_id == item_id:
            return False, "Cannot create circular reference: item would become its own ancestor"
        
        if current_id in visited:
            return False, "Circular reference detected in existing hierarchy"
        visited.add(current_id)
        
        cursor.execute("SELECT parent_id FROM list_items WHERE id = ?", (current_id,))
        result = cursor.fetchone()
        if result is None:
            break
        current_id = result[0]
    
    return True, ""


# ==================================================================
# DATABASE HELPERS
# ==================================================================

def get_connection(row_factory=None):
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    if row_factory is not None:
        conn.row_factory = row_factory
    try:
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass
    return conn

def init_lists_db():
    """Initialize lists tables"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Create lists table (without items column - items are in separate table)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            list_type TEXT NOT NULL,
            list_category TEXT DEFAULT 'personal',
            name TEXT NOT NULL,
            description TEXT,
            metadata JSON,
            shared_with JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create list_items table for storing actual list items
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS list_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL,
            task_text TEXT NOT NULL,
            priority TEXT DEFAULT 'medium',
            completed BOOLEAN DEFAULT 0,
            completed_at TIMESTAMP,
            metadata JSON,
            journey_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (list_id) REFERENCES lists(id) ON DELETE CASCADE
        )
    """)
    
    # Add columns if they don't exist
    try:
        cursor.execute("ALTER TABLE list_items ADD COLUMN metadata JSON")
    except:
        pass
    
    try:
        cursor.execute("ALTER TABLE list_items ADD COLUMN journey_id INTEGER")
    except:
        pass
    
    # Add due_date, transaction_id, and sort_order columns for week planner integration
    try:
        cursor.execute("ALTER TABLE list_items ADD COLUMN due_date DATE")
    except:
        pass  # Column already exists
    try:
        cursor.execute("ALTER TABLE list_items ADD COLUMN transaction_id INTEGER REFERENCES transactions(id)")
    except:
        pass  # Column already exists
    try:
        cursor.execute("ALTER TABLE list_items ADD COLUMN sort_order INTEGER DEFAULT 0")
    except:
        pass  # Column already exists
    
    # Run migration for Enhanced Lists features
    try:
        import subprocess
        import sys
        migration_script = "/app/scripts/maintenance/migrate_lists_enhancements.py"
        if not os.path.exists(migration_script):
            migration_script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts/maintenance/migrate_lists_enhancements.py")
        if os.path.exists(migration_script):
            subprocess.run([sys.executable, migration_script], check=False, timeout=30)
    except Exception as e:
        print(f"⚠️  Could not run migration script: {e}")
        # Fallback: Add columns manually if migration script fails
        try:
            cursor.execute("ALTER TABLE list_items ADD COLUMN parent_id INTEGER REFERENCES list_items(id) ON DELETE CASCADE")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE list_items ADD COLUMN reminder_time DATETIME")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE list_items ADD COLUMN repeat_pattern TEXT")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE list_items ADD COLUMN repeat_end_date DATE")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE list_items ADD COLUMN due_time TIME")
        except:
            pass
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_lists_category 
        ON lists(list_category, user_id)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_list_items_list_id
        ON list_items(list_id)
    """)
    
    # Create indexes for Enhanced Lists features
    try:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_list_items_parent_id
            ON list_items(parent_id)
        """)
    except:
        pass
    
    try:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_list_items_reminder_time
            ON list_items(reminder_time)
        """)
    except:
        pass
    
    try:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_list_items_due_date_time
            ON list_items(due_date, due_time)
        """)
    except:
        pass
    
    conn.commit()
    conn.close()

# Database will be initialized on first use
# (removed module-level init to avoid path issues)

def normalize_list_type(list_type: str, category: str = "personal") -> str:
    """Convert old list types to new ones for backward compatibility"""
    if list_type == "tasks":
        return "personal_todos" if category == "personal" else "work_todos"
    return list_type

def get_platform() -> str:
    """Get hardware platform for depth limits"""
    try:
        from model_config import detect_hardware
        return detect_hardware()
    except:
        return "unknown"

def prevent_circular_reference(item_id: int, new_parent_id: Optional[int], conn: sqlite3.Connection) -> tuple:
    """
    Prevent item from becoming its own ancestor.
    Returns (is_valid, error_message)
    """
    if new_parent_id is None:
        return True, ""  # Moving to top level is always safe
    
    if item_id == new_parent_id:
        return False, "Item cannot be its own parent"
    
    # Check if new_parent_id is a descendant of item_id
    current_id = new_parent_id
    visited = set()
    
    while current_id is not None:
        if current_id == item_id:
            return False, "Cannot create circular reference: item would become its own ancestor"
        
        if current_id in visited:
            return False, "Circular reference detected in existing hierarchy"
        
        visited.add(current_id)
        
        cursor = conn.execute(
            "SELECT parent_id FROM list_items WHERE id = ?",
            (current_id,)
        )
        result = cursor.fetchone()
        if result is None:
            break
        current_id = result[0] if result[0] is not None else None
        
        # Prevent infinite loops
        if len(visited) > 100:
            return False, "Hierarchy too deep (safety limit)"
    
    return True, ""  # No circular reference

async def sync_item_reminder(item_id: int, reminder_time: Optional[str], repeat_pattern: Optional[str], user_id: str) -> bool:
    """
    Sync list item reminder using MCP server
    This integrates with Zoe's intelligent reminder system
    """
    try:
        # Check if MCP reminder tool exists
        # First, try to import MCP client
        mcp_client = None
        try:
            # Try different possible import paths
            try:
                from services.mcp_client import MCPClient
                mcp_client = MCPClient()
            except ImportError:
                try:
                    from mcp_client import MCPClient
                    mcp_client = MCPClient()
                except ImportError:
                    # MCP client not available, use direct API call as fallback
                    import httpx
                    
                    if reminder_time:
                        # Create reminder via API
                        async with httpx.AsyncClient() as client:
                            reminder_data = {
                                "title": f"List item reminder",
                                "linked_list_item_id": item_id,
                                "reminder_time": reminder_time,
                                "reminder_type": "once",
                                "category": "personal",
                                "priority": "medium"
                            }
                            if repeat_pattern:
                                reminder_data["recurring_pattern"] = json.loads(repeat_pattern) if isinstance(repeat_pattern, str) else repeat_pattern
                            
                            response = await client.post(
                                "http://localhost:8000/api/reminders",
                                json=reminder_data,
                                params={"user_id": user_id},
                                timeout=5.0
                            )
                            if response.status_code in [200, 201]:
                                return True
                    else:
                        # Delete reminder if reminder_time is None
                        async with httpx.AsyncClient() as client:
                            # Find existing reminder
                            response = await client.get(
                                f"http://localhost:8000/api/reminders",
                                params={"linked_list_item_id": item_id, "user_id": user_id},
                                timeout=5.0
                            )
                            if response.status_code == 200:
                                reminders = response.json()
                                if reminders.get("reminders"):
                                    for reminder in reminders["reminders"]:
                                        await client.delete(
                                            f"http://localhost:8000/api/reminders/{reminder['id']}",
                                            params={"user_id": user_id},
                                            timeout=5.0
                                        )
                                return True
                    
                    return False
        
        except Exception as e:
            print(f"⚠️  Error syncing reminder via MCP/API: {e}")
            return False
        
        if mcp_client:
            # Use MCP tool for reminder management
            try:
                if reminder_time:
                    result = await mcp_client.call_tool(
                        tool_name="set_reminder",
                        arguments={
                            "linked_list_item_id": item_id,
                            "reminder_time": reminder_time,
                            "repeat_pattern": json.loads(repeat_pattern) if isinstance(repeat_pattern, str) else repeat_pattern if repeat_pattern else None,
                            "source": "list_item"
                        }
                    )
                    return result is not None
                else:
                    # Delete reminder via MCP if available
                    # For now, return True (reminder cleared)
                    return True
            except Exception as e:
                print(f"⚠️  Error calling MCP tool: {e}")
                return False
        
        return False
        
    except Exception as e:
        print(f"⚠️  Error in sync_item_reminder: {e}")
        return False

# Request/Response models
class ListItem(BaseModel):
    id: Optional[int] = None
    text: str
    completed: bool = False
    priority: Optional[str] = "medium"
    category: Optional[str] = "personal"
    metadata: Optional[Dict[str, Any]] = {}
    # Time estimation fields
    estimated_duration_minutes: Optional[int] = None
    estimated_duration_hours: Optional[float] = None
    estimated_duration_days: Optional[float] = None
    time_estimation_confidence: Optional[str] = None  # low, medium, high
    actual_duration_minutes: Optional[int] = None
    time_tracking_enabled: bool = True
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    
    # Reminder fields
    reminder_type: Optional[str] = None  # once, daily, weekly, monthly
    reminder_category: Optional[str] = None  # medical, household, personal, work, family
    due_date: Optional[str] = None  # YYYY-MM-DD
    due_time: Optional[str] = None  # HH:MM:SS
    requires_acknowledgment: bool = False
    snooze_minutes: int = 5

class ListCreate(BaseModel):
    category: str = "personal"  # personal, work
    name: str
    items: List[ListItem] = []

class ListUpdate(BaseModel):
    name: Optional[str] = None
    items: Optional[List[ListItem]] = None
    category: Optional[str] = None

@router.get("/tasks")
async def get_all_tasks(session: AuthenticatedSession = Depends(validate_session)):
    """Get all tasks from personal and work todos for dashboard"""
    user_id = session.user_id
    init_lists_db()
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Get personal todos
        cursor.execute("""
            SELECT id, name, list_category, description, created_at, updated_at
            FROM lists 
            WHERE list_category = 'personal' AND user_id = ?
            ORDER BY updated_at DESC
        """, (user_id,))
        personal_lists = cursor.fetchall()
        
        # Get work todos
        cursor.execute("""
            SELECT id, name, list_category, description, created_at, updated_at
            FROM lists 
            WHERE list_category = 'work' AND user_id = ?
            ORDER BY updated_at DESC
        """, (user_id,))
        work_lists = cursor.fetchall()
        
        all_tasks = []
        task_count = 0
        
        # Process personal todos
        for list_data in personal_lists:
            list_id, name, category, description, created_at, updated_at = list_data
            
            # Since there's no items column, create a placeholder task for the list
            all_tasks.append({
                "id": f"list-{list_id}",
                "text": name,
                "list_name": name,
                "list_category": "personal",
                "priority": "medium",
                "due_date": None,
                "created_at": created_at
            })
            task_count += 1
        
        # Process work todos
        for list_data in work_lists:
            list_id, name, category, description, created_at, updated_at = list_data
            
            # Since there's no items column, create a placeholder task for the list
            all_tasks.append({
                "id": f"list-{list_id}",
                "text": name,
                "list_name": name,
                "list_category": "work",
                "priority": "medium",
                "due_date": None,
                "created_at": created_at
            })
            task_count += 1
        
        # Sort by priority and creation date
        priority_order = {"high": 0, "medium": 1, "low": 2}
        all_tasks.sort(key=lambda x: (priority_order.get(x["priority"], 1), x["created_at"]))
        
        # Format for dashboard compatibility - return as lists with items
        dashboard_lists = []
        if all_tasks:
            # Group tasks by category for dashboard display
            personal_tasks = [t for t in all_tasks if t["list_category"] == "personal"]
            work_tasks = [t for t in all_tasks if t["list_category"] == "work"]
            
            if personal_tasks:
                dashboard_lists.append({
                    "id": "personal_dashboard",
                    "name": "Personal Tasks",
                    "items": personal_tasks[:5],  # Limit per category
                    "category": "personal"
                })
            
            if work_tasks:
                dashboard_lists.append({
                    "id": "work_dashboard", 
                    "name": "Work Tasks",
                    "items": work_tasks[:5],  # Limit per category
                    "category": "work"
                })
        
        return {
            "lists": dashboard_lists,
            "tasks": all_tasks[:10],  # Keep for other uses
            "count": task_count,
            "personal_count": len([t for t in all_tasks if t["list_category"] == "personal"]),
            "work_count": len([t for t in all_tasks if t["list_category"] == "work"])
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching tasks: {str(e)}")
    finally:
        conn.close()

@router.get("/types")
async def get_list_types():
    """Get available list types and categories"""
    return {
        "types": ["shopping", "bucket", "personal_todos", "work_todos", "custom"],
        "categories": ["personal", "work"],
        "templates": {
            "shopping": ["Groceries", "Home Supplies", "Electronics"],
            "bucket": ["Travel", "Skills", "Experiences"],
            "personal_todos": ["Daily", "Health", "Home", "Hobbies"],
            "work_todos": ["Projects", "Meetings", "Deadlines", "Admin"],
            "custom": []
        }
    }

@router.get("/templates")
async def get_list_templates():
    """Get comprehensive list templates for quick setup"""
    return {
        "templates": {
            "shopping": {
                "groceries": [
                    "Milk", "Bread", "Eggs", "Butter", "Cheese", "Yogurt",
                    "Fruits", "Vegetables", "Meat", "Fish", "Pasta", "Rice"
                ],
                "home_supplies": [
                    "Toilet Paper", "Paper Towels", "Cleaning Supplies",
                    "Laundry Detergent", "Dish Soap", "Trash Bags"
                ],
                "electronics": [
                    "Phone Charger", "USB Cable", "Headphones", "Batteries",
                    "Memory Card", "Power Bank"
                ]
            },
            "bucket": {
                "travel": [
                    "Visit Japan", "Road Trip Across USA", "Backpack Europe",
                    "Safari in Africa", "Northern Lights", "Machu Picchu"
                ],
                "skills": [
                    "Learn Spanish", "Play Guitar", "Cook Italian Food",
                    "Photography", "Coding", "Dancing"
                ],
                "experiences": [
                    "Skydiving", "Scuba Diving", "Hot Air Balloon",
                    "Concert", "Theater Show", "Wine Tasting"
                ]
            },
            "personal_todos": {
                "daily": [
                    "Morning Exercise", "Read 30 minutes", "Meditate",
                    "Walk the Dog", "Prepare Healthy Meals"
                ],
                "health": [
                    "Annual Checkup", "Dentist Appointment", "Eye Exam",
                    "Update Vaccinations", "Health Insurance Review"
                ],
                "home": [
                    "Deep Clean Kitchen", "Organize Closet", "Fix Leaky Faucet",
                    "Paint Living Room", "Garden Maintenance"
                ],
                "hobbies": [
                    "Finish Reading Book", "Complete Art Project",
                    "Practice Instrument", "Join Local Club"
                ]
            },
            "work_todos": {
                "projects": [
                    "Project Planning", "Research Phase", "Design Mockups",
                    "Development", "Testing", "Deployment"
                ],
                "meetings": [
                    "Team Standup", "Client Call", "Project Review",
                    "One-on-One", "Training Session"
                ],
                "deadlines": [
                    "Quarterly Report", "Budget Review", "Performance Review",
                    "Contract Renewal", "Tax Preparation"
                ],
                "admin": [
                    "Update Timesheet", "Submit Expenses", "Update Resume",
                    "Training Completion", "Documentation"
                ]
            }
        }
    }

@router.post("/create-from-template")
async def create_list_from_template(
    list_type: str,
    template_category: str,
    list_name: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Create a new list from a template"""
    try:
        # Get the template data
        templates_response = await get_list_templates()
        templates = templates_response["templates"]
        
        if list_type not in templates:
            raise HTTPException(status_code=400, detail=f"List type {list_type} not supported")
        
        if template_category not in templates[list_type]:
            raise HTTPException(status_code=400, detail=f"Template category {template_category} not found for {list_type}")
        
        template_items = templates[list_type][template_category]
        
        # Create the list with template items
        conn = get_connection()
        cursor = conn.cursor()
        
        # Create items in the format expected by the frontend
        items = []
        for i, item_text in enumerate(template_items):
            items.append({
                "id": f"{int(datetime.now().timestamp() * 1000)}_{i}",
                "text": item_text,
                "completed": False,
                "priority": "medium",
                "category": "personal",
                "metadata": {}
            })
        
        # Create the list with items in JSON format
        cursor.execute("""
            INSERT INTO lists (user_id, name, list_type, list_category, items, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (user_id, list_name, list_type, "personal", json.dumps(items)))
        
        list_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return {
            "message": f"List '{list_name}' created successfully with {len(template_items)} items",
            "list_id": list_id,
            "items_added": len(template_items)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/time-analytics")
async def get_time_analytics(
    list_type: Optional[str] = None,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Get time analytics across all lists"""
    try:
        conn = get_connection(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        
        # Build query
        query = "SELECT items FROM lists WHERE user_id = ?"
        params = [user_id]
        
        if list_type:
            query += " AND list_type = ?"
            params.append(list_type)
        
        cursor.execute(query, params)
        results = cursor.fetchall()
        
        # Analyze all items
        total_estimated_minutes = 0
        total_actual_minutes = 0
        total_items = 0
        completed_items = 0
        items_with_estimates = 0
        items_with_actual_time = 0
        
        for result in results:
            items = json.loads(result['items']) if result['items'] else []
            total_items += len(items)
            
            for item in items:
                if item.get('completed'):
                    completed_items += 1
                
                if item.get('estimated_duration_minutes'):
                    total_estimated_minutes += item['estimated_duration_minutes']
                    items_with_estimates += 1
                
                if item.get('actual_duration_minutes'):
                    total_actual_minutes += item['actual_duration_minutes']
                    items_with_actual_time += 1
        
        # Calculate accuracy
        accuracy = None
        if total_estimated_minutes > 0 and total_actual_minutes > 0:
            accuracy = (total_estimated_minutes / total_actual_minutes) * 100
        
        conn.close()
        
        return {
            "total_items": total_items,
            "completed_items": completed_items,
            "completion_percentage": (completed_items / total_items * 100) if total_items > 0 else 0,
            "total_estimated_minutes": total_estimated_minutes,
            "total_actual_minutes": total_actual_minutes,
            "items_with_estimates": items_with_estimates,
            "items_with_actual_time": items_with_actual_time,
            "estimation_accuracy": accuracy,
            "estimated_hours": round(total_estimated_minutes / 60, 2),
            "actual_hours": round(total_actual_minutes / 60, 2),
            "average_estimation_accuracy": accuracy
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting time analytics: {str(e)}")

# Smart Scheduling Integration
@router.post("/{list_type}/{list_id}/items/{item_index}/schedule")
async def schedule_list_item(
    list_type: str,
    list_id: int,
    item_index: int,
    task_type: str = "focus",
    priority: int = 3,
    preferred_times: Optional[List[str]] = None,
    deadline: Optional[str] = None,
    energy_requirement: str = "medium",
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Schedule a list item using smart scheduling and add to calendar"""
    try:
        # Get the list item
        conn = get_connection(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT items FROM lists 
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (list_id, list_type, user_id))
        
        result = cursor.fetchone()
        if not result:
            conn.close()
            raise HTTPException(status_code=404, detail="List not found")
        
        items = json.loads(result['items']) if result['items'] else []
        
        if item_index < 0 or item_index >= len(items):
            conn.close()
            raise HTTPException(status_code=404, detail="Item index out of range")
        
        item = items[item_index]
        conn.close()
        
        # Get estimated duration from item
        estimated_duration = item.get('estimated_duration_minutes', 60)  # Default 1 hour
        
        # Create schedule request
        from routers.smart_scheduling import ScheduleRequest
        schedule_request = ScheduleRequest(
            task_id=f"{list_type}_{list_id}_{item_index}",
            estimated_duration_minutes=estimated_duration,
            task_type=task_type,
            priority=priority,
            preferred_times=preferred_times,
            deadline=deadline,
            energy_requirement=energy_requirement
        )
        
        # Get smart scheduling suggestions
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://localhost:8000/api/smart-scheduling/schedule",
                json=schedule_request.dict(),
                params={"user_id": user_id}
            )
            schedule_response = response.json()
        
        return {
            "message": "Schedule suggestions generated",
            "item": item,
            "schedule_suggestions": schedule_response,
            "next_step": "Use /confirm-schedule to add to calendar"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error scheduling item: {str(e)}")

@router.post("/{list_type}/{list_id}/items/{item_index}/confirm-schedule")
async def confirm_schedule_list_item(
    list_type: str,
    list_id: int,
    item_index: int,
    start_time: str,
    end_time: str,
    energy_level: str = "medium",
    task_type: str = "focus",
    priority: int = 3,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Confirm schedule for a list item and add to calendar"""
    try:
        # Get the list item
        conn = get_connection(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT items FROM lists 
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (list_id, list_type, user_id))
        
        result = cursor.fetchone()
        if not result:
            conn.close()
            raise HTTPException(status_code=404, detail="List not found")
        
        items = json.loads(result['items']) if result['items'] else []
        
        if item_index < 0 or item_index >= len(items):
            conn.close()
            raise HTTPException(status_code=404, detail="Item index out of range")
        
        item = items[item_index]
        
        # Parse times
        start_dt = datetime.fromisoformat(start_time)
        end_dt = datetime.fromisoformat(end_time)
        
        # Add to smart scheduling system
        from routers.smart_scheduling import TimeSlot
        slot = TimeSlot(
            start_time=start_time,
            end_time=end_time,
            duration_minutes=int((end_dt - start_dt).total_seconds() / 60),
            energy_level=energy_level,
            task_type=task_type,
            priority=priority
        )
        
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://localhost:8000/api/smart-scheduling/schedule/{user_id}/confirm",
                json={
                    "task_id": f"{list_type}_{list_id}_{item_index}",
                    "slot": slot.dict()
                }
            )
            schedule_result = response.json()
        
        # Add to calendar
        calendar_event = {
            "title": item['text'],
            "description": f"Task from {list_type} list",
            "start_date": start_dt.date().isoformat(),
            "start_time": start_dt.time().isoformat(),
            "end_date": end_dt.date().isoformat(),
            "end_time": end_dt.time().isoformat(),
            "category": "task",
            "metadata": {
                "list_type": list_type,
                "list_id": list_id,
                "item_index": item_index,
                "scheduled_id": schedule_result.get("scheduled_id"),
                "energy_level": energy_level,
                "task_type": task_type,
                "priority": priority
            }
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8000/api/calendar/events",
                json=calendar_event,
                params={"user_id": user_id}
            )
            calendar_result = response.json()
        
        # Update list item with scheduling info
        items[item_index]['scheduled'] = True
        items[item_index]['scheduled_start'] = start_time
        items[item_index]['scheduled_end'] = end_time
        items[item_index]['calendar_event_id'] = calendar_result.get("id")
        items[item_index]['scheduled_id'] = schedule_result.get("scheduled_id")
        
        # Update the list
        cursor.execute("""
            UPDATE lists 
            SET items = ?, updated_at = ?
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (json.dumps(items), datetime.now().isoformat(), list_id, list_type, user_id))
        
        conn.commit()
        conn.close()
        
        return {
            "message": "Item scheduled and added to calendar",
            "item": items[item_index],
            "schedule_id": schedule_result.get("scheduled_id"),
            "calendar_event_id": calendar_result.get("id")
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error confirming schedule: {str(e)}")

@router.get("/{list_type}/{list_id}/scheduled-items")
async def get_scheduled_items(
    list_type: str,
    list_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Get all scheduled items from a list"""
    try:
        conn = get_connection(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT items FROM lists 
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (list_id, list_type, user_id))
        
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="List not found")
        
        items = json.loads(result['items']) if result['items'] else []
        
        # Filter scheduled items
        scheduled_items = [
            {**item, "index": i} for i, item in enumerate(items) 
            if item.get('scheduled', False)
        ]
        
        conn.close()
        
        return {
            "list_id": list_id,
            "scheduled_items": scheduled_items,
            "total_scheduled": len(scheduled_items)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching scheduled items: {str(e)}")

@router.get("/productivity-analytics")
async def get_productivity_analytics(session: AuthenticatedSession = Depends(validate_session)):
    """Get productivity analytics and insights"""
    user_id = session.user_id
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Get focus session data
        cursor.execute("""
            SELECT 
                COUNT(*) as total_sessions,
                SUM(CASE WHEN completed = 1 THEN duration_minutes ELSE 0 END) as total_focus_time,
                AVG(CASE WHEN completed = 1 THEN duration_minutes ELSE NULL END) as avg_session_length,
                AVG(productivity_score) as avg_productivity_score
            FROM focus_sessions 
            WHERE DATE(start_time) >= DATE('now', '-7 days')
        """)
        
        focus_data = cursor.fetchone()
        
        # Get task completion data
        cursor.execute("""
            SELECT COUNT(*) as completed_tasks
            FROM lists 
            WHERE list_type IN ('personal_todos', 'work_todos')
            AND items LIKE '%"completed": true%'
            AND DATE(updated_at) >= DATE('now', '-7 days')
        """)
        
        task_data = cursor.fetchone()
        
        # Calculate productivity score (0-100)
        total_focus_time = focus_data[1] or 0
        completed_tasks = task_data[0] or 0
        avg_productivity = focus_data[3] or 0
        
        productivity_score = min(100, (total_focus_time / 60) * 2 + completed_tasks * 5 + avg_productivity)
        
        analytics = {
            "total_focus_time": total_focus_time,
            "total_break_time": 0,  # Could be calculated from break sessions
            "completed_tasks": completed_tasks,
            "average_session_length": focus_data[2] or 0,
            "productivity_score": round(productivity_score, 1),
            "most_productive_hour": "09:00",  # Could be calculated from session data
            "focus_streak": 0,  # Could be calculated from consecutive days
            "break_compliance": 85.0,  # Could be calculated from break reminders
            "total_sessions": focus_data[0] or 0,
            "average_productivity": round(avg_productivity, 1)
        }
        
        conn.close()
        return analytics
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting productivity analytics: {str(e)}")

@router.get("/{list_type}")
async def get_lists(
    list_type: str,
    category: Optional[str] = Query(None, description="Filter by category (personal/work)"),
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Get all lists of a specific type with their items"""
    conn = get_connection()
    cursor = conn.cursor()
    
    if category:
        cursor.execute("""
            SELECT l.id, l.name, l.list_type, l.list_category, l.created_at, l.updated_at
            FROM lists l
            WHERE l.list_type = ? AND l.list_category = ? AND l.user_id = ?
            ORDER BY l.updated_at DESC
        """, (list_type, category, user_id))
    else:
        cursor.execute("""
            SELECT l.id, l.name, l.list_type, l.list_category, l.created_at, l.updated_at
            FROM lists l
            WHERE l.list_type = ? AND l.user_id = ?
            ORDER BY l.updated_at DESC
        """, (list_type, user_id))
    
    rows = cursor.fetchall()
    
    lists = []
    for row in rows:
        list_id = row[0]
        
        # Check which columns exist first
        cursor.execute("PRAGMA table_info(list_items)")
        columns_info = cursor.fetchall()
        column_names = [col[1] for col in columns_info]
        
        # Build SELECT based on available columns
        base_columns = ["id", "task_text", "priority", "completed", "completed_at", "created_at", "updated_at"]
        optional_columns = ["parent_id", "reminder_time", "repeat_pattern", "due_date", "due_time", "repeat_end_date", "sort_order"]
        
        select_columns = base_columns.copy()
        for col in optional_columns:
            if col in column_names:
                select_columns.append(col)
        
        # Get items for this list (with or without hierarchical structure)
        try:
            if "parent_id" in column_names and "sort_order" in column_names:
                order_clause = "ORDER BY parent_id IS NULL DESC, sort_order, created_at ASC"
            else:
                order_clause = "ORDER BY created_at ASC"
            
            cursor.execute(f"""
                SELECT {', '.join(select_columns)}
                FROM list_items
                WHERE list_id = ?
                {order_clause}
            """, (list_id,))
        except sqlite3.OperationalError as e:
            # Fallback to simple query if ORDER BY fails
            cursor.execute(f"""
                SELECT {', '.join([c for c in select_columns if c != 'parent_id' and c != 'sort_order'])}
                FROM list_items
                WHERE list_id = ?
                ORDER BY created_at ASC
            """, (list_id,))
        
        item_rows = cursor.fetchall()
        
        # Build hierarchical structure if parent_id exists
        has_hierarchy = "parent_id" in column_names
        all_items = {}
        root_items = []
        
        # First pass: create all item dictionaries
        for item_row in item_rows:
            # Build a dict mapping column names to values
            row_dict = {}
            for idx, col_name in enumerate(select_columns):
                if idx < len(item_row):
                    row_dict[col_name] = item_row[idx]
            
            item_id = row_dict.get("id")
            if item_id is None:
                continue  # Skip invalid rows
            
            item_data = {
                "id": item_id,
                "text": row_dict.get("task_text", ""),
                "priority": row_dict.get("priority", "medium"),
                "completed": bool(row_dict.get("completed", False)),
                "completed_at": row_dict.get("completed_at"),
                "created_at": row_dict.get("created_at"),
                "updated_at": row_dict.get("updated_at"),
                "parent_id": row_dict.get("parent_id"),
                "reminder_time": row_dict.get("reminder_time"),
                "repeat_pattern": row_dict.get("repeat_pattern"),
                "due_date": row_dict.get("due_date"),
                "due_time": row_dict.get("due_time"),
                "repeat_end_date": row_dict.get("repeat_end_date"),
                "sub_items": []
            }
            
            if has_hierarchy:
                # Calculate depth
                depth = 0
                current_parent = item_data["parent_id"]
                while current_parent is not None:
                    depth += 1
                    if current_parent in all_items:
                        current_parent = all_items[current_parent].get("parent_id")
                    else:
                        break
                    if depth > 10:  # Safety check
                        break
                item_data["depth"] = depth
            
            all_items[item_id] = item_data
        
        # Second pass: build tree structure (or flat structure if no hierarchy)
        items = []
        if has_hierarchy:
            for item_id, item_data in all_items.items():
                if item_data["parent_id"] is None:
                    items.append(item_data)
                else:
                    parent = all_items.get(item_data["parent_id"])
                    if parent:
                        parent["sub_items"].append(item_data)
                    else:
                        # Parent not found, treat as root
                        items.append(item_data)
        else:
            # No hierarchy - return flat list
            items = list(all_items.values())
        
        lists.append({
            "id": row[0],
            "name": row[1],
            "list_type": row[2],
            "category": row[3],
            "description": None,
            "items": items,
            "created_at": row[4],
            "updated_at": row[5]
        })
    
    conn.close()
    return {"lists": lists, "count": len(lists)}

@router.post("/focus-session")
async def start_focus_session(session: FocusSession):
    """Start a new focus session (Pomodoro timer)"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Create focus_sessions table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS focus_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                task_text TEXT NOT NULL,
                duration_minutes INTEGER DEFAULT 25,
                break_duration_minutes INTEGER DEFAULT 5,
                session_type TEXT DEFAULT 'focus',
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                completed BOOLEAN DEFAULT FALSE,
                productivity_score REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Insert new session
        cursor.execute("""
            INSERT INTO focus_sessions (task_id, task_text, duration_minutes, break_duration_minutes, session_type, start_time)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session.task_id, session.task_text, session.duration_minutes, 
              session.break_duration_minutes, session.session_type, datetime.now().isoformat()))
        
        session_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            "id": session_id,
            "message": "Focus session started",
            "duration_minutes": session.duration_minutes,
            "start_time": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting focus session: {str(e)}")

@router.post("/{list_type}")
async def create_list(
    list_type: str,
    list_data: ListCreate,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Create a new list"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Normalize list type for backward compatibility
    normalized_type = normalize_list_type(list_type, list_data.category)
    
    cursor.execute("""
        INSERT INTO lists (user_id, list_type, list_category, name)
        VALUES (?, ?, ?, ?)
    """, (user_id, normalized_type, list_data.category, list_data.name))
    
    list_id = cursor.lastrowid
    
    # Add items to list_items table
    for item in list_data.items:
        cursor.execute("""
            INSERT INTO list_items (list_id, task_text, priority, completed)
            VALUES (?, ?, ?, ?)
        """, (list_id, item.text, item.priority or "medium", item.completed))
    
    conn.commit()
    conn.close()
    
    # Broadcast update to WebSocket clients
    await lists_ws_manager.broadcast_to_user(user_id, {
        "type": "list_created",
        "list_type": normalized_type,
        "list_id": list_id,
        "action": "created"
    })
    
    return {
        "id": list_id,
        "message": f"{list_type.title()} list created",
        "name": list_data.name,
        "category": list_data.category
    }

@router.put("/{list_type}/{list_id}")
async def update_list(
    list_type: str,
    list_id: int,
    update_data: ListUpdate,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Update a list"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Build update query dynamically
    updates = []
    params = []
    
    if update_data.name:
        updates.append("name = ?")
        params.append(update_data.name)
    
    if update_data.category:
        updates.append("list_category = ?")
        params.append(update_data.category)
    
    if updates:
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([list_id, list_type, user_id])
        
        cursor.execute(f"""
            UPDATE lists 
            SET {', '.join(updates)}
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, params)
    
    # Update items if provided
    if update_data.items is not None:
        # Delete existing items
        cursor.execute("DELETE FROM list_items WHERE list_id = ?", (list_id,))
        
        # Add new items
        for item in update_data.items:
            cursor.execute("""
                INSERT INTO list_items (list_id, task_text, priority, completed)
                VALUES (?, ?, ?, ?)
            """, (list_id, item.text, item.priority or "medium", item.completed))
    
    conn.commit()
    conn.close()
    
    # Broadcast update to WebSocket clients
    await lists_ws_manager.broadcast_to_user(user_id, {
        "type": "list_updated",
        "list_type": list_type,
        "list_id": list_id,
        "action": "updated"
    })
    
    return {"message": "List updated", "id": list_id}

@router.post("/{list_type}/{list_id}/items")
async def add_list_item(
    list_type: str,
    list_id: int,
    task_text: str = Query(..., description="Task text"),
    parent_id: Optional[int] = Query(None, description="Parent item ID for sub-items"),
    priority: str = Query("medium", description="Priority: low, medium, high"),
    reminder_time: Optional[str] = Query(None, description="Reminder time (YYYY-MM-DD HH:MM:SS)"),
    due_date: Optional[str] = Query(None, description="Due date (YYYY-MM-DD)"),
    due_time: Optional[str] = Query(None, description="Due time (HH:MM:SS)"),
    repeat_pattern: Optional[str] = Query(None, description="Repeat pattern JSON"),
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Add an item to a list"""
    init_lists_db()
    conn = get_connection()
    cursor = conn.cursor()
    
    # Verify list exists and belongs to user
    cursor.execute("""
        SELECT id FROM lists 
        WHERE id = ? AND list_type = ? AND user_id = ?
    """, (list_id, list_type, user_id))
    
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="List not found")
    
    # Validate hierarchy depth if parent_id is provided
    if parent_id is not None:
        is_valid, error_msg = validate_hierarchy_depth(parent_id, conn)
        if not is_valid:
            conn.close()
            raise HTTPException(status_code=400, detail=error_msg)
        
        # Verify parent item exists and belongs to same list
        cursor.execute("""
            SELECT id FROM list_items 
            WHERE id = ? AND list_id = ?
        """, (parent_id, list_id))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Parent item not found")
    
    # Insert new item
    cursor.execute("""
        INSERT INTO list_items (list_id, task_text, priority, parent_id, reminder_time, due_date, due_time, repeat_pattern)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (list_id, task_text, priority, parent_id, reminder_time, due_date, due_time, repeat_pattern))
    
    item_id = cursor.lastrowid
    conn.commit()
    
    # Sync reminder via MCP if reminder_time is set
    if reminder_time:
        await sync_item_reminder(item_id, reminder_time, repeat_pattern, user_id)
    
    conn.close()
    
    # Broadcast update via WebSocket
    await lists_ws_manager.broadcast_to_user(user_id, {
        "type": "item_added",
        "list_type": list_type,
        "list_id": list_id,
        "item_id": item_id,
        "parent_id": parent_id,
        "action": "added"
    })
    
    return {
        "id": item_id,
        "message": "Item added",
        "list_id": list_id,
        "text": task_text,
        "parent_id": parent_id
    }

@router.put("/{list_type}/{list_id}/items/{item_id}")
async def update_list_item(
    list_type: str,
    list_id: int,
    item_id: int,
    task_text: Optional[str] = Query(None, description="Task text"),
    parent_id: Optional[int] = Query(None, description="Parent item ID (use -1 to remove parent)"),
    priority: Optional[str] = Query(None, description="Priority: low, medium, high"),
    completed: Optional[bool] = Query(None, description="Completed status"),
    reminder_time: Optional[str] = Query(None, description="Reminder time (YYYY-MM-DD HH:MM:SS)"),
    due_date: Optional[str] = Query(None, description="Due date (YYYY-MM-DD)"),
    due_time: Optional[str] = Query(None, description="Due time (HH:MM:SS)"),
    repeat_pattern: Optional[str] = Query(None, description="Repeat pattern JSON"),
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Update a list item"""
    init_lists_db()
    conn = get_connection()
    cursor = conn.cursor()
    
    # Verify item exists and belongs to user's list
    cursor.execute("""
        SELECT li.id, li.parent_id, li.reminder_time FROM list_items li
        JOIN lists l ON li.list_id = l.id
        WHERE li.id = ? AND li.list_id = ? AND l.user_id = ?
    """, (item_id, list_id, user_id))
    
    item = cursor.fetchone()
    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")
    
    old_parent_id = item[1]
    old_reminder_time = item[2]
    
    # Handle parent_id update
    new_parent_id = parent_id
    if parent_id == -1:
        new_parent_id = None
    
    if new_parent_id is not None and new_parent_id != old_parent_id:
        # Validate circular reference
        is_valid, error_msg = prevent_circular_reference(item_id, new_parent_id, conn)
        if not is_valid:
            conn.close()
            raise HTTPException(status_code=400, detail=error_msg)
        
        # Validate hierarchy depth
        is_valid, error_msg = validate_hierarchy_depth(new_parent_id, conn)
        if not is_valid:
            conn.close()
            raise HTTPException(status_code=400, detail=error_msg)
        
        # Verify new parent exists and belongs to same list
        cursor.execute("""
            SELECT id FROM list_items 
            WHERE id = ? AND list_id = ?
        """, (new_parent_id, list_id))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Parent item not found")
    
    # Build update query
    updates = []
    params = []
    
    if task_text is not None:
        updates.append("task_text = ?")
        params.append(task_text)
    if parent_id is not None:
        updates.append("parent_id = ?")
        params.append(new_parent_id)
    if priority is not None:
        updates.append("priority = ?")
        params.append(priority)
    if completed is not None:
        updates.append("completed = ?")
        params.append(completed)
        if completed:
            updates.append("completed_at = ?")
            params.append(datetime.now().isoformat())
        else:
            updates.append("completed_at = NULL")
    if reminder_time is not None:
        updates.append("reminder_time = ?")
        params.append(reminder_time)
    if due_date is not None:
        updates.append("due_date = ?")
        params.append(due_date)
    if due_time is not None:
        updates.append("due_time = ?")
        params.append(due_time)
    if repeat_pattern is not None:
        updates.append("repeat_pattern = ?")
        params.append(repeat_pattern)
    
    updates.append("updated_at = ?")
    params.append(datetime.now().isoformat())
    params.append(item_id)
    
    if updates:
        cursor.execute(f"""
            UPDATE list_items 
            SET {', '.join(updates)}
            WHERE id = ?
        """, params)
        conn.commit()
    
    # Sync reminder via MCP if reminder_time changed
    if reminder_time != old_reminder_time:
        await sync_item_reminder(item_id, reminder_time, repeat_pattern, user_id)
    
    conn.close()
    
    # Broadcast update via WebSocket
    await lists_ws_manager.broadcast_to_user(user_id, {
        "type": "item_updated",
        "list_type": list_type,
        "list_id": list_id,
        "item_id": item_id,
        "action": "updated"
    })
    
    return {
        "id": item_id,
        "message": "Item updated",
        "list_id": list_id
    }

@router.patch("/{list_type}/{list_id}")
async def rename_list(
    list_type: str,
    list_id: int,
    name: str = Query(..., description="New list name"),
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Rename a list"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Update list name
    cursor.execute("""
        UPDATE lists 
        SET name = ?, updated_at = ?
        WHERE id = ? AND list_type = ? AND user_id = ?
    """, (name, datetime.now().isoformat(), list_id, list_type, user_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="List not found")
    
    conn.commit()
    
    # Get updated list
    cursor.execute("""
        SELECT id, name, list_type, list_category, created_at, updated_at
        FROM lists
        WHERE id = ?
    """, (list_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="List not found after update")
    
    return {
        "id": row[0],
        "name": row[1],
        "list_type": row[2],
        "category": row[3],
        "created_at": row[4],
        "updated_at": row[5]
    }

@router.delete("/{list_type}/{list_id}/items/{item_id}")
async def delete_list_item(
    list_type: str,
    list_id: int,
    item_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Delete a list item"""
    init_lists_db()
    conn = get_connection()
    cursor = conn.cursor()
    
    # Verify item exists and belongs to user's list
    cursor.execute("""
        SELECT li.id FROM list_items li
        JOIN lists l ON li.list_id = l.id
        WHERE li.id = ? AND li.list_id = ? AND l.user_id = ?
    """, (item_id, list_id, user_id))
    
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")
    
    # Delete item (CASCADE will handle sub-items)
    cursor.execute("""
        DELETE FROM list_items
        WHERE id = ?
    """, (item_id,))
    
    conn.commit()
    conn.close()
    
    # Broadcast update via WebSocket
    await lists_ws_manager.broadcast_to_user(user_id, {
        "type": "item_deleted",
        "list_type": list_type,
        "list_id": list_id,
        "item_id": item_id,
        "action": "deleted"
    })
    
    return {"message": "Item deleted", "id": item_id}

@router.delete("/{list_type}/{list_id}")
async def delete_list(
    list_type: str,
    list_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Delete a list"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        DELETE FROM lists 
        WHERE id = ? AND list_type = ? AND user_id = ?
    """, (list_id, list_type, user_id))
    
    conn.commit()
    conn.close()
    
    return {"message": "List deleted", "id": list_id}

@router.post("/{list_type}/{list_id}/share")
async def share_list(
    list_type: str,
    list_id: int,
    share_with: List[str],
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Share a list with other users"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE lists 
        SET shared_with = ?
        WHERE id = ? AND list_type = ? AND user_id = ?
    """, (json.dumps(share_with), list_id, list_type, user_id))
    
    conn.commit()
    conn.close()
    
    return {"message": "List shared", "shared_with": share_with}

# Reminder-specific endpoints for lists

@router.post("/{list_type}/{list_id}/items/{item_index}/set-reminder")
async def set_item_reminder(
    list_type: str,
    list_id: int,
    item_index: int,
    reminder_type: str = Query(..., description="Reminder type: once, daily, weekly, monthly"),
    reminder_category: str = Query(..., description="Reminder category: medical, household, personal, work, family"),
    due_date: Optional[str] = Query(None, description="Due date (YYYY-MM-DD)"),
    due_time: Optional[str] = Query(None, description="Due time (HH:MM:SS)"),
    requires_acknowledgment: bool = Query(False, description="Requires acknowledgment"),
    snooze_minutes: int = Query(5, description="Snooze minutes"),
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Set a reminder for a list item"""
    try:
        conn = get_connection(row_factory=sqlite3.Row)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get the current list
        cursor.execute("""
            SELECT items FROM lists 
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (list_id, list_type, user_id))
        
        result = cursor.fetchone()
        if not result:
            conn.close()
            raise HTTPException(status_code=404, detail="List not found")
        
        items = json.loads(result['items']) if result['items'] else []
        
        # Find and update the item by index
        if item_index < 0 or item_index >= len(items):
            conn.close()
            raise HTTPException(status_code=404, detail="Item index out of range")
        
        # Update the item with reminder fields
        items[item_index]['reminder_type'] = reminder_type
        items[item_index]['reminder_category'] = reminder_category
        items[item_index]['due_date'] = due_date
        items[item_index]['due_time'] = due_time
        items[item_index]['requires_acknowledgment'] = requires_acknowledgment
        items[item_index]['snooze_minutes'] = snooze_minutes
        
        # Update the list
        cursor.execute("""
            UPDATE lists 
            SET items = ?, updated_at = ?
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (json.dumps(items), datetime.now().isoformat(), list_id, list_type, user_id))
        
        conn.commit()
        conn.close()
        
        return {"message": "Reminder set for item", "item_index": item_index}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/calendar-items")
async def get_calendar_items(
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Get list items with reminder times for calendar display"""
    init_lists_db()
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Query items with reminder_time set within date range
        cursor.execute("""
            SELECT li.id, li.task_text, li.reminder_time, li.due_date, li.due_time,
                   l.id as list_id, l.name as list_name, l.list_type, l.list_category
            FROM list_items li
            JOIN lists l ON li.list_id = l.id
            WHERE l.user_id = ?
              AND (li.reminder_time IS NOT NULL OR (li.due_date IS NOT NULL AND li.due_time IS NOT NULL))
              AND (
                DATE(li.reminder_time) BETWEEN ? AND ?
                OR (li.due_date BETWEEN ? AND ?)
              )
            ORDER BY COALESCE(li.reminder_time, li.due_date || ' ' || li.due_time) ASC
        """, (user_id, start_date, end_date, start_date, end_date))
        
        items = []
        for row in cursor.fetchall():
            items.append({
                "id": row[0],
                "task_text": row[1],
                "reminder_time": row[2],
                "due_date": row[3],
                "due_time": row[4],
                "list_id": row[5],
                "list_name": row[6],
                "list_type": row[7],
                "list_category": row[8]
            })
        
        conn.close()
        return {"items": items, "count": len(items)}
    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=f"Error getting calendar items: {str(e)}")

@router.get("/{list_type}/reminders/today")
async def get_todays_reminders(
    list_type: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Get today's reminders for a specific list type"""
    try:
        today = date.today().isoformat()
        conn = get_connection(row_factory=sqlite3.Row)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get all lists of this type
        cursor.execute("""
            SELECT id, name, items FROM lists 
            WHERE list_type = ? AND user_id = ?
        """, (list_type, user_id))
        
        reminders = []
        for row in cursor.fetchall():
            items = json.loads(row['items']) if row['items'] else []
            for item in items:
                if (item.get('due_date') == today or 
                    (item.get('reminder_type') == 'daily' and item.get('due_time'))):
                    reminders.append({
                        "list_id": row['id'],
                        "list_name": row['name'],
                        "item_text": item['text'],
                        "due_time": item.get('due_time'),
                        "reminder_category": item.get('reminder_category', 'personal'),
                        "priority": item.get('priority', 'medium'),
                        "requires_acknowledgment": item.get('requires_acknowledgment', False)
                    })
        
        conn.close()
        return {"reminders": reminders, "date": today, "count": len(reminders)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Time Estimation Endpoints
@router.post("/{list_type}/{list_id}/items/{item_index}/estimate-time")
async def estimate_item_time(
    list_type: str,
    list_id: int,
    item_index: int,
    estimated_duration_minutes: Optional[int] = None,
    estimated_duration_hours: Optional[float] = None,
    estimated_duration_days: Optional[float] = None,
    time_estimation_confidence: Optional[str] = "medium",
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Add time estimation to a list item"""
    try:
        conn = get_connection(row_factory=sqlite3.Row)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get the current list
        cursor.execute("""
            SELECT items FROM lists 
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (list_id, list_type, user_id))
        
        result = cursor.fetchone()
        if not result:
            conn.close()
            raise HTTPException(status_code=404, detail="List not found")
        
        items = json.loads(result['items']) if result['items'] else []
        
        # Find and update the item by index
        if item_index < 0 or item_index >= len(items):
            conn.close()
            raise HTTPException(status_code=404, detail="Item index out of range")
        
        # Update the item at the specified index
        items[item_index]['estimated_duration_minutes'] = estimated_duration_minutes
        items[item_index]['estimated_duration_hours'] = estimated_duration_hours
        items[item_index]['estimated_duration_days'] = estimated_duration_days
        items[item_index]['time_estimation_confidence'] = time_estimation_confidence
        items[item_index]['time_tracking_enabled'] = True
        
        # Update the list
        cursor.execute("""
            UPDATE lists 
            SET items = ?, updated_at = ?
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (json.dumps(items), datetime.now().isoformat(), list_id, list_type, user_id))
        
        conn.commit()
        conn.close()
        
        return {"message": "Time estimation added to item", "item_index": item_index}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error estimating time: {str(e)}")

@router.post("/{list_type}/{list_id}/items/{item_index}/start-timer")
async def start_item_timer(
    list_type: str,
    list_id: int,
    item_index: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Start time tracking for a list item"""
    try:
        conn = get_connection(row_factory=sqlite3.Row)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get the current list
        cursor.execute("""
            SELECT items FROM lists 
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (list_id, list_type, user_id))
        
        result = cursor.fetchone()
        if not result:
            conn.close()
            raise HTTPException(status_code=404, detail="List not found")
        
        items = json.loads(result['items']) if result['items'] else []
        
        # Find and update the item by index
        if item_index < 0 or item_index >= len(items):
            conn.close()
            raise HTTPException(status_code=404, detail="Item index out of range")
        
        # Update the item at the specified index
        items[item_index]['start_time'] = datetime.now().isoformat()
        items[item_index]['time_tracking_enabled'] = True
        
        # Update the list
        cursor.execute("""
            UPDATE lists 
            SET items = ?, updated_at = ?
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (json.dumps(items), datetime.now().isoformat(), list_id, list_type, user_id))
        
        conn.commit()
        conn.close()
        
        return {"message": "Timer started for item", "item_index": item_index, "start_time": datetime.now().isoformat()}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting timer: {str(e)}")

@router.post("/{list_type}/{list_id}/items/{item_index}/stop-timer")
async def stop_item_timer(
    list_type: str,
    list_id: int,
    item_index: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Stop time tracking for a list item and calculate actual duration"""
    try:
        conn = get_connection(row_factory=sqlite3.Row)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get the current list
        cursor.execute("""
            SELECT items FROM lists 
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (list_id, list_type, user_id))
        
        result = cursor.fetchone()
        if not result:
            conn.close()
            raise HTTPException(status_code=404, detail="List not found")
        
        items = json.loads(result['items']) if result['items'] else []
        
        # Find and update the item by index
        if item_index < 0 or item_index >= len(items):
            conn.close()
            raise HTTPException(status_code=404, detail="Item index out of range")
        
        # Check if timer was started
        if not items[item_index].get('start_time'):
            conn.close()
            raise HTTPException(status_code=400, detail="Timer was not started for this item")
        
        # Calculate duration and update item
        start_time = datetime.fromisoformat(items[item_index]['start_time'])
        end_time = datetime.now()
        duration_minutes = int((end_time - start_time).total_seconds() / 60)
        
        items[item_index]['end_time'] = end_time.isoformat()
        items[item_index]['actual_duration_minutes'] = duration_minutes
        items[item_index]['time_tracking_enabled'] = False
        
        # Update the list
        cursor.execute("""
            UPDATE lists 
            SET items = ?, updated_at = ?
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (json.dumps(items), datetime.now().isoformat(), list_id, list_type, user_id))
        
        conn.commit()
        conn.close()
        
        return {
            "message": "Timer stopped for item", 
            "item_index": item_index, 
            "actual_duration_minutes": duration_minutes,
            "end_time": end_time.isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error stopping timer: {str(e)}")

@router.get("/{list_type}/{list_id}/time-summary")
async def get_list_time_summary(
    list_type: str,
    list_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Get time summary for all items in a list"""
    try:
        conn = get_connection(row_factory=sqlite3.Row)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get the list
        cursor.execute("""
            SELECT items FROM lists 
            WHERE id = ? AND list_type = ? AND user_id = ?
        """, (list_id, list_type, user_id))
        
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="List not found")
        
        items = json.loads(result['items']) if result['items'] else []
        
        # Calculate time summary
        total_estimated_minutes = 0
        total_actual_minutes = 0
        completed_items = 0
        items_with_estimates = 0
        items_with_actual_time = 0
        
        for item in items:
            if item.get('completed'):
                completed_items += 1
            
            if item.get('estimated_duration_minutes'):
                total_estimated_minutes += item['estimated_duration_minutes']
                items_with_estimates += 1
            
            if item.get('actual_duration_minutes'):
                total_actual_minutes += item['actual_duration_minutes']
                items_with_actual_time += 1
        
        # Calculate accuracy
        accuracy = None
        if total_estimated_minutes > 0 and total_actual_minutes > 0:
            accuracy = (total_estimated_minutes / total_actual_minutes) * 100
        
        conn.close()
        
        return {
            "list_id": list_id,
            "total_items": len(items),
            "completed_items": completed_items,
            "completion_percentage": (completed_items / len(items) * 100) if items else 0,
            "total_estimated_minutes": total_estimated_minutes,
            "total_actual_minutes": total_actual_minutes,
            "items_with_estimates": items_with_estimates,
            "items_with_actual_time": items_with_actual_time,
            "estimation_accuracy": accuracy,
            "estimated_hours": round(total_estimated_minutes / 60, 2),
            "actual_hours": round(total_actual_minutes / 60, 2)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting time summary: {str(e)}")


# Martin-inspired productivity endpoints


@router.put("/focus-session/{session_id}/complete")
async def complete_focus_session(session_id: int, productivity_score: Optional[float] = None):
    """Complete a focus session"""
    try:
        conn = get_connection(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE focus_sessions 
            SET completed = TRUE, end_time = ?, productivity_score = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), productivity_score, session_id))
        
        conn.commit()
        conn.close()
        
        return {"message": "Focus session completed", "session_id": session_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error completing focus session: {str(e)}")

@router.get("/focus-sessions")
async def get_focus_sessions(session: AuthenticatedSession = Depends(validate_session)):
    """Get focus session history"""
    user_id = session.user_id
    try:
        conn = get_connection(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM focus_sessions 
            ORDER BY start_time DESC 
            LIMIT 50
        """)
        
        sessions = []
        for row in cursor.fetchall():
            sessions.append({
                "id": row[0],
                "task_id": row[1],
                "task_text": row[2],
                "duration_minutes": row[3],
                "break_duration_minutes": row[4],
                "session_type": row[5],
                "start_time": row[6],
                "end_time": row[7],
                "completed": bool(row[8]),
                "productivity_score": row[9],
                "created_at": row[10]
            })
        
        conn.close()
        return {"sessions": sessions, "count": len(sessions)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting focus sessions: {str(e)}")


@router.post("/break-reminder")
async def create_break_reminder(reminder: BreakReminder):
    """Create a break reminder"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Create break_reminders table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS break_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT 'default',
                reminder_type TEXT DEFAULT 'break',
                message TEXT NOT NULL,
                interval_minutes INTEGER DEFAULT 25,
                last_reminder TIMESTAMP,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            INSERT INTO break_reminders (user_id, reminder_type, message, interval_minutes, enabled)
            VALUES (?, ?, ?, ?, ?)
        """, (reminder.user_id, reminder.reminder_type, reminder.message, 
              reminder.interval_minutes, reminder.enabled))
        
        reminder_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            "id": reminder_id,
            "message": "Break reminder created",
            "interval_minutes": reminder.interval_minutes
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating break reminder: {str(e)}")

@router.get("/break-reminders")
async def get_break_reminders(session: AuthenticatedSession = Depends(validate_session)):
    """Get active break reminders"""
    user_id = session.user_id
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM break_reminders 
            WHERE user_id = ? AND enabled = TRUE
            ORDER BY created_at DESC
        """, (user_id,))
        
        reminders = []
        for row in cursor.fetchall():
            reminders.append({
                "id": row[0],
                "user_id": row[1],
                "reminder_type": row[2],
                "message": row[3],
                "interval_minutes": row[4],
                "last_reminder": row[5],
                "enabled": bool(row[6]),
                "created_at": row[7]
            })
        
        conn.close()
        return {"reminders": reminders, "count": len(reminders)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting break reminders: {str(e)}")

@router.put("/items/{item_id}/move")
async def move_list_item(
    item_id: int,
    new_date: str = Query(..., description="New due date (YYYY-MM-DD)"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Move a list item to a different due date"""
    user_id = session.user_id
    conn = get_connection()
    cursor = conn.cursor()
    
    # Verify item exists and belongs to user via list ownership
    cursor.execute("""
        SELECT li.id FROM list_items li
        JOIN lists l ON li.list_id = l.id
        WHERE li.id = ? AND l.user_id = ?
    """, (item_id, user_id))
    
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="List item not found")
    
    # Update the item due_date
    cursor.execute("""
        UPDATE list_items 
        SET due_date = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (new_date, item_id))
    
    conn.commit()
    conn.close()
    
    return {
        "message": "List item moved successfully",
        "id": item_id,
        "due_date": new_date
    }

# WebSocket endpoint for real-time list updates
@router.websocket("/ws/{user_id}")
async def lists_websocket(websocket: WebSocket, user_id: str):
    """WebSocket endpoint for real-time list synchronization across devices"""
    await lists_ws_manager.connect(websocket, user_id)
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connected",
            "message": "Lists WebSocket connected",
            "user_id": user_id
        })
        
        # Keep connection alive and handle pings
        while True:
            data = await websocket.receive_text()
            # Echo back as heartbeat
            await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
    except WebSocketDisconnect:
        await lists_ws_manager.disconnect(websocket, user_id)
    except Exception as e:
        print(f"WebSocket error: {e}")
        await lists_ws_manager.disconnect(websocket, user_id)

