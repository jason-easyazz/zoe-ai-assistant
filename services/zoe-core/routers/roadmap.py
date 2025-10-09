from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import sqlite3
import json
from datetime import datetime

router = APIRouter(prefix="/api/developer/roadmap")

class PhaseCreate(BaseModel):
    phase_name: str
    phase_number: int
    description: str
    success_criteria: List[str]
    start_date: str
    target_end_date: str

class PhaseUpdate(BaseModel):
    phase_name: Optional[str] = None
    description: Optional[str] = None
    success_criteria: Optional[List[str]] = None
    start_date: Optional[str] = None
    target_end_date: Optional[str] = None
    status: Optional[str] = None

class TaskPhaseAssignment(BaseModel):
    task_id: str
    phase_id: str
    phase_order: int

@router.get("/phases")
async def get_phases():
    """Get all roadmap phases"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, phase_name, phase_number, description, success_criteria, 
                   start_date, target_end_date, status
            FROM roadmap_phases 
            ORDER BY phase_number
        """)
        
        phases = []
        for row in cursor.fetchall():
            phase = dict(row)
            phase['success_criteria'] = json.loads(phase['success_criteria'])
            phases.append(phase)
        
        conn.close()
        return {"phases": phases}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching phases: {str(e)}")

@router.get("/phases/{phase_id}")
async def get_phase(phase_id: str):
    """Get specific phase with tasks"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get phase details
        cursor.execute("""
            SELECT id, phase_name, phase_number, description, success_criteria, 
                   start_date, target_end_date, status
            FROM roadmap_phases 
            WHERE id = ?
        """, (phase_id,))
        
        phase_row = cursor.fetchone()
        if not phase_row:
            raise HTTPException(status_code=404, detail="Phase not found")
        
        phase = dict(phase_row)
        phase['success_criteria'] = json.loads(phase['success_criteria'])
        
        # Get tasks in this phase
        cursor.execute("""
            SELECT id, title, objective, priority, status, assigned_to, 
                   phase_order, roadmap_priority, zoe_vs_411
            FROM dynamic_tasks 
            WHERE phase_id = ?
            ORDER BY phase_order
        """, (phase_id,))
        
        tasks = [dict(row) for row in cursor.fetchall()]
        
        phase['tasks'] = tasks
        conn.close()
        return phase
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching phase: {str(e)}")

@router.post("/phases")
async def create_phase(phase: PhaseCreate):
    """Create new roadmap phase"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        phase_id = f"phase_{phase.phase_number}"
        
        cursor.execute("""
            INSERT INTO roadmap_phases 
            (id, phase_name, phase_number, description, success_criteria, start_date, target_end_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            phase_id, phase.phase_name, phase.phase_number, phase.description,
            json.dumps(phase.success_criteria), phase.start_date, phase.target_end_date
        ))
        
        conn.commit()
        conn.close()
        return {"message": "Phase created successfully", "phase_id": phase_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating phase: {str(e)}")

@router.put("/phases/{phase_id}")
async def update_phase(phase_id: str, phase_update: PhaseUpdate):
    """Update roadmap phase"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        # Build dynamic update query
        update_fields = []
        values = []
        
        if phase_update.phase_name is not None:
            update_fields.append("phase_name = ?")
            values.append(phase_update.phase_name)
        if phase_update.description is not None:
            update_fields.append("description = ?")
            values.append(phase_update.description)
        if phase_update.success_criteria is not None:
            update_fields.append("success_criteria = ?")
            values.append(json.dumps(phase_update.success_criteria))
        if phase_update.start_date is not None:
            update_fields.append("start_date = ?")
            values.append(phase_update.start_date)
        if phase_update.target_end_date is not None:
            update_fields.append("target_end_date = ?")
            values.append(phase_update.target_end_date)
        if phase_update.status is not None:
            update_fields.append("status = ?")
            values.append(phase_update.status)
        
        if not update_fields:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        values.append(phase_id)
        
        query = f"UPDATE roadmap_phases SET {', '.join(update_fields)} WHERE id = ?"
        cursor.execute(query, values)
        
        conn.commit()
        conn.close()
        return {"message": "Phase updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating phase: {str(e)}")

@router.get("/status")
async def get_roadmap_status():
    """Get overall roadmap progress"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get phase status
        cursor.execute("""
            SELECT rp.id, rp.phase_name, rp.phase_number, rp.status, 
                   COUNT(dt.id) as total_tasks,
                   COUNT(CASE WHEN dt.status = 'completed' THEN 1 END) as completed_tasks
            FROM roadmap_phases rp
            LEFT JOIN dynamic_tasks dt ON rp.id = dt.phase_id
            GROUP BY rp.id, rp.phase_name, rp.phase_number, rp.status
            ORDER BY rp.phase_number
        """)
        
        phases = []
        for row in cursor.fetchall():
            phase = dict(row)
            if phase['total_tasks'] > 0:
                phase['completion_percentage'] = (phase['completed_tasks'] / phase['total_tasks']) * 100
            else:
                phase['completion_percentage'] = 0
            phases.append(phase)
        
        # Get overall stats
        cursor.execute("""
            SELECT 
                COUNT(*) as total_tasks,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed_tasks,
                COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending_tasks,
                COUNT(CASE WHEN status = 'in_progress' THEN 1 END) as in_progress_tasks
            FROM dynamic_tasks
        """)
        
        stats = dict(cursor.fetchone())
        if stats['total_tasks'] > 0:
            stats['overall_completion'] = (stats['completed_tasks'] / stats['total_tasks']) * 100
        else:
            stats['overall_completion'] = 0
        
        conn.close()
        return {
            "phases": phases,
            "overall_stats": stats,
            "last_updated": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching roadmap status: {str(e)}")

@router.post("/assign-task")
async def assign_task_to_phase(assignment: TaskPhaseAssignment):
    """Assign task to phase"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        # Update task with phase assignment
        cursor.execute("""
            UPDATE dynamic_tasks 
            SET phase_id = ?, phase_order = ?
            WHERE id = ?
        """, (assignment.phase_id, assignment.phase_order, assignment.task_id))
        
        # Add to task_phase_assignments table
        cursor.execute("""
            INSERT OR REPLACE INTO task_phase_assignments 
            (task_id, phase_id, phase_order)
            VALUES (?, ?, ?)
        """, (assignment.task_id, assignment.phase_id, assignment.phase_order))
        
        conn.commit()
        conn.close()
        return {"message": "Task assigned to phase successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error assigning task: {str(e)}")
