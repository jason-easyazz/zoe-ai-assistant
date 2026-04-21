"""
Simple n8n Workflow Generator
==============================

Generates simple 2-5 node workflows from natural language descriptions.
"""

import logging
from typing import Dict, List, Any, Optional
import json
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)

class SimpleWorkflowGenerator:
    """Generates simple n8n workflows"""
    
    def __init__(self):
        self.templates = self._load_templates()
    
    def _load_templates(self) -> Dict[str, Dict]:
        """Load common workflow templates"""
        return {
            "webhook_to_slack": {
                "name": "Webhook to Slack Notification",
                "description": "Send Slack message when webhook triggered",
                "nodes": [
                    {
                        "type": "n8n-nodes-base.webhook",
                        "name": "Webhook",
                        "parameters": {
                            "path": "webhook",
                            "httpMethod": "POST"
                        }
                    },
                    {
                        "type": "n8n-nodes-base.slack",
                        "name": "Slack",
                        "parameters": {
                            "channel": "#general",
                            "text": "=Received webhook: {{$json[\"message\"]}}"
                        }
                    }
                ]
            },
            "schedule_to_api": {
                "name": "Scheduled API Call",
                "description": "Make API call on schedule",
                "nodes": [
                    {
                        "type": "n8n-nodes-base.scheduleTrigger",
                        "name": "Schedule",
                        "parameters": {
                            "rule": {"interval": [{"field": "hours", "hoursInterval": 1}]}
                        }
                    },
                    {
                        "type": "n8n-nodes-base.httpRequest",
                        "name": "HTTP Request",
                        "parameters": {
                            "method": "GET",
                            "url": "https://api.example.com/data"
                        }
                    }
                ]
            },
            "email_to_database": {
                "name": "Email to Database",
                "description": "Save email content to database",
                "nodes": [
                    {
                        "type": "n8n-nodes-base.emailTrigger",
                        "name": "Email Trigger",
                        "parameters": {}
                    },
                    {
                        "type": "n8n-nodes-base.set",
                        "name": "Extract Data",
                        "parameters": {
                            "values": {
                                "string": [
                                    {"name": "subject", "value": "={{$json[\"subject\"]}}"},
                                    {"name": "from", "value": "={{$json[\"from\"]}}"}
                                ]
                            }
                        }
                    },
                    {
                        "type": "n8n-nodes-base.postgres",
                        "name": "Save to DB",
                        "parameters": {
                            "operation": "insert",
                            "table": "emails"
                        }
                    }
                ]
            }
        }
    
    def detect_template(self, description: str) -> Optional[str]:
        """Detect which template matches the description"""
        desc_lower = description.lower()
        
        if "webhook" in desc_lower and "slack" in desc_lower:
            return "webhook_to_slack"
        elif "schedule" in desc_lower and ("api" in desc_lower or "request" in desc_lower):
            return "schedule_to_api"
        elif "email" in desc_lower and "database" in desc_lower:
            return "email_to_database"
        
        return None
    
    def generate_from_template(self, template_name: str, parameters: Dict[str, Any] = None) -> Dict:
        """Generate workflow from template"""
        if template_name not in self.templates:
            raise ValueError(f"Template '{template_name}' not found")
        
        template = self.templates[template_name]
        parameters = parameters or {}
        
        # Create workflow structure
        workflow = {
            "name": parameters.get("name", template["name"]),
            "nodes": [],
            "connections": {},
            "active": False,
            "settings": {},
            "id": str(uuid.uuid4())
        }
        
        # Add nodes
        for i, node_template in enumerate(template["nodes"]):
            node = {
                "id": str(uuid.uuid4()),
                "name": node_template["name"],
                "type": node_template["type"],
                "typeVersion": 1,
                "position": [250, 300 + (i * 100)],
                "parameters": node_template["parameters"].copy()
            }
            
            # Apply custom parameters
            if node["name"] in parameters:
                node["parameters"].update(parameters[node["name"]])
            
            workflow["nodes"].append(node)
        
        # Create connections
        for i in range(len(workflow["nodes"]) - 1):
            source_node = workflow["nodes"][i]["name"]
            target_node = workflow["nodes"][i + 1]["name"]
            
            workflow["connections"][source_node] = {
                "main": [[{"node": target_node, "type": "main", "index": 0}]]
            }
        
        return workflow
    
    def generate_simple_workflow(self, description: str) -> Dict:
        """Generate a simple workflow from natural language"""
        # Try to detect template
        template_name = self.detect_template(description)
        
        if template_name:
            logger.info(f"✅ Using template: {template_name}")
            return self.generate_from_template(template_name)
        else:
            # Fallback: create basic webhook workflow
            logger.info("📝 Creating custom workflow")
            return self._create_custom_workflow(description)
    
    def _create_custom_workflow(self, description: str) -> Dict:
        """Create a custom workflow based on description"""
        # Simple heuristic-based generation
        desc_lower = description.lower()
        
        nodes = []
        
        # Start with a trigger
        if "schedule" in desc_lower or "cron" in desc_lower:
            nodes.append({
                "name": "Schedule Trigger",
                "type": "n8n-nodes-base.scheduleTrigger",
                "parameters": {
                    "rule": {"interval": [{"field": "hours", "hoursInterval": 1}]}
                }
            })
        else:
            nodes.append({
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "parameters": {
                    "path": "webhook",
                    "httpMethod": "POST"
                }
            })
        
        # Add action nodes based on keywords
        if "slack" in desc_lower:
            nodes.append({
                "name": "Slack",
                "type": "n8n-nodes-base.slack",
                "parameters": {
                    "channel": "#general",
                    "text": "=Notification: {{$json[\"message\"]}}"
                }
            })
        
        if "email" in desc_lower and "send" in desc_lower:
            nodes.append({
                "name": "Send Email",
                "type": "n8n-nodes-base.emailSend",
                "parameters": {
                    "toEmail": "user@example.com",
                    "subject": "Notification",
                    "text": "={{$json[\"message\"]}}"
                }
            })
        
        if "http" in desc_lower or "api" in desc_lower:
            nodes.append({
                "name": "HTTP Request",
                "type": "n8n-nodes-base.httpRequest",
                "parameters": {
                    "method": "POST",
                    "url": "https://api.example.com/endpoint"
                }
            })
        
        if "database" in desc_lower or "postgres" in desc_lower:
            nodes.append({
                "name": "Database",
                "type": "n8n-nodes-base.postgres",
                "parameters": {
                    "operation": "insert",
                    "table": "data"
                }
            })
        
        # If no specific actions, add a generic HTTP request
        if len(nodes) == 1:
            nodes.append({
                "name": "Process Data",
                "type": "n8n-nodes-base.set",
                "parameters": {
                    "values": {
                        "string": [
                            {"name": "result", "value": "={{$json[\"data\"]}}"}
                        ]
                    }
                }
            })
        
        # Build workflow
        workflow = {
            "name": f"Generated: {description[:50]}",
            "nodes": [],
            "connections": {},
            "active": False,
            "settings": {},
            "id": str(uuid.uuid4())
        }
        
        # Add nodes with IDs and positions
        for i, node_template in enumerate(nodes):
            node = {
                "id": str(uuid.uuid4()),
                "name": node_template["name"],
                "type": node_template["type"],
                "typeVersion": 1,
                "position": [250, 300 + (i * 100)],
                "parameters": node_template["parameters"]
            }
            workflow["nodes"].append(node)
        
        # Create connections
        for i in range(len(workflow["nodes"]) - 1):
            source_node = workflow["nodes"][i]["name"]
            target_node = workflow["nodes"][i + 1]["name"]
            
            workflow["connections"][source_node] = {
                "main": [[{"node": target_node, "type": "main", "index": 0}]]
            }
        
        return workflow
    
    def get_available_templates(self) -> List[Dict[str, str]]:
        """Get list of available templates"""
        return [
            {
                "id": name,
                "name": template["name"],
                "description": template["description"],
                "node_count": len(template["nodes"])
            }
            for name, template in self.templates.items()
        ]

# Global instance
simple_generator = SimpleWorkflowGenerator()


