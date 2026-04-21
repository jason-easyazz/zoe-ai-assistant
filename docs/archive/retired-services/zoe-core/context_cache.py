"""
Context Caching Module for Zoe
Implements aggressive caching for MCP tools, user context, and system prompts
"""
import json
import logging
import hashlib
import os
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import asyncio

logger = logging.getLogger(__name__)

# Try to import Redis, fallback to in-memory cache if not available
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available, using in-memory cache")

# In-memory cache fallback
_memory_cache: Dict[str, Dict[str, Any]] = {}
_cache_locks: Dict[str, asyncio.Lock] = {}

class ContextCache:
    """Caching layer for context data with TTL support"""
    
    def __init__(self):
        self.redis_client: Optional[redis.Redis] = None
        self.enabled = True
        
        if REDIS_AVAILABLE:
            try:
                redis_host = os.getenv("ZOE_REDIS_HOST", "zoe-redis")
                redis_port = int(os.getenv("ZOE_REDIS_PORT", "6379"))
                self.redis_client = redis.Redis(
                    host=redis_host,
                    port=redis_port,
                    decode_responses=True,
                    socket_connect_timeout=1.0,
                    socket_timeout=1.0
                )
                logger.info("✅ Redis cache initialized")
            except Exception as e:
                logger.warning(f"Redis connection failed, using in-memory cache: {e}")
                self.redis_client = None
        else:
            logger.info("Using in-memory cache (Redis not available)")
    
    def _get_cache_key(self, cache_type: str, identifier: str) -> str:
        """Generate cache key"""
        return f"zoe:cache:{cache_type}:{identifier}"
    
    async def get(self, cache_type: str, identifier: str) -> Optional[Any]:
        """Get cached value"""
        if not self.enabled:
            return None
        
        cache_key = self._get_cache_key(cache_type, identifier)
        
        try:
            if self.redis_client:
                # Try Redis first
                try:
                    cached = await self.redis_client.get(cache_key)
                    if cached:
                        data = json.loads(cached)
                        # Check TTL
                        if data.get("expires_at"):
                            expires = datetime.fromisoformat(data["expires_at"])
                            if datetime.now() < expires:
                                logger.debug(f"✅ Cache hit: {cache_type}:{identifier[:20]}")
                                return data.get("value")
                            else:
                                # Expired, delete it
                                await self.redis_client.delete(cache_key)
                                return None
                        return data.get("value")
                except Exception as e:
                    logger.debug(f"Redis get failed: {e}, trying memory cache")
            
            # Fallback to memory cache
            if cache_key in _memory_cache:
                data = _memory_cache[cache_key]
                if data.get("expires_at"):
                    expires = datetime.fromisoformat(data["expires_at"])
                    if datetime.now() < expires:
                        logger.debug(f"✅ Memory cache hit: {cache_type}:{identifier[:20]}")
                        return data.get("value")
                    else:
                        del _memory_cache[cache_key]
                        return None
                return data.get("value")
            
            return None
        except Exception as e:
            logger.warning(f"Cache get error: {e}")
            return None
    
    async def set(self, cache_type: str, identifier: str, value: Any, ttl_seconds: int):
        """Set cached value with TTL"""
        if not self.enabled:
            return
        
        cache_key = self._get_cache_key(cache_type, identifier)
        expires_at = (datetime.now() + timedelta(seconds=ttl_seconds)).isoformat()
        
        cache_data = {
            "value": value,
            "expires_at": expires_at,
            "cached_at": datetime.now().isoformat()
        }
        
        try:
            if self.redis_client:
                # Try Redis first
                try:
                    await self.redis_client.setex(
                        cache_key,
                        ttl_seconds,
                        json.dumps(cache_data)
                    )
                    logger.debug(f"✅ Cached in Redis: {cache_type}:{identifier[:20]} (TTL: {ttl_seconds}s)")
                    return
                except Exception as e:
                    logger.debug(f"Redis set failed: {e}, using memory cache")
            
            # Fallback to memory cache
            _memory_cache[cache_key] = cache_data
            logger.debug(f"✅ Cached in memory: {cache_type}:{identifier[:20]} (TTL: {ttl_seconds}s)")
        except Exception as e:
            logger.warning(f"Cache set error: {e}")
    
    async def delete(self, cache_type: str, identifier: str):
        """Delete cached value"""
        cache_key = self._get_cache_key(cache_type, identifier)
        
        try:
            if self.redis_client:
                await self.redis_client.delete(cache_key)
            if cache_key in _memory_cache:
                del _memory_cache[cache_key]
        except Exception as e:
            logger.warning(f"Cache delete error: {e}")
    
    async def clear_type(self, cache_type: str):
        """Clear all cache entries of a specific type"""
        pattern = f"zoe:cache:{cache_type}:*"
        
        try:
            if self.redis_client:
                # Redis pattern delete
                keys = await self.redis_client.keys(pattern)
                if keys:
                    await self.redis_client.delete(*keys)
            
            # Memory cache cleanup
            keys_to_delete = [k for k in _memory_cache.keys() if k.startswith(f"zoe:cache:{cache_type}:")]
            for key in keys_to_delete:
                del _memory_cache[key]
        except Exception as e:
            logger.warning(f"Cache clear error: {e}")

# Global cache instance
context_cache = ContextCache()

# Cache configuration
CACHE_TTL = {
    "mcp_tools": 300,  # 5 minutes - tools rarely change
    "user_context": 30,  # 30 seconds - user data changes frequently
    "system_prompt": 3600,  # 1 hour - prompts are stable per routing type
    "memory_search": 10,  # 10 seconds - memory search results
    "routing_decision": 60,  # 1 minute - routing decisions
}
