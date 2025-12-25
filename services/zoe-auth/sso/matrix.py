"""
Matrix (Synapse/Dendrite) SSO Integration
Authentication provider for Matrix homeserver integration
"""

import json
import aiohttp
import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import logging

from core.auth import auth_manager
from core.sessions import session_manager, AuthenticationRequest, SessionType, AuthMethod
from core.rbac import rbac_manager
from models.database import auth_db

logger = logging.getLogger(__name__)

@dataclass
class MatrixUser:
    """Matrix user representation"""
    user_id: str  # @username:domain
    display_name: str
    avatar_url: Optional[str]
    admin: bool
    deactivated: bool
    password_hash: Optional[str]

class MatrixIntegration:
    """Matrix homeserver SSO integration"""
    
    def __init__(self, matrix_config: Dict[str, Any]):
        self.homeserver_url = matrix_config.get("homeserver_url", "http://matrix:8008")
        self.server_name = matrix_config.get("server_name", "zoe.local")
        self.admin_token = matrix_config.get("admin_token")
        self.sync_interval = matrix_config.get("sync_interval", 600)  # 10 minutes
        
        # Role to Matrix admin mapping
        self.admin_roles = matrix_config.get("admin_roles", ["admin", "super_admin"])
        
        # Room auto-join configuration
        self.auto_join_rooms = matrix_config.get("auto_join_rooms", {
            "admin": ["#admin:zoe.local"],
            "family": ["#family:zoe.local", "#general:zoe.local"],
            "user": ["#general:zoe.local"],
            "child": ["#children:zoe.local"],
            "guest": ["#guests:zoe.local"]
        })

    async def create_matrix_auth_provider(self) -> Dict[str, Any]:
        """
        Create Matrix authentication provider configuration for Synapse
        
        Returns:
            Auth provider configuration
        """
        return {
            "password_providers": [
                {
                    "module": "zoe_auth_provider.ZoeAuthProvider",
                    "config": {
                        "auth_url": "http://zoe-auth:8002/api/sso/matrix/auth",
                        "user_info_url": "http://zoe-auth:8002/api/sso/matrix/user",
                        "server_name": self.server_name,
                        "create_users": True,
                        "update_profile": True
                    }
                }
            ]
        }

    async def authenticate_matrix_user(self, username: str, password: str) -> Tuple[bool, Optional[Dict]]:
        """
        Authenticate user for Matrix
        
        Args:
            username: Username (without @domain)
            password: Password
            
        Returns:
            Tuple of (success, user_data)
        """
        try:
            # Find user by username
            with auth_db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT user_id, email FROM auth_users WHERE username = ? AND is_active = 1",
                    (username,)
                )
                row = cursor.fetchone()
                if not row:
                    return False, None
                
                user_id, email = row

            # Verify password
            auth_result = auth_manager.verify_password(user_id, password)
            if not auth_result.success:
                return False, None

            # Create SSO session
            auth_request = AuthenticationRequest(
                user_id=user_id,
                auth_method=AuthMethod.SSO,
                credentials={"service": "matrix"},
                device_info={"type": "matrix", "service": "matrix"},
                requested_session_type=SessionType.SSO
            )
            
            session_result = session_manager.authenticate(auth_request)
            if not session_result.success:
                return False, None

            # Get user info and map to Matrix format
            user_info = auth_manager.get_user_info(user_id)
            matrix_user = await self._map_user_to_matrix(user_info)
            
            return True, matrix_user

        except Exception as e:
            logger.error(f"Matrix authentication error for {username}: {e}")
            return False, None

    async def sync_user_to_matrix(self, user_id: str) -> bool:
        """
        Sync Zoe user to Matrix homeserver
        
        Args:
            user_id: Zoe user ID
            
        Returns:
            True if successful
        """
        try:
            user_info = auth_manager.get_user_info(user_id)
            if not user_info:
                return False

            matrix_user_id = f"@{user_info['username']}:{self.server_name}"
            
            # Check if user exists in Matrix
            user_exists = await self._matrix_user_exists(matrix_user_id)
            
            if not user_exists:
                # Create new Matrix user
                success = await self._create_matrix_user(user_info)
            else:
                # Update existing Matrix user
                success = await self._update_matrix_user(user_info)
            
            if success:
                # Auto-join rooms based on role
                await self._auto_join_user_rooms(user_info)
                
                logger.info(f"Synced user {user_info['username']} to Matrix")
                
            return success

        except Exception as e:
            logger.error(f"Failed to sync user {user_id} to Matrix: {e}")
            return False

    async def sync_password_change(self, user_id: str) -> bool:
        """
        Sync password change to Matrix
        
        Args:
            user_id: User ID
            
        Returns:
            True if successful
        """
        try:
            user_info = auth_manager.get_user_info(user_id)
            if not user_info:
                return False

            matrix_user_id = f"@{user_info['username']}:{self.server_name}"
            
            # Reset password in Matrix (forces re-auth)
            # Matrix will use external auth provider for subsequent logins
            await self._reset_matrix_password(matrix_user_id)
            
            logger.info(f"Password sync completed for Matrix user {user_info['username']}")
            return True

        except Exception as e:
            logger.error(f"Failed to sync password change for {user_id}: {e}")
            return False

    async def deactivate_matrix_user(self, username: str) -> bool:
        """
        Deactivate Matrix user
        
        Args:
            username: Username to deactivate
            
        Returns:
            True if successful
        """
        try:
            matrix_user_id = f"@{username}:{self.server_name}"
            
            deactivate_data = {
                "deactivated": True,
                "reason": "User deactivated in Zoe system"
            }
            
            success = await self._call_matrix_admin_api(
                "PUT",
                f"/v1/deactivate/{matrix_user_id}",
                deactivate_data
            )
            
            if success:
                logger.info(f"Deactivated Matrix user {username}")
                
            return success

        except Exception as e:
            logger.error(f"Failed to deactivate Matrix user {username}: {e}")
            return False

    async def create_matrix_room(self, room_config: Dict[str, Any]) -> Optional[str]:
        """
        Create Matrix room
        
        Args:
            room_config: Room configuration
            
        Returns:
            Room ID if successful
        """
        try:
            room_data = {
                "name": room_config.get("name"),
                "topic": room_config.get("topic"),
                "preset": room_config.get("preset", "private_chat"),
                "visibility": room_config.get("visibility", "private"),
                "room_alias_name": room_config.get("alias"),
                "invite": room_config.get("invites", [])
            }
            
            result = await self._call_matrix_api("POST", "/createRoom", room_data)
            
            if result and "room_id" in result:
                room_id = result["room_id"]
                logger.info(f"Created Matrix room: {room_id}")
                return room_id
                
            return None

        except Exception as e:
            logger.error(f"Failed to create Matrix room: {e}")
            return None

    async def join_user_to_room(self, user_id: str, room_id: str) -> bool:
        """
        Join user to Matrix room
        
        Args:
            user_id: Zoe user ID
            room_id: Matrix room ID
            
        Returns:
            True if successful
        """
        try:
            user_info = auth_manager.get_user_info(user_id)
            if not user_info:
                return False

            matrix_user_id = f"@{user_info['username']}:{self.server_name}"
            
            # Join user to room
            success = await self._call_matrix_admin_api(
                "POST",
                f"/v1/join/{room_id}",
                {"user_id": matrix_user_id}
            )
            
            if success:
                logger.info(f"Joined user {user_info['username']} to room {room_id}")
                
            return success

        except Exception as e:
            logger.error(f"Failed to join user {user_id} to room {room_id}: {e}")
            return False

    async def get_user_rooms(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get Matrix rooms for user
        
        Args:
            user_id: Zoe user ID
            
        Returns:
            List of room information
        """
        try:
            user_info = auth_manager.get_user_info(user_id)
            if not user_info:
                return []

            matrix_user_id = f"@{user_info['username']}:{self.server_name}"
            
            # Get user's joined rooms
            result = await self._call_matrix_admin_api(
                "GET",
                f"/v1/users/{matrix_user_id}/joined_rooms"
            )
            
            if result and "joined_rooms" in result:
                rooms = []
                for room_id in result["joined_rooms"]:
                    room_info = await self._get_room_info(room_id)
                    if room_info:
                        rooms.append(room_info)
                
                return rooms
                
            return []

        except Exception as e:
            logger.error(f"Failed to get rooms for user {user_id}: {e}")
            return []

    async def _map_user_to_matrix(self, user_info: Dict[str, Any]) -> Dict[str, Any]:
        """Map Zoe user to Matrix user format"""
        matrix_user_id = f"@{user_info['username']}:{self.server_name}"
        
        # Determine if user should be admin
        is_admin = user_info["role"] in self.admin_roles
        
        return {
            "user_id": matrix_user_id,
            "display_name": user_info.get("display_name", user_info["username"]),
            "avatar_url": user_info.get("avatar_url"),
            "admin": is_admin,
            "deactivated": not user_info["is_active"],
            "zoe_user_id": user_info["user_id"],
            "zoe_role": user_info["role"]
        }

    async def _matrix_user_exists(self, matrix_user_id: str) -> bool:
        """Check if user exists in Matrix"""
        try:
            result = await self._call_matrix_admin_api("GET", f"/v2/users/{matrix_user_id}")
            return result is not None
        except Exception:
            return False

    async def _create_matrix_user(self, user_info: Dict[str, Any]) -> bool:
        """Create new Matrix user"""
        try:
            matrix_user_id = f"@{user_info['username']}:{self.server_name}"
            
            user_data = {
                "password": secrets.token_urlsafe(32),  # Random password, will use external auth
                "displayname": user_info.get("display_name", user_info["username"]),
                "threepids": [
                    {
                        "medium": "email",
                        "address": user_info["email"]
                    }
                ],
                "admin": user_info["role"] in self.admin_roles,
                "deactivated": not user_info["is_active"],
                "external_ids": [
                    {
                        "auth_provider": "zoe",
                        "external_id": user_info["user_id"]
                    }
                ]
            }
            
            success = await self._call_matrix_admin_api(
                "PUT",
                f"/v2/users/{matrix_user_id}",
                user_data
            )
            
            return success is not None

        except Exception as e:
            logger.error(f"Failed to create Matrix user: {e}")
            return False

    async def _update_matrix_user(self, user_info: Dict[str, Any]) -> bool:
        """Update existing Matrix user"""
        try:
            matrix_user_id = f"@{user_info['username']}:{self.server_name}"
            
            user_data = {
                "displayname": user_info.get("display_name", user_info["username"]),
                "admin": user_info["role"] in self.admin_roles,
                "deactivated": not user_info["is_active"]
            }
            
            success = await self._call_matrix_admin_api(
                "PUT",
                f"/v2/users/{matrix_user_id}",
                user_data
            )
            
            return success is not None

        except Exception as e:
            logger.error(f"Failed to update Matrix user: {e}")
            return False

    async def _auto_join_user_rooms(self, user_info: Dict[str, Any]):
        """Auto-join user to rooms based on role"""
        try:
            rooms_to_join = self.auto_join_rooms.get(user_info["role"], [])
            matrix_user_id = f"@{user_info['username']}:{self.server_name}"
            
            for room_alias in rooms_to_join:
                try:
                    # Resolve room alias to room ID
                    room_info = await self._resolve_room_alias(room_alias)
                    if room_info and "room_id" in room_info:
                        await self._call_matrix_admin_api(
                            "POST",
                            f"/v1/join/{room_info['room_id']}",
                            {"user_id": matrix_user_id}
                        )
                except Exception as e:
                    logger.error(f"Failed to auto-join {matrix_user_id} to {room_alias}: {e}")

        except Exception as e:
            logger.error(f"Auto-join error: {e}")

    async def _reset_matrix_password(self, matrix_user_id: str):
        """Reset Matrix user password"""
        try:
            # Generate new random password
            new_password = secrets.token_urlsafe(32)
            
            password_data = {"new_password": new_password}
            
            await self._call_matrix_admin_api(
                "POST",
                f"/v1/reset_password/{matrix_user_id}",
                password_data
            )

        except Exception as e:
            logger.error(f"Failed to reset Matrix password: {e}")

    async def _resolve_room_alias(self, room_alias: str) -> Optional[Dict]:
        """Resolve room alias to room ID"""
        try:
            result = await self._call_matrix_api("GET", f"/directory/room/{room_alias}")
            return result
        except Exception:
            return None

    async def _get_room_info(self, room_id: str) -> Optional[Dict]:
        """Get room information"""
        try:
            result = await self._call_matrix_admin_api("GET", f"/v1/rooms/{room_id}")
            return result
        except Exception:
            return None

    async def _call_matrix_api(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Any:
        """Call Matrix Client-Server API"""
        try:
            headers = {
                "Content-Type": "application/json"
            }

            url = f"{self.homeserver_url}/_matrix/client/r0{endpoint}"
            
            async with aiohttp.ClientSession() as session:
                if method == "GET":
                    async with session.get(url, headers=headers) as response:
                        if response.status < 400:
                            return await response.json()
                elif method == "POST":
                    async with session.post(url, headers=headers, json=data) as response:
                        if response.status < 400:
                            return await response.json()

            return None

        except Exception as e:
            logger.error(f"Matrix API call failed: {e}")
            return None

    async def _call_matrix_admin_api(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Any:
        """Call Matrix Admin API"""
        if not self.admin_token:
            logger.warning("No Matrix admin token configured")
            return None

        try:
            headers = {
                "Authorization": f"Bearer {self.admin_token}",
                "Content-Type": "application/json"
            }

            url = f"{self.homeserver_url}/_synapse/admin{endpoint}"
            
            async with aiohttp.ClientSession() as session:
                if method == "GET":
                    async with session.get(url, headers=headers) as response:
                        if response.status < 400:
                            return await response.json()
                elif method == "POST":
                    async with session.post(url, headers=headers, json=data) as response:
                        if response.status < 400:
                            return await response.json()
                elif method == "PUT":
                    async with session.put(url, headers=headers, json=data) as response:
                        if response.status < 400:
                            return await response.json()

            return None

        except Exception as e:
            logger.error(f"Matrix Admin API call failed: {e}")
            return None

# Matrix Authentication Provider for Synapse
def create_synapse_auth_provider() -> str:
    """Create Synapse authentication provider module"""
    provider_code = '''
"""
Zoe Authentication Provider for Matrix Synapse
Custom password provider that integrates with Zoe auth service
"""

import aiohttp
import json
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

class ZoeAuthProvider:
    """Zoe authentication provider for Synapse"""
    
    def __init__(self, config, account_handler):
        self.config = config
        self.account_handler = account_handler
        self.auth_url = config.get("auth_url")
        self.user_info_url = config.get("user_info_url")
        self.server_name = config.get("server_name")
        self.create_users = config.get("create_users", True)
        
    async def check_password(self, user_id: str, password: str) -> bool:
        """Check password against Zoe auth service"""
        try:
            # Extract username from Matrix user ID
            username = user_id.split(":")[0][1:]  # Remove @ and domain
            
            # Call Zoe auth service
            auth_data = {
                "username": username,
                "password": password,
                "device_info": {"type": "matrix", "service": "matrix"}
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(self.auth_url, json=auth_data) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get("success"):
                            # Create user if needed
                            if self.create_users:
                                await self._ensure_user_exists(username, result.get("user_info", {}))
                            return True
            
            return False
            
        except Exception as e:
            logger.error(f"Zoe auth check failed: {e}")
            return False
    
    async def _ensure_user_exists(self, username: str, user_info: dict):
        """Ensure Matrix user exists"""
        try:
            user_id = f"@{username}:{self.server_name}"
            
            # Check if user exists
            if not await self.account_handler.check_user_exists(user_id):
                # Create user
                await self.account_handler.register_user(
                    localpart=username,
                    password=None,  # External auth
                    displayname=user_info.get("username", username),
                    emails=[user_info.get("email")] if user_info.get("email") else []
                )
                logger.info(f"Created Matrix user {user_id}")
                
        except Exception as e:
            logger.error(f"Failed to ensure user exists: {e}")
'''
    
    return provider_code

# Global Matrix integration instance
matrix_integration: Optional[MatrixIntegration] = None

def initialize_matrix_integration(config: Dict[str, Any]) -> MatrixIntegration:
    """Initialize Matrix integration"""
    global matrix_integration
    matrix_integration = MatrixIntegration(config)
    return matrix_integration

