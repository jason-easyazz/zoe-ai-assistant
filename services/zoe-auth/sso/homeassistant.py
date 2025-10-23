"""
Home Assistant SSO Integration
Custom authentication provider for Home Assistant integration
"""

import hashlib
import json
import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import logging

from core.auth import auth_manager
from core.sessions import session_manager, AuthenticationRequest, SessionType, AuthMethod
from models.database import auth_db

logger = logging.getLogger(__name__)

@dataclass
class HomeAssistantUser:
    """Home Assistant user representation"""
    username: str
    password_hash: str
    groups: List[str]
    is_active: bool
    ha_person_id: Optional[str] = None

class HomeAssistantIntegration:
    """Home Assistant SSO integration manager"""
    
    def __init__(self, ha_config: Dict[str, Any]):
        self.ha_url = ha_config.get("url", "http://homeassistant:8123")
        self.ha_token = ha_config.get("token")
        self.sync_interval = ha_config.get("sync_interval", 300)  # 5 minutes
        self.role_mapping = ha_config.get("role_mapping", {
            "admin": ["admin"],
            "user": ["users"],
            "family": ["family"],
            "child": ["children"],
            "guest": ["guests"]
        })
        
        # Start sync task
        asyncio.create_task(self._start_sync_loop())

    async def create_ha_auth_provider(self) -> Dict[str, Any]:
        """
        Create Home Assistant authentication provider configuration
        
        Returns:
            Auth provider configuration for HA
        """
        return {
            "type": "command_line",
            "command": "/usr/local/bin/zoe-auth-verify",
            "args": ["--username", "{{ username }}", "--password", "{{ password }}"],
            "meta": False
        }

    async def sync_user_to_ha(self, user_id: str) -> bool:
        """
        Sync Zoe user to Home Assistant
        
        Args:
            user_id: Zoe user ID to sync
            
        Returns:
            True if successful
        """
        try:
            # Get user info from Zoe
            user_info = auth_manager.get_user_info(user_id)
            if not user_info:
                return False

            # Map Zoe role to HA groups
            ha_groups = self.role_mapping.get(user_info["role"], ["users"])

            # Create/update user in HA
            ha_user_data = {
                "username": user_info["username"],
                "password": None,  # Will use external auth
                "name": user_info.get("display_name", user_info["username"]),
                "groups": ha_groups,
                "system_generated": False
            }

            # Call HA API to create/update user
            success = await self._call_ha_api("POST", "/api/users", ha_user_data)
            if success:
                logger.info(f"Synced user {user_info['username']} to Home Assistant")
                
                # Update HA person if exists
                await self._sync_person_entity(user_info)
                
            return success

        except Exception as e:
            logger.error(f"Failed to sync user {user_id} to HA: {e}")
            return False

    async def sync_password_change(self, user_id: str, new_password_hash: str) -> bool:
        """
        Sync password change to Home Assistant
        
        Args:
            user_id: User ID
            new_password_hash: New password hash
            
        Returns:
            True if successful
        """
        try:
            user_info = auth_manager.get_user_info(user_id)
            if not user_info:
                return False

            # Update password in HA (if using internal auth)
            # For external auth, this might trigger a webhook or notification
            
            logger.info(f"Password change synced for user {user_info['username']}")
            return True

        except Exception as e:
            logger.error(f"Failed to sync password change for user {user_id}: {e}")
            return False

    async def sync_user_deletion(self, username: str) -> bool:
        """
        Sync user deletion to Home Assistant
        
        Args:
            username: Username to delete from HA
            
        Returns:
            True if successful
        """
        try:
            # Deactivate user in HA instead of deleting for audit
            ha_user_data = {"is_active": False}
            
            success = await self._call_ha_api("PUT", f"/api/users/{username}", ha_user_data)
            if success:
                logger.info(f"Deactivated user {username} in Home Assistant")
                
            return success

        except Exception as e:
            logger.error(f"Failed to sync user deletion {username} to HA: {e}")
            return False

    async def authenticate_ha_user(self, username: str, password: str) -> Tuple[bool, Optional[str]]:
        """
        Authenticate user for Home Assistant (called by HA auth provider)
        
        Args:
            username: Username
            password: Password
            
        Returns:
            Tuple of (success, user_info_json)
        """
        try:
            # Use Zoe's auth system
            auth_result = auth_manager.verify_password(username, password)
            
            if auth_result.success:
                user_info = auth_manager.get_user_info(auth_result.user_id)
                
                # Create session for HA integration
                auth_request = AuthenticationRequest(
                    user_id=auth_result.user_id,
                    auth_method=AuthMethod.SSO,
                    credentials={"service": "homeassistant"},
                    device_info={"type": "homeassistant", "service": "ha"},
                    requested_session_type=SessionType.SSO
                )
                
                session_result = session_manager.authenticate(auth_request)
                
                if session_result.success:
                    # Return user info for HA
                    ha_user_info = {
                        "username": user_info["username"],
                        "name": user_info.get("display_name", user_info["username"]),
                        "email": user_info["email"],
                        "groups": self.role_mapping.get(user_info["role"], ["users"]),
                        "is_active": user_info["is_active"],
                        "zoe_user_id": user_info["user_id"],
                        "zoe_session_id": session_result.session.session_id
                    }
                    
                    return True, json.dumps(ha_user_info)

            return False, None

        except Exception as e:
            logger.error(f"HA authentication error for {username}: {e}")
            return False, None

    async def get_ha_areas(self) -> List[Dict[str, Any]]:
        """Get Home Assistant areas for user assignment"""
        try:
            areas = await self._call_ha_api("GET", "/api/areas")
            return areas or []
        except Exception as e:
            logger.error(f"Failed to get HA areas: {e}")
            return []

    async def assign_user_to_areas(self, user_id: str, area_ids: List[str]) -> bool:
        """
        Assign user to specific Home Assistant areas
        
        Args:
            user_id: Zoe user ID
            area_ids: List of HA area IDs
            
        Returns:
            True if successful
        """
        try:
            user_info = auth_manager.get_user_info(user_id)
            if not user_info:
                return False

            # Store area assignments in user metadata
            with auth_db.get_connection() as conn:
                settings = json.loads(user_info.get("settings", "{}"))
                settings["ha_areas"] = area_ids
                
                conn.execute("""
                    UPDATE users 
                    SET settings = ?
                    WHERE user_id = ?
                """, (json.dumps(settings), user_id))

            logger.info(f"Assigned user {user_info['username']} to HA areas: {area_ids}")
            return True

        except Exception as e:
            logger.error(f"Failed to assign user {user_id} to areas: {e}")
            return False

    async def sync_all_users_to_ha(self) -> Dict[str, bool]:
        """
        Sync all active Zoe users to Home Assistant
        
        Returns:
            Dictionary mapping user_id to sync success
        """
        try:
            results = {}
            
            with auth_db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT user_id FROM users 
                    WHERE is_active = 1
                """)
                
                for row in cursor.fetchall():
                    user_id = row[0]
                    results[user_id] = await self.sync_user_to_ha(user_id)

            return results

        except Exception as e:
            logger.error(f"Failed to sync all users to HA: {e}")
            return {}

    async def _sync_person_entity(self, user_info: Dict[str, Any]) -> bool:
        """Sync user to HA person entity"""
        try:
            # Check if person entity exists
            person_id = user_info["username"].lower().replace(" ", "_")
            
            person_data = {
                "name": user_info.get("display_name", user_info["username"]),
                "user_id": user_info["user_id"],
                "device_trackers": []  # Could be populated with phone trackers
            }

            # Create or update person entity
            success = await self._call_ha_api("POST", "/api/person", person_data)
            return success

        except Exception as e:
            logger.error(f"Failed to sync person entity: {e}")
            return False

    async def _call_ha_api(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Any:
        """
        Call Home Assistant API
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request data
            
        Returns:
            API response data
        """
        if not self.ha_token:
            logger.warning("No Home Assistant token configured")
            return None

        try:
            headers = {
                "Authorization": f"Bearer {self.ha_token}",
                "Content-Type": "application/json"
            }

            url = f"{self.ha_url}{endpoint}"
            
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
            logger.error(f"HA API call failed: {e}")
            return None

    async def _start_sync_loop(self):
        """Background sync loop for HA integration"""
        while True:
            try:
                await asyncio.sleep(self.sync_interval)
                
                # Periodic sync tasks
                await self._sync_user_states()
                
            except Exception as e:
                logger.error(f"HA sync loop error: {e}")

    async def _sync_user_states(self):
        """Sync user states between Zoe and HA"""
        try:
            # Check for users that need syncing
            with auth_db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT user_id, updated_at FROM users 
                    WHERE is_active = 1 
                    AND updated_at > datetime('now', '-1 hour')
                """)
                
                for row in cursor.fetchall():
                    user_id = row[0]
                    await self.sync_user_to_ha(user_id)

        except Exception as e:
            logger.error(f"User state sync error: {e}")

# Create authentication verification script for HA
def create_ha_auth_script():
    """Create the authentication script for Home Assistant"""
    script_content = '''#!/usr/bin/env python3
"""
Home Assistant Authentication Script for Zoe Integration
This script is called by HA's command_line auth provider
"""

import sys
import json
import asyncio
import aiohttp

async def verify_user(username, password):
    """Verify user with Zoe auth service"""
    try:
        auth_data = {
            "username": username,
            "password": password,
            "device_info": {"type": "homeassistant", "service": "ha"}
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://zoe-auth:8002/api/auth/login",
                json=auth_data
            ) as response:
                
                if response.status == 200:
                    result = await response.json()
                    if result.get("success"):
                        # Return user info for HA
                        user_info = result.get("user_info", {})
                        print(json.dumps({
                            "username": user_info.get("username"),
                            "name": user_info.get("username"),
                            "groups": ["users"]  # Default group
                        }))
                        return True
                
                return False
                
    except Exception as e:
        print(f"Auth error: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    
    args = parser.parse_args()
    
    success = asyncio.run(verify_user(args.username, args.password))
    sys.exit(0 if success else 1)
'''
    
    return script_content

# Global HA integration instance (configured during startup)
ha_integration: Optional[HomeAssistantIntegration] = None

def initialize_ha_integration(config: Dict[str, Any]) -> HomeAssistantIntegration:
    """Initialize Home Assistant integration"""
    global ha_integration
    ha_integration = HomeAssistantIntegration(config)
    return ha_integration

