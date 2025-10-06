"""
Context Cache API Router
========================

Provides endpoints for context summarization and caching functionality.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sys
sys.path.append('/app')
from context_cache import context_cache, ContextSummary, ContextType, CacheStatus

router = APIRouter(prefix="/api/context-cache", tags=["context-cache"])

# Request/Response models
class ContextCacheRequest(BaseModel):
    context_type: str
    context_data: Dict[str, Any]
    ttl_hours: Optional[int] = None

class ContextRetrieveRequest(BaseModel):
    context_type: str
    context_data: Dict[str, Any]

@router.post("/cache")
async def cache_context(
    cache_request: ContextCacheRequest,
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Cache a context summary"""
    try:
        context_type = ContextType(cache_request.context_type)
        cache_id = context_cache.cache_context(
            user_id=user_id,
            context_type=context_type,
            context_data=cache_request.context_data,
            ttl_hours=cache_request.ttl_hours
        )
        
        if cache_id:
            return {
                "cache_id": cache_id,
                "message": "Context cached successfully",
                "context_type": cache_request.context_type
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to cache context")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid context type")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/retrieve")
async def retrieve_cached_context(
    retrieve_request: ContextRetrieveRequest,
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Retrieve cached context summary"""
    try:
        context_type = ContextType(retrieve_request.context_type)
        cached_context = context_cache.get_cached_context(
            user_id=user_id,
            context_type=context_type,
            context_data=retrieve_request.context_data
        )
        
        if cached_context:
            return {
                "found": True,
                "summary": cached_context.summary,
                "context_type": cached_context.context_type.value,
                "created_at": cached_context.created_at,
                "expires_at": cached_context.expires_at,
                "access_count": cached_context.access_count,
                "confidence_score": cached_context.confidence_score,
                "model_used": cached_context.model_used
            }
        else:
            return {
                "found": False,
                "message": "No cached context found"
            }
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid context type")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/invalidate")
async def invalidate_context(
    context_type: str = Query(..., description="Context type to invalidate"),
    reason: str = Query("manual_invalidation", description="Reason for invalidation"),
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Invalidate cached context for a user and context type"""
    try:
        context_type_enum = ContextType(context_type)
        context_cache.invalidate_context(
            user_id=user_id,
            context_type=context_type_enum,
            reason=reason
        )
        
        return {
            "message": f"Context cache invalidated for {context_type}",
            "reason": reason
        }
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid context type")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats")
async def get_cache_stats(
    user_id: Optional[str] = Query(None, description="User ID for privacy isolation")
):
    """Get cache statistics"""
    try:
        stats = context_cache.get_cache_stats(user_id)
        return {
            "cache_stats": stats,
            "user_id": user_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/performance")
async def get_performance_metrics(
    context_type: Optional[str] = Query(None, description="Context type filter"),
    days: int = Query(7, description="Number of days to look back")
):
    """Get performance metrics for context operations"""
    try:
        context_type_enum = ContextType(context_type) if context_type else None
        metrics = context_cache.get_performance_metrics(
            context_type=context_type_enum,
            days=days
        )
        
        return {
            "performance_metrics": metrics,
            "context_type": context_type,
            "days": days
        }
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid context type")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/types")
async def get_context_types():
    """Get available context types"""
    return {
        "context_types": [
            {"value": ctype.value, "name": ctype.name}
            for ctype in ContextType
        ]
    }

@router.get("/status")
async def get_cache_status():
    """Get overall cache system status"""
    try:
        # Get basic stats
        stats = context_cache.get_cache_stats()
        
        # Get performance metrics
        performance = context_cache.get_performance_metrics()
        
        # Calculate overall health
        total_entries = sum(ctype_stats["total_entries"] for ctype_stats in stats.values())
        valid_entries = sum(ctype_stats["valid_entries"] for ctype_stats in stats.values())
        
        health_score = (valid_entries / total_entries * 100) if total_entries > 0 else 0
        
        return {
            "status": "healthy" if health_score > 80 else "degraded",
            "health_score": health_score,
            "total_entries": total_entries,
            "valid_entries": valid_entries,
            "performance_metrics": performance,
            "cache_config": {
                "default_ttl_hours": context_cache.default_ttl_hours,
                "max_cache_size": context_cache.max_cache_size,
                "performance_threshold_ms": context_cache.performance_threshold_ms
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
