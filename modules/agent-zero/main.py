#!/usr/bin/env python3
"""
Agent Zero Bridge Module
========================

Provides Zoe AI with Agent Zero's autonomous capabilities.
Self-contained module that exposes HTTP endpoints for intent handlers.

Architecture:
  zoe-core → intent handler → HTTP endpoint → Agent Zero WebSocket API
  
Features:
  - Research: Complex multi-step research tasks
  - Planning: Break down tasks into actionable steps
  - Analysis: Review configurations, code, systems
  - Comparison: Compare technologies, products, approaches
  
Safety:
  - Grandma mode: Research and planning only (safe for everyone)
  - Developer mode: Full capabilities with sandboxing
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging
import os
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Agent Zero Bridge Module",
    description="Bridge between Zoe AI and Agent Zero autonomous agent",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration from environment
AGENT_ZERO_URL = os.getenv("AGENT_ZERO_URL", "http://zoe-agent0:80")
SAFETY_MODE = os.getenv("SAFETY_MODE", "grandma")
DATABASE_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# Import client and safety
from client import AgentZeroClient
from safety import SafetyGuardrails

# Initialize
client = AgentZeroClient(AGENT_ZERO_URL)
safety = SafetyGuardrails(mode=SAFETY_MODE)

logger.info(f"🚀 Agent Zero Bridge starting...")
logger.info(f"   Agent Zero URL: {AGENT_ZERO_URL}")
logger.info(f"   Safety Mode: {SAFETY_MODE}")
logger.info(f"   Capabilities: {', '.join(safety.get_enabled_capabilities())}")


# ============================================================
# Pydantic Models
# ============================================================

class ResearchRequest(BaseModel):
    """Request model for research endpoint."""
    query: str
    depth: str = "thorough"
    user_id: str = "default"


class PlanRequest(BaseModel):
    """Request model for planning endpoint."""
    task: str
    user_id: str = "default"


class AnalyzeRequest(BaseModel):
    """Request model for analysis endpoint."""
    target: str
    user_id: str = "default"


class CompareRequest(BaseModel):
    """Request model for comparison endpoint."""
    item_a: str
    item_b: str
    user_id: str = "default"


class BrowseRequest(BaseModel):
    """Request model for browser automation endpoint."""
    url: str
    actions: List[str] = []
    extract_fields: List[str] = []
    user_id: str = "default"


# ============================================================
# Health & Status Endpoints
# ============================================================

@app.get("/health")
async def health():
    """
    Health check endpoint.
    
    Returns service health and Agent Zero availability.
    """
    agent_zero_available = await client.is_available()
    
    return {
        "status": "healthy",
        "agent_zero_connected": agent_zero_available,
        "safety_mode": SAFETY_MODE,
        "capabilities": safety.get_enabled_capabilities()
    }


@app.get("/tools/status")
async def status():
    """
    Get detailed status of Agent Zero service.
    
    Returns:
        Status information including availability and capabilities
    """
    agent_zero_available = await client.is_available()
    
    return {
        "available": agent_zero_available,
        "mode": SAFETY_MODE,
        "capabilities": safety.get_enabled_capabilities(),
        "agent_zero_url": AGENT_ZERO_URL,
        "version": "1.0.0"
    }


# ============================================================
# Tool Endpoints (Called by Intent Handlers)
# ============================================================

@app.post("/tools/research")
async def research(request: ResearchRequest):
    """
    Research endpoint - uses Agent Zero for complex research.
    
    Performs multi-step research using Claude 3.5 Sonnet via Agent Zero:
      1. Web searches for relevant information
      2. Synthesizes findings across sources
      3. Provides structured summary with citations
    
    Allowed in: GRANDMA_MODE, DEVELOPER_MODE
    
    Args:
        request: Research request with query and depth
        
    Returns:
        Research results with summary, details, and sources
    """
    # Safety check
    allowed, reason = safety.validate_request("research", {})
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)
    
    logger.info(f"🔍 Research request from user {request.user_id}: {request.query}")
    
    try:
        # Call Agent Zero for research
        result = await client.research(request.query, depth=request.depth)
        
        return {
            "success": True,
            "summary": result.get("summary", ""),
            "details": result.get("details", ""),
            "sources": result.get("sources", []),
            "depth": request.depth,
            "status": result.get("status", "complete")
        }
        
    except Exception as e:
        logger.error(f"Research failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": "Research task failed. Agent Zero might be unavailable."
        }


@app.post("/tools/plan")
async def plan(request: PlanRequest):
    """
    Planning endpoint - creates multi-step plans.
    
    Uses Agent Zero to:
      1. Analyze task requirements
      2. Break down into actionable steps
      3. Estimate time and complexity
      4. Identify dependencies
    
    Allowed in: GRANDMA_MODE, DEVELOPER_MODE
    
    Args:
        request: Planning request with task description
        
    Returns:
        Plan with steps, time estimate, and complexity
    """
    # Safety check
    allowed, reason = safety.validate_request("planning", {})
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)
    
    logger.info(f"📋 Planning request from user {request.user_id}: {request.task}")
    
    try:
        # Call Agent Zero for planning
        result = await client.plan(request.task)
        
        return {
            "success": True,
            "steps": result.get("steps", []),
            "estimated_time": result.get("estimated_time", ""),
            "complexity": result.get("complexity", "unknown"),
            "status": result.get("status", "complete")
        }
        
    except Exception as e:
        logger.error(f"Planning failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": "Planning task failed. Agent Zero might be unavailable."
        }


@app.post("/tools/analyze")
async def analyze(request: AnalyzeRequest):
    """
    Analysis endpoint - analyzes files, configurations, systems.
    
    Uses Agent Zero to:
      1. Review target (file, config, system)
      2. Identify issues and opportunities
      3. Provide actionable recommendations
    
    Allowed in: GRANDMA_MODE (read-only), DEVELOPER_MODE (full access)
    
    Args:
        request: Analysis request with target description
        
    Returns:
        Analysis with findings and recommendations
    """
    # Safety check
    allowed, reason = safety.validate_request("research", {})  # Analysis uses research capability
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)
    
    logger.info(f"🔬 Analysis request from user {request.user_id}: {request.target}")
    
    try:
        # Call Agent Zero for analysis
        result = await client.analyze(request.target)
        
        return {
            "success": True,
            "analysis": result.get("analysis", ""),
            "findings": result.get("findings", []),
            "recommendations": result.get("recommendations", []),
            "status": result.get("status", "complete")
        }
        
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": "Analysis task failed. Agent Zero might be unavailable."
        }


@app.post("/tools/compare")
async def compare(request: CompareRequest):
    """
    Comparison endpoint - compares two items.
    
    Uses Agent Zero to:
      1. Research both items independently
      2. Identify key differences
      3. Analyze pros/cons of each
      4. Provide recommendation
    
    Allowed in: GRANDMA_MODE, DEVELOPER_MODE
    
    Args:
        request: Comparison request with two items
        
    Returns:
        Comparison with pros/cons and recommendation
    """
    # Safety check
    allowed, reason = safety.validate_request("research", {})
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)
    
    logger.info(f"⚖️ Comparison request from user {request.user_id}: {request.item_a} vs {request.item_b}")
    
    try:
        # Call Agent Zero for comparison
        result = await client.compare(request.item_a, request.item_b)
        
        return {
            "success": True,
            "comparison": result.get("comparison", ""),
            "item_a_pros": result.get("item_a_pros", []),
            "item_b_pros": result.get("item_b_pros", []),
            "recommendation": result.get("recommendation", ""),
            "status": result.get("status", "complete")
        }
        
    except Exception as e:
        logger.error(f"Comparison failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": "Comparison task failed. Agent Zero might be unavailable."
        }


@app.post("/tools/browse")
async def browse(request: BrowseRequest):
    """
    Browser automation endpoint - navigates websites and extracts data.
    
    Uses Agent Zero's browser capability to:
      1. Navigate to a URL
      2. Perform ordered actions (click, fill, submit)
      3. Extract structured data from the page
    
    Allowed in: DEVELOPER_MODE only
    
    Args:
        request: Browse request with URL, actions, and fields to extract
        
    Returns:
        Extracted data and raw response
    """
    allowed, reason = safety.validate_request("browser_automation", {})
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)
    
    logger.info(f"🌐 Browse request from user {request.user_id}: {request.url}")
    
    try:
        result = await client.browse_and_extract(
            url=request.url,
            actions=request.actions,
            extract_fields=request.extract_fields,
            user_id=request.user_id
        )
        
        return {
            "success": result.get("success", False),
            "extracted": result.get("extracted", {}),
            "raw_response": result.get("raw_response", ""),
            "url": request.url,
            "status": result.get("status", "complete")
        }
        
    except Exception as e:
        logger.error(f"Browse failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": "Browser automation failed. Agent Zero might be unavailable."
        }


# ============================================================
# Startup Event
# ============================================================

@app.on_event("startup")
async def startup_event():
    """Log startup information."""
    logger.info("=" * 60)
    logger.info("🤖 Agent Zero Bridge Module Started")
    logger.info("=" * 60)
    logger.info(f"Safety Mode: {SAFETY_MODE}")
    logger.info(f"Enabled Capabilities: {', '.join(safety.get_enabled_capabilities())}")
    logger.info(f"Agent Zero URL: {AGENT_ZERO_URL}")
    
    # Check Agent Zero availability
    if await client.is_available():
        logger.info("✅ Agent Zero is AVAILABLE")
    else:
        logger.warning("⚠️ Agent Zero is NOT AVAILABLE - check zoe-agent0 container")
    
    logger.info("=" * 60)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8101, log_level="info")
