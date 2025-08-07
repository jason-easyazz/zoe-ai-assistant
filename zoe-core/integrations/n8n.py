"""
Zoe v3.1 n8n Automation Integration
Workflow automation and background processing
"""

import aiohttp
import asyncio
import logging
import os
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class N8NService:
    def __init__(self):
        self.n8n_url = os.getenv('N8N_URL', 'http://zoe-n8n:5678')
        self.enabled = os.getenv('N8N_ENABLED', 'true').lower() == 'true'
        
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
