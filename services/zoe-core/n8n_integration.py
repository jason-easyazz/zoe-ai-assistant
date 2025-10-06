"""
N8N Workflow Integration for Zoe AI Assistant
Connects automation workflows and event triggers
"""
import httpx
import json
import asyncio
import logging
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import uuid

logger = logging.getLogger(__name__)

class WorkflowStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"
    RUNNING = "running"

@dataclass
class N8NConfig:
    """Configuration for N8N integration"""
    base_url: str = "http://localhost:5678"
    api_key: Optional[str] = None
    webhook_secret: Optional[str] = None
    timeout_seconds: int = 30
    retry_attempts: int = 3
    rate_limit_per_minute: int = 60

@dataclass
class WorkflowTemplate:
    """N8N workflow template"""
    id: str
    name: str
    description: str
    category: str
    nodes: List[Dict[str, Any]]
    connections: List[Dict[str, Any]]
    settings: Dict[str, Any]

class N8NIntegration:
    """Integration with N8N workflow automation"""
    
    def __init__(self, config: N8NConfig = None):
        self.config = config or N8NConfig()
        self.workflows: Dict[str, Dict[str, Any]] = {}
        self.webhook_endpoints: Dict[str, str] = {}
        self.event_callbacks: List[Callable] = []
        self.rate_limiter = {}
        
    async def test_connection(self) -> Dict[str, Any]:
        """Test connection to N8N instance"""
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                headers = self._get_headers()
                response = await client.get(f"{self.config.base_url}/api/v1/workflows", headers=headers)
                
                if response.status_code == 200:
                    return {
                        "connected": True,
                        "status": "healthy",
                        "n8n_version": response.headers.get("x-n8n-version", "unknown"),
                        "workflow_count": len(response.json().get("data", []))
                    }
                else:
                    return {
                        "connected": False,
                        "status": "error",
                        "error": f"HTTP {response.status_code}: {response.text}"
                    }
                    
        except Exception as e:
            return {
                "connected": False,
                "status": "error",
                "error": str(e)
            }
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for N8N API requests"""
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["X-N8N-API-KEY"] = self.config.api_key
        return headers
    
    async def get_workflows(self) -> List[Dict[str, Any]]:
        """Get all workflows from N8N"""
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                headers = self._get_headers()
                response = await client.get(f"{self.config.base_url}/api/v1/workflows", headers=headers)
                
                if response.status_code == 200:
                    workflows = response.json().get("data", [])
                    self.workflows = {wf["id"]: wf for wf in workflows}
                    return workflows
                else:
                    logger.error(f"Failed to get workflows: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error getting workflows: {e}")
            return []
    
    async def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Get specific workflow by ID"""
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                headers = self._get_headers()
                response = await client.get(f"{self.config.base_url}/api/v1/workflows/{workflow_id}", headers=headers)
                
                if response.status_code == 200:
                    workflow = response.json()
                    self.workflows[workflow_id] = workflow
                    return workflow
                else:
                    logger.error(f"Failed to get workflow {workflow_id}: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting workflow {workflow_id}: {e}")
            return None
    
    async def create_workflow(self, workflow_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create new workflow in N8N"""
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                headers = self._get_headers()
                response = await client.post(
                    f"{self.config.base_url}/api/v1/workflows",
                    headers=headers,
                    json=workflow_data
                )
                
                if response.status_code == 201:
                    workflow = response.json()
                    self.workflows[workflow["id"]] = workflow
                    return workflow
                else:
                    logger.error(f"Failed to create workflow: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error creating workflow: {e}")
            return None
    
    async def update_workflow(self, workflow_id: str, workflow_data: Dict[str, Any]) -> bool:
        """Update existing workflow"""
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                headers = self._get_headers()
                response = await client.put(
                    f"{self.config.base_url}/api/v1/workflows/{workflow_id}",
                    headers=headers,
                    json=workflow_data
                )
                
                if response.status_code == 200:
                    workflow = response.json()
                    self.workflows[workflow_id] = workflow
                    return True
                else:
                    logger.error(f"Failed to update workflow {workflow_id}: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error updating workflow {workflow_id}: {e}")
            return False
    
    async def activate_workflow(self, workflow_id: str) -> bool:
        """Activate workflow"""
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                headers = self._get_headers()
                response = await client.post(
                    f"{self.config.base_url}/api/v1/workflows/{workflow_id}/activate",
                    headers=headers
                )
                
                if response.status_code == 200:
                    logger.info(f"Workflow {workflow_id} activated")
                    return True
                else:
                    logger.error(f"Failed to activate workflow {workflow_id}: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error activating workflow {workflow_id}: {e}")
            return False
    
    async def deactivate_workflow(self, workflow_id: str) -> bool:
        """Deactivate workflow"""
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                headers = self._get_headers()
                response = await client.post(
                    f"{self.config.base_url}/api/v1/workflows/{workflow_id}/deactivate",
                    headers=headers
                )
                
                if response.status_code == 200:
                    logger.info(f"Workflow {workflow_id} deactivated")
                    return True
                else:
                    logger.error(f"Failed to deactivate workflow {workflow_id}: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error deactivating workflow {workflow_id}: {e}")
            return False
    
    async def execute_workflow(self, workflow_id: str, input_data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """Execute workflow manually"""
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                headers = self._get_headers()
                payload = {"data": input_data} if input_data else {}
                
                response = await client.post(
                    f"{self.config.base_url}/api/v1/workflows/{workflow_id}/execute",
                    headers=headers,
                    json=payload
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"Workflow {workflow_id} executed successfully")
                    return result
                else:
                    logger.error(f"Failed to execute workflow {workflow_id}: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error executing workflow {workflow_id}: {e}")
            return None
    
    async def trigger_webhook(self, webhook_url: str, data: Dict[str, Any]) -> bool:
        """Trigger webhook with data"""
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(webhook_url, json=data)
                
                if response.status_code in [200, 201, 202]:
                    logger.info(f"Webhook triggered successfully: {webhook_url}")
                    return True
                else:
                    logger.error(f"Webhook failed: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error triggering webhook: {e}")
            return False
    
    def create_webhook_endpoint(self, workflow_id: str, event_type: str) -> str:
        """Create webhook endpoint for workflow"""
        webhook_id = str(uuid.uuid4())
        webhook_url = f"{self.config.base_url}/webhook/{webhook_id}"
        
        self.webhook_endpoints[webhook_id] = {
            "workflow_id": workflow_id,
            "event_type": event_type,
            "url": webhook_url,
            "created_at": datetime.now().isoformat()
        }
        
        return webhook_url
    
    def get_workflow_templates(self) -> List[WorkflowTemplate]:
        """Get predefined workflow templates"""
        templates = [
            WorkflowTemplate(
                id="zoe-chat-trigger",
                name="Zoe Chat Trigger",
                description="Trigger workflow when Zoe receives a chat message",
                category="chat",
                nodes=[
                    {
                        "id": "webhook",
                        "type": "n8n-nodes-base.webhook",
                        "name": "Chat Webhook",
                        "parameters": {
                            "httpMethod": "POST",
                            "path": "zoe-chat"
                        }
                    },
                    {
                        "id": "zoe-process",
                        "type": "n8n-nodes-base.function",
                        "name": "Process Chat",
                        "parameters": {
                            "functionCode": "return { chat_message: $input.first().json.message };"
                        }
                    }
                ],
                connections=[
                    {
                        "from": "webhook",
                        "to": "zoe-process"
                    }
                ],
                settings={}
            ),
            WorkflowTemplate(
                id="task-completion-notification",
                name="Task Completion Notification",
                description="Send notification when task is completed",
                category="notifications",
                nodes=[
                    {
                        "id": "webhook",
                        "type": "n8n-nodes-base.webhook",
                        "name": "Task Webhook",
                        "parameters": {
                            "httpMethod": "POST",
                            "path": "task-completed"
                        }
                    },
                    {
                        "id": "email",
                        "type": "n8n-nodes-base.emailSend",
                        "name": "Send Email",
                        "parameters": {
                            "toEmail": "admin@zoe-ai.local",
                            "subject": "Task Completed: {{ $json.task_title }}",
                            "message": "Task {{ $json.task_title }} has been completed successfully."
                        }
                    }
                ],
                connections=[
                    {
                        "from": "webhook",
                        "to": "email"
                    }
                ],
                settings={}
            ),
            WorkflowTemplate(
                id="system-health-monitor",
                name="System Health Monitor",
                description="Monitor system health and send alerts",
                category="monitoring",
                nodes=[
                    {
                        "id": "webhook",
                        "type": "n8n-nodes-base.webhook",
                        "name": "Health Webhook",
                        "parameters": {
                            "httpMethod": "POST",
                            "path": "health-alert"
                        }
                    },
                    {
                        "id": "condition",
                        "type": "n8n-nodes-base.if",
                        "name": "Check Alert Level",
                        "parameters": {
                            "conditions": {
                                "string": [
                                    {
                                        "value1": "={{ $json.level }}",
                                        "operation": "equal",
                                        "value2": "critical"
                                    }
                                ]
                            }
                        }
                    },
                    {
                        "id": "slack",
                        "type": "n8n-nodes-base.slack",
                        "name": "Send Slack Alert",
                        "parameters": {
                            "channel": "#alerts",
                            "text": "🚨 Critical system alert: {{ $json.message }}"
                        }
                    }
                ],
                connections=[
                    {
                        "from": "webhook",
                        "to": "condition"
                    },
                    {
                        "from": "condition",
                        "to": "slack"
                    }
                ],
                settings={}
            )
        ]
        
        return templates
    
    async def create_workflow_from_template(self, template_id: str, custom_name: str = None) -> Optional[Dict[str, Any]]:
        """Create workflow from template"""
        templates = self.get_workflow_templates()
        template = next((t for t in templates if t.id == template_id), None)
        
        if not template:
            logger.error(f"Template {template_id} not found")
            return None
        
        workflow_data = {
            "name": custom_name or template.name,
            "nodes": template.nodes,
            "connections": template.connections,
            "settings": template.settings,
            "active": False
        }
        
        return await self.create_workflow(workflow_data)
    
    def add_event_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Add callback for workflow events"""
        self.event_callbacks.append(callback)
    
    async def handle_webhook_event(self, webhook_id: str, event_data: Dict[str, Any]):
        """Handle incoming webhook event"""
        if webhook_id not in self.webhook_endpoints:
            logger.warning(f"Unknown webhook ID: {webhook_id}")
            return
        
        webhook_info = self.webhook_endpoints[webhook_id]
        workflow_id = webhook_info["workflow_id"]
        
        # Execute the workflow
        result = await self.execute_workflow(workflow_id, event_data)
        
        # Call event callbacks
        for callback in self.event_callbacks:
            try:
                callback({
                    "webhook_id": webhook_id,
                    "workflow_id": workflow_id,
                    "event_data": event_data,
                    "result": result
                })
            except Exception as e:
                logger.error(f"Event callback failed: {e}")
    
    async def get_workflow_executions(self, workflow_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get workflow execution history"""
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                headers = self._get_headers()
                response = await client.get(
                    f"{self.config.base_url}/api/v1/executions",
                    headers=headers,
                    params={"workflowId": workflow_id, "limit": limit}
                )
                
                if response.status_code == 200:
                    return response.json().get("data", [])
                else:
                    logger.error(f"Failed to get executions: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error getting executions: {e}")
            return []
    
    def get_integration_status(self) -> Dict[str, Any]:
        """Get N8N integration status"""
        return {
            "connected": len(self.workflows) > 0,
            "workflow_count": len(self.workflows),
            "webhook_count": len(self.webhook_endpoints),
            "base_url": self.config.base_url,
            "templates_available": len(self.get_workflow_templates())
        }

# Global instance
n8n_integration = N8NIntegration()
