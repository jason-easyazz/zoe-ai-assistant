"""
Quick Authentication for Touch Panels
Optimized for fast user switching and offline operation
"""

import asyncio
import aiohttp
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import logging

from .cache import TouchPanelCache, cache_manager
from ..core.sessions import AuthenticationRequest, SessionType, AuthMethod
from ..models.database import auth_db

logger = logging.getLogger(__name__)

@dataclass
class QuickAuthResult:
    """Result of quick authentication"""
    success: bool
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    username: Optional[str] = None
    permissions: List[str] = None
    session_expires: Optional[datetime] = None
    offline_mode: bool = False
    error_message: Optional[str] = None

@dataclass
class TouchPanelConfig:
    """Configuration for touch panel"""
    device_id: str
    location: str
    allowed_auth_methods: List[str] = None
    offline_enabled: bool = True
    auto_sync_interval: int = 300  # 5 minutes
    server_timeout: int = 10  # seconds
    max_offline_duration: int = 3600  # 1 hour

class QuickAuthManager:
    """Manages quick authentication for touch panels"""
    
    def __init__(self, config: TouchPanelConfig):
        self.config = config
        self.cache = cache_manager.get_cache(config.device_id)
        self.server_url = "http://zoe-core:8000"  # Central auth server
        self.offline_mode = False
        self.last_server_contact = datetime.now()
        
        # Start background sync if enabled
        if config.offline_enabled:
            asyncio.create_task(self._start_sync_loop())

    async def authenticate_passcode(self, username: str, passcode: str, 
                                  device_info: Dict[str, Any] = None) -> QuickAuthResult:
        """
        Quick passcode authentication with fallback to offline
        
        Args:
            username: Username
            passcode: Passcode (4-8 digits)
            device_info: Device information
            
        Returns:
            QuickAuthResult with authentication details
        """
        device_info = device_info or {"type": "touch_panel", "device_id": self.config.device_id}
        
        try:
            # Try server authentication first
            if not self.offline_mode:
                server_result = await self._authenticate_with_server(username, passcode, device_info)
                if server_result.success:
                    # Cache successful auth for offline use
                    self.cache.cache_session(
                        server_result.session_id,
                        server_result.user_id,
                        server_result.permissions or []
                    )
                    return server_result
                
                # If server is unreachable, switch to offline mode
                if "connection" in (server_result.error_message or "").lower():
                    self.offline_mode = True
                    logger.warning(f"Touch panel {self.config.device_id} switching to offline mode")

        except Exception as e:
            logger.error(f"Server authentication error: {e}")
            self.offline_mode = True

        # Fallback to offline authentication
        if self.config.offline_enabled and self.offline_mode:
            return await self._authenticate_offline(username, passcode, device_info)
        
        return QuickAuthResult(
            success=False,
            error_message="Authentication server unavailable and offline mode disabled"
        )

    async def quick_user_switch(self, current_session_id: str, new_username: str, 
                              new_passcode: str) -> QuickAuthResult:
        """
        Quick user switching for touch panels
        
        Args:
            current_session_id: Current session to invalidate
            new_username: New user's username
            new_passcode: New user's passcode
            
        Returns:
            QuickAuthResult for new user
        """
        try:
            # Invalidate current session
            if current_session_id:
                await self._invalidate_session(current_session_id)

            # Authenticate new user
            return await self.authenticate_passcode(new_username, new_passcode)

        except Exception as e:
            logger.error(f"Quick user switch error: {e}")
            return QuickAuthResult(
                success=False,
                error_message="User switch failed"
            )

    async def validate_session(self, session_id: str) -> QuickAuthResult:
        """
        Validate existing session (with offline support)
        
        Args:
            session_id: Session to validate
            
        Returns:
            QuickAuthResult with session validation result
        """
        try:
            # Check server first if online
            if not self.offline_mode:
                server_result = await self._validate_session_with_server(session_id)
                if server_result.success:
                    return server_result

            # Check local cache
            cached_session = self.cache.get_cached_session(session_id)
            if cached_session:
                return QuickAuthResult(
                    success=True,
                    user_id=cached_session.user_id,
                    session_id=cached_session.session_id,
                    permissions=cached_session.permissions,
                    session_expires=cached_session.expires_at,
                    offline_mode=True
                )

            return QuickAuthResult(
                success=False,
                error_message="Session not found or expired"
            )

        except Exception as e:
            logger.error(f"Session validation error: {e}")
            return QuickAuthResult(
                success=False,
                error_message="Session validation failed"
            )

    async def get_cached_users(self) -> List[Dict[str, Any]]:
        """Get list of cached users for offline display"""
        try:
            users = []
            with self.cache.lock:
                import sqlite3
                with sqlite3.connect(self.cache.db_path) as conn:
                    cursor = conn.execute("""
                        SELECT user_id, username, role, last_sync
                        FROM cached_users 
                        WHERE expires_at > ? AND passcode_hash IS NOT NULL
                        ORDER BY last_sync DESC
                    """, (datetime.now().isoformat(),))

                    for row in cursor.fetchall():
                        users.append({
                            "user_id": row[0],
                            "username": row[1],
                            "role": row[2],
                            "last_sync": row[3],
                            "has_passcode": True
                        })

            return users

        except Exception as e:
            logger.error(f"Failed to get cached users: {e}")
            return []

    async def sync_with_server(self) -> bool:
        """Manually trigger sync with server"""
        try:
            if self.offline_mode:
                # Try to reconnect
                if await self._check_server_connectivity():
                    self.offline_mode = False
                    logger.info(f"Touch panel {self.config.device_id} back online")

            if not self.offline_mode:
                return await self._sync_cache_from_server()

            return False

        except Exception as e:
            logger.error(f"Manual sync error: {e}")
            return False

    async def get_device_status(self) -> Dict[str, Any]:
        """Get device status and statistics"""
        try:
            cache_stats = self.cache.get_cache_stats()
            
            status = {
                "device_id": self.config.device_id,
                "location": self.config.location,
                "offline_mode": self.offline_mode,
                "offline_enabled": self.config.offline_enabled,
                "last_server_contact": self.last_server_contact.isoformat(),
                "cache_stats": cache_stats,
                "server_reachable": not self.offline_mode
            }

            # Check if sync is needed
            if cache_stats.get("sync_stale", True):
                status["sync_needed"] = True

            return status

        except Exception as e:
            logger.error(f"Failed to get device status: {e}")
            return {"error": str(e)}

    async def _authenticate_with_server(self, username: str, passcode: str, 
                                      device_info: Dict[str, Any]) -> QuickAuthResult:
        """Authenticate with central server"""
        try:
            auth_data = {
                "username": username,
                "passcode": passcode,
                "device_info": device_info
            }

            timeout = aiohttp.ClientTimeout(total=self.config.server_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.server_url}/api/auth/login/passcode",
                    json=auth_data
                ) as response:
                    
                    self.last_server_contact = datetime.now()
                    
                    if response.status == 200:
                        result = await response.json()
                        if result.get("success"):
                            return QuickAuthResult(
                                success=True,
                                user_id=result.get("user_info", {}).get("user_id"),
                                session_id=result.get("session_id"),
                                username=username,
                                permissions=result.get("user_info", {}).get("permissions", []),
                                session_expires=datetime.fromisoformat(result.get("expires_at", "")),
                                offline_mode=False
                            )
                        else:
                            return QuickAuthResult(
                                success=False,
                                error_message=result.get("error_message", "Authentication failed")
                            )
                    else:
                        error_data = await response.json()
                        return QuickAuthResult(
                            success=False,
                            error_message=error_data.get("detail", "Server error")
                        )

        except asyncio.TimeoutError:
            return QuickAuthResult(
                success=False,
                error_message="Connection timeout - server unreachable"
            )
        except Exception as e:
            return QuickAuthResult(
                success=False,
                error_message=f"Connection error: {str(e)}"
            )

    async def _authenticate_offline(self, username: str, passcode: str, 
                                  device_info: Dict[str, Any]) -> QuickAuthResult:
        """Authenticate using cached data"""
        try:
            success, cached_user = self.cache.verify_passcode_offline(username, passcode)
            
            if success and cached_user:
                # Create temporary session ID for offline use
                import secrets
                session_id = f"offline_{secrets.token_hex(16)}"
                
                # Cache the offline session
                self.cache.cache_session(session_id, cached_user.user_id, cached_user.permissions)
                
                return QuickAuthResult(
                    success=True,
                    user_id=cached_user.user_id,
                    session_id=session_id,
                    username=cached_user.username,
                    permissions=cached_user.permissions,
                    session_expires=datetime.now() + timedelta(hours=1),  # Short offline session
                    offline_mode=True
                )
            else:
                return QuickAuthResult(
                    success=False,
                    error_message="Invalid credentials or user not cached",
                    offline_mode=True
                )

        except Exception as e:
            logger.error(f"Offline authentication error: {e}")
            return QuickAuthResult(
                success=False,
                error_message="Offline authentication failed",
                offline_mode=True
            )

    async def _validate_session_with_server(self, session_id: str) -> QuickAuthResult:
        """Validate session with server"""
        try:
            timeout = aiohttp.ClientTimeout(total=self.config.server_timeout)
            headers = {"X-Session-ID": session_id}
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self.server_url}/api/auth/user",
                    headers=headers
                ) as response:
                    
                    self.last_server_contact = datetime.now()
                    
                    if response.status == 200:
                        user_data = await response.json()
                        return QuickAuthResult(
                            success=True,
                            user_id=user_data.get("user_id"),
                            session_id=session_id,
                            username=user_data.get("username"),
                            permissions=user_data.get("permissions", []),
                            offline_mode=False
                        )
                    else:
                        return QuickAuthResult(
                            success=False,
                            error_message="Session invalid or expired"
                        )

        except Exception as e:
            logger.error(f"Server session validation error: {e}")
            return QuickAuthResult(
                success=False,
                error_message="Server validation failed"
            )

    async def _invalidate_session(self, session_id: str):
        """Invalidate session on server and locally"""
        try:
            # Try server invalidation
            if not self.offline_mode:
                headers = {"X-Session-ID": session_id}
                timeout = aiohttp.ClientTimeout(total=self.config.server_timeout)
                
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        f"{self.server_url}/api/auth/logout",
                        headers=headers
                    ) as response:
                        pass  # Don't care about response for logout

            # Remove from local cache
            with self.cache.lock:
                import sqlite3
                with sqlite3.connect(self.cache.db_path) as conn:
                    conn.execute("DELETE FROM cached_sessions WHERE session_id = ?", (session_id,))

        except Exception as e:
            logger.error(f"Session invalidation error: {e}")

    async def _sync_cache_from_server(self) -> bool:
        """Sync cache with server data"""
        try:
            timeout = aiohttp.ClientTimeout(total=30)  # Longer timeout for sync
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self.server_url}/api/admin/sync-data",
                    params={"device_id": self.config.device_id}
                ) as response:
                    
                    if response.status == 200:
                        sync_data = await response.json()
                        success = self.cache.sync_from_server(sync_data)
                        if success:
                            logger.info(f"Successfully synced cache for device {self.config.device_id}")
                        return success
                    else:
                        logger.error(f"Sync failed with status {response.status}")
                        return False

        except Exception as e:
            logger.error(f"Cache sync error: {e}")
            return False

    async def _check_server_connectivity(self) -> bool:
        """Check if server is reachable"""
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.server_url}/health") as response:
                    return response.status == 200

        except Exception:
            return False

    async def _start_sync_loop(self):
        """Background sync loop"""
        while True:
            try:
                await asyncio.sleep(self.config.auto_sync_interval)
                
                # Check if we need to sync
                if self.cache.is_sync_stale():
                    await self.sync_with_server()
                
                # Cleanup expired cache entries
                self.cache.cleanup_expired_cache()
                
                # Check if we've been offline too long
                if (self.offline_mode and 
                    datetime.now() - self.last_server_contact > timedelta(seconds=self.config.max_offline_duration)):
                    # Try to reconnect
                    if await self._check_server_connectivity():
                        self.offline_mode = False
                        logger.info(f"Touch panel {self.config.device_id} reconnected after extended offline period")

            except Exception as e:
                logger.error(f"Sync loop error: {e}")

# Global quick auth managers for different devices
_auth_managers: Dict[str, QuickAuthManager] = {}

def get_quick_auth_manager(device_id: str, location: str = "unknown") -> QuickAuthManager:
    """Get or create quick auth manager for device"""
    global _auth_managers
    
    if device_id not in _auth_managers:
        config = TouchPanelConfig(
            device_id=device_id,
            location=location,
            allowed_auth_methods=["passcode"],
            offline_enabled=True
        )
        _auth_managers[device_id] = QuickAuthManager(config)
    
    return _auth_managers[device_id]

