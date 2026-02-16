"""
Learnings Router
=================

Phase 2: API endpoints for managing self-improvement learnings.

Endpoints:
    GET  /api/learnings              -- List learnings for user
    GET  /api/learnings/pending      -- List pending learnings needing review
    POST /api/learnings/{id}/confirm -- Confirm a pending learning
    POST /api/learnings/{id}/dismiss -- Dismiss a pending learning
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from auth_integration import validate_session, AuthenticatedSession
from learning.learnings_store import (
    get_relevant_learnings, get_pending_count,
    confirm_learning, dismiss_learning,
)
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learnings", tags=["learnings"])


@router.get("")
async def list_learnings(
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(20, le=100),
    session: AuthenticatedSession = Depends(validate_session),
):
    """List learnings for the authenticated user."""
    learnings = get_relevant_learnings(
        user_id=session.user_id,
        category=category,
        limit=limit,
    )
    return {
        "learnings": learnings,
        "count": len(learnings),
        "pending_count": get_pending_count(session.user_id),
    }


@router.get("/pending")
async def list_pending_learnings(
    session: AuthenticatedSession = Depends(validate_session),
):
    """List learnings pending review (from trusted contacts, not yet confirmed)."""
    learnings = get_relevant_learnings(
        user_id=session.user_id,
        limit=50,
    )
    pending = [l for l in learnings if l["status"] == "pending_review"]
    return {
        "learnings": pending,
        "count": len(pending),
    }


@router.post("/{learning_id}/confirm")
async def confirm_learning_endpoint(
    learning_id: int,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Confirm a pending learning (primary user approval)."""
    result = confirm_learning(session.user_id, learning_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/{learning_id}/dismiss")
async def dismiss_learning_endpoint(
    learning_id: int,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Dismiss a pending learning."""
    result = dismiss_learning(session.user_id, learning_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
