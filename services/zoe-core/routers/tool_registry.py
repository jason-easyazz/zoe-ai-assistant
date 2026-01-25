"""
Tool Registry & AI-Driven Invocation System
==========================================

Implements advanced agent concepts for safe, intelligent tool selection and execution.
Based on the analysis document priorities for tool use and APIs.

Features:
- ToolRegistry with permission-safe execution
- AI tool selection and invocation
- Confirmation prompts for destructive actions
- Tool wrapping and sandboxing
- Execution monitoring and rollback
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Callable, Union
from datetime import datetime
from enum import Enum
import sqlite3
import json
import asyncio
import inspect
import subprocess
import os
import logging
from dataclasses import dataclass
import importlib
import sys

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["tool-registry"])

class ToolCategory(str, Enum):
    SYSTEM = "system"
    FILE = "file"
    NETWORK = "network"
    DATABASE = "database"
    AI = "ai"
    CALENDAR = "calendar"
    MEMORY = "memory"
    HOMEASSISTANT = "homeassistant"
    NOTIFICATION = "notification"
    MUSIC = "music"
    CUSTOM = "custom"

class ToolPermission(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ADMIN = "admin"
    DESTRUCTIVE = "destructive"

class ToolStatus(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    ERROR = "error"
    MAINTENANCE = "maintenance"

@dataclass
class ToolParameter:
    name: str
    type: str
    required: bool
    description: str
    default: Any = None
    validation: Optional[Callable] = None

class ToolDefinition(BaseModel):
    """Definition of a tool in the registry"""
    tool_id: str
    name: str
    description: str
    category: ToolCategory
    permissions: List[ToolPermission]
    parameters: List[ToolParameter] = Field(default_factory=list)
    function: Optional[Callable] = None
    status: ToolStatus = ToolStatus.AVAILABLE
    requires_confirmation: bool = False
    timeout_seconds: int = 30
    retry_count: int = 3
    rollback_function: Optional[Callable] = None
    created_at: Optional[datetime] = None
    last_used: Optional[datetime] = None
    usage_count: int = 0
    success_rate: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ToolExecution(BaseModel):
    """Record of a tool execution"""
    execution_id: str
    tool_id: str
    parameters: Dict[str, Any]
    status: str = "pending"  # pending, running, completed, failed, cancelled
    result: Optional[Any] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    user_id: str = "default"
    session_id: Optional[str] = None
    requires_confirmation: bool = False
    confirmed: bool = False

class ToolInvocationRequest(BaseModel):
    """Request to invoke a tool"""
    tool_id: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    user_id: str = "default"
    session_id: Optional[str] = None
    auto_confirm: bool = False
    timeout_seconds: Optional[int] = None

class AIInvocationRequest(BaseModel):
    """Request for AI to select and invoke tools"""
    user_request: str
    context: Dict[str, Any] = Field(default_factory=dict)
    user_id: str = "default"
    session_id: Optional[str] = None
    max_tools: int = 5
    require_confirmation: bool = True

# Database setup
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def init_tool_registry_db():
    """Initialize tool registry database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Tools table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tools (
            tool_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            permissions TEXT,  -- JSON array
            parameters TEXT,   -- JSON array
            status TEXT DEFAULT 'available',
            requires_confirmation BOOLEAN DEFAULT FALSE,
            timeout_seconds INTEGER DEFAULT 30,
            retry_count INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_used TEXT,
            usage_count INTEGER DEFAULT 0,
            success_rate REAL DEFAULT 1.0,
            metadata TEXT   -- JSON object
        )
    ''')
    
    # Tool executions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tool_executions (
            execution_id TEXT PRIMARY KEY,
            tool_id TEXT NOT NULL,
            parameters TEXT,  -- JSON object
            status TEXT DEFAULT 'pending',
            result TEXT,      -- JSON object
            error_message TEXT,
            started_at TEXT,
            completed_at TEXT,
            duration_ms INTEGER,
            user_id TEXT DEFAULT 'default',
            session_id TEXT,
            requires_confirmation BOOLEAN DEFAULT FALSE,
            confirmed BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (tool_id) REFERENCES tools(tool_id)
        )
    ''')
    
    # AI invocations table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_invocations (
            invocation_id TEXT PRIMARY KEY,
            user_request TEXT NOT NULL,
            context TEXT,     -- JSON object
            selected_tools TEXT,  -- JSON array
            execution_ids TEXT,   -- JSON array
            status TEXT DEFAULT 'pending',
            user_id TEXT DEFAULT 'default',
            session_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

# Initialize database
init_tool_registry_db()

class ToolRegistry:
    """Main tool registry and execution engine"""
    
    def __init__(self):
        self.tools: Dict[str, ToolDefinition] = {}
        self.execution_queue = asyncio.Queue()
        self.active_executions: Dict[str, ToolExecution] = {}
        self._register_default_tools()
    
    def _register_default_tools(self):
        """Register default tools in the system"""
        
        # File operations
        self.register_tool(ToolDefinition(
            tool_id="file_read",
            name="Read File",
            description="Read contents of a file",
            category=ToolCategory.FILE,
            permissions=[ToolPermission.READ],
            parameters=[
                ToolParameter("file_path", "string", True, "Path to the file to read")
            ],
            function=self._file_read,
            requires_confirmation=False
        ))
        
        self.register_tool(ToolDefinition(
            tool_id="file_write",
            name="Write File",
            description="Write content to a file",
            category=ToolCategory.FILE,
            permissions=[ToolPermission.WRITE],
            parameters=[
                ToolParameter("file_path", "string", True, "Path to the file to write"),
                ToolParameter("content", "string", True, "Content to write to the file"),
                ToolParameter("append", "boolean", False, "Whether to append to existing file", False)
            ],
            function=self._file_write,
            requires_confirmation=True,
            rollback_function=self._file_write_rollback
        ))
        
        # Database operations
        self.register_tool(ToolDefinition(
            tool_id="db_query",
            name="Database Query",
            description="Execute a SQL query on the database",
            category=ToolCategory.DATABASE,
            permissions=[ToolPermission.READ],
            parameters=[
                ToolParameter("query", "string", True, "SQL query to execute"),
                ToolParameter("database", "string", False, "Database file path", "/app/data/zoe.db")
            ],
            function=self._db_query,
            requires_confirmation=False
        ))
        
        # Calendar operations
        self.register_tool(ToolDefinition(
            tool_id="calendar_create_event",
            name="Create Calendar Event",
            description="Create a new calendar event",
            category=ToolCategory.CALENDAR,
            permissions=[ToolPermission.WRITE],
            parameters=[
                ToolParameter("title", "string", True, "Event title"),
                ToolParameter("start_time", "string", True, "Event start time (ISO format)"),
                ToolParameter("end_time", "string", False, "Event end time (ISO format)"),
                ToolParameter("description", "string", False, "Event description"),
                ToolParameter("location", "string", False, "Event location")
            ],
            function=self._calendar_create_event,
            requires_confirmation=True
        ))
        
        # Memory operations
        self.register_tool(ToolDefinition(
            tool_id="memory_search",
            name="Search Memory",
            description="Search through user memories",
            category=ToolCategory.MEMORY,
            permissions=[ToolPermission.READ],
            parameters=[
                ToolParameter("query", "string", True, "Search query"),
                ToolParameter("limit", "integer", False, "Maximum results to return", 10)
            ],
            function=self._memory_search,
            requires_confirmation=False
        ))
        
        # Notification operations
        self.register_tool(ToolDefinition(
            tool_id="send_notification",
            name="Send Notification",
            description="Send a notification to the user",
            category=ToolCategory.NOTIFICATION,
            permissions=[ToolPermission.WRITE],
            parameters=[
                ToolParameter("title", "string", True, "Notification title"),
                ToolParameter("message", "string", True, "Notification message"),
                ToolParameter("priority", "string", False, "Notification priority", "medium")
            ],
            function=self._send_notification,
            requires_confirmation=False
        ))
        
        # System operations
        self.register_tool(ToolDefinition(
            tool_id="system_info",
            name="System Information",
            description="Get system information and status",
            category=ToolCategory.SYSTEM,
            permissions=[ToolPermission.READ],
            parameters=[],
            function=self._system_info,
            requires_confirmation=False
        ))
        
        # HomeAssistant operations
        self.register_tool(ToolDefinition(
            tool_id="ha_turn_on_light",
            name="Turn On Light",
            description="Turn on a HomeAssistant light",
            category=ToolCategory.HOMEASSISTANT,
            permissions=[ToolPermission.EXECUTE],
            parameters=[
                ToolParameter("entity_id", "string", True, "HomeAssistant entity ID"),
                ToolParameter("brightness", "integer", False, "Light brightness (0-255)"),
                ToolParameter("color", "string", False, "Light color (hex code)")
            ],
            function=self._ha_turn_on_light,
            requires_confirmation=True
        ))
        
        self.register_tool(ToolDefinition(
            tool_id="ha_play_music",
            name="Play Music",
            description="Play music on HomeAssistant media player",
            category=ToolCategory.HOMEASSISTANT,
            permissions=[ToolPermission.EXECUTE],
            parameters=[
                ToolParameter("entity_id", "string", True, "Media player entity ID"),
                ToolParameter("media_content_id", "string", True, "Media content to play"),
                ToolParameter("media_content_type", "string", False, "Media content type", "music")
            ],
            function=self._ha_play_music,
            requires_confirmation=True
        ))
        
        # 🎵 Music control tools
        self.register_tool(ToolDefinition(
            tool_id="music_play",
            name="Play Music",
            description="Search for and play music. Use for requests like 'play some jazz' or 'play Celine Dion'.",
            category=ToolCategory.MUSIC,
            permissions=[ToolPermission.EXECUTE],
            parameters=[
                ToolParameter("query", "string", True, "Search query - artist name, song title, genre, or mood"),
                ToolParameter("user_id", "string", False, "User ID", "default")
            ],
            function=self._music_play,
            requires_confirmation=False
        ))
        
        self.register_tool(ToolDefinition(
            tool_id="music_pause",
            name="Pause Music",
            description="Pause the currently playing music.",
            category=ToolCategory.MUSIC,
            permissions=[ToolPermission.EXECUTE],
            parameters=[
                ToolParameter("user_id", "string", False, "User ID", "default")
            ],
            function=self._music_pause,
            requires_confirmation=False
        ))
        
        self.register_tool(ToolDefinition(
            tool_id="music_resume",
            name="Resume Music",
            description="Resume paused music playback.",
            category=ToolCategory.MUSIC,
            permissions=[ToolPermission.EXECUTE],
            parameters=[
                ToolParameter("user_id", "string", False, "User ID", "default")
            ],
            function=self._music_resume,
            requires_confirmation=False
        ))
        
        self.register_tool(ToolDefinition(
            tool_id="music_skip",
            name="Skip Track",
            description="Skip to the next track in the queue.",
            category=ToolCategory.MUSIC,
            permissions=[ToolPermission.EXECUTE],
            parameters=[
                ToolParameter("user_id", "string", False, "User ID", "default")
            ],
            function=self._music_skip,
            requires_confirmation=False
        ))
        
        self.register_tool(ToolDefinition(
            tool_id="music_queue_add",
            name="Add to Queue",
            description="Add a song to the music queue without interrupting current playback.",
            category=ToolCategory.MUSIC,
            permissions=[ToolPermission.EXECUTE],
            parameters=[
                ToolParameter("query", "string", True, "Search query for the song to add"),
                ToolParameter("user_id", "string", False, "User ID", "default")
            ],
            function=self._music_queue_add,
            requires_confirmation=False
        ))
        
        self.register_tool(ToolDefinition(
            tool_id="music_search",
            name="Search Music",
            description="Search for music without playing it. Returns a list of matching tracks.",
            category=ToolCategory.MUSIC,
            permissions=[ToolPermission.READ],
            parameters=[
                ToolParameter("query", "string", True, "Search query"),
                ToolParameter("limit", "integer", False, "Maximum results to return", 5),
                ToolParameter("user_id", "string", False, "User ID", "default")
            ],
            function=self._music_search,
            requires_confirmation=False
        ))
        
        self.register_tool(ToolDefinition(
            tool_id="music_recommend",
            name="Get Music Recommendations",
            description="Get personalized music recommendations based on listening history.",
            category=ToolCategory.MUSIC,
            permissions=[ToolPermission.READ],
            parameters=[
                ToolParameter("type", "string", False, "Recommendation type: 'radio', 'discover', or 'similar'", "radio"),
                ToolParameter("limit", "integer", False, "Maximum results to return", 10),
                ToolParameter("user_id", "string", False, "User ID", "default")
            ],
            function=self._music_recommend,
            requires_confirmation=False
        ))
        
        self.register_tool(ToolDefinition(
            tool_id="music_now_playing",
            name="Now Playing",
            description="Get information about the currently playing track.",
            category=ToolCategory.MUSIC,
            permissions=[ToolPermission.READ],
            parameters=[
                ToolParameter("user_id", "string", False, "User ID", "default")
            ],
            function=self._music_now_playing,
            requires_confirmation=False
        ))
        
        logger.info(f"Registered {len(self.tools)} default tools")
    
    def register_tool(self, tool: ToolDefinition):
        """Register a new tool in the registry"""
        tool.created_at = datetime.now()
        self.tools[tool.tool_id] = tool
        
        # Store in database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO tools 
            (tool_id, name, description, category, permissions, parameters, 
             status, requires_confirmation, timeout_seconds, retry_count, 
             created_at, usage_count, success_rate, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            tool.tool_id,
            tool.name,
            tool.description,
            tool.category.value,
            json.dumps([p.value for p in tool.permissions]),
            json.dumps([{
                "name": p.name,
                "type": p.type,
                "required": p.required,
                "description": p.description,
                "default": p.default
            } for p in tool.parameters]),
            tool.status.value,
            tool.requires_confirmation,
            tool.timeout_seconds,
            tool.retry_count,
            tool.created_at.isoformat(),
            tool.usage_count,
            tool.success_rate,
            json.dumps(tool.metadata)
        ))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Registered tool: {tool.tool_id} ({tool.name})")
    
    async def invoke_tool(self, request: ToolInvocationRequest) -> ToolExecution:
        """Invoke a tool with the given parameters"""
        if request.tool_id not in self.tools:
            raise HTTPException(status_code=404, detail=f"Tool {request.tool_id} not found")
        
        tool = self.tools[request.tool_id]
        
        # Check if tool requires confirmation
        if tool.requires_confirmation and not request.auto_confirm:
            execution = ToolExecution(
                execution_id=f"exec_{datetime.now().timestamp()}",
                tool_id=request.tool_id,
                parameters=request.parameters,
                user_id=request.user_id,
                session_id=request.session_id,
                requires_confirmation=True,
                status="pending_confirmation"
            )
            
            # Store in database
            self._store_execution(execution)
            return execution
        
        # Execute tool
        return await self._execute_tool(tool, request.parameters, request.user_id, request.session_id)
    
    async def _execute_tool(self, tool: ToolDefinition, parameters: Dict[str, Any], 
                           user_id: str, session_id: Optional[str] = None) -> ToolExecution:
        """Execute a tool with monitoring and error handling"""
        execution = ToolExecution(
            execution_id=f"exec_{datetime.now().timestamp()}",
            tool_id=tool.tool_id,
            parameters=parameters,
            user_id=user_id,
            session_id=session_id,
            status="running",
            started_at=datetime.now()
        )
        
        self.active_executions[execution.execution_id] = execution
        self._store_execution(execution)
        
        try:
            # Validate parameters
            self._validate_parameters(tool, parameters)
            
            # Execute the tool function
            if asyncio.iscoroutinefunction(tool.function):
                result = await asyncio.wait_for(
                    tool.function(**parameters), 
                    timeout=tool.timeout_seconds
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, tool.function, **parameters
                    ),
                    timeout=tool.timeout_seconds
                )
            
            # Update execution
            execution.status = "completed"
            execution.result = result
            execution.completed_at = datetime.now()
            execution.duration_ms = int((execution.completed_at - execution.started_at).total_seconds() * 1000)
            
            # Update tool usage statistics
            tool.usage_count += 1
            tool.last_used = datetime.now()
            self._update_tool_stats(tool)
            
            logger.info(f"Tool {tool.tool_id} executed successfully in {execution.duration_ms}ms")
            
        except Exception as e:
            execution.status = "failed"
            execution.error_message = str(e)
            execution.completed_at = datetime.now()
            execution.duration_ms = int((execution.completed_at - execution.started_at).total_seconds() * 1000)
            
            logger.error(f"Tool {tool.tool_id} execution failed: {e}")
            
            # Update tool success rate
            tool.usage_count += 1
            tool.success_rate = max(0, tool.success_rate - 0.1)
            self._update_tool_stats(tool)
        
        finally:
            self.active_executions.pop(execution.execution_id, None)
            self._update_execution(execution)
        
        return execution
    
    def _validate_parameters(self, tool: ToolDefinition, parameters: Dict[str, Any]):
        """Validate tool parameters"""
        for param in tool.parameters:
            if param.required and param.name not in parameters:
                raise ValueError(f"Required parameter '{param.name}' missing")
            
            if param.name in parameters and param.validation:
                if not param.validation(parameters[param.name]):
                    raise ValueError(f"Parameter '{param.name}' validation failed")
    
    def _store_execution(self, execution: ToolExecution):
        """Store execution record in database"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO tool_executions 
            (execution_id, tool_id, parameters, status, result, error_message,
             started_at, completed_at, duration_ms, user_id, session_id,
             requires_confirmation, confirmed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            execution.execution_id,
            execution.tool_id,
            json.dumps(execution.parameters),
            execution.status,
            json.dumps(execution.result) if execution.result else None,
            execution.error_message,
            execution.started_at.isoformat() if execution.started_at else None,
            execution.completed_at.isoformat() if execution.completed_at else None,
            execution.duration_ms,
            execution.user_id,
            execution.session_id,
            execution.requires_confirmation,
            execution.confirmed
        ))
        
        conn.commit()
        conn.close()
    
    def _update_execution(self, execution: ToolExecution):
        """Update execution record in database"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE tool_executions 
            SET status = ?, result = ?, error_message = ?, completed_at = ?, duration_ms = ?, confirmed = ?
            WHERE execution_id = ?
        ''', (
            execution.status,
            json.dumps(execution.result) if execution.result else None,
            execution.error_message,
            execution.completed_at.isoformat() if execution.completed_at else None,
            execution.duration_ms,
            execution.confirmed,
            execution.execution_id
        ))
        
        conn.commit()
        conn.close()
    
    def _update_tool_stats(self, tool: ToolDefinition):
        """Update tool statistics in database"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE tools 
            SET usage_count = ?, last_used = ?, success_rate = ?
            WHERE tool_id = ?
        ''', (
            tool.usage_count,
            tool.last_used.isoformat() if tool.last_used else None,
            tool.success_rate,
            tool.tool_id
        ))
        
        conn.commit()
        conn.close()
    
    async def ai_select_tools(self, request: AIInvocationRequest) -> List[str]:
        """Use AI to select appropriate tools based on user request"""
        # Simple rule-based tool selection (in real implementation, use LLM)
        selected_tools = []
        user_request = request.user_request.lower()
        
        # Calendar-related tools
        if any(word in user_request for word in ["event", "meeting", "appointment", "schedule", "calendar"]):
            selected_tools.append("calendar_create_event")
        
        # File-related tools
        if any(word in user_request for word in ["read", "write", "file", "document"]):
            if "write" in user_request or "create" in user_request:
                selected_tools.append("file_write")
            else:
                selected_tools.append("file_read")
        
        # Memory-related tools
        if any(word in user_request for word in ["remember", "memory", "search", "find"]):
            selected_tools.append("memory_search")
        
        # HomeAssistant tools
        if any(word in user_request for word in ["light", "turn on", "brightness"]):
            selected_tools.append("ha_turn_on_light")
        
        if any(word in user_request for word in ["music", "play", "song", "audio"]):
            selected_tools.append("ha_play_music")
        
        # Notification tools
        if any(word in user_request for word in ["notify", "alert", "remind"]):
            selected_tools.append("send_notification")
        
        # System tools
        if any(word in user_request for word in ["system", "status", "info", "health"]):
            selected_tools.append("system_info")
        
        return selected_tools[:request.max_tools]
    
    # Tool implementations
    
    def _file_read(self, file_path: str) -> str:
        """Read file contents"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            raise Exception(f"Failed to read file {file_path}: {e}")
    
    def _file_write(self, file_path: str, content: str, append: bool = False) -> Dict[str, Any]:
        """Write content to file"""
        try:
            mode = 'a' if append else 'w'
            with open(file_path, mode, encoding='utf-8') as f:
                f.write(content)
            
            return {
                "success": True,
                "file_path": file_path,
                "bytes_written": len(content),
                "mode": "append" if append else "write"
            }
        except Exception as e:
            raise Exception(f"Failed to write file {file_path}: {e}")
    
    def _file_write_rollback(self, file_path: str, content: str, append: bool = False):
        """Rollback file write operation"""
        # In a real implementation, this would restore the original file content
        logger.info(f"Rolling back file write operation for {file_path}")
    
    def _db_query(self, query: str, database: str = "/app/data/zoe.db") -> List[Dict[str, Any]]:
        """Execute database query"""
        try:
            conn = sqlite3.connect(database)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute(query)
            results = [dict(row) for row in cursor.fetchall()]
            
            conn.close()
            return results
        except Exception as e:
            raise Exception(f"Database query failed: {e}")
    
    def _calendar_create_event(self, title: str, start_time: str, end_time: str = None, 
                             description: str = "", location: str = "") -> Dict[str, Any]:
        """Create calendar event"""
        try:
            # This would integrate with the actual calendar system
            return {
                "success": True,
                "event_id": f"event_{datetime.now().timestamp()}",
                "title": title,
                "start_time": start_time,
                "end_time": end_time,
                "description": description,
                "location": location
            }
        except Exception as e:
            raise Exception(f"Failed to create calendar event: {e}")
    
    def _memory_search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search user memories"""
        try:
            # This would integrate with the actual memory system
            return [
                {
                    "id": f"memory_{i}",
                    "content": f"Memory result {i} for query: {query}",
                    "relevance": 0.9 - (i * 0.1)
                }
                for i in range(min(limit, 5))
            ]
        except Exception as e:
            raise Exception(f"Memory search failed: {e}")
    
    def _send_notification(self, title: str, message: str, priority: str = "medium") -> Dict[str, Any]:
        """Send notification"""
        try:
            # This would integrate with the actual notification system
            return {
                "success": True,
                "notification_id": f"notif_{datetime.now().timestamp()}",
                "title": title,
                "message": message,
                "priority": priority
            }
        except Exception as e:
            raise Exception(f"Failed to send notification: {e}")
    
    def _system_info(self) -> Dict[str, Any]:
        """Get system information"""
        try:
            return {
                "uptime": "24h 30m",
                "memory_usage": "2.1GB / 8GB",
                "cpu_usage": "15%",
                "disk_usage": "45GB / 500GB",
                "services": {
                    "zoe-core": "running",
                    "zoe-ui": "running",
                    "zoe-litellm": "running"
                }
            }
        except Exception as e:
            raise Exception(f"Failed to get system info: {e}")
    
    def _ha_turn_on_light(self, entity_id: str, brightness: int = None, color: str = None) -> Dict[str, Any]:
        """Turn on HomeAssistant light"""
        try:
            # This would integrate with HomeAssistant API
            return {
                "success": True,
                "entity_id": entity_id,
                "brightness": brightness,
                "color": color,
                "state": "on"
            }
        except Exception as e:
            raise Exception(f"Failed to turn on light {entity_id}: {e}")
    
    def _ha_play_music(self, entity_id: str, media_content_id: str, 
                      media_content_type: str = "music") -> Dict[str, Any]:
        """Play music on HomeAssistant media player"""
        try:
            # This would integrate with HomeAssistant API
            return {
                "success": True,
                "entity_id": entity_id,
                "media_content_id": media_content_id,
                "media_content_type": media_content_type,
                "state": "playing"
            }
        except Exception as e:
            raise Exception(f"Failed to play music on {entity_id}: {e}")
    
    # 🎵 Music tool implementations
    async def _music_play(self, query: str, user_id: str = "default") -> Dict[str, Any]:
        """Search for and play music"""
        try:
            from services.music.youtube_provider import YouTubeMusicProvider
            from services.music.media_controller import MediaController
            
            provider = YouTubeMusicProvider()
            controller = MediaController(provider)
            
            # Search for tracks
            results = await provider.search(query, user_id, limit=1)
            if not results:
                return {"success": False, "error": f"No results found for '{query}'"}
            
            track = results[0]
            # Play the first result
            result = await controller.play(
                track_id=track.get("videoId") or track.get("id"),
                target_device_id=None,
                user_id=user_id,
                track_info=track
            )
            
            return {
                "success": result.get("success", False),
                "track": {
                    "title": track.get("title", "Unknown"),
                    "artist": track.get("artists", [{}])[0].get("name", "Unknown") if track.get("artists") else "Unknown"
                },
                "message": f"Now playing '{track.get('title', 'Unknown')}'"
            }
        except Exception as e:
            logger.error(f"Music play failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _music_pause(self, user_id: str = "default") -> Dict[str, Any]:
        """Pause music playback"""
        try:
            from services.music.youtube_provider import YouTubeMusicProvider
            from services.music.media_controller import MediaController
            
            provider = YouTubeMusicProvider()
            controller = MediaController(provider)
            
            result = await controller.pause(user_id)
            return {"success": True, "message": "Music paused"}
        except Exception as e:
            logger.error(f"Music pause failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _music_resume(self, user_id: str = "default") -> Dict[str, Any]:
        """Resume music playback"""
        try:
            from services.music.youtube_provider import YouTubeMusicProvider
            from services.music.media_controller import MediaController
            
            provider = YouTubeMusicProvider()
            controller = MediaController(provider)
            
            result = await controller.resume(user_id)
            return {"success": True, "message": "Music resumed"}
        except Exception as e:
            logger.error(f"Music resume failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _music_skip(self, user_id: str = "default") -> Dict[str, Any]:
        """Skip to next track"""
        try:
            from services.music.youtube_provider import YouTubeMusicProvider
            from services.music.media_controller import MediaController
            
            provider = YouTubeMusicProvider()
            controller = MediaController(provider)
            
            result = await controller.skip(user_id)
            if result.get("queue_empty"):
                return {"success": True, "message": "Queue is empty, playback stopped"}
            
            track_info = result.get("track_info", {})
            return {
                "success": True,
                "message": f"Skipped to '{track_info.get('title', 'next track')}'"
            }
        except Exception as e:
            logger.error(f"Music skip failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _music_queue_add(self, query: str, user_id: str = "default") -> Dict[str, Any]:
        """Add a song to the queue"""
        try:
            from services.music.youtube_provider import YouTubeMusicProvider
            from services.music.media_controller import MediaController
            
            provider = YouTubeMusicProvider()
            controller = MediaController(provider)
            
            # Search for tracks
            results = await provider.search(query, user_id, limit=1)
            if not results:
                return {"success": False, "error": f"No results found for '{query}'"}
            
            track = results[0]
            # Add to queue
            await controller.add_to_queue(
                track_id=track.get("videoId") or track.get("id"),
                user_id=user_id,
                track_info=track
            )
            
            return {
                "success": True,
                "message": f"Added '{track.get('title', 'Unknown')}' to queue"
            }
        except Exception as e:
            logger.error(f"Music queue add failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _music_search(self, query: str, limit: int = 5, user_id: str = "default") -> Dict[str, Any]:
        """Search for music"""
        try:
            from services.music.youtube_provider import YouTubeMusicProvider
            
            provider = YouTubeMusicProvider()
            results = await provider.search(query, user_id, limit=limit)
            
            tracks = [
                {
                    "id": r.get("videoId") or r.get("id"),
                    "title": r.get("title", "Unknown"),
                    "artist": r.get("artists", [{}])[0].get("name", "Unknown") if r.get("artists") else "Unknown",
                    "album": r.get("album", {}).get("name") if r.get("album") else None
                }
                for r in results
            ]
            
            return {"success": True, "results": tracks, "count": len(tracks)}
        except Exception as e:
            logger.error(f"Music search failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _music_recommend(self, type: str = "radio", limit: int = 10, user_id: str = "default") -> Dict[str, Any]:
        """Get music recommendations"""
        try:
            from services.music.youtube_provider import YouTubeMusicProvider
            from services.music.recommendation_engine import MetadataRecommendationEngine
            
            provider = YouTubeMusicProvider()
            engine = MetadataRecommendationEngine(provider)
            
            if type == "radio":
                results = await engine.get_radio(user_id, limit=limit)
            elif type == "discover":
                results = await engine.get_discover(user_id, limit=limit)
            elif type == "similar":
                results = await engine.get_mood_based(user_id, limit=limit)
            else:
                results = await engine.get_radio(user_id, limit=limit)
            
            tracks = [
                {
                    "id": r.get("videoId") or r.get("id"),
                    "title": r.get("title", "Unknown"),
                    "artist": r.get("artists", [{}])[0].get("name", "Unknown") if r.get("artists") else "Unknown"
                }
                for r in results
            ]
            
            return {"success": True, "recommendations": tracks, "type": type}
        except Exception as e:
            logger.error(f"Music recommend failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _music_now_playing(self, user_id: str = "default") -> Dict[str, Any]:
        """Get currently playing track"""
        try:
            from services.music.youtube_provider import YouTubeMusicProvider
            from services.music.media_controller import MediaController
            
            provider = YouTubeMusicProvider()
            controller = MediaController(provider)
            
            state = await controller.get_state(user_id)
            if not state or not state.get("track_title"):
                return {"success": True, "is_playing": False, "message": "Nothing is currently playing"}
            
            return {
                "success": True,
                "is_playing": state.get("is_playing", False),
                "track": {
                    "id": state.get("track_id"),
                    "title": state.get("track_title"),
                    "artist": state.get("artist"),
                    "album": state.get("album"),
                    "position_ms": state.get("position_ms"),
                    "duration_ms": state.get("duration_ms")
                }
            }
        except Exception as e:
            logger.error(f"Music now playing failed: {e}")
            return {"success": False, "error": str(e)}

# Initialize tool registry
tool_registry = ToolRegistry()

# API Endpoints

@router.get("/available")
async def list_available_tools(category: Optional[str] = None):
    """List all available tools"""
    tools = list(tool_registry.tools.values())
    
    if category:
        tools = [t for t in tools if t.category.value == category]
    
    return {
        "tools": [
            {
                "tool_id": tool.tool_id,
                "name": tool.name,
                "description": tool.description,
                "category": tool.category.value,
                "permissions": [p.value for p in tool.permissions],
                "requires_confirmation": tool.requires_confirmation,
                "status": tool.status.value,
                "usage_count": tool.usage_count,
                "success_rate": tool.success_rate,
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "required": p.required,
                        "description": p.description,
                        "default": p.default
                    }
                    for p in tool.parameters
                ]
            }
            for tool in tools
        ],
        "total": len(tools)
    }

@router.post("/invoke")
async def invoke_tool(request: ToolInvocationRequest):
    """Invoke a specific tool"""
    try:
        execution = await tool_registry.invoke_tool(request)
        return {
            "execution": execution.dict(),
            "status": execution.status,
            "requires_confirmation": execution.requires_confirmation
        }
    except Exception as e:
        logger.error(f"Tool invocation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/ai-invoke")
async def ai_invoke_tools(request: AIInvocationRequest, background_tasks: BackgroundTasks):
    """Use AI to select and invoke appropriate tools"""
    try:
        # Select tools using AI
        selected_tool_ids = await tool_registry.ai_select_tools(request)
        
        if not selected_tool_ids:
            return {
                "message": "No suitable tools found for the request",
                "selected_tools": [],
                "executions": []
            }
        
        # Store AI invocation record
        invocation_id = f"ai_invoke_{datetime.now().timestamp()}"
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO ai_invocations 
            (invocation_id, user_request, context, selected_tools, user_id, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            invocation_id,
            request.user_request,
            json.dumps(request.context),
            json.dumps(selected_tool_ids),
            request.user_id,
            request.session_id
        ))
        
        conn.commit()
        conn.close()
        
        # Execute tools
        executions = []
        execution_ids = []
        
        for tool_id in selected_tool_ids:
            try:
                tool_request = ToolInvocationRequest(
                    tool_id=tool_id,
                    parameters={},  # AI would determine parameters
                    user_id=request.user_id,
                    session_id=request.session_id,
                    auto_confirm=not request.require_confirmation
                )
                
                execution = await tool_registry.invoke_tool(tool_request)
                executions.append(execution.dict())
                execution_ids.append(execution.execution_id)
                
            except Exception as e:
                logger.error(f"Failed to execute tool {tool_id}: {e}")
                executions.append({
                    "tool_id": tool_id,
                    "status": "failed",
                    "error": str(e)
                })
        
        # Update AI invocation with execution IDs
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE ai_invocations 
            SET execution_ids = ?, status = 'completed', completed_at = ?
            WHERE invocation_id = ?
        ''', (
            json.dumps(execution_ids),
            datetime.now().isoformat(),
            invocation_id
        ))
        conn.commit()
        conn.close()
        
        return {
            "invocation_id": invocation_id,
            "selected_tools": selected_tool_ids,
            "executions": executions,
            "status": "completed"
        }
        
    except Exception as e:
        logger.error(f"AI tool invocation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str):
    """Get execution details"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM tool_executions WHERE execution_id = ?", (execution_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    return {
        "execution_id": row[0],
        "tool_id": row[1],
        "parameters": json.loads(row[2] or '{}'),
        "status": row[3],
        "result": json.loads(row[4]) if row[4] else None,
        "error_message": row[5],
        "started_at": row[6],
        "completed_at": row[7],
        "duration_ms": row[8],
        "user_id": row[9],
        "session_id": row[10],
        "requires_confirmation": bool(row[11]),
        "confirmed": bool(row[12])
    }

@router.post("/executions/{execution_id}/confirm")
async def confirm_execution(execution_id: str):
    """Confirm a pending tool execution"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM tool_executions WHERE execution_id = ?", (execution_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Execution not found")
    
    if row[3] != "pending_confirmation":
        conn.close()
        raise HTTPException(status_code=400, detail="Execution does not require confirmation")
    
    # Mark as confirmed and execute
    cursor.execute('''
        UPDATE tool_executions 
        SET confirmed = TRUE, status = 'confirmed'
        WHERE execution_id = ?
    ''', (execution_id,))
    
    conn.commit()
    conn.close()
    
    # Execute the tool
    tool_id = row[1]
    parameters = json.loads(row[2] or '{}')
    user_id = row[9]
    session_id = row[10]
    
    if tool_id in tool_registry.tools:
        execution = await tool_registry._execute_tool(
            tool_registry.tools[tool_id], 
            parameters, 
            user_id, 
            session_id
        )
        return {"execution": execution.dict()}
    
    return {"message": "Execution confirmed"}

@router.get("/stats")
async def get_tool_stats():
    """Get tool registry statistics"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Tool statistics
    cursor.execute("SELECT COUNT(*), AVG(success_rate) FROM tools")
    tool_count, avg_success = cursor.fetchone()
    
    # Execution statistics
    cursor.execute("SELECT status, COUNT(*) FROM tool_executions GROUP BY status")
    exec_stats = dict(cursor.fetchall())
    
    # AI invocation statistics
    cursor.execute("SELECT COUNT(*) FROM ai_invocations")
    ai_invocations = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        "total_tools": tool_count,
        "average_success_rate": round(avg_success or 0, 3),
        "execution_stats": exec_stats,
        "total_executions": sum(exec_stats.values()),
        "ai_invocations": ai_invocations,
        "active_executions": len(tool_registry.active_executions),
        "categories": list(set(tool.category.value for tool in tool_registry.tools.values()))
    }

@router.post("/register")
async def register_tool(tool: ToolDefinition):
    """Register a new tool in the registry"""
    try:
        tool_registry.register_tool(tool)
        return {"message": f"Tool {tool.tool_id} registered successfully"}
    except Exception as e:
        logger.error(f"Tool registration failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

