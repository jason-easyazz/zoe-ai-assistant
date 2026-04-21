#!/usr/bin/env python3
"""
Simple HTTP-based MCP Server for Zoe
Provides tools via HTTP API for LLM integration
"""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe Simple MCP Server")

class ToolRequest(BaseModel):
    _auth_token: Optional[str] = "default"
    _session_id: Optional[str] = "default"

class AddToListRequest(ToolRequest):
    list_name: str
    task_text: str
    priority: str = "medium"

class CreatePersonRequest(ToolRequest):
    name: str
    relationship: Optional[str] = ""
    notes: Optional[str] = ""

class CreateCalendarEventRequest(ToolRequest):
    title: str
    start_date: str
    start_time: Optional[str] = ""
    description: Optional[str] = ""
    location: Optional[str] = ""

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "zoe-simple-mcp-server"}

@app.post("/tools/list")
async def list_tools(request: ToolRequest):
    """List available tools"""
    tools = [
        {
            "name": "add_to_list",
            "description": "Add an item to a user's todo list",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string", "description": "Name of the list"},
                    "task_text": {"type": "string", "description": "Task description"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"}
                },
                "required": ["list_name", "task_text"]
            }
        },
        {
            "name": "create_person",
            "description": "Create a new person in Zoe's memory system",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Person's name"},
                    "relationship": {"type": "string", "description": "Relationship to user"},
                    "notes": {"type": "string", "description": "Additional notes"}
                },
                "required": ["name"]
            }
        },
        {
            "name": "create_calendar_event",
            "description": "Create a new calendar event",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Event title"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "start_time": {"type": "string", "description": "Start time (HH:MM)"},
                    "description": {"type": "string", "description": "Event description"},
                    "location": {"type": "string", "description": "Event location"}
                },
                "required": ["title", "start_date"]
            }
        },
        {
            "name": "get_lists",
            "description": "Get all user's todo lists",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "get_calendar_events",
            "description": "Get calendar events for a date range",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"}
                }
            }
        }
    ]
    
    return {"tools": tools}

@app.post("/tools/add_to_list")
async def add_to_list(request: AddToListRequest):
    """Add item to list"""
    try:
        db_path = "/app/data/zoe.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Find or create list
        cursor.execute("SELECT id FROM lists WHERE name = ? AND user_id = ?", (request.list_name, "default"))
        list_row = cursor.fetchone()
        
        if not list_row:
            # Create new list
            cursor.execute("""
                INSERT INTO lists (user_id, name, category, description)
                VALUES (?, ?, ?, ?)
            """, ("default", request.list_name, "personal", f"List created by MCP server"))
            list_id = cursor.lastrowid
        else:
            list_id = list_row[0]
        
        # Add item to list
        cursor.execute("""
            INSERT INTO list_items (list_id, task_text, priority, completed)
            VALUES (?, ?, ?, ?)
        """, (list_id, request.task_text, request.priority, False))
        
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "message": f"Successfully added '{request.task_text}' to list '{request.list_name}'",
            "tool_name": "add_to_list"
        }
        
    except Exception as e:
        logger.error(f"Error adding to list: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/create_person")
async def create_person(request: CreatePersonRequest):
    """Create a new person"""
    try:
        db_path = "/app/data/zoe.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if person already exists
        cursor.execute("SELECT name FROM people WHERE user_id = ? AND name = ?", ("default", request.name))
        if cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=f"Person '{request.name}' already exists")
        
        # Create profile JSON
        profile = {
            "relationship": request.relationship,
            "notes": request.notes,
            "created_by": "mcp_server",
            "created_at": datetime.now().isoformat()
        }
        
        cursor.execute("""
            INSERT INTO people (user_id, name, profile, facts, important_dates, preferences)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("default", request.name, json.dumps(profile), "{}", "{}", "{}"))
        
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "message": f"Successfully created person: {request.name}",
            "tool_name": "create_person"
        }
        
    except Exception as e:
        logger.error(f"Error creating person: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/create_calendar_event")
async def create_calendar_event(request: CreateCalendarEventRequest):
    """Create a calendar event"""
    try:
        db_path = "/app/data/zoe.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO events (user_id, title, start_date, start_time, description, location, category)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("default", request.title, request.start_date, request.start_time, request.description, request.location, "personal"))
        
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "message": f"Successfully created calendar event: {request.title}",
            "tool_name": "create_calendar_event"
        }
        
    except Exception as e:
        logger.error(f"Error creating calendar event: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_lists")
async def get_lists(request: ToolRequest):
    """Get all lists"""
    try:
        db_path = "/app/data/zoe.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT l.name, l.category, COUNT(li.id) as item_count
            FROM lists l
            LEFT JOIN list_items li ON l.id = li.list_id
            WHERE l.user_id = ?
            GROUP BY l.id, l.name, l.category
        """, ("default",))
        
        lists = cursor.fetchall()
        conn.close()
        
        if not lists:
            return {
                "success": True,
                "message": "No lists found.",
                "data": [],
                "tool_name": "get_lists"
            }
        
        list_data = []
        for list_info in lists:
            list_data.append({
                "name": list_info[0],
                "category": list_info[1],
                "item_count": list_info[2]
            })
        
        return {
            "success": True,
            "message": f"Found {len(list_data)} lists",
            "data": list_data,
            "tool_name": "get_lists"
        }
        
    except Exception as e:
        logger.error(f"Error getting lists: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_calendar_events")
async def get_calendar_events(request: ToolRequest):
    """Get calendar events"""
    try:
        db_path = "/app/data/zoe.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT title, start_date, start_time, description, location 
            FROM events 
            WHERE user_id = ?
            ORDER BY start_date, start_time
        """, ("default",))
        
        events = cursor.fetchall()
        conn.close()
        
        if not events:
            return {
                "success": True,
                "message": "No calendar events found.",
                "data": [],
                "tool_name": "get_calendar_events"
            }
        
        event_data = []
        for event in events:
            event_data.append({
                "title": event[0],
                "start_date": event[1],
                "start_time": event[2],
                "description": event[3],
                "location": event[4]
            })
        
        return {
            "success": True,
            "message": f"Found {len(event_data)} calendar events",
            "data": event_data,
            "tool_name": "get_calendar_events"
        }
        
    except Exception as e:
        logger.error(f"Error getting calendar events: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)

