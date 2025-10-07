"""
Cross-Agent Collaboration API Router
====================================

Provides endpoints for orchestrating multiple experts for complex tasks.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sys
sys.path.append('/app')
from cross_agent_collaboration import orchestrator, OrchestrationResult, ExpertTask, TaskStatus

router = APIRouter(prefix="/api/orchestration", tags=["orchestration"])

# Request/Response models
class OrchestrationRequest(BaseModel):
    request: str
    context: Optional[Dict[str, Any]] = None

class OrchestrationResponse(BaseModel):
    orchestration_id: str
    user_id: str
    original_request: str
    success: bool
    total_duration: float
    summary: str
    results: Dict[str, Any]
    errors: List[str]
    created_at: str
    completed_at: str

@router.post("/orchestrate")
async def orchestrate_complex_task(
    orchestration_request: OrchestrationRequest,
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Orchestrate a complex task across multiple experts"""
    try:
        result = await orchestrator.orchestrate_task(
            user_id=user_id,
            request=orchestration_request.request,
            context=orchestration_request.context
        )
        
        return {
            "orchestration_id": result.orchestration_id,
            "user_id": result.user_id,
            "original_request": result.original_request,
            "success": result.success,
            "total_duration": result.total_duration,
            "summary": result.final_result.get("summary", "No summary available"),
            "results": result.final_result,
            "errors": result.errors,
            "created_at": result.created_at,
            "completed_at": result.completed_at,
            "decomposed_tasks": [
                {
                    "id": task.id,
                    "expert_type": task.expert_type.value,
                    "description": task.task_description,
                    "status": task.status.value,
                    "result": task.result,
                    "error_message": task.error_message,
                    "duration": (
                        (datetime.fromisoformat(task.completed_at) - datetime.fromisoformat(task.started_at)).total_seconds()
                        if task.started_at and task.completed_at else 0
                    )
                }
                for task in result.decomposed_tasks
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status/{orchestration_id}")
async def get_orchestration_status(orchestration_id: str):
    """Get status of a specific orchestration"""
    try:
        result = await orchestrator.get_orchestration_status(orchestration_id)
        
        if not result:
            raise HTTPException(status_code=404, detail="Orchestration not found")
        
        return {
            "orchestration_id": result.orchestration_id,
            "user_id": result.user_id,
            "original_request": result.original_request,
            "success": result.success,
            "total_duration": result.total_duration,
            "summary": result.final_result.get("summary", "No summary available"),
            "results": result.final_result,
            "errors": result.errors,
            "created_at": result.created_at,
            "completed_at": result.completed_at,
            "decomposed_tasks": [
                {
                    "id": task.id,
                    "expert_type": task.expert_type.value,
                    "description": task.task_description,
                    "status": task.status.value,
                    "result": task.result,
                    "error_message": task.error_message,
                    "duration": (
                        (datetime.fromisoformat(task.completed_at) - datetime.fromisoformat(task.started_at)).total_seconds()
                        if task.started_at and task.completed_at else 0
                    )
                }
                for task in result.decomposed_tasks
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/history")
async def get_orchestration_history(
    user_id: str = Query(..., description="User ID for privacy isolation"),
    limit: int = Query(10, description="Number of orchestrations to return")
):
    """Get orchestration history for a user"""
    try:
        results = await orchestrator.get_user_orchestrations(user_id, limit)
        
        return {
            "orchestrations": [
                {
                    "orchestration_id": result.orchestration_id,
                    "user_id": result.user_id,
                    "original_request": result.original_request,
                    "success": result.success,
                    "total_duration": result.total_duration,
                    "summary": result.final_result.get("summary", "No summary available"),
                    "created_at": result.created_at,
                    "completed_at": result.completed_at,
                    "task_count": len(result.decomposed_tasks),
                    "successful_tasks": len([t for t in result.decomposed_tasks if t.status == TaskStatus.COMPLETED]),
                    "failed_tasks": len([t for t in result.decomposed_tasks if t.status in [TaskStatus.FAILED, TaskStatus.TIMEOUT]])
                }
                for result in results
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/cancel/{orchestration_id}")
async def cancel_orchestration(orchestration_id: str):
    """Cancel a running orchestration"""
    try:
        success = await orchestrator.cancel_orchestration(orchestration_id)
        
        if success:
            return {"message": "Orchestration cancelled successfully"}
        else:
            raise HTTPException(status_code=404, detail="Orchestration not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/experts")
async def get_available_experts():
    """Get list of available experts and their capabilities"""
    try:
        experts = {
            "calendar": {
                "name": "Calendar Expert",
                "description": "Schedule events, manage calendar, handle appointments",
                "capabilities": ["create_event", "list_events", "update_event", "delete_event"],
                "endpoint": "/api/calendar"
            },
            "lists": {
                "name": "Lists Expert",
                "description": "Manage to-do lists, shopping lists, reminders",
                "capabilities": ["create_list", "add_item", "update_item", "delete_item"],
                "endpoint": "/api/lists"
            },
            "memory": {
                "name": "Memory Expert",
                "description": "Store and retrieve information, manage knowledge",
                "capabilities": ["store_fact", "search_memories", "update_memory", "delete_memory"],
                "endpoint": "/api/memories"
            },
            "planning": {
                "name": "Planning Expert",
                "description": "Create plans, roadmaps, project management",
                "capabilities": ["create_plan", "update_plan", "track_progress", "generate_roadmap"],
                "endpoint": "/api/developer/tasks"
            },
            "development": {
                "name": "Development Expert",
                "description": "Code generation, debugging, technical tasks",
                "capabilities": ["generate_code", "debug_code", "review_code", "explain_code"],
                "endpoint": "/api/developer"
            },
            "weather": {
                "name": "Weather Expert",
                "description": "Weather information and forecasts",
                "capabilities": ["current_weather", "forecast", "weather_alerts"],
                "endpoint": "/api/weather"
            },
            "homeassistant": {
                "name": "Home Assistant Expert",
                "description": "Smart home control and automation",
                "capabilities": ["control_device", "get_status", "automation", "scenes"],
                "endpoint": "/api/homeassistant"
            }
        }
        
        return {"experts": experts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats")
async def get_orchestration_stats(
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Get orchestration statistics for a user"""
    try:
        results = await orchestrator.get_user_orchestrations(user_id, 100)  # Get more for stats
        
        if not results:
            return {
                "total_orchestrations": 0,
                "successful_orchestrations": 0,
                "failed_orchestrations": 0,
                "average_duration": 0,
                "most_used_experts": {},
                "common_errors": []
            }
        
        total = len(results)
        successful = len([r for r in results if r.success])
        failed = total - successful
        
        # Calculate average duration
        durations = [r.total_duration for r in results if r.total_duration > 0]
        avg_duration = sum(durations) / len(durations) if durations else 0
        
        # Count expert usage
        expert_usage = {}
        for result in results:
            for task in result.decomposed_tasks:
                expert = task.expert_type.value
                expert_usage[expert] = expert_usage.get(expert, 0) + 1
        
        # Get common errors
        all_errors = []
        for result in results:
            all_errors.extend(result.errors)
        
        error_counts = {}
        for error in all_errors:
            error_counts[error] = error_counts.get(error, 0) + 1
        
        common_errors = sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        
        return {
            "total_orchestrations": total,
            "successful_orchestrations": successful,
            "failed_orchestrations": failed,
            "success_rate": (successful / total * 100) if total > 0 else 0,
            "average_duration": avg_duration,
            "most_used_experts": expert_usage,
            "common_errors": [{"error": error, "count": count} for error, count in common_errors]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



