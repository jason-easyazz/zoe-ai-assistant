"""
memvid Archive Management Router (Phase 6A)
API endpoints for managing video-based learning archives
"""

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging
import sys

sys.path.append('/app')
from memvid_archiver import archiver

router = APIRouter(prefix="/api/archives", tags=["memvid-archives"])
logger = logging.getLogger(__name__)


class ArchiveRequest(BaseModel):
    """Request to create archive"""
    year: int
    quarter: int  # 1-4
    dry_run: bool = True  # Safety: default to dry run


class SearchRequest(BaseModel):
    """Request to search archives"""
    query: str
    archive_types: Optional[List[str]] = None  # chats, journals, tasks, patterns
    user_id: Optional[str] = None
    top_k: int = 10


@router.post("/create")
async def create_archive(request: ArchiveRequest, background_tasks: BackgroundTasks):
    """
    Create memvid archive for specified quarter.
    
    Archives historical data (>90 days old) to video format for learning.
    Default: dry_run=True (safe preview of what would be archived)
    """
    try:
        # Run archival (async in background if not dry_run)
        if request.dry_run:
            result = await archiver.archive_all_data_types(
                request.year, 
                request.quarter, 
                dry_run=True
            )
        else:
            # Background job for actual archival
            background_tasks.add_task(
                archiver.archive_all_data_types,
                request.year,
                request.quarter,
                False
            )
            result = {
                "message": "Archival started in background",
                "quarter": f"{request.year}-Q{request.quarter}",
                "dry_run": False
            }
        
        return result
        
    except Exception as e:
        logger.error(f"Archive creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def list_archives():
    """List all available memvid archives"""
    try:
        archives = archiver.list_archives()
        return {
            "archives": archives,
            "count": len(archives)
        }
    except Exception as e:
        logger.error(f"Failed to list archives: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search")
async def search_archives(request: SearchRequest):
    """
    Search across memvid archives for historical patterns.
    
    Example queries:
    - "when did I add milk to shopping list?"
    - "what were my stress patterns in Q1?"
    - "how often did I turn on lights at 8pm?"
    """
    try:
        results = await archiver.search_archives(
            query=request.query,
            archive_types=request.archive_types,
            user_id=request.user_id,
            top_k=request.top_k
        )
        
        return {
            "query": request.query,
            "results": results,
            "count": len(results)
        }
        
    except Exception as e:
        logger.error(f"Archive search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_archive_stats():
    """Get statistics about all archives"""
    try:
        stats = archiver.get_archive_stats()
        return stats
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def archive_health():
    """Check memvid archive system health"""
    try:
        import memvid
        archives = archiver.list_archives()
        
        return {
            "status": "healthy",
            "memvid_available": True,
            "archive_count": len(archives),
            "total_size_mb": sum(a['size_mb'] for a in archives),
            "archive_dir": str(archiver.archive_dir)
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


# Phase 6B: Learning Engine Endpoints
@router.get("/learning/analyze/{user_id}")
async def analyze_user_history(user_id: str):
    """
    Analyze user's complete interaction history from archives.
    Returns patterns across chats, journals, tasks, behaviors.
    """
    try:
        from unified_learner import unified_learner
        analysis = await unified_learner.analyze_complete_history(user_id)
        return analysis
    except Exception as e:
        logger.error(f"History analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/learning/evolve")
async def trigger_evolution(user_id: str = "system"):
    """
    Trigger evolution of all learning systems from archive data.
    Updates preferences, learning patterns, model selection from complete history.
    """
    try:
        from preference_learner import preference_learner
        from learning_system import learning_system
        from intelligent_model_manager import intelligent_manager
        
        results = {
            "preferences": await preference_learner.learn_from_archives(user_id),
            "learning_system": await learning_system.evolve_from_complete_history(user_id),
            "model_optimization": await intelligent_manager.analyze_model_performance_history(user_id)
        }
        
        return {
            "evolution_triggered": True,
            "systems_updated": sum(1 for r in results.values() if r.get('learned') or r.get('evolved') or r.get('analyzed')),
            "results": results
        }
    except Exception as e:
        logger.error(f"Evolution trigger failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Phase 6C: Predictive Intelligence Endpoints
@router.get("/predictions/{user_id}")
async def get_predictions(user_id: str):
    """
    Get predictive assistance for user based on historical patterns.
    Suggests likely next actions and proactive help.
    """
    try:
        from predictive_intelligence import predictive_intelligence
        
        current_context = {
            "timestamp": datetime.now().isoformat(),
            "day_of_week": datetime.now().strftime("%A"),
            "hour": datetime.now().hour
        }
        
        predictions = await predictive_intelligence.predict_next_action(user_id, current_context)
        proactive = await predictive_intelligence.enable_proactive_support(user_id)
        
        return {
            "user_id": user_id,
            "predictions": predictions,
            "proactive_suggestions": proactive,
            "generated_at": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Prediction generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

