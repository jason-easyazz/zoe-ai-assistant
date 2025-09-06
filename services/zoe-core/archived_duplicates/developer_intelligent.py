"""Intelligent Developer Assistant - Understands the PROJECT"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict, Any
import sys
import json
sys.path.append('/app')
from project_analyzer import project_analyzer
from ai_client import ai_client

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = {}

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Intelligent chat that understands the project"""
    
    message_lower = msg.message.lower()
    
    # Analyze project if asking about improvements, suggestions, or analysis
    if any(word in message_lower for word in ['improve', 'suggest', 'analyze', 'project', 'feature', 'what', 'build', 'create']):
        
        # Get complete project analysis
        report = project_analyzer.generate_report()
        
        # Create context for LLM
        context = f"""You are Zack, the lead developer of the Zoe AI Assistant project.

PROJECT STATUS:
- Completeness: {report['project_health']['completeness']}
- Routers: {report['project_health']['routers']}
- UI Pages: {report['project_health']['ui_pages']}
- API Endpoints: {report['project_health']['api_endpoints']}

VISION COMPLETED:
{json.dumps(report['vision_status']['completed'], indent=2)}

IN PROGRESS:
{json.dumps(report['vision_status']['in_progress'], indent=2)}

GAPS FOUND:
{json.dumps(report['gaps'][:3], indent=2)}

TOP IMPROVEMENTS:
{json.dumps(report['improvements'][:3], indent=2)}

USER QUESTION: {msg.message}

Provide an executive analysis with:
1. Direct answer to their question
2. Specific suggestions based on the project state
3. Code examples if relevant
4. Clear next steps

Be specific about THIS project, not generic advice."""

        # Get intelligent response
        response = await ai_client.generate_response(context, {"mode": "developer", "temperature": 0.3})
        
        # Format with plan
        plan = {
            "title": "Project Enhancement Plan",
            "based_on": "Complete project analysis",
            "top_priorities": report["top_priorities"],
            "gaps_found": len(report["gaps"]),
            "improvements_available": len(report["improvements"])
        }
        
        return {
            "response": response.get("response", "Analysis complete"),
            "plan": plan,
            "project_health": report["project_health"]
        }
    
    else:
        # For other queries, still provide intelligent context
        codebase = project_analyzer.analyze_codebase()
        
        context = f"""You are Zack, lead developer. The Zoe project has:
- {len(codebase['routers'])} routers: {', '.join(codebase['routers'])}
- {len(codebase['ui_pages'])} UI pages: {', '.join(codebase['ui_pages'])}
- {len(codebase['api_endpoints'])} API endpoints
- {codebase['scripts']['total']} automation scripts

USER: {msg.message}

Provide specific, actionable response about THIS project."""
        
        response = await ai_client.generate_response(context, {"mode": "developer", "temperature": 0.3})
        
        return {
            "response": response.get("response", "Ready to help"),
            "context_aware": True
        }

@router.get("/project/analysis")
async def get_project_analysis():
    """Get complete project analysis"""
    return project_analyzer.generate_report()

@router.get("/project/gaps")
async def get_project_gaps():
    """Get gaps between vision and implementation"""
    codebase = project_analyzer.analyze_codebase()
    gaps = project_analyzer.identify_gaps(codebase)
    return {"gaps": gaps, "total": len(gaps)}

@router.get("/project/improvements")
async def get_improvements():
    """Get suggested improvements"""
    codebase = project_analyzer.analyze_codebase()
    improvements = project_analyzer.suggest_improvements(codebase)
    return {"improvements": improvements, "total": len(improvements)}

@router.get("/status")
async def status():
    return {"status": "intelligent", "mode": "project-aware", "personality": "Zack"}
