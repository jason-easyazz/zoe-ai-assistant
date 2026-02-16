"""
Self-Creation Router
=====================

Phase 8: API endpoints for skill/widget self-creation.

Endpoints:
    GET  /api/self-creation/patterns      -- List detected patterns
    POST /api/self-creation/detect        -- Run pattern detection
    GET  /api/self-creation/pending       -- List pending skills
    POST /api/self-creation/approve/{name} -- Approve a pending skill
    POST /api/self-creation/reject/{name}  -- Reject a pending skill
    POST /api/self-creation/widget        -- Generate a widget
    GET  /api/self-creation/widgets       -- List user widgets
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from auth_integration import validate_session, AuthenticatedSession
from self_creation.pattern_detector import detect_patterns, get_unproposed_patterns
from self_creation.skill_generator import (
    generate_skill_from_pattern, approve_pending_skill,
    reject_pending_skill, list_pending_skills,
)
from self_creation.widget_generator import generate_widget, list_user_widgets
from skills.registry import skills_registry
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/self-creation", tags=["self-creation"])


class WidgetRequest(BaseModel):
    name: str
    title: str
    description: str = ""
    allowed_endpoints: List[str]
    custom_css: str = ""
    custom_js: str = ""


@router.get("/patterns")
async def list_patterns(session: AuthenticatedSession = Depends(validate_session)):
    """List detected patterns that could become skills."""
    patterns = get_unproposed_patterns(session.user_id)
    return {"patterns": patterns, "count": len(patterns)}


@router.post("/detect")
async def run_detection(session: AuthenticatedSession = Depends(validate_session)):
    """Run pattern detection on recent conversation history."""
    new_patterns = detect_patterns(session.user_id)
    return {
        "new_patterns": new_patterns,
        "count": len(new_patterns),
        "message": f"Found {len(new_patterns)} new patterns" if new_patterns else "No new patterns detected",
    }


@router.get("/pending")
async def get_pending(session: AuthenticatedSession = Depends(validate_session)):
    """List pending skills awaiting approval."""
    pending = list_pending_skills()
    return {"pending": pending, "count": len(pending)}


@router.post("/approve/{name}")
async def approve_skill(
    name: str,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Approve a pending skill, moving it to active user skills."""
    result = approve_pending_skill(name)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])

    # Reload skills registry to pick up the new skill
    skills_registry.load()

    return {**result, "message": f"Skill '{name}' approved and activated. Skills registry reloaded."}


@router.post("/reject/{name}")
async def reject_skill(
    name: str,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Reject and remove a pending skill."""
    result = reject_pending_skill(name)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/widget")
async def create_widget(
    request: WidgetRequest,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Generate a new widget from a definition."""
    result = generate_widget(
        name=request.name,
        title=request.title,
        description=request.description,
        allowed_endpoints=request.allowed_endpoints,
        custom_css=request.custom_css,
        custom_js=request.custom_js,
    )
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.get("/widgets")
async def list_widgets(session: AuthenticatedSession = Depends(validate_session)):
    """List all user-created widgets."""
    widgets = list_user_widgets()
    return {"widgets": widgets, "count": len(widgets)}
