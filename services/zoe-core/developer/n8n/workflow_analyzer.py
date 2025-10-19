"""
n8n Workflow Analyzer
=====================

Analyzes n8n's capabilities and builds searchable index of node types.
"""

import httpx
import logging
from typing import Dict, List, Optional, Any
import json

logger = logging.getLogger(__name__)

class WorkflowAnalyzer:
    """Analyzes n8n workflow capabilities"""
    
    def __init__(self, n8n_base_url: str = "http://zoe-n8n:5678"):
        self.n8n_base_url = n8n_base_url
        self.nodes_cache = None
    
    async def fetch_available_nodes(self) -> List[Dict[str, Any]]:
        """Fetch all available n8n nodes"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # n8n API endpoint for node types
                response = await client.get(f"{self.n8n_base_url}/types/nodes.json")
                
                if response.status_code == 200:
                    self.nodes_cache = response.json()
                    logger.info(f"✅ Fetched {len(self.nodes_cache)} n8n node types")
                    return self.nodes_cache
                else:
                    logger.error(f"Failed to fetch nodes: {response.status_code}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching n8n nodes: {e}")
            return []
    
    def get_node_by_type(self, node_type: str) -> Optional[Dict[str, Any]]:
        """Get node configuration by type"""
        if not self.nodes_cache:
            return None
        
        for node in self.nodes_cache:
            if node.get("name") == node_type:
                return node
        return None
    
    def search_nodes_by_capability(self, capability: str) -> List[Dict[str, Any]]:
        """Search nodes that match a capability"""
        if not self.nodes_cache:
            return []
        
        capability_lower = capability.lower()
        matching_nodes = []
        
        for node in self.nodes_cache:
            name = node.get("displayName", "").lower()
            description = node.get("description", "").lower()
            
            if capability_lower in name or capability_lower in description:
                matching_nodes.append(node)
        
        return matching_nodes
    
    def get_common_node_patterns(self) -> Dict[str, List[str]]:
        """Return common workflow patterns"""
        return {
            "webhook_to_action": ["Webhook", "Set", "HTTP Request"],
            "schedule_task": ["Schedule Trigger", "Code", "HTTP Request"],
            "email_processing": ["Email Trigger", "Extract from File", "HTTP Request"],
            "database_sync": ["Webhook", "Postgres", "Set"],
            "slack_notification": ["Webhook", "IF", "Slack"],
            "api_to_database": ["HTTP Request", "Set", "Postgres"],
        }
    
    def suggest_nodes_for_task(self, task_description: str) -> List[str]:
        """Suggest node types based on task description"""
        task_lower = task_description.lower()
        suggested_nodes = []
        
        # Common patterns
        if any(word in task_lower for word in ["webhook", "api", "trigger"]):
            suggested_nodes.append("Webhook")
        
        if any(word in task_lower for word in ["schedule", "cron", "every"]):
            suggested_nodes.append("Schedule Trigger")
        
        if any(word in task_lower for word in ["slack", "message", "notify"]):
            suggested_nodes.append("Slack")
        
        if any(word in task_lower for word in ["email", "send email", "mail"]):
            suggested_nodes.append("Send Email")
        
        if any(word in task_lower for word in ["database", "postgres", "sql"]):
            suggested_nodes.append("Postgres")
        
        if any(word in task_lower for word in ["http", "api call", "request"]):
            suggested_nodes.append("HTTP Request")
        
        if any(word in task_lower for word in ["if", "condition", "check"]):
            suggested_nodes.append("IF")
        
        if any(word in task_lower for word in ["set", "transform", "map"]):
            suggested_nodes.append("Set")
        
        if any(word in task_lower for word in ["code", "function", "javascript"]):
            suggested_nodes.append("Code")
        
        return suggested_nodes if suggested_nodes else ["Webhook", "HTTP Request"]

# Global instance
workflow_analyzer = WorkflowAnalyzer()


