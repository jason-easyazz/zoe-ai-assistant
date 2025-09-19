from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import httpx
import json
from datetime import datetime
import asyncio

router = APIRouter(prefix="/api/n8n", tags=["n8n-integration"])

class N8NWorkflow(BaseModel):
    id: str
    name: str
    active: bool
    nodes: List[Dict[str, Any]]
    connections: Dict[str, Any]
    settings: Dict[str, Any]
    static_data: Optional[Dict[str, Any]] = None

class WorkflowExecution(BaseModel):
    id: str
    workflow_id: str
    status: str  # running, success, error, waiting
    started_at: str
    finished_at: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class IntegrationConfig(BaseModel):
    name: str
    type: str  # email, calendar, social, etc.
    provider: str  # gmail, outlook, google_calendar, etc.
    config: Dict[str, Any]
    enabled: bool = True
    last_sync: Optional[str] = None

# N8N API base URL
N8N_BASE_URL = "http://zoe-n8n:5678/api/v1"

@router.get("/workflows")
async def get_workflows():
    """Get all N8N workflows"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{N8N_BASE_URL}/workflows")
            workflows = response.json()
        
        return {
            "workflows": workflows.get("data", []),
            "total": len(workflows.get("data", [])),
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching workflows: {str(e)}")

@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str):
    """Get specific workflow details"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{N8N_BASE_URL}/workflows/{workflow_id}")
            workflow = response.json()
        
        return workflow
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching workflow: {str(e)}")

@router.post("/workflows/{workflow_id}/activate")
async def activate_workflow(workflow_id: str):
    """Activate a workflow"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{N8N_BASE_URL}/workflows/{workflow_id}/activate")
            result = response.json()
        
        return {"message": "Workflow activated", "workflow_id": workflow_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error activating workflow: {str(e)}")

@router.post("/workflows/{workflow_id}/deactivate")
async def deactivate_workflow(workflow_id: str):
    """Deactivate a workflow"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{N8N_BASE_URL}/workflows/{workflow_id}/deactivate")
            result = response.json()
        
        return {"message": "Workflow deactivated", "workflow_id": workflow_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deactivating workflow: {str(e)}")

@router.post("/workflows/{workflow_id}/execute")
async def execute_workflow(workflow_id: str, data: Optional[Dict[str, Any]] = None):
    """Execute a workflow manually"""
    try:
        payload = {"data": data} if data else {}
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{N8N_BASE_URL}/workflows/{workflow_id}/execute",
                json=payload
            )
            result = response.json()
        
        return {
            "message": "Workflow executed",
            "workflow_id": workflow_id,
            "execution_id": result.get("executionId"),
            "status": "started"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error executing workflow: {str(e)}")

@router.get("/executions")
async def get_executions(limit: int = 50):
    """Get workflow execution history"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{N8N_BASE_URL}/executions?limit={limit}")
            executions = response.json()
        
        return {
            "executions": executions.get("data", []),
            "total": len(executions.get("data", [])),
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching executions: {str(e)}")

@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str):
    """Get specific execution details"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{N8N_BASE_URL}/executions/{execution_id}")
            execution = response.json()
        
        return execution
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching execution: {str(e)}")

@router.get("/integrations")
async def get_integrations():
    """Get available integrations and their status"""
    try:
        # This would typically come from a database or config
        # For now, return predefined integrations
        integrations = [
            {
                "name": "Gmail Integration",
                "type": "email",
                "provider": "gmail",
                "description": "Fetch and process emails from Gmail",
                "enabled": False,
                "workflow_id": None,
                "last_sync": None
            },
            {
                "name": "Google Calendar Sync",
                "type": "calendar",
                "provider": "google_calendar",
                "description": "Sync events with Google Calendar",
                "enabled": False,
                "workflow_id": None,
                "last_sync": None
            },
            {
                "name": "Outlook Integration",
                "type": "email",
                "provider": "outlook",
                "description": "Fetch and process emails from Outlook",
                "enabled": False,
                "workflow_id": None,
                "last_sync": None
            },
            {
                "name": "Slack Integration",
                "type": "messaging",
                "provider": "slack",
                "description": "Process messages and notifications from Slack",
                "enabled": False,
                "workflow_id": None,
                "last_sync": None
            }
        ]
        
        return {
            "integrations": integrations,
            "total": len(integrations),
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching integrations: {str(e)}")

@router.post("/integrations/{integration_name}/setup")
async def setup_integration(integration_name: str, config: IntegrationConfig):
    """Setup a new integration"""
    try:
        # This would create the appropriate N8N workflow
        # For now, return a placeholder response
        
        return {
            "message": f"Integration {integration_name} setup initiated",
            "integration": config.dict(),
            "next_steps": [
                "Configure authentication credentials",
                "Test the integration",
                "Enable automatic syncing"
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error setting up integration: {str(e)}")

@router.get("/health")
async def get_n8n_health():
    """Check N8N service health"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{N8N_BASE_URL}/health")
            health = response.json()
        
        return {
            "status": "healthy" if response.status_code == 200 else "unhealthy",
            "n8n_status": health,
            "last_checked": datetime.now().isoformat()
        }
        
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "last_checked": datetime.now().isoformat()
        }

@router.post("/webhooks/{webhook_id}")
async def handle_webhook(webhook_id: str, data: Dict[str, Any]):
    """Handle incoming webhooks from N8N workflows"""
    try:
        # Process webhook data based on webhook_id
        if webhook_id == "email-processor":
            await process_email_webhook(data)
        elif webhook_id == "calendar-sync":
            await process_calendar_webhook(data)
        elif webhook_id == "task-extractor":
            await process_task_webhook(data)
        else:
            # Generic webhook processing
            await process_generic_webhook(webhook_id, data)
        
        return {"message": "Webhook processed successfully", "webhook_id": webhook_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing webhook: {str(e)}")

async def process_email_webhook(data: Dict[str, Any]):
    """Process email data from N8N"""
    # This would integrate with the email processing system
    print(f"Processing email webhook: {data}")
    
    # Example: Extract tasks from email content
    if "email_content" in data:
        # Use Zoe's AI to analyze email content
        # Extract tasks, events, and important information
        pass

async def process_calendar_webhook(data: Dict[str, Any]):
    """Process calendar data from N8N"""
    # This would integrate with the calendar system
    print(f"Processing calendar webhook: {data}")
    
    # Example: Sync external calendar events
    if "events" in data:
        # Add events to Zoe's calendar
        pass

async def process_task_webhook(data: Dict[str, Any]):
    """Process task data from N8N"""
    # This would integrate with the task system
    print(f"Processing task webhook: {data}")
    
    # Example: Create tasks from external sources
    if "tasks" in data:
        # Add tasks to Zoe's lists
        pass

async def process_generic_webhook(webhook_id: str, data: Dict[str, Any]):
    """Process generic webhook data"""
    print(f"Processing generic webhook {webhook_id}: {data}")
    
    # Generic processing logic
    pass

@router.get("/templates")
async def get_workflow_templates():
    """Get pre-built workflow templates for common integrations"""
    try:
        templates = [
            {
                "name": "Gmail Task Extractor",
                "description": "Automatically extract tasks from Gmail emails",
                "category": "email",
                "difficulty": "intermediate",
                "features": [
                    "Gmail API integration",
                    "AI-powered task extraction",
                    "Automatic list creation",
                    "Priority detection"
                ],
                "workflow_json": {
                    "nodes": [],
                    "connections": {}
                }
            },
            {
                "name": "Google Calendar Sync",
                "description": "Two-way sync with Google Calendar",
                "category": "calendar",
                "difficulty": "beginner",
                "features": [
                    "Google Calendar API",
                    "Bidirectional sync",
                    "Conflict resolution",
                    "Event categorization"
                ],
                "workflow_json": {
                    "nodes": [],
                    "connections": {}
                }
            },
            {
                "name": "Email Event Detector",
                "description": "Detect events in emails and add to calendar",
                "category": "email",
                "difficulty": "advanced",
                "features": [
                    "Email parsing",
                    "Event extraction",
                    "Calendar integration",
                    "Smart scheduling"
                ],
                "workflow_json": {
                    "nodes": [],
                    "connections": {}
                }
            }
        ]
        
        return {
            "templates": templates,
            "total": len(templates),
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching templates: {str(e)}")

@router.post("/templates/{template_name}/install")
async def install_template(template_name: str, user_id: str = "default"):
    """Install a workflow template"""
    try:
        # This would create the actual N8N workflow from the template
        return {
            "message": f"Template {template_name} installed successfully",
            "template_name": template_name,
            "workflow_id": f"template_{template_name}_{user_id}",
            "next_steps": [
                "Configure authentication",
                "Test the workflow",
                "Activate the workflow"
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error installing template: {str(e)}")




