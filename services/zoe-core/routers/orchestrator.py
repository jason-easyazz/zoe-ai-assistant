"""
API router for the Life Orchestrator.
"""
from fastapi import APIRouter, Depends, Query
from typing import Dict, Any
from life_orchestrator import life_orchestrator
# Assuming a dependency for getting the current user_id
# from ..dependencies import get_current_user_id 

router = APIRouter()

# This is a placeholder for a real dependency injection for user_id
async def get_current_user_id(user_id: str = Query("default", description="User ID for privacy isolation")) -> str:
    return user_id

@router.get("/api/orchestrator/insights", response_model=Dict[str, Any])
async def get_life_insights(user_id: str = Depends(get_current_user_id)):
    """
    Provides a comprehensive analysis of the user's life, generating
    actionable insights and suggestions.
    """
    context = {} # context can be expanded later
    insights = await life_orchestrator.analyze_everything(user_id, context)
    return insights
