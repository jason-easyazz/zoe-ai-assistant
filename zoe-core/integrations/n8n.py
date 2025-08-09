"""
Zoe v3.1 n8n Automation Integration
Workflow automation and background processing
"""

import aiohttp
import asyncio
import json
import logging
import os
from typing import Optional, Dict, Any, List

import httpx

logger = logging.getLogger(__name__)

class N8NService:
    def __init__(self):
        self.n8n_url = os.getenv('N8N_URL', 'http://zoe-n8n:5678')
        self.enabled = os.getenv('N8N_ENABLED', 'true').lower() == 'true'
        self.api_key = os.getenv('N8N_API_KEY', '')
        self.ollama_url = os.getenv('OLLAMA_URL', 'http://zoe-ollama:11434')
        
    async def trigger_workflow(self, workflow_id: str, data: Dict[str, Any]) -> Optional[Dict]:
        """Trigger an n8n workflow with data"""
        if not self.enabled:
            return None
            
        try:
            webhook_url = f'{self.n8n_url}/webhook/{workflow_id}'
            
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=data) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"n8n workflow trigger failed: {response.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"n8n trigger error: {e}")
            return None
    
    async def get_workflow_status(self) -> List[Dict]:
        """Get status of all active workflows"""
        if not self.enabled:
            return []
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f'{self.n8n_url}/rest/workflows') as response:
                    if response.status == 200:
                        return await response.json()
                    return []
        except:
            return []

    async def create_workflow_from_prompt(self, prompt: str) -> Dict[str, Any]:
        """Generate and deploy a workflow from natural language prompt"""
        if not self.enabled:
            raise RuntimeError("n8n integration disabled")

        llm_prompt = (
            "Convert the following request into a valid n8n workflow JSON. "
            "Return only JSON.\n" + prompt
        )

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                llm_resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": "mistral:7b",
                        "prompt": llm_prompt,
                        "stream": False,
                    },
                )
            llm_data = llm_resp.json()
            workflow_text = llm_data.get("response", "").strip()
            workflow = json.loads(workflow_text)
        except Exception as e:
            logger.error(f"Workflow generation failed: {e}")
            raise

        if not isinstance(workflow, dict) or "name" not in workflow:
            raise ValueError("Invalid workflow JSON returned by LLM")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                n8n_resp = await client.post(
                    f"{self.n8n_url}/rest/workflows",
                    headers=headers,
                    json=workflow,
                )
            status = n8n_resp.status_code
            logger.info(
                f"Workflow '{workflow.get('name')}' creation status: {status}"
            )
            n8n_resp.raise_for_status()
            return n8n_resp.json()
        except Exception as e:
            logger.error(f"n8n workflow creation error: {e}")
            raise
    
    async def process_task_reminder(self, task_data: Dict[str, Any]) -> None:
        """Process task reminder automation"""
        await self.trigger_workflow('task-reminder', {
            'task': task_data,
            'timestamp': asyncio.get_event_loop().time(),
            'source': 'zoe-core'
        })
    
    async def process_daily_agenda(self, agenda_data: Dict[str, Any]) -> None:
        """Process daily agenda automation"""
        await self.trigger_workflow('daily-agenda', {
            'agenda': agenda_data,
            'date': agenda_data.get('date'),
            'source': 'zoe-core'
        })
    
    async def health_check(self) -> Dict[str, Any]:
        """Check n8n service health"""
        if not self.enabled:
            return {'status': 'disabled'}
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f'{self.n8n_url}/healthz') as response:
                    return {'status': 'healthy' if response.status == 200 else 'unhealthy'}
        except:
            return {'status': 'offline'}

# Global instance
n8n_service = N8NService()
