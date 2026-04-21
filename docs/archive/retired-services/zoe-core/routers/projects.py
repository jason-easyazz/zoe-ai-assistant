from auth_integration import validate_session, AuthenticatedSession
"""
Project Lists with Stages Management
Multi-stage workflow support for complex projects
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import os

router = APIRouter(prefix="/api/projects", tags=["projects"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# ==================================================================
# PYDANTIC MODELS
# ==================================================================

class CreateProjectRequest(BaseModel):
    name: str
    stages: List[str]

class AddItemRequest(BaseModel):
    task_text: str
    parent_id: Optional[int] = None
    priority: str = "medium"
    reminder_time: Optional[str] = None
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    repeat_pattern: Optional[str] = None

class UpdateItemRequest(BaseModel):
    task_text: Optional[str] = None
    completed: Optional[bool] = None
    priority: Optional[str] = None
    stage_id: Optional[int] = None
    reminder_time: Optional[str] = None
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    repeat_pattern: Optional[str] = None

class MoveItemRequest(BaseModel):
    target_stage_id: int

class NavigateRequest(BaseModel):
    stage_id: int

class UpdateProjectRequest(BaseModel):
    name: str

# ==================================================================
# DATABASE HELPERS
# ==================================================================

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

# ==================================================================
# AUTO-ADVANCE LOGIC
# ==================================================================

def check_and_advance_stage(project_id: int, conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Check if current stage is complete and auto-advance if true.
    Returns dict with 'advanced': bool, 'new_stage_id': int or None, 'project_complete': bool
    """
    cursor = conn.cursor()
    
    # Get current stage
    cursor.execute("""
        SELECT id, stage_order 
        FROM project_stages 
        WHERE project_id = ? AND id = (
            SELECT current_stage_id FROM project_lists WHERE id = ?
        )
    """, (project_id, project_id))
    current_stage = cursor.fetchone()
    
    if not current_stage:
        return {'advanced': False, 'new_stage_id': None, 'project_complete': False}
    
    stage_id, stage_order = current_stage
    
    # Check if all items in current stage are completed
    cursor.execute("""
        SELECT COUNT(*) FROM project_items 
        WHERE stage_id = ? AND completed = FALSE
    """, (stage_id,))
    incomplete_count = cursor.fetchone()[0]
    
    if incomplete_count > 0:
        return {'advanced': False, 'new_stage_id': None, 'project_complete': False}
    
    # Check if stage has any items at all
    cursor.execute("""
        SELECT COUNT(*) FROM project_items WHERE stage_id = ?
    """, (stage_id,))
    total_items = cursor.fetchone()[0]
    
    if total_items == 0:
        # Empty stage, don't auto-advance
        return {'advanced': False, 'new_stage_id': None, 'project_complete': False}
    
    # Mark current stage as completed
    cursor.execute("""
        UPDATE project_stages 
        SET completed = TRUE, completed_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (stage_id,))
    
    # Find next stage
    cursor.execute("""
        SELECT id FROM project_stages 
        WHERE project_id = ? AND stage_order > ?
        ORDER BY stage_order ASC LIMIT 1
    """, (project_id, stage_order))
    next_stage = cursor.fetchone()
    
    if next_stage:
        # Advance to next stage
        cursor.execute("""
            UPDATE project_lists 
            SET current_stage_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (next_stage[0], project_id))
        conn.commit()
        return {'advanced': True, 'new_stage_id': next_stage[0], 'project_complete': False}
    
    # No next stage - project complete!
    return {'advanced': False, 'new_stage_id': None, 'project_complete': True}

# ==================================================================
# API ENDPOINTS
# ==================================================================

@router.post("")
async def create_project(
    request: CreateProjectRequest,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Create a new project with stages"""
    user_id = session.user_id
    
    if not request.stages or len(request.stages) == 0:
        raise HTTPException(status_code=400, detail="At least one stage is required")
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Create project
        cursor.execute("""
            INSERT INTO project_lists (name, user_id)
            VALUES (?, ?)
        """, (request.name, user_id))
        project_id = cursor.lastrowid
        
        # Create stages
        stage_ids = []
        for idx, stage_name in enumerate(request.stages):
            cursor.execute("""
                INSERT INTO project_stages (project_id, name, stage_order)
                VALUES (?, ?, ?)
            """, (project_id, stage_name, idx))
            stage_ids.append(cursor.lastrowid)
        
        # Set first stage as current
        cursor.execute("""
            UPDATE project_lists SET current_stage_id = ?
            WHERE id = ?
        """, (stage_ids[0], project_id))
        
        conn.commit()
        
        # Return created project
        return {
            "id": project_id,
            "name": request.name,
            "current_stage_id": stage_ids[0],
            "stages": [
                {"id": stage_ids[i], "name": name, "stage_order": i, "completed": False}
                for i, name in enumerate(request.stages)
            ]
        }
    finally:
        conn.close()

@router.get("")
async def get_projects(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get all projects for the current user"""
    user_id = session.user_id
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Get projects
        cursor.execute("""
            SELECT id, name, current_stage_id, created_at, updated_at
            FROM project_lists
            WHERE user_id = ?
            ORDER BY updated_at DESC
        """, (user_id,))
        
        projects = []
        for row in cursor.fetchall():
            project_id, name, current_stage_id, created_at, updated_at = row
            
            # Get current stage info
            cursor.execute("""
                SELECT id, name, stage_order, completed
                FROM project_stages
                WHERE id = ?
            """, (current_stage_id,))
            current_stage_row = cursor.fetchone()
            current_stage = None
            if current_stage_row:
                current_stage = {
                    "id": current_stage_row[0],
                    "name": current_stage_row[1],
                    "stage_order": current_stage_row[2],
                    "completed": bool(current_stage_row[3])
                }
            
            # Get all stages
            cursor.execute("""
                SELECT id, name, stage_order, completed, completed_at
                FROM project_stages
                WHERE project_id = ?
                ORDER BY stage_order ASC
            """, (project_id,))
            stages = [
                {
                    "id": s[0],
                    "name": s[1],
                    "stage_order": s[2],
                    "completed": bool(s[3]),
                    "completed_at": s[4]
                }
                for s in cursor.fetchall()
            ]
            
            # Get items for current stage
            items = []
            if current_stage_id:
                cursor.execute("""
                    SELECT id, task_text, completed, priority, parent_id,
                           reminder_time, due_date, due_time, repeat_pattern,
                           created_at, completed_at
                    FROM project_items
                    WHERE stage_id = ?
                    ORDER BY completed ASC, created_at DESC
                """, (current_stage_id,))
                items = [
                    {
                        "id": i[0],
                        "text": i[1],
                        "completed": bool(i[2]),
                        "priority": i[3],
                        "parent_id": i[4],
                        "reminder_time": i[5],
                        "due_date": i[6],
                        "due_time": i[7],
                        "repeat_pattern": i[8],
                        "created_at": i[9],
                        "completed_at": i[10],
                        "sub_items": []
                    }
                    for i in cursor.fetchall()
                ]
            
            projects.append({
                "id": project_id,
                "name": name,
                "current_stage": current_stage,
                "stages": stages,
                "items": items,
                "created_at": created_at,
                "updated_at": updated_at
            })
        
        return {"projects": projects}
    finally:
        conn.close()

@router.get("/{project_id}")
async def get_project_details(
    project_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get detailed project information with all stages and items"""
    user_id = session.user_id
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Verify project ownership
        cursor.execute("""
            SELECT name, current_stage_id, created_at, updated_at
            FROM project_lists
            WHERE id = ? AND user_id = ?
        """, (project_id, user_id))
        project_row = cursor.fetchone()
        
        if not project_row:
            raise HTTPException(status_code=404, detail="Project not found")
        
        name, current_stage_id, created_at, updated_at = project_row
        
        # Get all stages with items
        cursor.execute("""
            SELECT id, name, stage_order, completed, completed_at
            FROM project_stages
            WHERE project_id = ?
            ORDER BY stage_order ASC
        """, (project_id,))
        
        stages = []
        for stage_row in cursor.fetchall():
            stage_id, stage_name, stage_order, completed, completed_at = stage_row
            
            # Get items for this stage
            cursor.execute("""
                SELECT id, task_text, completed, priority, parent_id,
                       reminder_time, due_date, due_time, repeat_pattern,
                       created_at, completed_at
                FROM project_items
                WHERE stage_id = ?
                ORDER BY completed ASC, created_at DESC
            """, (stage_id,))
            
            items = [
                {
                    "id": i[0],
                    "text": i[1],
                    "completed": bool(i[2]),
                    "priority": i[3],
                    "parent_id": i[4],
                    "reminder_time": i[5],
                    "due_date": i[6],
                    "due_time": i[7],
                    "repeat_pattern": i[8],
                    "created_at": i[9],
                    "completed_at": i[10],
                    "sub_items": []
                }
                for i in cursor.fetchall()
            ]
            
            stages.append({
                "id": stage_id,
                "name": stage_name,
                "stage_order": stage_order,
                "completed": bool(completed),
                "completed_at": completed_at,
                "items": items
            })
        
        return {
            "id": project_id,
            "name": name,
            "current_stage_id": current_stage_id,
            "stages": stages,
            "created_at": created_at,
            "updated_at": updated_at
        }
    finally:
        conn.close()

@router.post("/{project_id}/stages/{stage_id}/items")
async def add_item_to_stage(
    project_id: int,
    stage_id: int,
    request: AddItemRequest,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Add an item to a specific stage"""
    user_id = session.user_id
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Verify project ownership and stage belongs to project
        cursor.execute("""
            SELECT pl.current_stage_id, ps.stage_order
            FROM project_lists pl
            JOIN project_stages ps ON ps.id = ?
            WHERE pl.id = ? AND pl.user_id = ? AND ps.project_id = ?
        """, (stage_id, project_id, user_id, project_id))
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Project or stage not found")
        
        current_stage_id, target_stage_order = result
        
        # Add item
        cursor.execute("""
            INSERT INTO project_items 
            (stage_id, task_text, priority, parent_id, reminder_time, due_date, due_time, repeat_pattern)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (stage_id, request.task_text, request.priority, request.parent_id,
              request.reminder_time, request.due_date, request.due_time, request.repeat_pattern))
        item_id = cursor.lastrowid
        
        # If adding to a previous stage, set it as current
        cursor.execute("""
            SELECT stage_order FROM project_stages WHERE id = ?
        """, (current_stage_id,))
        current_stage_order = cursor.fetchone()[0]
        
        if target_stage_order < current_stage_order:
            cursor.execute("""
                UPDATE project_lists 
                SET current_stage_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (stage_id, project_id))
            # Mark stages after target as incomplete
            cursor.execute("""
                UPDATE project_stages 
                SET completed = FALSE, completed_at = NULL
                WHERE project_id = ? AND stage_order >= ?
            """, (project_id, target_stage_order))
        
        conn.commit()
        
        # Check for auto-advance
        advance_result = check_and_advance_stage(project_id, conn)
        
        return {
            "id": item_id,
            "text": request.task_text,
            "completed": False,
            "priority": request.priority,
            "parent_id": request.parent_id,
            "auto_advanced": advance_result['advanced'],
            "new_stage_id": advance_result['new_stage_id'],
            "project_complete": advance_result['project_complete']
        }
    finally:
        conn.close()

@router.put("/{project_id}/items/{item_id}")
async def update_item(
    project_id: int,
    item_id: int,
    request: UpdateItemRequest,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update an item"""
    user_id = session.user_id
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Verify ownership
        cursor.execute("""
            SELECT pi.stage_id, ps.project_id
            FROM project_items pi
            JOIN project_stages ps ON ps.id = pi.stage_id
            JOIN project_lists pl ON pl.id = ps.project_id
            WHERE pi.id = ? AND pl.user_id = ?
        """, (item_id, user_id))
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Item not found")
        
        current_stage_id, item_project_id = result
        
        if item_project_id != project_id:
            raise HTTPException(status_code=400, detail="Item does not belong to this project")
        
        # Build update query
        updates = []
        params = []
        
        if request.task_text is not None:
            updates.append("task_text = ?")
            params.append(request.task_text)
        
        if request.completed is not None:
            updates.append("completed = ?")
            params.append(request.completed)
            if request.completed:
                updates.append("completed_at = CURRENT_TIMESTAMP")
            else:
                updates.append("completed_at = NULL")
        
        if request.priority is not None:
            updates.append("priority = ?")
            params.append(request.priority)
        
        if request.reminder_time is not None:
            updates.append("reminder_time = ?")
            params.append(request.reminder_time)
        
        if request.due_date is not None:
            updates.append("due_date = ?")
            params.append(request.due_date)
        
        if request.due_time is not None:
            updates.append("due_time = ?")
            params.append(request.due_time)
        
        if request.repeat_pattern is not None:
            updates.append("repeat_pattern = ?")
            params.append(request.repeat_pattern)
        
        if request.stage_id is not None:
            # Moving to different stage
            updates.append("stage_id = ?")
            params.append(request.stage_id)
            
            # Check if moving to previous stage
            cursor.execute("""
                SELECT stage_order FROM project_stages WHERE id = ?
            """, (request.stage_id,))
            target_order = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT current_stage_id FROM project_lists WHERE id = ?
            """, (project_id,))
            current_stage_id_project = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT stage_order FROM project_stages WHERE id = ?
            """, (current_stage_id_project,))
            current_order = cursor.fetchone()[0]
            
            if target_order < current_order:
                # Moving to previous stage, set it as current
                cursor.execute("""
                    UPDATE project_lists 
                    SET current_stage_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (request.stage_id, project_id))
                # Mark stages after target as incomplete
                cursor.execute("""
                    UPDATE project_stages 
                    SET completed = FALSE, completed_at = NULL
                    WHERE project_id = ? AND stage_order >= ?
                """, (project_id, target_order))
        
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(item_id)
        
        if updates:
            query = f"UPDATE project_items SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(query, params)
            conn.commit()
        
        # Check for auto-advance
        advance_result = check_and_advance_stage(project_id, conn)
        
        return {
            "success": True,
            "auto_advanced": advance_result['advanced'],
            "new_stage_id": advance_result['new_stage_id'],
            "project_complete": advance_result['project_complete']
        }
    finally:
        conn.close()

@router.patch("/{project_id}/items/{item_id}/move")
async def move_item_to_stage(
    project_id: int,
    item_id: int,
    request: MoveItemRequest,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Move an item to a different stage"""
    user_id = session.user_id
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Verify ownership
        cursor.execute("""
            SELECT pi.stage_id, ps.project_id
            FROM project_items pi
            JOIN project_stages ps ON ps.id = pi.stage_id
            JOIN project_lists pl ON pl.id = ps.project_id
            WHERE pi.id = ? AND pl.user_id = ?
        """, (item_id, user_id))
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Item not found")
        
        current_stage_id, item_project_id = result
        
        if item_project_id != project_id:
            raise HTTPException(status_code=400, detail="Item does not belong to this project")
        
        # Verify target stage exists
        cursor.execute("""
            SELECT stage_order FROM project_stages 
            WHERE id = ? AND project_id = ?
        """, (request.target_stage_id, project_id))
        target_result = cursor.fetchone()
        
        if not target_result:
            raise HTTPException(status_code=404, detail="Target stage not found")
        
        target_order = target_result[0]
        
        # Move item
        cursor.execute("""
            UPDATE project_items 
            SET stage_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (request.target_stage_id, item_id))
        
        # Check if moving to previous stage
        cursor.execute("""
            SELECT current_stage_id FROM project_lists WHERE id = ?
        """, (project_id,))
        current_stage_id_project = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT stage_order FROM project_stages WHERE id = ?
        """, (current_stage_id_project,))
        current_order = cursor.fetchone()[0]
        
        if target_order < current_order:
            # Set target stage as current
            cursor.execute("""
                UPDATE project_lists 
                SET current_stage_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (request.target_stage_id, project_id))
            # Mark stages after target as incomplete
            cursor.execute("""
                UPDATE project_stages 
                SET completed = FALSE, completed_at = NULL
                WHERE project_id = ? AND stage_order >= ?
            """, (project_id, target_order))
        
        conn.commit()
        
        # Check for auto-advance
        advance_result = check_and_advance_stage(project_id, conn)
        
        return {
            "success": True,
            "auto_advanced": advance_result['advanced'],
            "new_stage_id": advance_result['new_stage_id'],
            "project_complete": advance_result['project_complete']
        }
    finally:
        conn.close()

@router.delete("/{project_id}/items/{item_id}")
async def delete_item(
    project_id: int,
    item_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Delete an item"""
    user_id = session.user_id
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Verify ownership
        cursor.execute("""
            SELECT ps.project_id
            FROM project_items pi
            JOIN project_stages ps ON ps.id = pi.stage_id
            JOIN project_lists pl ON pl.id = ps.project_id
            WHERE pi.id = ? AND pl.user_id = ?
        """, (item_id, user_id))
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Item not found")
        
        item_project_id = result[0]
        
        if item_project_id != project_id:
            raise HTTPException(status_code=400, detail="Item does not belong to this project")
        
        # Delete item (cascade will handle children)
        cursor.execute("DELETE FROM project_items WHERE id = ?", (item_id,))
        conn.commit()
        
        return {"success": True}
    finally:
        conn.close()

@router.patch("/{project_id}")
async def update_project(
    project_id: int,
    request: UpdateProjectRequest,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update project name"""
    user_id = session.user_id
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Verify ownership
        cursor.execute("""
            SELECT id FROM project_lists WHERE id = ? AND user_id = ?
        """, (project_id, user_id))
        
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Project not found")
        
        # Update name
        cursor.execute("""
            UPDATE project_lists 
            SET name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (request.name, project_id))
        conn.commit()
        
        return {"success": True, "name": request.name}
    finally:
        conn.close()

@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Delete a project"""
    user_id = session.user_id
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Verify ownership
        cursor.execute("""
            SELECT id FROM project_lists WHERE id = ? AND user_id = ?
        """, (project_id, user_id))
        
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Project not found")
        
        # Delete project (cascade will handle stages and items)
        cursor.execute("DELETE FROM project_lists WHERE id = ?", (project_id,))
        conn.commit()
        
        return {"success": True}
    finally:
        conn.close()

@router.patch("/{project_id}/navigate")
async def navigate_to_stage(
    project_id: int,
    request: NavigateRequest,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Navigate to a specific stage"""
    user_id = session.user_id
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Verify ownership and stage belongs to project
        cursor.execute("""
            SELECT pl.id
            FROM project_lists pl
            JOIN project_stages ps ON ps.id = ? AND ps.project_id = pl.id
            WHERE pl.id = ? AND pl.user_id = ?
        """, (request.stage_id, project_id, user_id))
        
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Project or stage not found")
        
        # Update current stage
        cursor.execute("""
            UPDATE project_lists 
            SET current_stage_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (request.stage_id, project_id))
        conn.commit()
        
        # Get items for new stage
        cursor.execute("""
            SELECT id, task_text, completed, priority, parent_id,
                   reminder_time, due_date, due_time, repeat_pattern,
                   created_at, completed_at
            FROM project_items
            WHERE stage_id = ?
            ORDER BY completed ASC, created_at DESC
        """, (request.stage_id,))
        
        items = [
            {
                "id": i[0],
                "text": i[1],
                "completed": bool(i[2]),
                "priority": i[3],
                "parent_id": i[4],
                "reminder_time": i[5],
                "due_date": i[6],
                "due_time": i[7],
                "repeat_pattern": i[8],
                "created_at": i[9],
                "completed_at": i[10],
                "sub_items": []
            }
            for i in cursor.fetchall()
        ]
        
        return {
            "success": True,
            "current_stage_id": request.stage_id,
            "items": items
        }
    finally:
        conn.close()

