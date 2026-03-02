"""
API router for the Life Orchestrator.
"""
from fastapi import APIRouter, Depends, Query
from typing import Dict, Any
from life_orchestrator import life_orchestrator
from auth_integration import AuthenticatedSession, validate_session

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


@router.get("/analyze")
async def analyze_everything(
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Provides a comprehensive analysis of the user's life, generating
    actionable insights and suggestions.
    """
    user_id = session.user_id
    context = {}
    insights = await life_orchestrator.analyze_everything(user_id, context)
    return insights
