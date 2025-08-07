"""
Zoe v3.1 Home Assistant Integration
Smart home device control and context awareness
"""

import aiohttp
import asyncio
import logging
import os
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class HomeAssistantService:
    def __init__(self):
        self.ha_url = os.getenv('HA_URL', 'http://zoe-homeassistant:8123')
        self.enabled = os.getenv('HA_ENABLED', 'true').lower() == 'true'
        self.token = None  # Set via settings later
        
    def set_token(self, token: str):
        """Set Home Assistant API token"""
        self.token = token
    
    async def get_states(self) -> List[Dict]:
        """Get all entity states from Home Assistant"""
        if not self.enabled or not self.token:
            return []
            
        try:
            headers = {'Authorization': f'Bearer {self.token}'}
            async with aiohttp.ClientSession() as session:
                async with session.get(f'{self.ha_url}/api/states', headers=headers) as response:
                    if response.status == 200:
                        return await response.json()
                    return []
        except Exception as e:
            logger.error(f"HA states error: {e}")
            return []
    
    async def call_service(self, domain: str, service: str, entity_id: str = None, **kwargs) -> bool:
        """Call Home Assistant service"""
        if not self.enabled or not self.token:
            return False
            
        try:
            headers = {'Authorization': f'Bearer {self.token}'}
            data = {'entity_id': entity_id} if entity_id else {}
            data.update(kwargs)
            
            async with aiohttp.ClientSession() as session:
                url = f'{self.ha_url}/api/services/{domain}/{service}'
                async with session.post(url, headers=headers, json=data) as response:
                    return response.status < 400
                    
        except Exception as e:
            logger.error(f"HA service call error: {e}")
            return False
    
    async def get_personality_settings(self) -> Dict[str, float]:
        """Get Zoe personality settings from HA sliders"""
        states = await self.get_states()
        
        settings = {}
        for state in states:
            entity_id = state.get('entity_id', '')
            if entity_id.startswith('input_number.zoe_'):
                setting_name = entity_id.replace('input_number.zoe_', '').replace('_level', '')
                settings[setting_name] = float(state.get('state', 5))
                
        return settings
    
    async def health_check(self) -> Dict[str, Any]:
        """Check Home Assistant health"""
        if not self.enabled:
            return {'status': 'disabled'}
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f'{self.ha_url}/api/') as response:
                    return {'status': 'healthy' if response.status == 200 else 'unhealthy'}
        except:
            return {'status': 'offline'}

# Global instance
ha_service = HomeAssistantService()
