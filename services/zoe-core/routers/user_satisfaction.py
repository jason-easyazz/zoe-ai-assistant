"""
User Satisfaction API Router
============================

Provides endpoints for user satisfaction measurement and feedback collection.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sys
sys.path.append('/app')
from user_satisfaction import satisfaction_system, UserFeedback, SatisfactionMetrics, FeedbackType, SatisfactionLevel

router = APIRouter(prefix="/api/satisfaction", tags=["satisfaction"])

# Request/Response models
class FeedbackRequest(BaseModel):
    interaction_id: str
    rating: int  # 1-5 scale
    feedback_text: Optional[str] = None
    context: Optional[Dict[str, Any]] = None

class InteractionRecord(BaseModel):
    interaction_id: str
    request_text: str
    response_text: str
    response_time: float
    context: Optional[Dict[str, Any]] = None

@router.post("/feedback")
async def submit_feedback(
    feedback_request: FeedbackRequest,
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Submit explicit user feedback"""
    try:
        if not 1 <= feedback_request.rating <= 5:
            raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
        
        feedback_id = satisfaction_system.record_explicit_feedback(
            user_id=user_id,
            interaction_id=feedback_request.interaction_id,
            rating=feedback_request.rating,
            feedback_text=feedback_request.feedback_text,
            context=feedback_request.context
        )
        
        if feedback_id:
            return {
                "feedback_id": feedback_id,
                "message": "Feedback recorded successfully",
                "satisfaction_level": SatisfactionLevel(
                    satisfaction_system._rating_to_satisfaction_level(feedback_request.rating)
                ).name
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to record feedback")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/interaction")
async def record_interaction(
    interaction_record: InteractionRecord,
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Record an interaction for implicit satisfaction analysis"""
    try:
        success = satisfaction_system.record_interaction(
            interaction_id=interaction_record.interaction_id,
            user_id=user_id,
            request_text=interaction_record.request_text,
            response_text=interaction_record.response_text,
            response_time=interaction_record.response_time,
            context=interaction_record.context
        )
        
        if success:
            return {"message": "Interaction recorded successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to record interaction")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/metrics")
async def get_satisfaction_metrics(
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Get satisfaction metrics for a user"""
    try:
        metrics = satisfaction_system.get_satisfaction_metrics(user_id)
        
        if not metrics:
            return {
                "user_id": user_id,
                "message": "No satisfaction data available",
                "metrics": None
            }
        
        return {
            "user_id": metrics.user_id,
            "total_interactions": metrics.total_interactions,
            "explicit_feedback_count": metrics.explicit_feedback_count,
            "implicit_feedback_count": metrics.implicit_feedback_count,
            "average_satisfaction": metrics.average_satisfaction,
            "satisfaction_trend": metrics.satisfaction_trend,
            "top_positive_factors": metrics.top_positive_factors,
            "top_negative_factors": metrics.top_negative_factors,
            "last_updated": metrics.last_updated
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/feedback/history")
async def get_feedback_history(
    user_id: str = Query(..., description="User ID for privacy isolation"),
    limit: int = Query(20, description="Number of feedback entries to return")
):
    """Get feedback history for a user"""
    try:
        feedback_list = satisfaction_system.get_user_feedback_history(user_id, limit)
        
        return {
            "feedback_history": [
                {
                    "id": feedback.id,
                    "interaction_id": feedback.interaction_id,
                    "feedback_type": feedback.feedback_type.value,
                    "satisfaction_level": feedback.satisfaction_level.name if feedback.satisfaction_level else None,
                    "explicit_rating": feedback.explicit_rating,
                    "implicit_signals": feedback.implicit_signals,
                    "feedback_text": feedback.feedback_text,
                    "timestamp": feedback.timestamp
                }
                for feedback in feedback_list
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats/system")
async def get_system_satisfaction_stats():
    """Get system-wide satisfaction statistics"""
    try:
        stats = satisfaction_system.get_system_satisfaction_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/levels")
async def get_satisfaction_levels():
    """Get available satisfaction levels"""
    return {
        "satisfaction_levels": [
            {"value": level.value, "name": level.name}
            for level in SatisfactionLevel
        ],
        "feedback_types": [
            {"value": ftype.value, "name": ftype.name}
            for ftype in FeedbackType
        ]
    }



