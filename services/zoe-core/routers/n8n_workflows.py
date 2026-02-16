"""
n8n Workflow Generation API
============================

Natural language to n8n workflow generation.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from auth_integration import validate_session, AuthenticatedSession
import logging
import sys
import os
import httpx

# Add parent directory to path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from developer.n8n.workflow_analyzer import workflow_analyzer
from developer.n8n.simple_generator import simple_generator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/n8n", tags=["n8n-workflows"])

class WorkflowGenerateRequest(BaseModel):
    description: str
    name: Optional[str] = None
    activate: bool = False

class WorkflowTemplateRequest(BaseModel):
    template_id: str
    parameters: Optional[Dict[str, Any]] = None
    activate: bool = False

@router.get("/status")
async def get_n8n_status():
    """Check n8n service status"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("http://zoe-n8n:5678/healthz")
            return {
                "available": response.status_code == 200,
                "service": "n8n Workflow Automation",
                "version": "1.58.2"
            }
    except Exception as e:
        return {
            "available": False,
            "error": str(e)
        }

@router.get("/templates")
async def list_templates(session: AuthenticatedSession = Depends(validate_session)):
    """List available workflow templates"""
    try:
        templates = simple_generator.get_available_templates()
        return {
            "templates": templates,
            "count": len(templates)
        }
    except Exception as e:
        logger.error(f"Failed to list templates: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate")
async def generate_workflow(request: WorkflowGenerateRequest):
    """Generate workflow from natural language description"""
    try:
        # Generate workflow structure
        workflow = simple_generator.generate_simple_workflow(request.description)
        
        if request.name:
            workflow["name"] = request.name
        
        return {
            "success": True,
            "workflow": workflow,
            "message": f"Generated workflow with {len(workflow['nodes'])} nodes",
            "preview": {
                "name": workflow["name"],
                "node_count": len(workflow["nodes"]),
                "nodes": [node["name"] for node in workflow["nodes"]]
            }
        }
        
    except Exception as e:
        logger.error(f"Workflow generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate-from-template")
async def generate_from_template(request: WorkflowTemplateRequest):
    """Generate workflow from template"""
    try:
        workflow = simple_generator.generate_from_template(
            request.template_id,
            request.parameters
        )
        
        return {
            "success": True,
            "workflow": workflow,
            "message": f"Generated workflow from template '{request.template_id}'",
            "preview": {
                "name": workflow["name"],
                "node_count": len(workflow["nodes"]),
                "nodes": [node["name"] for node in workflow["nodes"]]
            }
        }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Template generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/analyze-request")
async def analyze_workflow_request(description: str):
    """Analyze what workflow will be generated (preview)"""
    try:
        # Detect template
        template_name = simple_generator.detect_template(description)
        
        # Suggest nodes
        suggested_nodes = workflow_analyzer.suggest_nodes_for_task(description)
        
        return {
            "description": description,
            "template_detected": template_name,
            "suggested_nodes": suggested_nodes,
            "estimated_complexity": "simple" if len(suggested_nodes) <= 5 else "complex",
            "can_generate": True
        }
        
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/deploy")
async def deploy_workflow(workflow: Dict[str, Any]):
    """Deploy workflow to n8n via the N8N API.

    Phase -1 Fix 2: Actually deploys workflow to N8N instead of returning
    a placeholder. Uses the n8n REST API to create the workflow.
    """
    try:
        # Import auth helpers from the n8n integration router
        from .n8n_integration import _n8n_request

        # Create workflow in N8N via API
        workflow_payload = {
            "name": workflow.get("name", "Zoe Generated Workflow"),
            "nodes": workflow.get("nodes", []),
            "connections": workflow.get("connections", {}),
            "settings": workflow.get("settings", {}),
            "active": workflow.get("activate", False),
        }

        response = await _n8n_request("POST", "/workflows", json=workflow_payload)

        if response.status_code in [200, 201]:
            result = response.json()
            return {
                "success": True,
                "message": "Workflow deployed to N8N",
                "workflow_id": result.get("id"),
                "name": result.get("name"),
                "active": result.get("active", False),
            }
        else:
            logger.error(f"N8N deployment failed: {response.status_code} - {response.text}")
            return {
                "success": False,
                "message": f"N8N API returned {response.status_code}",
                "error": response.text[:500],
                "fallback": "Copy the workflow JSON and import manually in N8N UI"
            }

    except ImportError:
        logger.error("Cannot import _n8n_request from n8n_integration")
        return {
            "success": False,
            "message": "N8N integration module not available",
            "fallback": "Copy the workflow JSON and import manually in N8N UI"
        }
    except Exception as e:
        logger.error(f"Deployment failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/capabilities")
async def get_n8n_capabilities():
    """Get n8n workflow generation capabilities"""
    return {
        "features": [
            "Natural language to workflow generation",
            "Template-based workflow creation",
            "Workflow preview and analysis",
            "Common pattern detection",
            "Multi-node workflow support"
        ],
        "supported_patterns": [
            "webhook_to_action",
            "schedule_task",
            "email_processing",
            "database_sync",
            "api_integration"
        ],
        "node_types": [
            "Webhook", "Schedule Trigger", "HTTP Request",
            "Slack", "Email", "Postgres", "Code", "Set", "IF"
        ]
    }

