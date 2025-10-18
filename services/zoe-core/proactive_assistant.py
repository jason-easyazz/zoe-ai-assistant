"""
Proactive Assistant Background Service (Phase 6C)
Monitors context and offers proactive assistance based on learned patterns
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any

from predictive_intelligence import predictive_intelligence

logger = logging.getLogger(__name__)


class ProactiveAssistant:
    """Background service for proactive assistance"""
    
    def __init__(self):
        self.enabled = True
        self.check_interval_minutes = 60  # Check every hour
        self.active_users = set()
    
    async def start(self):
        """Start proactive assistance background loop"""
        logger.info("🤖 Proactive Assistant started")
        
        while self.enabled:
            try:
                await asyncio.sleep(self.check_interval_minutes * 60)
                
                if self.active_users:
                    await self._check_proactive_opportunities()
                    
            except Exception as e:
                logger.error(f"Proactive assistant error: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error
    
    async def _check_proactive_opportunities(self):
        """Check if any users could benefit from proactive assistance"""
        for user_id in list(self.active_users):
            try:
                suggestions = await predictive_intelligence.enable_proactive_support(user_id)
                
                if suggestions.get('proactive_suggestions'):
                    logger.info(f"💡 Proactive suggestions for {user_id}: {len(suggestions['proactive_suggestions'])}")
                    # Would send notification here
                    
            except Exception as e:
                logger.error(f"Error checking proactive for {user_id}: {e}")
    
    def register_active_user(self, user_id: str):
        """Register user as active for proactive assistance"""
        self.active_users.add(user_id)
    
    def unregister_user(self, user_id: str):
        """Unregister user from proactive assistance"""
        self.active_users.discard(user_id)
    
    def stop(self):
        """Stop proactive assistance"""
        self.enabled = False
        logger.info("🛑 Proactive Assistant stopped")


# Global instance (started on demand)
proactive_assistant = ProactiveAssistant()



