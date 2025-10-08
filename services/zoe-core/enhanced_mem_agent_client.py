"""
Enhanced MEM Agent Client
=========================

Updated client to work with the Enhanced MEM Agent service
that includes Multi-Expert Model with action execution capabilities.
"""

import aiohttp
import asyncio
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class EnhancedMemAgentClient:
    """
    Enhanced client for the Multi-Expert Model MEM Agent
    Provides both memory search AND action execution capabilities
    """
    
    def __init__(self, base_url: str = "http://mem-agent:11435"):
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
        self.connector = None
        self.enabled = True
        self.failure_count = 0
        self.max_failures = 3
    
    async def initialize(self):
        """Initialize persistent session with connection pooling"""
        if not self.session or self.session.closed:
            if not self.connector:
                self.connector = aiohttp.TCPConnector(
                    limit=10,
                    limit_per_host=5,
                    ttl_dns_cache=300,
                    keepalive_timeout=30
                )
            
            self.session = aiohttp.ClientSession(
                connector=self.connector,
                timeout=aiohttp.ClientTimeout(total=5.0),  # Longer timeout for actions
                headers={"User-Agent": "Zoe-Core-Enhanced/2.0"}
            )
            logger.info("✅ Enhanced MEM Agent client initialized")
    
    async def close(self):
        """Cleanup session"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("Enhanced MEM Agent client closed")
    
    async def enhanced_search(
        self, 
        query: str, 
        user_id: str, 
        max_results: int = 5,
        execute_actions: bool = True
    ) -> Dict:
        """
        Enhanced search with action execution capabilities
        
        Returns:
            {
                "experts": [...],
                "primary_expert": "list",
                "actions_executed": 2,
                "total_confidence": 0.9,
                "execution_summary": "✅ 2 actions executed by list, calendar experts"
            }
        """
        
        if not self.enabled:
            return {"error": "Enhanced MEM Agent disabled", "fallback": True}
        
        if not self.session:
            await self.initialize()
        
        try:
            payload = {
                "query": query,
                "user_id": user_id,
                "max_results": max_results,
                "include_graph": True,
                "execute_actions": execute_actions
            }
            
            async with self.session.post(
                f"{self.base_url}/search",
                json=payload
            ) as response:
                if response.status == 200:
                    self.failure_count = 0  # Reset on success
                    data = await response.json()
                    
                    # Format response for compatibility with existing chat system
                    extracted_results = self._extract_results(data)
                    return {
                        "enhanced": True,
                        "experts": data.get("experts", []),
                        "primary_expert": data.get("primary_expert", "memory"),
                        "actions_executed": data.get("actions_executed", 0),
                        "total_confidence": data.get("total_confidence", 0.5),
                        "execution_summary": data.get("execution_summary", ""),
                        "fallback": False,
                        # Legacy format for compatibility
                        "results": extracted_results,
                        "semantic_results": extracted_results,  # Add semantic_results for chat.py compatibility
                        "confidence": data.get("total_confidence", 0.5)
                    }
                else:
                    raise Exception(f"Enhanced MEM Agent returned {response.status}")
                    
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            self.failure_count += 1
            logger.warning(f"Enhanced MEM Agent request failed: {e}")
            
            if self.failure_count >= self.max_failures:
                self.enabled = False
                logger.error(f"Enhanced MEM Agent disabled after {self.max_failures} failures")
            
            return {"error": str(e), "fallback": True}
    
    async def call_expert_directly(
        self, 
        expert_name: str, 
        query: str, 
        user_id: str
    ) -> Dict:
        """Call a specific expert directly"""
        if not self.session:
            await self.initialize()
        
        try:
            payload = {
                "query": query,
                "user_id": user_id,
                "max_results": 5,
                "include_graph": False,
                "execute_actions": True
            }
            
            async with self.session.post(
                f"{self.base_url}/experts/{expert_name}",
                json=payload
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    raise Exception(f"Expert {expert_name} returned {response.status}")
                    
        except Exception as e:
            logger.error(f"Expert {expert_name} call failed: {e}")
            return {"error": str(e), "expert": expert_name}
    
    def _extract_results(self, data: Dict) -> List[Dict]:
        """Extract results in legacy format for compatibility"""
        results = []
        
        for expert_data in data.get("experts", []):
            if expert_data.get("action_taken") and expert_data.get("result", {}).get("success"):
                result = expert_data["result"]
                results.append({
                    "entity": expert_data["expert"],
                    "content": result.get("message", ""),
                    "score": expert_data.get("confidence", 0.5),
                    "action": result.get("action", ""),
                    "success": result.get("success", False)
                })
        
        return results
    
    async def health_check(self) -> bool:
        """Check if Enhanced MEM Agent is responsive"""
        if not self.session:
            await self.initialize()
        
        try:
            async with self.session.get(
                f"{self.base_url}/health",
                timeout=aiohttp.ClientTimeout(total=2.0)
            ) as response:
                is_healthy = response.status == 200
                if is_healthy and not self.enabled:
                    # Re-enable if health check passes
                    self.enabled = True
                    self.failure_count = 0
                    logger.info("Enhanced MEM Agent re-enabled after successful health check")
                return is_healthy
        except Exception as e:
            logger.debug(f"Enhanced MEM Agent health check failed: {e}")
            return False
    
    def get_status(self) -> Dict:
        """Get client status"""
        return {
            "enabled": self.enabled,
            "failure_count": self.failure_count,
            "session_active": self.session is not None and not self.session.closed,
            "enhanced": True
        }

# Global instance
enhanced_mem_agent_client = EnhancedMemAgentClient()

# Helper function for enhanced search with fallback
async def enhanced_search_with_fallback(
    query: str, 
    user_id: str,
    execute_actions: bool = True,
    fallback_func=None
) -> Dict:
    """
    Enhanced search with automatic fallback
    
    Args:
        query: Search query
        user_id: User ID
        execute_actions: Whether to execute actions
        fallback_func: Function to call if enhanced MEM agent fails
    
    Returns:
        {
            "method": "enhanced-mem-agent" or "fallback",
            "experts": [...],
            "actions_executed": int,
            "execution_summary": str,
            "confidence": float
        }
    """
    
    # Try Enhanced MEM Agent first
    result = await enhanced_mem_agent_client.enhanced_search(
        query, user_id, execute_actions=execute_actions
    )
    
    if not result.get("fallback"):
        # Success - format results
        return {
            "method": "enhanced-mem-agent",
            "experts": result.get("experts", []),
            "actions_executed": result.get("actions_executed", 0),
            "execution_summary": result.get("execution_summary", ""),
            "confidence": result.get("confidence", 0.5),
            "primary_expert": result.get("primary_expert", "memory"),
            "enhanced": True
        }
    
    # Fallback to basic search or provided function
    logger.info(f"Enhanced MEM Agent unavailable, using fallback for user {user_id}")
    
    if fallback_func:
        fallback_results = await fallback_func(query, user_id)
    else:
        # Basic fallback
        fallback_results = {
            "experts": [],
            "actions_executed": 0,
            "execution_summary": "No actions executed (fallback mode)",
            "confidence": 0.3
        }
    
    return {
        "method": "fallback",
        "experts": fallback_results.get("experts", []),
        "actions_executed": 0,
        "execution_summary": "No actions executed (fallback mode)",
        "confidence": 0.3,
        "enhanced": False
    }
