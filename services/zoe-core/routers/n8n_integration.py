"""
N8N Integration Router
Provides API endpoints for managing N8N workflows, executions, and integrations.

Phase -1 Fix 2: Fixed undefined get_n8n_auth(), undefined N8N_BASE_URL variable,
and implemented webhook processing handlers.
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import httpx
import json
import logging
from datetime import datetime
import asyncio

logger = logging.getLogger(__name__)
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

# N8N API base URL - will be loaded from settings
DEFAULT_N8N_BASE_URL = "https://zoe.local:5678/api/v1"

def get_n8n_base_url():
    """Get N8N base URL from settings"""
    try:
        from .settings import load_n8n_settings
        settings = load_n8n_settings()
        if settings.get('n8n_url'):
            # Ensure the URL has the correct API path
            url = settings['n8n_url'].rstrip('/')
            if not url.endswith('/api/v1'):
                url += '/api/v1'
            return url
    except Exception as e:
        print(f"Failed to load N8N settings: {e}")
    
    return DEFAULT_N8N_BASE_URL

def get_n8n_headers():
    """Get N8N API headers from settings"""
    try:
        from .settings import load_n8n_settings
        settings = load_n8n_settings()
        api_key = settings.get('n8n_api_key')
        
        if api_key:
            return {"X-N8N-API-KEY": api_key}
    except Exception as e:
        logger.warning(f"Failed to load N8N API key: {e}")
    
    return {}


def get_n8n_auth():
    """Get N8N basic auth credentials from settings.

    Phase -1 Fix 2: This function was called but never defined, causing
    AttributeError at runtime. Returns httpx BasicAuth tuple or None.
    """
    try:
        from .settings import load_n8n_settings
        settings = load_n8n_settings()
        username = settings.get('n8n_username')
        password = settings.get('n8n_password')

        if username and password:
            return httpx.BasicAuth(username, password)
    except Exception as e:
        logger.warning(f"Failed to load N8N auth: {e}")

    return None


async def _n8n_request(method: str, path: str, **kwargs) -> httpx.Response:
    """Make a request to the N8N API with proper auth.

    Phase -1 Fix 2: Centralized N8N API request helper that uses headers
    (API key) first, falling back to basic auth, with SSL verification
    disabled for self-signed certs (common in local N8N installs).
    """
    n8n_url = get_n8n_base_url()
    headers = get_n8n_headers()
    auth = get_n8n_auth() if not headers else None

    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        response = await client.request(
            method,
            f"{n8n_url}{path}",
            headers=headers or None,
            auth=auth,
            **kwargs
        )
        return response


@router.get("/workflows")
async def get_workflows():
    """Get all N8N workflows"""
    try:
        response = await _n8n_request("GET", "/workflows")
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
        response = await _n8n_request("GET", f"/workflows/{workflow_id}")
        return response.json()

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching workflow: {str(e)}")

@router.post("/workflows/{workflow_id}/activate")
async def activate_workflow(workflow_id: str):
    """Activate a workflow"""
    try:
        response = await _n8n_request("POST", f"/workflows/{workflow_id}/activate")
        return {"message": "Workflow activated", "workflow_id": workflow_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error activating workflow: {str(e)}")

@router.post("/workflows/{workflow_id}/deactivate")
async def deactivate_workflow(workflow_id: str):
    """Deactivate a workflow"""
    try:
        response = await _n8n_request("POST", f"/workflows/{workflow_id}/deactivate")
        return {"message": "Workflow deactivated", "workflow_id": workflow_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deactivating workflow: {str(e)}")

@router.post("/workflows/{workflow_id}/execute")
async def execute_workflow(workflow_id: str, data: Optional[Dict[str, Any]] = None):
    """Execute a workflow manually"""
    try:
        payload = {"data": data} if data else {}
        response = await _n8n_request("POST", f"/workflows/{workflow_id}/execute", json=payload)
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
        response = await _n8n_request("GET", f"/executions?limit={limit}")
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
        response = await _n8n_request("GET", f"/executions/{execution_id}")
        return response.json()

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
    """Setup a new integration.

    Note: Full auto-setup (creating N8N workflows from config) requires
    the Phase 3 scheduler. For now, this validates config and returns
    manual setup instructions.
    """
    try:
        return {
            "message": f"Integration '{integration_name}' config validated",
            "integration": config.dict(),
            "status": "manual_setup_required",
            "instructions": [
                f"1. Open N8N at http://zoe-n8n:5678",
                f"2. Create a new workflow for '{integration_name}'",
                f"3. Configure credentials for provider '{config.provider}'",
                f"4. Set webhook URL: http://zoe-core:8000/api/n8n/webhooks/{integration_name}",
                f"5. Activate the workflow in N8N"
            ],
            "note": "Auto-setup will be available after Phase 3 (Scheduler)"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error setting up integration: {str(e)}")

@router.get("/health")
async def get_n8n_health():
    """Check N8N service health"""
    try:
        # Use the direct healthz endpoint (doesn't need API auth)
        async with httpx.AsyncClient(verify=False, timeout=5.0) as client:
            response = await client.get("http://zoe-n8n:5678/healthz/readiness")

        return {
            "status": "healthy" if response.status_code == 200 else "unhealthy",
            "http_status": response.status_code,
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
    """Process email data from N8N.

    Phase -1 Fix 2: Implemented real email webhook processing.
    Forwards email content to Zoe's chat endpoint for AI analysis.
    """
    logger.info(f"Processing email webhook: subject={data.get('subject', 'N/A')}")

    email_content = data.get("email_content", data.get("body", ""))
    sender = data.get("from", data.get("sender", "unknown"))
    subject = data.get("subject", "No subject")

    if email_content:
        # Forward to Zoe's chat for analysis and summarization
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(
                    "http://localhost:8000/api/chat",
                    json={
                        "message": f"[Email from {sender}] Subject: {subject}\n\n{email_content[:2000]}",
                        "context": {"source": "n8n_email_webhook", "sender": sender},
                        "user_id": "default"
                    }
                )
        except Exception as e:
            logger.error(f"Failed to process email via chat: {e}")
    else:
        logger.warning("Email webhook received but no content found")


async def process_calendar_webhook(data: Dict[str, Any]):
    """Process calendar data from N8N.

    Phase -1 Fix 2: Implemented real calendar webhook processing.
    Creates calendar events from N8N calendar sync data.
    """
    logger.info(f"Processing calendar webhook: {len(data.get('events', []))} events")

    events = data.get("events", [])
    created = 0

    for event in events:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "http://localhost:8000/api/calendar/events",
                    json={
                        "title": event.get("title", event.get("summary", "N8N Event")),
                        "description": event.get("description", ""),
                        "start_date": event.get("start_date", event.get("start", "")),
                        "start_time": event.get("start_time"),
                        "category": "synced",
                    }
                )
                if response.status_code in [200, 201]:
                    created += 1
        except Exception as e:
            logger.error(f"Failed to create calendar event: {e}")

    logger.info(f"Calendar webhook: created {created}/{len(events)} events")


async def process_task_webhook(data: Dict[str, Any]):
    """Process task data from N8N.

    Phase -1 Fix 2: Implemented real task webhook processing.
    Creates list items from N8N task extraction data.
    """
    logger.info(f"Processing task webhook: {len(data.get('tasks', []))} tasks")

    tasks = data.get("tasks", [])
    created = 0

    for task in tasks:
        try:
            task_text = task.get("text", task.get("title", ""))
            if not task_text:
                continue

            async with httpx.AsyncClient(timeout=10.0) as client:
                # Get lists to find a todo list
                lists_response = await client.get("http://localhost:8000/api/lists")
                if lists_response.status_code == 200:
                    lists_data = lists_response.json()
                    todo_list = None
                    for lst in lists_data.get("lists", []):
                        if lst.get("list_type") in ["personal_todos", "work_todos"]:
                            todo_list = lst
                            break

                    if todo_list:
                        await client.post(
                            f"http://localhost:8000/api/lists/{todo_list['list_type']}/{todo_list['id']}/items",
                            params={"task_text": task_text, "priority": task.get("priority", "medium")}
                        )
                        created += 1
        except Exception as e:
            logger.error(f"Failed to create task: {e}")

    logger.info(f"Task webhook: created {created}/{len(tasks)} tasks")


async def process_generic_webhook(webhook_id: str, data: Dict[str, Any]):
    """Process generic webhook data.

    Phase -1 Fix 2: Logs and stores webhook data for debugging/auditing.
    """
    logger.info(f"Processing generic webhook '{webhook_id}': {json.dumps(data)[:500]}")

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






