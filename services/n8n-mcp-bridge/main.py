#!/usr/bin/env python3
"""
Zoe N8N MCP Bridge Service
Provides MCP tools for managing N8N workflows, executions, and nodes
"""

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import httpx
import json
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.append('/app')

app = FastAPI(title="Zoe N8N MCP Bridge", version="1.0.0")

# Configuration
N8N_BASE_URL = os.getenv("N8N_BASE_URL", "http://n8n:5678")
N8N_API_KEY = os.getenv("N8N_API_KEY", "")

# Initialize N8N bridge service
class N8NBridge:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {
            "X-N8N-API-KEY": api_key,
            "Content-Type": "application/json"
        }
    
    async def _make_request(self, method: str, endpoint: str, data: Dict = None) -> Dict:
        """Make HTTP request to N8N API"""
        url = f"{self.base_url}/api/v1/{endpoint}"
        
        async with httpx.AsyncClient() as client:
            try:
                if method.upper() == "GET":
                    response = await client.get(url, headers=self.headers, timeout=10.0)
                elif method.upper() == "POST":
                    response = await client.post(url, headers=self.headers, json=data, timeout=10.0)
                elif method.upper() == "PUT":
                    response = await client.put(url, headers=self.headers, json=data, timeout=10.0)
                elif method.upper() == "DELETE":
                    response = await client.delete(url, headers=self.headers, timeout=10.0)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                
                if response.status_code in [200, 201]:
                    return response.json()
                else:
                    raise HTTPException(status_code=response.status_code, detail=response.text)
                    
            except httpx.TimeoutException:
                raise HTTPException(status_code=408, detail="N8N request timeout")
            except httpx.ConnectError:
                raise HTTPException(status_code=503, detail="Cannot connect to N8N")
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"N8N API error: {str(e)}")
    
    async def get_workflows(self) -> List[Dict]:
        """Get all workflows from N8N"""
        return await self._make_request("GET", "workflows")
    
    async def get_workflow(self, workflow_id: str) -> Dict:
        """Get specific workflow from N8N"""
        return await self._make_request("GET", f"workflows/{workflow_id}")
    
    async def create_workflow(self, workflow_data: Dict) -> Dict:
        """Create a new workflow in N8N"""
        return await self._make_request("POST", "workflows", workflow_data)
    
    async def update_workflow(self, workflow_id: str, workflow_data: Dict) -> Dict:
        """Update an existing workflow in N8N"""
        return await self._make_request("PUT", f"workflows/{workflow_id}", workflow_data)
    
    async def delete_workflow(self, workflow_id: str) -> Dict:
        """Delete a workflow from N8N"""
        return await self._make_request("DELETE", f"workflows/{workflow_id}")
    
    async def activate_workflow(self, workflow_id: str) -> Dict:
        """Activate a workflow in N8N"""
        return await self._make_request("POST", f"workflows/{workflow_id}/activate")
    
    async def deactivate_workflow(self, workflow_id: str) -> Dict:
        """Deactivate a workflow in N8N"""
        return await self._make_request("POST", f"workflows/{workflow_id}/deactivate")
    
    async def execute_workflow(self, workflow_id: str, input_data: Dict = None) -> Dict:
        """Execute a workflow in N8N"""
        payload = {}
        if input_data:
            payload["data"] = input_data
        return await self._make_request("POST", f"workflows/{workflow_id}/execute", payload)
    
    async def get_executions(self, workflow_id: str = None, limit: int = 20) -> List[Dict]:
        """Get workflow executions from N8N"""
        endpoint = "executions"
        if workflow_id:
            endpoint += f"?workflowId={workflow_id}&limit={limit}"
        else:
            endpoint += f"?limit={limit}"
        return await self._make_request("GET", endpoint)
    
    async def get_execution(self, execution_id: str) -> Dict:
        """Get specific execution from N8N"""
        return await self._make_request("GET", f"executions/{execution_id}")
    
    async def stop_execution(self, execution_id: str) -> Dict:
        """Stop a running execution in N8N"""
        return await self._make_request("POST", f"executions/{execution_id}/stop")
    
    async def get_nodes(self) -> List[Dict]:
        """Get all available nodes from N8N"""
        return await self._make_request("GET", "nodes")
    
    async def get_credentials(self) -> List[Dict]:
        """Get all credentials from N8N"""
        return await self._make_request("GET", "credentials")
    
    async def create_credential(self, credential_data: Dict) -> Dict:
        """Create a new credential in N8N"""
        return await self._make_request("POST", "credentials", credential_data)
    
    async def get_workflow_statistics(self, workflow_id: str) -> Dict:
        """Get workflow statistics from N8N"""
        return await self._make_request("GET", f"workflows/{workflow_id}/statistics")

# Initialize bridge
n8n_bridge = N8NBridge(N8N_BASE_URL, N8N_API_KEY)

# Pydantic models
class WorkflowCreate(BaseModel):
    name: str
    nodes: List[Dict[str, Any]]
    connections: Dict[str, Any]
    active: Optional[bool] = False
    settings: Optional[Dict[str, Any]] = None

class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    nodes: Optional[List[Dict[str, Any]]] = None
    connections: Optional[Dict[str, Any]] = None
    active: Optional[bool] = None
    settings: Optional[Dict[str, Any]] = None

class WorkflowExecute(BaseModel):
    workflow_id: str
    input_data: Optional[Dict[str, Any]] = None

class CredentialCreate(BaseModel):
    name: str
    type: str
    data: Dict[str, Any]

# API Endpoints
@app.get("/")
async def root():
    """Service health check"""
    try:
        # Test connection to N8N
        workflows = await n8n_bridge.get_workflows()
        return {
            "service": "Zoe N8N MCP Bridge",
            "status": "healthy",
            "version": "1.0.0",
            "n8n_connected": True,
            "workflows_count": len(workflows)
        }
    except Exception as e:
        return {
            "service": "Zoe N8N MCP Bridge",
            "status": "unhealthy",
            "version": "1.0.0",
            "n8n_connected": False,
            "error": str(e)
        }

@app.get("/workflows")
async def get_workflows():
    """Get all workflows from N8N"""
    try:
        workflows = await n8n_bridge.get_workflows()
        
        formatted_workflows = []
        for workflow in workflows:
            formatted_workflows.append({
                "id": workflow.get("id"),
                "name": workflow.get("name"),
                "active": workflow.get("active", False),
                "created_at": workflow.get("createdAt"),
                "updated_at": workflow.get("updatedAt"),
                "nodes_count": len(workflow.get("nodes", [])),
                "connections_count": len(workflow.get("connections", {}))
            })
        
        return {"workflows": formatted_workflows, "count": len(formatted_workflows)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str):
    """Get specific workflow from N8N"""
    try:
        workflow = await n8n_bridge.get_workflow(workflow_id)
        
        return {
            "id": workflow.get("id"),
            "name": workflow.get("name"),
            "active": workflow.get("active", False),
            "nodes": workflow.get("nodes", []),
            "connections": workflow.get("connections", {}),
            "settings": workflow.get("settings", {}),
            "created_at": workflow.get("createdAt"),
            "updated_at": workflow.get("updatedAt")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/workflows")
async def create_workflow(workflow: WorkflowCreate):
    """Create a new workflow in N8N"""
    try:
        workflow_data = {
            "name": workflow.name,
            "nodes": workflow.nodes,
            "connections": workflow.connections,
            "active": workflow.active,
            "settings": workflow.settings or {}
        }
        
        result = await n8n_bridge.create_workflow(workflow_data)
        
        return {
            "message": f"Successfully created workflow '{workflow.name}'",
            "workflow": {
                "id": result.get("id"),
                "name": result.get("name"),
                "active": result.get("active", False)
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/workflows/{workflow_id}")
async def update_workflow(workflow_id: str, workflow: WorkflowUpdate):
    """Update an existing workflow in N8N"""
    try:
        # Get existing workflow first
        existing_workflow = await n8n_bridge.get_workflow(workflow_id)
        
        # Update only provided fields
        update_data = {}
        if workflow.name is not None:
            update_data["name"] = workflow.name
        if workflow.nodes is not None:
            update_data["nodes"] = workflow.nodes
        if workflow.connections is not None:
            update_data["connections"] = workflow.connections
        if workflow.active is not None:
            update_data["active"] = workflow.active
        if workflow.settings is not None:
            update_data["settings"] = workflow.settings
        
        # Merge with existing data
        workflow_data = {**existing_workflow, **update_data}
        
        result = await n8n_bridge.update_workflow(workflow_id, workflow_data)
        
        return {
            "message": f"Successfully updated workflow '{workflow_id}'",
            "workflow": {
                "id": result.get("id"),
                "name": result.get("name"),
                "active": result.get("active", False)
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str):
    """Delete a workflow from N8N"""
    try:
        result = await n8n_bridge.delete_workflow(workflow_id)
        
        return {
            "message": f"Successfully deleted workflow '{workflow_id}'",
            "result": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/workflows/{workflow_id}/activate")
async def activate_workflow(workflow_id: str):
    """Activate a workflow in N8N"""
    try:
        result = await n8n_bridge.activate_workflow(workflow_id)
        
        return {
            "message": f"Successfully activated workflow '{workflow_id}'",
            "result": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/workflows/{workflow_id}/deactivate")
async def deactivate_workflow(workflow_id: str):
    """Deactivate a workflow in N8N"""
    try:
        result = await n8n_bridge.deactivate_workflow(workflow_id)
        
        return {
            "message": f"Successfully deactivated workflow '{workflow_id}'",
            "result": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/workflows/{workflow_id}/execute")
async def execute_workflow(workflow_id: str, execution_data: WorkflowExecute):
    """Execute a workflow in N8N"""
    try:
        result = await n8n_bridge.execute_workflow(workflow_id, execution_data.input_data)
        
        return {
            "message": f"Successfully executed workflow '{workflow_id}'",
            "execution_id": result.get("executionId"),
            "result": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/executions")
async def get_executions(
    workflow_id: Optional[str] = Query(None, description="Filter by workflow ID"),
    limit: int = Query(20, description="Limit number of results")
):
    """Get workflow executions from N8N"""
    try:
        executions = await n8n_bridge.get_executions(workflow_id, limit)
        
        formatted_executions = []
        for execution in executions:
            formatted_executions.append({
                "id": execution.get("id"),
                "workflow_id": execution.get("workflowId"),
                "status": execution.get("status"),
                "started_at": execution.get("startedAt"),
                "finished_at": execution.get("finishedAt"),
                "mode": execution.get("mode"),
                "retry_of": execution.get("retryOf")
            })
        
        return {"executions": formatted_executions, "count": len(formatted_executions)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/executions/{execution_id}")
async def get_execution(execution_id: str):
    """Get specific execution from N8N"""
    try:
        execution = await n8n_bridge.get_execution(execution_id)
        
        return {
            "id": execution.get("id"),
            "workflow_id": execution.get("workflowId"),
            "status": execution.get("status"),
            "started_at": execution.get("startedAt"),
            "finished_at": execution.get("finishedAt"),
            "mode": execution.get("mode"),
            "data": execution.get("data"),
            "retry_of": execution.get("retryOf")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/executions/{execution_id}/stop")
async def stop_execution(execution_id: str):
    """Stop a running execution in N8N"""
    try:
        result = await n8n_bridge.stop_execution(execution_id)
        
        return {
            "message": f"Successfully stopped execution '{execution_id}'",
            "result": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/nodes")
async def get_nodes():
    """Get all available nodes from N8N"""
    try:
        nodes = await n8n_bridge.get_nodes()
        
        formatted_nodes = []
        for node in nodes:
            formatted_nodes.append({
                "name": node.get("name"),
                "display_name": node.get("displayName"),
                "description": node.get("description"),
                "version": node.get("version"),
                "defaults": node.get("defaults", {}),
                "properties": node.get("properties", [])
            })
        
        return {"nodes": formatted_nodes, "count": len(formatted_nodes)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/credentials")
async def get_credentials():
    """Get all credentials from N8N"""
    try:
        credentials = await n8n_bridge.get_credentials()
        
        formatted_credentials = []
        for credential in credentials:
            formatted_credentials.append({
                "id": credential.get("id"),
                "name": credential.get("name"),
                "type": credential.get("type"),
                "created_at": credential.get("createdAt"),
                "updated_at": credential.get("updatedAt")
            })
        
        return {"credentials": formatted_credentials, "count": len(formatted_credentials)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/credentials")
async def create_credential(credential: CredentialCreate):
    """Create a new credential in N8N"""
    try:
        credential_data = {
            "name": credential.name,
            "type": credential.type,
            "data": credential.data
        }
        
        result = await n8n_bridge.create_credential(credential_data)
        
        return {
            "message": f"Successfully created credential '{credential.name}'",
            "credential": {
                "id": result.get("id"),
                "name": result.get("name"),
                "type": result.get("type")
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/workflows/{workflow_id}/statistics")
async def get_workflow_statistics(workflow_id: str):
    """Get workflow statistics from N8N"""
    try:
        stats = await n8n_bridge.get_workflow_statistics(workflow_id)
        
        return {
            "workflow_id": workflow_id,
            "statistics": stats
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/analysis")
async def analyze_n8n():
    """Get comprehensive analysis of N8N setup"""
    try:
        workflows = await n8n_bridge.get_workflows()
        executions = await n8n_bridge.get_executions(limit=100)
        nodes = await n8n_bridge.get_nodes()
        credentials = await n8n_bridge.get_credentials()
        
        # Analyze workflows
        active_workflows = sum(1 for w in workflows if w.get("active", False))
        inactive_workflows = len(workflows) - active_workflows
        
        # Analyze executions
        execution_stats = {}
        for execution in executions:
            status = execution.get("status", "unknown")
            if status not in execution_stats:
                execution_stats[status] = 0
            execution_stats[status] += 1
        
        # Analyze nodes usage
        node_usage = {}
        for workflow in workflows:
            for node in workflow.get("nodes", []):
                node_type = node.get("type", "unknown")
                if node_type not in node_usage:
                    node_usage[node_type] = 0
                node_usage[node_type] += 1
        
        # Calculate insights
        total_workflows = len(workflows)
        total_executions = len(executions)
        total_nodes = len(nodes)
        total_credentials = len(credentials)
        
        # Find most used node type
        most_used_node = max(node_usage.keys(), key=lambda k: node_usage[k]) if node_usage else None
        
        # Find most active workflow
        most_active_workflow = None
        if executions:
            workflow_execution_counts = {}
            for execution in executions:
                workflow_id = execution.get("workflowId")
                if workflow_id:
                    workflow_execution_counts[workflow_id] = workflow_execution_counts.get(workflow_id, 0) + 1
            
            if workflow_execution_counts:
                most_active_workflow_id = max(workflow_execution_counts.keys(), key=lambda k: workflow_execution_counts[k])
                most_active_workflow = {
                    "workflow_id": most_active_workflow_id,
                    "execution_count": workflow_execution_counts[most_active_workflow_id]
                }
        
        analysis = {
            "summary": {
                "total_workflows": total_workflows,
                "active_workflows": active_workflows,
                "inactive_workflows": inactive_workflows,
                "total_executions": total_executions,
                "total_nodes": total_nodes,
                "total_credentials": total_credentials
            },
            "execution_stats": execution_stats,
            "node_usage": node_usage,
            "insights": {
                "most_used_node": most_used_node,
                "most_active_workflow": most_active_workflow,
                "automation_level": "high" if active_workflows > 10 else "medium" if active_workflows > 5 else "low",
                "execution_activity": "high" if total_executions > 100 else "medium" if total_executions > 50 else "low",
                "node_diversity": "high" if len(node_usage) > 10 else "medium" if len(node_usage) > 5 else "low"
            }
        }
        
        return {"analysis": analysis}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8009)

