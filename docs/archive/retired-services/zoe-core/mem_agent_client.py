"""
Phase 4: mem-agent Connection Pool Client
Persistent HTTP client for mem-agent semantic search
"""
import aiohttp
import asyncio
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class MemAgentClient:
    """
    Persistent connection pool client for mem-agent
    Provides semantic memory search with fallback
    """
    
    def __init__(self, base_url: str = "http://zoe-mem-agent:8000"):
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
        self.connector = None
        self.enabled = True
        self.failure_count = 0
        self.max_failures = 3
    
    async def initialize(self):
        """Initialize persistent session with connection pooling"""
        if not self.session or self.session.closed:
            # Create connector inside async context
            if not self.connector:
                self.connector = aiohttp.TCPConnector(
                    limit=10,
                    limit_per_host=5,
                    ttl_dns_cache=300,
                    keepalive_timeout=30
                )
            
            self.session = aiohttp.ClientSession(
                connector=self.connector,
                timeout=aiohttp.ClientTimeout(total=2.0),
                headers={"User-Agent": "Zoe-Core/1.0"}
            )
            logger.info("✅ mem-agent client initialized")
    
    async def close(self):
        """Cleanup session"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("mem-agent client closed")
    
    async def search(
        self, 
        query: str, 
        user_id: str, 
        max_results: int = 5
    ) -> Dict:
        """
        Search user's memories via mem-agent
        
        Returns:
            {
                "results": [...],
                "graph": {...},
                "confidence": float,
                "fallback": bool
            }
        """
        
        if not self.enabled:
            return {"error": "mem-agent disabled", "fallback": True}
        
        if not self.session:
            await self.initialize()
        
        try:
            payload = {
                "query": query,
                "user_id": user_id,
                "max_results": max_results,
                "include_graph": True
            }
            
            async with self.session.post(
                f"{self.base_url}/search",
                json=payload
            ) as response:
                if response.status == 200:
                    self.failure_count = 0  # Reset on success
                    data = await response.json()
                    return {
                        "results": data.get("results", []),
                        "graph": data.get("graph", {}),
                        "confidence": data.get("confidence", 0.5),
                        "fallback": False
                    }
                else:
                    raise Exception(f"mem-agent returned {response.status}")
                    
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            self.failure_count += 1
            logger.warning(f"mem-agent request failed: {e}")
            
            if self.failure_count >= self.max_failures:
                self.enabled = False
                logger.error(f"mem-agent disabled after {self.max_failures} failures")
            
            return {"error": str(e), "fallback": True}
    
    async def health_check(self) -> bool:
        """Check if mem-agent is responsive"""
        if not self.session:
            await self.initialize()
        
        try:
            async with self.session.get(
                f"{self.base_url}/health",
                timeout=aiohttp.ClientTimeout(total=1.0)
            ) as response:
                is_healthy = response.status == 200
                if is_healthy and not self.enabled:
                    # Re-enable if health check passes
                    self.enabled = True
                    self.failure_count = 0
                    logger.info("mem-agent re-enabled after successful health check")
                return is_healthy
        except Exception as e:
            logger.debug(f"mem-agent health check failed: {e}")
            return False
    
    def get_status(self) -> Dict:
        """Get client status"""
        return {
            "enabled": self.enabled,
            "failure_count": self.failure_count,
            "session_active": self.session is not None and not self.session.closed
        }


# Global instance
mem_agent_client = MemAgentClient()


# FastAPI lifespan management
from contextlib import asynccontextmanager
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage mem-agent client lifecycle"""
    # Startup
    await mem_agent_client.initialize()
    
    # Health check in background
    async def periodic_health_check():
        while True:
            await asyncio.sleep(60)  # Check every minute
            await mem_agent_client.health_check()
    
    health_task = asyncio.create_task(periodic_health_check())
    
    yield
    
    # Shutdown
    health_task.cancel()
    await mem_agent_client.close()


# Helper function for memory search with fallback
async def search_memory_with_fallback(
    query: str, 
    user_id: str,
    fallback_func=None
) -> Dict:
    """
    Search memories with automatic fallback
    
    Args:
        query: Search query
        user_id: User ID
        fallback_func: Function to call if mem-agent fails
    
    Returns:
        {
            "method": "mem-agent" or "fallback",
            "matches": [...],
            "graph": {...},
            "confidence": float
        }
    """
    
    # Try mem-agent first
    result = await mem_agent_client.search(query, user_id)
    
    if not result.get("fallback"):
        # Success - format results
        return {
            "method": "mem-agent",
            "matches": [
                {
                    "entity": r.get("entity"),
                    "excerpt": r.get("content"),
                    "relevance": r.get("score", 0.5),
                    "wikilinks": r.get("related_entities", []),
                    "source_file": r.get("file")
                }
                for r in result["results"]
            ],
            "graph": result.get("graph", {}),
            "confidence": result.get("confidence", 0.5)
        }
    
    # Fallback to SQLite or provided function
    logger.info(f"mem-agent unavailable, using fallback for user {user_id}")
    
    if fallback_func:
        fallback_results = await fallback_func(query, user_id)
    else:
        # Basic SQLite fallback
        from memory_system import MemorySystem
        memory_system = MemorySystem("/app/data/memory.db")
        fallback_results = memory_system.search_memories(query, limit=5)
    
    return {
        "method": "fallback",
        "matches": [
            {
                "entity": r.get("entity_name", ""),
                "excerpt": r.get("fact", ""),
                "relevance": r.get("score", 0.5),
                "wikilinks": [],
                "source_file": None
            }
            for r in fallback_results
        ],
        "graph": {},
        "confidence": 0.5
    }
