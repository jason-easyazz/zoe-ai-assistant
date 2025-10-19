#!/usr/bin/env python3
"""
Zoe MCP Server - Model Context Protocol Server for Zoe
Provides standardized tools for LLMs to interact with Zoe's capabilities
"""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path

import httpx
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ListToolsRequest,
    ListToolsResult,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
    ServerCapabilities,
)

from security import MCPSecurityManager, SecureMCPServer, SECURITY_CONFIG

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ZoeMCPServer:
    """Zoe MCP Server implementation"""
    
    def __init__(self):
        self.server = Server("zoe-mcp-server")
        self.db_path = os.getenv("ZOE_DB_PATH", "/app/data/zoe.db")
        self.zoe_api_url = os.getenv("ZOE_API_URL", "http://zoe-core:8000")
        self.people_service_url = os.getenv("PEOPLE_SERVICE_URL", "http://people-service:8001")
        self.collections_service_url = os.getenv("COLLECTIONS_SERVICE_URL", "http://collections-service:8005")
        self.homeassistant_bridge_url = os.getenv("HOMEASSISTANT_BRIDGE_URL", "http://homeassistant-mcp-bridge:8007")
        self.n8n_bridge_url = os.getenv("N8N_BRIDGE_URL", "http://n8n-mcp-bridge:8009")
        self.auth_token = os.getenv("ZOE_AUTH_TOKEN", "")
        self.session_id = os.getenv("ZOE_SESSION_ID", "")
        
        # Initialize security
        self.security_manager = MCPSecurityManager(
            db_path=self.db_path,
            secret_key=SECURITY_CONFIG["jwt_secret"]
        )
        self.secure_server = SecureMCPServer(self.security_manager)
        
        # Initialize tools
        self._setup_tools()
        
    def _setup_tools(self):
        """Setup MCP tools"""
        
        @self.server.list_tools()
        async def list_tools() -> ListToolsResult:
            """List available tools"""
            tools = [
                Tool(
                    name="search_memories",
                    description="Search through Zoe's memory system for people, projects, facts, and collections",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query for memories"
                            },
                            "memory_type": {
                                "type": "string",
                                "enum": ["people", "projects", "facts", "collections", "all"],
                                "description": "Type of memory to search",
                                "default": "all"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results to return",
                                "default": 10
                            },
                            "include_metadata": {
                                "type": "boolean",
                                "description": "Include metadata in search results",
                                "default": true
                            }
                        },
                        "required": ["query"]
                    }
                ),
                Tool(
                    name="create_person",
                    description="Create a new person in Zoe's memory system",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Person's name"
                            },
                            "relationship": {
                                "type": "string",
                                "description": "Relationship to user (e.g., 'friend', 'family', 'colleague')"
                            },
                            "notes": {
                                "type": "string",
                                "description": "Additional notes about the person"
                            }
                        },
                        "required": ["name"]
                    }
                ),
                Tool(
                    name="create_calendar_event",
                    description="Create a new calendar event",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Event title"
                            },
                            "start_date": {
                                "type": "string",
                                "description": "Start date (YYYY-MM-DD)"
                            },
                            "start_time": {
                                "type": "string",
                                "description": "Start time (HH:MM)"
                            },
                            "description": {
                                "type": "string",
                                "description": "Event description"
                            },
                            "location": {
                                "type": "string",
                                "description": "Event location"
                            }
                        },
                        "required": ["title", "start_date"]
                    }
                ),
                Tool(
                    name="add_to_list",
                    description="Add an item to a user's todo list",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "list_name": {
                                "type": "string",
                                "description": "Name of the list to add to"
                            },
                            "task_text": {
                                "type": "string",
                                "description": "Task description"
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["low", "medium", "high", "critical"],
                                "description": "Task priority",
                                "default": "medium"
                            }
                        },
                        "required": ["list_name", "task_text"]
                    }
                ),
                Tool(
                    name="get_calendar_events",
                    description="Get calendar events for a date range",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "start_date": {
                                "type": "string",
                                "description": "Start date (YYYY-MM-DD)"
                            },
                            "end_date": {
                                "type": "string",
                                "description": "End date (YYYY-MM-DD)"
                            }
                        }
                    }
                ),
                Tool(
                    name="get_lists",
                    description="Get all user's todo lists",
                    inputSchema={
                        "type": "object",
                        "properties": {}
                    }
                ),
                Tool(
                    name="create_collection",
                    description="Create a new collection in Zoe's memory system",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Collection name"
                            },
                            "description": {
                                "type": "string",
                                "description": "Collection description"
                            },
                            "layout_config": {
                                "type": "object",
                                "description": "Visual layout configuration for the collection"
                            }
                        },
                        "required": ["name"]
                    }
                ),
                Tool(
                    name="get_people",
                    description="Get all people from the people service",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "user_id": {
                                "type": "string",
                                "description": "User ID to filter people",
                                "default": "default"
                            }
                        }
                    }
                ),
                Tool(
                    name="get_person_analysis",
                    description="Get comprehensive analysis of a person including relationships and timeline",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "person_id": {
                                "type": "integer",
                                "description": "Person ID to analyze"
                            },
                            "user_id": {
                                "type": "string",
                                "description": "User ID",
                                "default": "default"
                            }
                        },
                        "required": ["person_id"]
                    }
                ),
                Tool(
                    name="get_collections",
                    description="Get all collections from the collections service",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "user_id": {
                                "type": "string",
                                "description": "User ID to filter collections",
                                "default": "default"
                            }
                        }
                    }
                ),
                Tool(
                    name="get_collection_analysis",
                    description="Get comprehensive analysis of a collection including tiles, layouts, and curation",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "collection_id": {
                                "type": "integer",
                                "description": "Collection ID to analyze"
                            },
                            "user_id": {
                                "type": "string",
                                "description": "User ID",
                                "default": "default"
                            }
                        },
                        "required": ["collection_id"]
                    }
                ),
                Tool(
                    name="get_home_assistant_devices",
                    description="Get all devices from Home Assistant (lights, switches, sensors)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "device_type": {
                                "type": "string",
                                "enum": ["lights", "switches", "sensors", "all"],
                                "description": "Type of devices to retrieve",
                                "default": "all"
                            }
                        }
                    }
                ),
                Tool(
                    name="control_home_assistant_device",
                    description="Control a Home Assistant device (turn on/off, set brightness, etc.)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "entity_id": {
                                "type": "string",
                                "description": "Home Assistant entity ID (e.g., 'light.living_room')"
                            },
                            "action": {
                                "type": "string",
                                "enum": ["turn_on", "turn_off", "toggle", "set_brightness", "set_color"],
                                "description": "Action to perform on the device"
                            },
                            "data": {
                                "type": "object",
                                "description": "Additional data for the action (e.g., brightness, color)"
                            }
                        },
                        "required": ["entity_id", "action"]
                    }
                ),
                Tool(
                    name="get_home_assistant_automations",
                    description="Get all automations from Home Assistant",
                    inputSchema={
                        "type": "object",
                        "properties": {}
                    }
                ),
                Tool(
                    name="trigger_home_assistant_automation",
                    description="Trigger a Home Assistant automation",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "automation_id": {
                                "type": "string",
                                "description": "Home Assistant automation entity ID"
                            }
                        },
                        "required": ["automation_id"]
                    }
                ),
                Tool(
                    name="get_home_assistant_scenes",
                    description="Get all scenes from Home Assistant",
                    inputSchema={
                        "type": "object",
                        "properties": {}
                    }
                ),
                Tool(
                    name="activate_home_assistant_scene",
                    description="Activate a Home Assistant scene",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "scene_id": {
                                "type": "string",
                                "description": "Home Assistant scene entity ID"
                            }
                        },
                        "required": ["scene_id"]
                    }
                ),
                Tool(
                    name="get_n8n_workflows",
                    description="Get all workflows from N8N",
                    inputSchema={
                        "type": "object",
                        "properties": {}
                    }
                ),
                Tool(
                    name="create_n8n_workflow",
                    description="Create a new workflow in N8N",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Workflow name"
                            },
                            "nodes": {
                                "type": "array",
                                "description": "Array of workflow nodes"
                            },
                            "connections": {
                                "type": "object",
                                "description": "Node connections"
                            },
                            "active": {
                                "type": "boolean",
                                "description": "Whether to activate the workflow",
                                "default": false
                            }
                        },
                        "required": ["name", "nodes", "connections"]
                    }
                ),
                Tool(
                    name="execute_n8n_workflow",
                    description="Execute a workflow in N8N",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "workflow_id": {
                                "type": "string",
                                "description": "N8N workflow ID"
                            },
                            "input_data": {
                                "type": "object",
                                "description": "Input data for the workflow execution"
                            }
                        },
                        "required": ["workflow_id"]
                    }
                ),
                Tool(
                    name="get_n8n_executions",
                    description="Get workflow executions from N8N",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "workflow_id": {
                                "type": "string",
                                "description": "Filter by workflow ID"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Limit number of results",
                                "default": 20
                            }
                        }
                    }
                ),
                Tool(
                    name="get_n8n_nodes",
                    description="Get all available nodes from N8N",
                    inputSchema={
                        "type": "object",
                        "properties": {}
                    }
                ),
                Tool(
                    name="get_developer_tasks",
                    description="Get developer tasks from the roadmap",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "Filter by task status"
                            }
                        }
                    }
                )
            ]
            return ListToolsResult(tools=tools)
        
        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> CallToolResult:
            """Handle tool calls with security"""
            try:
                # Extract authentication from arguments (MCP doesn't have headers)
                auth_token = arguments.pop("_auth_token", None)
                session_id = arguments.pop("_session_id", None)
                
                # Authenticate user
                user_context = self.secure_server.authenticate_request(
                    f"Bearer {auth_token}" if auth_token else None,
                    session_id
                )
                
                if not user_context:
                    return CallToolResult(
                        content=[TextContent(type="text", text="Authentication required. Please provide valid credentials.")]
                    )
                
                # Execute tool with security
                if name == "search_memories":
                    return await self._search_memories(arguments, user_context)
                elif name == "create_person":
                    return await self._create_person(arguments, user_context)
                elif name == "create_collection":
                    return await self._create_collection(arguments, user_context)
                elif name == "create_calendar_event":
                    return await self._create_calendar_event(arguments, user_context)
                elif name == "add_to_list":
                    return await self._add_to_list(arguments, user_context)
                elif name == "get_calendar_events":
                    return await self._get_calendar_events(arguments, user_context)
                elif name == "get_lists":
                    return await self._get_lists(arguments, user_context)
                elif name == "get_people":
                    return await self._get_people(arguments, user_context)
                elif name == "get_person_analysis":
                    return await self._get_person_analysis(arguments, user_context)
                elif name == "get_collections":
                    return await self._get_collections(arguments, user_context)
                elif name == "get_collection_analysis":
                    return await self._get_collection_analysis(arguments, user_context)
                elif name == "get_home_assistant_devices":
                    return await self._get_home_assistant_devices(arguments, user_context)
                elif name == "control_home_assistant_device":
                    return await self._control_home_assistant_device(arguments, user_context)
                elif name == "get_home_assistant_automations":
                    return await self._get_home_assistant_automations(arguments, user_context)
                elif name == "trigger_home_assistant_automation":
                    return await self._trigger_home_assistant_automation(arguments, user_context)
                elif name == "get_home_assistant_scenes":
                    return await self._get_home_assistant_scenes(arguments, user_context)
                elif name == "activate_home_assistant_scene":
                    return await self._activate_home_assistant_scene(arguments, user_context)
                elif name == "get_n8n_workflows":
                    return await self._get_n8n_workflows(arguments, user_context)
                elif name == "create_n8n_workflow":
                    return await self._create_n8n_workflow(arguments, user_context)
                elif name == "execute_n8n_workflow":
                    return await self._execute_n8n_workflow(arguments, user_context)
                elif name == "get_n8n_executions":
                    return await self._get_n8n_executions(arguments, user_context)
                elif name == "get_n8n_nodes":
                    return await self._get_n8n_nodes(arguments, user_context)
                elif name == "get_developer_tasks":
                    return await self._get_developer_tasks(arguments, user_context)
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Unknown tool: {name}")]
                    )
            except PermissionError as e:
                logger.warning(f"Permission denied for tool {name}: {str(e)}")
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Permission denied: {str(e)}")]
                )
            except Exception as e:
                logger.error(f"Error calling tool {name}: {str(e)}")
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Error: {str(e)}")]
                )
    
    async def _search_memories(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Search memories with user isolation and advanced filtering"""
        query = args.get("query", "")
        memory_type = args.get("memory_type", "all")
        limit = args.get("limit", 10)
        include_metadata = args.get("include_metadata", True)
        
        if not query:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: Search query is required")]
            )
        
        # Search in database with user isolation
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        results = []
        
        if memory_type in ["people", "all"]:
            cursor.execute("""
                SELECT name, profile, facts, created_at FROM people 
                WHERE user_id = ? AND (name LIKE ? OR profile LIKE ? OR facts LIKE ?)
                ORDER BY created_at DESC LIMIT ?
            """, (user_context.user_id, f"%{query}%", f"%{query}%", f"%{query}%", limit))
            people = cursor.fetchall()
            for person in people:
                metadata_str = f" (Created: {person[3]})" if include_metadata else ""
                results.append(f"Person: {person[0]} - Profile: {person[1]} - Facts: {person[2]}{metadata_str}")
        
        if memory_type in ["projects", "all"]:
            cursor.execute("""
                SELECT name, description, metadata, created_at FROM projects 
                WHERE user_id = ? AND (name LIKE ? OR description LIKE ? OR metadata LIKE ?)
                ORDER BY created_at DESC LIMIT ?
            """, (user_context.user_id, f"%{query}%", f"%{query}%", f"%{query}%", limit))
            projects = cursor.fetchall()
            for project in projects:
                metadata_str = f" (Created: {project[3]})" if include_metadata else ""
                results.append(f"Project: {project[0]} - Description: {project[1]} - Metadata: {project[2]}{metadata_str}")
        
        if memory_type in ["facts", "all"]:
            cursor.execute("""
                SELECT fact_text, fact_type, source, created_at FROM memory_facts 
                WHERE user_id = ? AND (fact_text LIKE ? OR fact_type LIKE ?)
                ORDER BY created_at DESC LIMIT ?
            """, (user_context.user_id, f"%{query}%", f"%{query}%", limit))
            facts = cursor.fetchall()
            for fact in facts:
                metadata_str = f" (Created: {fact[3]})" if include_metadata else ""
                results.append(f"Fact: {fact[0]} - Type: {fact[1]} - Source: {fact[2]}{metadata_str}")
        
        if memory_type in ["collections", "all"]:
            cursor.execute("""
                SELECT name, description, layout_config, created_at FROM collections 
                WHERE user_id = ? AND (name LIKE ? OR description LIKE ?)
                ORDER BY created_at DESC LIMIT ?
            """, (user_context.user_id, f"%{query}%", f"%{query}%", limit))
            collections = cursor.fetchall()
            for collection in collections:
                metadata_str = f" (Created: {collection[3]})" if include_metadata else ""
                results.append(f"Collection: {collection[0]} - Description: {collection[1]} - Layout: {collection[2]}{metadata_str}")
        
        conn.close()
        
        # Limit total results
        if len(results) > limit:
            results = results[:limit]
        
        result_text = "\n".join(results) if results else "No memories found matching your query."
        
        return CallToolResult(
            content=[TextContent(type="text", text=result_text)]
        )
    
    async def _create_person(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Create a new person with user isolation and validation"""
        name = args.get("name", "").strip()
        relationship = args.get("relationship", "").strip()
        notes = args.get("notes", "").strip()
        
        # Validation
        if not name:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: Person name is required")]
            )
        
        if len(name) > 100:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: Person name must be less than 100 characters")]
            )
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if person already exists for this user
            cursor.execute("SELECT name FROM people WHERE user_id = ? AND name = ?", (user_context.user_id, name))
            if cursor.fetchone():
                conn.close()
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Error: Person '{name}' already exists")]
                )
            
            # Create profile JSON
            profile = {
                "relationship": relationship,
                "notes": notes,
                "created_by": "mcp_server",
                "created_at": datetime.now().isoformat(),
                "created_by_user": user_context.username
            }
            
            cursor.execute("""
                INSERT INTO people (user_id, name, profile, facts, important_dates, preferences)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_context.user_id, name, json.dumps(profile), "{}", "{}", "{}"))
            
            conn.commit()
            conn.close()
            
            return CallToolResult(
                content=[TextContent(type="text", text=f"Successfully created person: {name}")]
            )
            
        except sqlite3.Error as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Database error: {str(e)}")]
            )
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unexpected error: {str(e)}")]
            )
    
    async def _create_collection(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Create a new collection with user isolation and validation"""
        name = args.get("name", "").strip()
        description = args.get("description", "").strip()
        layout_config = args.get("layout_config", {})
        
        # Validation
        if not name:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: Collection name is required")]
            )
        
        if len(name) > 100:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: Collection name must be less than 100 characters")]
            )
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if collection already exists for this user
            cursor.execute("SELECT name FROM collections WHERE user_id = ? AND name = ?", (user_context.user_id, name))
            if cursor.fetchone():
                conn.close()
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Error: Collection '{name}' already exists")]
                )
            
            # Create collection with user isolation
            cursor.execute("""
                INSERT INTO collections (user_id, name, description, layout_config, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user_context.user_id, 
                name, 
                description, 
                json.dumps(layout_config), 
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            
            conn.commit()
            conn.close()
            
            return CallToolResult(
                content=[TextContent(type="text", text=f"Successfully created collection: {name}")]
            )
            
        except sqlite3.Error as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Database error: {str(e)}")]
            )
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unexpected error: {str(e)}")]
            )
    
    async def _create_calendar_event(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Create a calendar event with user isolation"""
        title = args.get("title", "")
        start_date = args.get("start_date", "")
        start_time = args.get("start_time", "")
        description = args.get("description", "")
        location = args.get("location", "")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO events (user_id, title, start_date, start_time, description, location, category)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_context.user_id, title, start_date, start_time, description, location, "personal"))
        
        conn.commit()
        conn.close()
        
        return CallToolResult(
            content=[TextContent(type="text", text=f"Successfully created calendar event: {title}")]
        )
    
    async def _add_to_list(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Add item to list with user isolation"""
        list_name = args.get("list_name", "")
        task_text = args.get("task_text", "")
        priority = args.get("priority", "medium")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Find or create list
        cursor.execute("SELECT id FROM lists WHERE name = ? AND user_id = ?", (list_name, user_context.user_id))
        list_row = cursor.fetchone()
        
        if not list_row:
            # Create new list
            cursor.execute("""
                INSERT INTO lists (user_id, name, category, description)
                VALUES (?, ?, ?, ?)
            """, (user_context.user_id, list_name, "personal", f"List created by MCP server for {user_context.username}"))
            list_id = cursor.lastrowid
        else:
            list_id = list_row[0]
        
        # Add item to list
        cursor.execute("""
            INSERT INTO list_items (list_id, task_text, priority, completed)
            VALUES (?, ?, ?, ?)
        """, (list_id, task_text, priority, False))
        
        conn.commit()
        conn.close()
        
        return CallToolResult(
            content=[TextContent(type="text", text=f"Successfully added '{task_text}' to list '{list_name}'")]
        )
    
    async def _get_calendar_events(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get calendar events with user isolation"""
        start_date = args.get("start_date", "")
        end_date = args.get("end_date", "")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if start_date and end_date:
            cursor.execute("""
                SELECT title, start_date, start_time, description, location 
                FROM events 
                WHERE start_date BETWEEN ? AND ? AND user_id = ?
                ORDER BY start_date, start_time
            """, (start_date, end_date, user_context.user_id))
        else:
            cursor.execute("""
                SELECT title, start_date, start_time, description, location 
                FROM events 
                WHERE user_id = ?
                ORDER BY start_date, start_time
            """, (user_context.user_id,))
        
        events = cursor.fetchall()
        conn.close()
        
        if not events:
            return CallToolResult(
                content=[TextContent(type="text", text="No calendar events found.")]
            )
        
        event_list = []
        for event in events:
            event_list.append(f"- {event[0]} on {event[1]} at {event[2] or 'All day'}")
            if event[3]:
                event_list.append(f"  Description: {event[3]}")
            if event[4]:
                event_list.append(f"  Location: {event[4]}")
        
        return CallToolResult(
            content=[TextContent(type="text", text="Calendar Events:\n" + "\n".join(event_list))]
        )
    
    async def _get_lists(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get all lists with user isolation"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT l.name, l.category, COUNT(li.id) as item_count
            FROM lists l
            LEFT JOIN list_items li ON l.id = li.list_id
            WHERE l.user_id = ?
            GROUP BY l.id, l.name, l.category
        """, (user_context.user_id,))
        
        lists = cursor.fetchall()
        conn.close()
        
        if not lists:
            return CallToolResult(
                content=[TextContent(type="text", text="No lists found.")]
            )
        
        list_text = "Your Lists:\n"
        for list_info in lists:
            list_text += f"- {list_info[0]} ({list_info[1]}) - {list_info[2]} items\n"
        
        return CallToolResult(
            content=[TextContent(type="text", text=list_text)]
        )
    
    async def _get_developer_tasks(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get developer tasks with user isolation"""
        status = args.get("status", "")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if status:
            cursor.execute("""
                SELECT title, status, priority, objective
                FROM developer_tasks
                WHERE status = ? AND user_id = ?
                ORDER BY priority DESC, created_at DESC
            """, (status, user_context.user_id))
        else:
            cursor.execute("""
                SELECT title, status, priority, objective
                FROM developer_tasks
                WHERE user_id = ?
                ORDER BY priority DESC, created_at DESC
            """, (user_context.user_id,))
        
        tasks = cursor.fetchall()
        conn.close()
        
        if not tasks:
            return CallToolResult(
                content=[TextContent(type="text", text="No developer tasks found.")]
            )
        
        task_list = []
        for task in tasks:
            task_list.append(f"- {task[0]} [{task[1]}] ({task[2]})")
            if task[3]:
                task_list.append(f"  Objective: {task[3]}")
        
        return CallToolResult(
            content=[TextContent(type="text", text="Developer Tasks:\n" + "\n".join(task_list))]
        )
    
    async def _get_people(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get all people from the people service"""
        user_id = args.get("user_id", user_context.user_id)
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.people_service_url}/people",
                    params={"user_id": user_id},
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    people = data.get("people", [])
                    
                    if not people:
                        return CallToolResult(
                            content=[TextContent(type="text", text="No people found.")]
                        )
                    
                    people_list = []
                    for person in people:
                        profile = person.get("profile", {})
                        people_list.append(f"- {person['name']} (ID: {person['id']})")
                        if profile.get("relationship"):
                            people_list.append(f"  Relationship: {profile['relationship']}")
                        if profile.get("notes"):
                            people_list.append(f"  Notes: {profile['notes']}")
                    
                    return CallToolResult(
                        content=[TextContent(type="text", text="People:\n" + "\n".join(people_list))]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error fetching people: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to people service: {str(e)}")]
            )
    
    async def _get_person_analysis(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get comprehensive analysis of a person"""
        person_id = args.get("person_id")
        user_id = args.get("user_id", user_context.user_id)
        
        if not person_id:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: Person ID is required")]
            )
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.people_service_url}/people/{person_id}/analysis",
                    params={"user_id": user_id},
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    analysis = data.get("analysis", {})
                    person = analysis.get("person", {})
                    relationships = analysis.get("relationships", {})
                    timeline = analysis.get("timeline", {})
                    insights = analysis.get("insights", {})
                    
                    result = f"Person Analysis for {person.get('name', 'Unknown')}:\n\n"
                    
                    # Person details
                    profile = person.get("profile", {})
                    result += f"Profile:\n"
                    if profile.get("relationship"):
                        result += f"- Relationship: {profile['relationship']}\n"
                    if profile.get("notes"):
                        result += f"- Notes: {profile['notes']}\n"
                    if profile.get("email"):
                        result += f"- Email: {profile['email']}\n"
                    
                    # Relationships
                    result += f"\nRelationships ({relationships.get('count', 0)}):\n"
                    if relationships.get("strongest"):
                        strongest = relationships["strongest"]
                        result += f"- Strongest: {strongest['relationship_type']} with {strongest['person2_name']} (strength: {strongest['strength']})\n"
                    
                    # Timeline
                    result += f"\nRecent Timeline Events ({timeline.get('total_events', 0)}):\n"
                    for event in timeline.get("recent_events", [])[:3]:
                        result += f"- {event['event_title']} ({event['event_date']}) - {event['event_type']}\n"
                    
                    # Insights
                    result += f"\nInsights:\n"
                    if insights.get("relationship_strength_avg"):
                        result += f"- Average relationship strength: {insights['relationship_strength_avg']:.1f}\n"
                    if insights.get("most_common_event_type"):
                        result += f"- Most common event type: {insights['most_common_event_type']}\n"
                    
                    return CallToolResult(
                        content=[TextContent(type="text", text=result)]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error fetching person analysis: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to people service: {str(e)}")]
            )
    
    async def _get_collections(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get all collections from the collections service"""
        user_id = args.get("user_id", user_context.user_id)
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.collections_service_url}/collections",
                    params={"user_id": user_id},
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    collections = data.get("collections", [])
                    
                    if not collections:
                        return CallToolResult(
                            content=[TextContent(type="text", text="No collections found.")]
                        )
                    
                    collections_list = []
                    for collection in collections:
                        collections_list.append(f"- {collection['name']} (ID: {collection['id']})")
                        if collection.get("description"):
                            collections_list.append(f"  Description: {collection['description']}")
                        if collection.get("layout_config"):
                            layout_type = collection['layout_config'].get("type", "unknown")
                            collections_list.append(f"  Layout: {layout_type}")
                    
                    return CallToolResult(
                        content=[TextContent(type="text", text="Collections:\n" + "\n".join(collections_list))]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error fetching collections: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to collections service: {str(e)}")]
            )
    
    async def _get_collection_analysis(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get comprehensive analysis of a collection"""
        collection_id = args.get("collection_id")
        user_id = args.get("user_id", user_context.user_id)
        
        if not collection_id:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: Collection ID is required")]
            )
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.collections_service_url}/collections/{collection_id}/analysis",
                    params={"user_id": user_id},
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    analysis = data.get("analysis", {})
                    collection = analysis.get("collection", {})
                    tiles = analysis.get("tiles", {})
                    layouts = analysis.get("layouts", {})
                    curation = analysis.get("curation", {})
                    insights = analysis.get("insights", {})
                    
                    result = f"Collection Analysis for {collection.get('name', 'Unknown')}:\n\n"
                    
                    # Collection details
                    result += f"Collection Details:\n"
                    if collection.get("description"):
                        result += f"- Description: {collection['description']}\n"
                    if collection.get("layout_config"):
                        layout_type = collection['layout_config'].get("type", "unknown")
                        result += f"- Layout Type: {layout_type}\n"
                    
                    # Tiles
                    result += f"\nTiles ({tiles.get('total_count', 0)}):\n"
                    if tiles.get("by_type"):
                        for tile_type, stats in tiles["by_type"].items():
                            result += f"- {tile_type}: {stats['count']} tiles (avg size: {stats['avg_width']:.0f}x{stats['avg_height']:.0f})\n"
                    
                    # Layouts
                    result += f"\nLayouts ({layouts.get('total_count', 0)}):\n"
                    if layouts.get("default_count"):
                        result += f"- Default layouts: {layouts['default_count']}\n"
                    
                    # Curation
                    result += f"\nCuration Rules ({curation.get('total_rules', 0)}):\n"
                    if curation.get("active_rules"):
                        result += f"- Active rules: {curation['active_rules']}\n"
                    
                    # Insights
                    result += f"\nInsights:\n"
                    if insights.get("most_common_tile_type"):
                        result += f"- Most common tile type: {insights['most_common_tile_type']}\n"
                    if insights.get("layout_diversity"):
                        result += f"- Layout diversity: {insights['layout_diversity']}\n"
                    if insights.get("curation_level"):
                        result += f"- Curation level: {insights['curation_level']}\n"
                    
                    return CallToolResult(
                        content=[TextContent(type="text", text=result)]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error fetching collection analysis: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to collections service: {str(e)}")]
            )
    
    async def _get_home_assistant_devices(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get devices from Home Assistant"""
        device_type = args.get("device_type", "all")
        
        try:
            async with httpx.AsyncClient() as client:
                if device_type == "all":
                    response = await client.get(
                        f"{self.homeassistant_bridge_url}/entities",
                        timeout=10.0
                    )
                else:
                    response = await client.get(
                        f"{self.homeassistant_bridge_url}/{device_type}",
                        timeout=10.0
                    )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if device_type == "all":
                        entities = data.get("entities", [])
                        if not entities:
                            return CallToolResult(
                                content=[TextContent(type="text", text="No devices found in Home Assistant.")]
                            )
                        
                        device_list = []
                        for entity in entities:
                            device_list.append(f"- {entity['entity_id']}: {entity['state']}")
                        
                        return CallToolResult(
                            content=[TextContent(type="text", text="Home Assistant Devices:\n" + "\n".join(device_list))]
                        )
                    else:
                        devices = data.get(device_type, [])
                        if not devices:
                            return CallToolResult(
                                content=[TextContent(type="text", text=f"No {device_type} found in Home Assistant.")]
                            )
                        
                        device_list = []
                        for device in devices:
                            device_list.append(f"- {device['entity_id']}: {device['state']}")
                        
                        return CallToolResult(
                            content=[TextContent(type="text", text=f"Home Assistant {device_type.title()}:\n" + "\n".join(device_list))]
                        )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error fetching devices: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to Home Assistant bridge: {str(e)}")]
            )
    
    async def _control_home_assistant_device(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Control a Home Assistant device"""
        entity_id = args.get("entity_id")
        action = args.get("action")
        data = args.get("data", {})
        
        if not entity_id or not action:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: entity_id and action are required")]
            )
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.homeassistant_bridge_url}/devices/control",
                    json={
                        "entity_id": entity_id,
                        "action": action,
                        "data": data
                    },
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return CallToolResult(
                        content=[TextContent(type="text", text=data.get("message", "Device controlled successfully"))]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error controlling device: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to Home Assistant bridge: {str(e)}")]
            )
    
    async def _get_home_assistant_automations(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get automations from Home Assistant"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.homeassistant_bridge_url}/automations",
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    automations = data.get("automations", [])
                    
                    if not automations:
                        return CallToolResult(
                            content=[TextContent(type="text", text="No automations found in Home Assistant.")]
                        )
                    
                    automation_list = []
                    for automation in automations:
                        automation_list.append(f"- {automation['entity_id']}: {automation['state']}")
                    
                    return CallToolResult(
                        content=[TextContent(type="text", text="Home Assistant Automations:\n" + "\n".join(automation_list))]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error fetching automations: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to Home Assistant bridge: {str(e)}")]
            )
    
    async def _trigger_home_assistant_automation(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Trigger a Home Assistant automation"""
        automation_id = args.get("automation_id")
        
        if not automation_id:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: automation_id is required")]
            )
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.homeassistant_bridge_url}/automations/trigger",
                    json={"automation_id": automation_id},
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return CallToolResult(
                        content=[TextContent(type="text", text=data.get("message", "Automation triggered successfully"))]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error triggering automation: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to Home Assistant bridge: {str(e)}")]
            )
    
    async def _get_home_assistant_scenes(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get scenes from Home Assistant"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.homeassistant_bridge_url}/scenes",
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    scenes = data.get("scenes", [])
                    
                    if not scenes:
                        return CallToolResult(
                            content=[TextContent(type="text", text="No scenes found in Home Assistant.")]
                        )
                    
                    scene_list = []
                    for scene in scenes:
                        scene_list.append(f"- {scene['entity_id']}: {scene['state']}")
                    
                    return CallToolResult(
                        content=[TextContent(type="text", text="Home Assistant Scenes:\n" + "\n".join(scene_list))]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error fetching scenes: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to Home Assistant bridge: {str(e)}")]
            )
    
    async def _activate_home_assistant_scene(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Activate a Home Assistant scene"""
        scene_id = args.get("scene_id")
        
        if not scene_id:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: scene_id is required")]
            )
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.homeassistant_bridge_url}/scenes/activate",
                    json={"scene_id": scene_id},
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return CallToolResult(
                        content=[TextContent(type="text", text=data.get("message", "Scene activated successfully"))]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error activating scene: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to Home Assistant bridge: {str(e)}")]
            )
    
    async def _get_n8n_workflows(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get workflows from N8N"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.n8n_bridge_url}/workflows",
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    workflows = data.get("workflows", [])
                    
                    if not workflows:
                        return CallToolResult(
                            content=[TextContent(type="text", text="No workflows found in N8N.")]
                        )
                    
                    workflow_list = []
                    for workflow in workflows:
                        status = "Active" if workflow.get("active", False) else "Inactive"
                        workflow_list.append(f"- {workflow['name']} (ID: {workflow['id']}) - {status}")
                        workflow_list.append(f"  Nodes: {workflow.get('nodes_count', 0)}, Connections: {workflow.get('connections_count', 0)}")
                    
                    return CallToolResult(
                        content=[TextContent(type="text", text="N8N Workflows:\n" + "\n".join(workflow_list))]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error fetching workflows: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to N8N bridge: {str(e)}")]
            )
    
    async def _create_n8n_workflow(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Create a workflow in N8N"""
        name = args.get("name")
        nodes = args.get("nodes")
        connections = args.get("connections")
        active = args.get("active", False)
        
        if not name or not nodes or not connections:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: name, nodes, and connections are required")]
            )
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.n8n_bridge_url}/workflows",
                    json={
                        "name": name,
                        "nodes": nodes,
                        "connections": connections,
                        "active": active
                    },
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return CallToolResult(
                        content=[TextContent(type="text", text=data.get("message", "Workflow created successfully"))]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error creating workflow: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to N8N bridge: {str(e)}")]
            )
    
    async def _execute_n8n_workflow(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Execute a workflow in N8N"""
        workflow_id = args.get("workflow_id")
        input_data = args.get("input_data")
        
        if not workflow_id:
            return CallToolResult(
                content=[TextContent(type="text", text="Error: workflow_id is required")]
            )
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.n8n_bridge_url}/workflows/{workflow_id}/execute",
                    json={"input_data": input_data},
                    timeout=30.0  # Longer timeout for workflow execution
                )
                
                if response.status_code == 200:
                    data = response.json()
                    execution_id = data.get("execution_id")
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Workflow executed successfully. Execution ID: {execution_id}")]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error executing workflow: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to N8N bridge: {str(e)}")]
            )
    
    async def _get_n8n_executions(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get executions from N8N"""
        workflow_id = args.get("workflow_id")
        limit = args.get("limit", 20)
        
        try:
            async with httpx.AsyncClient() as client:
                params = {"limit": limit}
                if workflow_id:
                    params["workflow_id"] = workflow_id
                
                response = await client.get(
                    f"{self.n8n_bridge_url}/executions",
                    params=params,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    executions = data.get("executions", [])
                    
                    if not executions:
                        return CallToolResult(
                            content=[TextContent(type="text", text="No executions found in N8N.")]
                        )
                    
                    execution_list = []
                    for execution in executions:
                        execution_list.append(f"- Execution {execution['id']}: {execution['status']}")
                        execution_list.append(f"  Workflow: {execution.get('workflow_id', 'Unknown')}")
                        execution_list.append(f"  Started: {execution.get('started_at', 'Unknown')}")
                        if execution.get('finished_at'):
                            execution_list.append(f"  Finished: {execution['finished_at']}")
                    
                    return CallToolResult(
                        content=[TextContent(type="text", text="N8N Executions:\n" + "\n".join(execution_list))]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error fetching executions: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to N8N bridge: {str(e)}")]
            )
    
    async def _get_n8n_nodes(self, args: Dict[str, Any], user_context) -> CallToolResult:
        """Get nodes from N8N"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.n8n_bridge_url}/nodes",
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    nodes = data.get("nodes", [])
                    
                    if not nodes:
                        return CallToolResult(
                            content=[TextContent(type="text", text="No nodes found in N8N.")]
                        )
                    
                    # Group nodes by category/type
                    node_categories = {}
                    for node in nodes:
                        node_type = node.get("name", "unknown")
                        if node_type not in node_categories:
                            node_categories[node_type] = []
                        node_categories[node_type].append(node.get("display_name", node_type))
                    
                    node_list = []
                    for category, node_names in node_categories.items():
                        node_list.append(f"- {category}: {', '.join(node_names[:5])}")  # Show first 5 nodes
                        if len(node_names) > 5:
                            node_list.append(f"  ... and {len(node_names) - 5} more")
                    
                    return CallToolResult(
                        content=[TextContent(type="text", text="N8N Available Nodes:\n" + "\n".join(node_list))]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error fetching nodes: {response.status_code}")]
                    )
                    
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error connecting to N8N bridge: {str(e)}")]
            )
    
    async def run(self):
        """Run the MCP server"""
        logger.info("Starting Zoe MCP Server...")
        
        # Run the server
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="zoe-mcp-server",
                    server_version="1.0.0",
                    capabilities=ServerCapabilities(
                        tools={}
                    ),
                ),
            )

async def main():
    """Main entry point"""
    server = ZoeMCPServer()
    await server.run()

if __name__ == "__main__":
    asyncio.run(main())
