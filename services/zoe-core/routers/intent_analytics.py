"""
Intent Analytics API
====================

REST API endpoints for intent system performance metrics and monitoring.
"""

from fastapi import APIRouter, Query
from typing import Optional
import logging

from intent_system.analytics import get_metrics_collector

router = APIRouter(prefix="/api/intent", tags=["intent-analytics"])
logger = logging.getLogger(__name__)


@router.get("/analytics")
async def get_intent_analytics(
    hours: int = Query(24, description="Time window in hours", ge=1, le=168)
):
    """
    Get comprehensive intent system analytics.
    
    Args:
        hours: Time window in hours (default: 24, max: 168/1 week)
        
    Returns:
        Performance summary including:
        - Tier distribution (how many queries use each tier)
        - Average latency per tier
        - Success rate
        - Top intents
    """
    try:
        metrics = get_metrics_collector()
        summary = metrics.get_performance_summary(hours)
        
        return {
            "success": True,
            "data": summary
        }
        
    except Exception as e:
        logger.error(f"Failed to get analytics: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/analytics/tier-distribution")
async def get_tier_distribution(
    hours: int = Query(24, description="Time window in hours", ge=1, le=168)
):
    """
    Get tier distribution showing how queries are classified.
    
    Target distribution:
    - Tier 0 (HassIL): 85-90%
    - Tier 1 (Keywords): 5-10%
    - Tier 2 (Context): 3-5%
    - Tier 3 (LLM): <2%
    
    Args:
        hours: Time window in hours
        
    Returns:
        Count and percentage for each tier
    """
    try:
        metrics = get_metrics_collector()
        distribution = metrics.get_tier_distribution(hours)
        total = sum(distribution.values())
        
        result = {}
        for tier in [0, 1, 2, 3]:
            count = distribution.get(tier, 0)
            percentage = (count / total * 100) if total > 0 else 0.0
            result[f"tier_{tier}"] = {
                "count": count,
                "percentage": round(percentage, 2)
            }
        
        return {
            "success": True,
            "total_queries": total,
            "distribution": result,
            "targets": {
                "tier_0": "85-90%",
                "tier_1": "5-10%",
                "tier_2": "3-5%",
                "tier_3": "<2%"
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to get tier distribution: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/analytics/latency")
async def get_latency_stats(
    hours: int = Query(24, description="Time window in hours", ge=1, le=168)
):
    """
    Get latency statistics per tier.
    
    Target latencies:
    - Tier 0 (HassIL): <5ms
    - Tier 1 (Keywords): <15ms
    - Tier 2 (Context): <200ms
    - Tier 3 (LLM): <500ms
    
    Args:
        hours: Time window in hours
        
    Returns:
        Average latency for each tier
    """
    try:
        metrics = get_metrics_collector()
        
        latency_stats = {
            "overall": {
                "avg_ms": round(metrics.get_avg_latency(None, hours), 2),
                "target_ms": "N/A"
            },
            "tier_0": {
                "avg_ms": round(metrics.get_avg_latency(0, hours), 2),
                "target_ms": 5
            },
            "tier_1": {
                "avg_ms": round(metrics.get_avg_latency(1, hours), 2),
                "target_ms": 15
            },
            "tier_2": {
                "avg_ms": round(metrics.get_avg_latency(2, hours), 2),
                "target_ms": 200
            },
            "tier_3": {
                "avg_ms": round(metrics.get_avg_latency(3, hours), 2),
                "target_ms": 500
            }
        }
        
        return {
            "success": True,
            "latency": latency_stats
        }
        
    except Exception as e:
        logger.error(f"Failed to get latency stats: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/analytics/top-intents")
async def get_top_intents(
    limit: int = Query(20, description="Number of intents to return", ge=1, le=100),
    hours: int = Query(24, description="Time window in hours", ge=1, le=168)
):
    """
    Get most commonly used intents.
    
    Args:
        limit: Maximum number of intents to return
        hours: Time window in hours
        
    Returns:
        List of intents with usage counts
    """
    try:
        metrics = get_metrics_collector()
        top_intents = metrics.get_top_intents(limit, hours)
        
        return {
            "success": True,
            "top_intents": [
                {"intent": name, "count": count}
                for name, count in top_intents
            ]
        }
        
    except Exception as e:
        logger.error(f"Failed to get top intents: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/analytics/failures")
async def get_failed_queries(
    limit: int = Query(50, description="Number of failures to return", ge=1, le=200),
    hours: int = Query(24, description="Time window in hours", ge=1, le=168)
):
    """
    Get recent failed queries for debugging.
    
    Args:
        limit: Maximum number of failures to return
        hours: Time window in hours
        
    Returns:
        List of failed queries with timestamps
    """
    try:
        metrics = get_metrics_collector()
        failures = metrics.get_failed_queries(limit, hours)
        
        return {
            "success": True,
            "failures": [
                {
                    "timestamp": timestamp,
                    "input_text": input_text,
                    "intent_name": intent_name
                }
                for timestamp, input_text, intent_name in failures
            ]
        }
        
    except Exception as e:
        logger.error(f"Failed to get failed queries: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/analytics/success-rate")
async def get_success_rate(
    hours: int = Query(24, description="Time window in hours", ge=1, le=168)
):
    """
    Get overall intent execution success rate.
    
    Target: >95%
    
    Args:
        hours: Time window in hours
        
    Returns:
        Success rate percentage
    """
    try:
        metrics = get_metrics_collector()
        success_rate = metrics.get_success_rate(hours)
        
        return {
            "success": True,
            "success_rate_pct": round(success_rate, 2),
            "target_pct": 95.0,
            "meets_target": success_rate >= 95.0
        }
        
    except Exception as e:
        logger.error(f"Failed to get success rate: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/health")
async def intent_system_health():
    """
    Check intent system health.
    
    Returns:
        Health status and key metrics
    """
    try:
        from intent_system.classifiers import UnifiedIntentClassifier
        from intent_system.executors import IntentExecutor
        
        # Check if components are initialized
        classifier = UnifiedIntentClassifier()
        executor = IntentExecutor()
        
        available_intents = classifier.get_available_intents()
        registered_handlers = executor.get_registered_intents()
        
        # Get recent metrics
        metrics = get_metrics_collector()
        success_rate = metrics.get_success_rate(hours=1)  # Last hour
        total_queries = sum(metrics.get_tier_distribution(hours=1).values())
        
        return {
            "success": True,
            "healthy": True,
            "status": {
                "available_intents": len(available_intents),
                "registered_handlers": len(registered_handlers),
                "queries_last_hour": total_queries,
                "success_rate_last_hour": round(success_rate, 2)
            }
        }
        
    except Exception as e:
        logger.error(f"Intent system health check failed: {e}", exc_info=True)
        return {
            "success": False,
            "healthy": False,
            "error": str(e)
        }

