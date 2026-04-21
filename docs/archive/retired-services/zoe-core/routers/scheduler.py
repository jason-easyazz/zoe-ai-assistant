"""
Scheduler Router
=================

Phase 3: API endpoints for managing scheduled jobs and viewing usage.

Endpoints:
    GET  /api/scheduler/jobs         -- List scheduled jobs
    POST /api/scheduler/jobs         -- Create a scheduled job
    DELETE /api/scheduler/jobs/{id}  -- Delete a scheduled job
    GET  /api/scheduler/usage        -- View rate limit usage
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Dict, Any
from auth_integration import validate_session, AuthenticatedSession
from scheduler.cron_manager import cron_manager, CronManager
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


class CreateJobRequest(BaseModel):
    name: str
    cron_expression: str
    action: Dict[str, Any]
    integration: str = "general"
    job_type: str = "cron"
    timezone: str = "UTC"


@router.get("/jobs")
async def list_jobs(session: AuthenticatedSession = Depends(validate_session)):
    """List all scheduled jobs for the user."""
    jobs = CronManager.list_jobs(session.user_id)
    return {"jobs": jobs, "count": len(jobs)}


@router.post("/jobs")
async def create_job(
    request: CreateJobRequest,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Create a new scheduled job."""
    result = CronManager.create_job(
        user_id=session.user_id,
        name=request.name,
        cron_expression=request.cron_expression,
        action=request.action,
        integration=request.integration,
        job_type=request.job_type,
        timezone=request.timezone,
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: int,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Delete a scheduled job."""
    result = CronManager.delete_job(session.user_id, job_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
