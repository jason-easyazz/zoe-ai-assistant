"""
n8n SSO Integration
Replace n8n's basic auth with Zoe's central authentication
"""

import json
import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import logging

from ..core.auth import auth_manager
from ..core.sessions import session_manager, AuthenticationRequest, SessionType, AuthMethod
from ..core.rbac import rbac_manager
from ..models.database import auth_db

logger = logging.getLogger(__name__)

@dataclass
class N8nUser:
    """n8n user representation"""
    id: str
    email: str
    firstName: str
    lastName: str
    role: str
    isActive: bool
    settings: Dict[str, Any]

class N8nIntegration:
    """n8n SSO integration manager"""
    
    def __init__(self, n8n_config: Dict[str, Any]):
        self.n8n_url = n8n_config.get("url", "http://zoe-n8n:5678")
        self.n8n_webhook_secret = n8n_config.get("webhook_secret")
        self.role_mapping = n8n_config.get("role_mapping", {
            "admin": "owner",
            "user": "member", 
            "family": "member",
            "child": "member",
            "guest": "member"
        })
        self.permission_mapping = n8n_config.get("permission_mapping", {
            "workflows.read": ["workflow:read"],
            "workflows.create": ["workflow:create"],
            "workflows.update": ["workflow:update"],
            "workflows.delete": ["workflow:delete"],
            "workflows.execute": ["workflow:execute"],
            "credentials.read": ["credential:read"],
            "credentials.create": ["credential:create"],
            "credentials.update": ["credential:update"],
            "credentials.delete": ["credential:delete"]
        })

    async def create_n8n_auth_hook(self) -> str:
        """
        Create authentication webhook for n8n
        
        Returns:
            Webhook URL for n8n auth configuration
        """
        return f"http://zoe-auth:8002/api/sso/n8n/auth"

    async def authenticate_n8n_user(self, email: str, password: str) -> Tuple[bool, Optional[Dict]]:
        """
        Authenticate user for n8n
        
        Args:
            email: User email
            password: Password
            
        Returns:
            Tuple of (success, user_data)
        """
        try:
            # Find user by email
            with auth_db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT user_id, username FROM users WHERE email = ? AND is_active = 1",
                    (email,)
                )
                row = cursor.fetchone()
                if not row:
                    return False, None
                
                user_id, username = row

            # Verify password
            auth_result = auth_manager.verify_password(user_id, password)
            if not auth_result.success:
                return False, None

            # Create SSO session
            auth_request = AuthenticationRequest(
                user_id=user_id,
                auth_method=AuthMethod.SSO,
                credentials={"service": "n8n"},
                device_info={"type": "web", "service": "n8n"},
                requested_session_type=SessionType.SSO
            )
            
            session_result = session_manager.authenticate(auth_request)
            if not session_result.success:
                return False, None

            # Get user info and permissions
            user_info = auth_manager.get_user_info(user_id)
            user_permissions = rbac_manager.list_user_permissions(user_id)
            
            # Map to n8n format
            n8n_user = self._map_user_to_n8n(user_info, user_permissions)
            
            return True, n8n_user

        except Exception as e:
            logger.error(f"n8n authentication error for {email}: {e}")
            return False, None

    async def sync_user_to_n8n(self, user_id: str) -> bool:
        """
        Sync Zoe user to n8n
        
        Args:
            user_id: Zoe user ID
            
        Returns:
            True if successful
        """
        try:
            user_info = auth_manager.get_user_info(user_id)
            if not user_info:
                return False

            user_permissions = rbac_manager.list_user_permissions(user_id)
            n8n_user = self._map_user_to_n8n(user_info, user_permissions)

            # Call n8n API to create/update user
            success = await self._call_n8n_api("POST", "/api/v1/users", n8n_user)
            if success:
                logger.info(f"Synced user {user_info['username']} to n8n")
                
            return success

        except Exception as e:
            logger.error(f"Failed to sync user {user_id} to n8n: {e}")
            return False

    async def sync_password_change(self, user_id: str) -> bool:
        """
        Handle password change for n8n user
        
        Args:
            user_id: User ID
            
        Returns:
            True if successful
        """
        try:
            # n8n will use external auth, so just log the change
            user_info = auth_manager.get_user_info(user_id)
            if user_info:
                logger.info(f"Password changed for n8n user {user_info['username']}")
                
                # Optionally notify n8n of password change
                await self._notify_n8n_password_change(user_info["email"])
                
            return True

        except Exception as e:
            logger.error(f"Failed to handle n8n password change for {user_id}: {e}")
            return False

    async def sync_user_permissions(self, user_id: str) -> bool:
        """
        Sync user permissions to n8n
        
        Args:
            user_id: User ID
            
        Returns:
            True if successful
        """
        try:
            user_info = auth_manager.get_user_info(user_id)
            if not user_info:
                return False

            user_permissions = rbac_manager.list_user_permissions(user_id)
            n8n_permissions = self._map_permissions_to_n8n(user_permissions)

            # Update user permissions in n8n
            user_data = {"permissions": n8n_permissions}
            success = await self._call_n8n_api("PUT", f"/api/v1/users/{user_info['email']}", user_data)
            
            if success:
                logger.info(f"Updated n8n permissions for user {user_info['username']}")
                
            return success

        except Exception as e:
            logger.error(f"Failed to sync permissions for user {user_id} to n8n: {e}")
            return False

    async def create_n8n_workflow_permissions(self, workflow_id: str, user_permissions: List[str]) -> bool:
        """
        Create workflow-specific permissions in n8n
        
        Args:
            workflow_id: n8n workflow ID
            user_permissions: List of user permissions
            
        Returns:
            True if successful
        """
        try:
            # Map Zoe permissions to n8n workflow permissions
            workflow_permissions = {
                "read": "workflows.read" in user_permissions,
                "write": "workflows.update" in user_permissions,
                "execute": "workflows.execute" in user_permissions,
                "delete": "workflows.delete" in user_permissions
            }

            # Set workflow permissions
            success = await self._call_n8n_api(
                "PUT", 
                f"/api/v1/workflows/{workflow_id}/permissions",
                workflow_permissions
            )
            
            return success

        except Exception as e:
            logger.error(f"Failed to set n8n workflow permissions: {e}")
            return False

    async def get_user_workflows(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get workflows accessible to user
        
        Args:
            user_id: User ID
            
        Returns:
            List of accessible workflows
        """
        try:
            user_info = auth_manager.get_user_info(user_id)
            if not user_info:
                return []

            # Get workflows from n8n API
            workflows = await self._call_n8n_api("GET", f"/api/v1/workflows")
            if not workflows:
                return []

            # Filter based on user permissions
            user_permissions = rbac_manager.list_user_permissions(user_id)
            accessible_workflows = []
            
            for workflow in workflows:
                if self._can_access_workflow(workflow, user_permissions):
                    accessible_workflows.append(workflow)

            return accessible_workflows

        except Exception as e:
            logger.error(f"Failed to get user workflows for {user_id}: {e}")
            return []

    async def execute_workflow_for_user(self, user_id: str, workflow_id: str, 
                                      input_data: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Execute workflow on behalf of user
        
        Args:
            user_id: User ID
            workflow_id: Workflow to execute
            input_data: Input data for workflow
            
        Returns:
            Tuple of (success, execution_result)
        """
        try:
            # Check permissions
            user_permissions = rbac_manager.list_user_permissions(user_id)
            if "workflows.execute" not in user_permissions:
                return False, {"error": "Insufficient permissions"}

            # Execute workflow via n8n API
            execution_data = {
                "workflowData": input_data,
                "runData": {}
            }
            
            result = await self._call_n8n_api(
                "POST",
                f"/api/v1/workflows/{workflow_id}/execute",
                execution_data
            )
            
            if result:
                logger.info(f"User {user_id} executed workflow {workflow_id}")
                return True, result
            else:
                return False, {"error": "Execution failed"}

        except Exception as e:
            logger.error(f"Failed to execute workflow {workflow_id} for user {user_id}: {e}")
            return False, {"error": str(e)}

    def _map_user_to_n8n(self, user_info: Dict[str, Any], permissions: List[str]) -> Dict[str, Any]:
        """Map Zoe user to n8n user format"""
        # Split name if available
        full_name = user_info.get("display_name", user_info["username"])
        name_parts = full_name.split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        # Map role
        n8n_role = self.role_mapping.get(user_info["role"], "member")

        return {
            "id": user_info["user_id"],
            "email": user_info["email"],
            "firstName": first_name,
            "lastName": last_name,
            "role": n8n_role,
            "isActive": user_info["is_active"],
            "settings": {
                "zoe_user_id": user_info["user_id"],
                "zoe_role": user_info["role"],
                "permissions": self._map_permissions_to_n8n(permissions)
            }
        }

    def _map_permissions_to_n8n(self, zoe_permissions: List[str]) -> List[str]:
        """Map Zoe permissions to n8n permissions"""
        n8n_permissions = []
        
        for zoe_perm in zoe_permissions:
            n8n_perms = self.permission_mapping.get(zoe_perm, [])
            n8n_permissions.extend(n8n_perms)
        
        return list(set(n8n_permissions))  # Remove duplicates

    def _can_access_workflow(self, workflow: Dict[str, Any], user_permissions: List[str]) -> bool:
        """Check if user can access workflow"""
        # Basic permission check
        if "workflows.read" not in user_permissions:
            return False
        
        # Check workflow-specific permissions if they exist
        workflow_tags = workflow.get("tags", [])
        
        # If workflow has restricted tags, check specific permissions
        if "admin-only" in workflow_tags and "admin.*" not in user_permissions:
            return False
        
        if "family-only" in workflow_tags and user_permissions and not any(
            p.startswith("family.") or p.startswith("shared.") for p in user_permissions
        ):
            return False
        
        return True

    async def _call_n8n_api(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Any:
        """
        Call n8n API
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request data
            
        Returns:
            API response data
        """
        try:
            headers = {
                "Content-Type": "application/json",
                "X-N8N-API-KEY": "your-n8n-api-key"  # Would be configured
            }

            url = f"{self.n8n_url}{endpoint}"
            
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
            logger.error(f"n8n API call failed: {e}")
            return None

    async def _notify_n8n_password_change(self, email: str):
        """Notify n8n of password change"""
        try:
            if self.n8n_webhook_secret:
                webhook_data = {
                    "event": "password_changed",
                    "user_email": email,
                    "timestamp": datetime.now().isoformat()
                }
                
                await self._call_n8n_api("POST", "/webhook/auth-change", webhook_data)
                
        except Exception as e:
            logger.error(f"Failed to notify n8n of password change: {e}")

# n8n Authentication Middleware Configuration
def create_n8n_auth_config() -> Dict[str, Any]:
    """Create n8n authentication configuration"""
    return {
        "authentication": {
            "type": "external",
            "settings": {
                "authUrl": "http://zoe-auth:8002/api/sso/n8n/auth",
                "userInfoUrl": "http://zoe-auth:8002/api/sso/n8n/user",
                "logoutUrl": "http://zoe-auth:8002/api/auth/logout",
                "sessionCookie": "zoe_session"
            }
        }
    }

# Global n8n integration instance
n8n_integration: Optional[N8nIntegration] = None

def initialize_n8n_integration(config: Dict[str, Any]) -> N8nIntegration:
    """Initialize n8n integration"""
    global n8n_integration
    n8n_integration = N8nIntegration(config)
    return n8n_integration

