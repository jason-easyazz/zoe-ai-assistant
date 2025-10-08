#!/usr/bin/env python3
"""
Zoe MCP Server Security Framework
Implements authentication, authorization, and data isolation
"""

import json
import jwt
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class UserContext:
    """User context for security"""
    user_id: str
    username: str
    email: str
    roles: List[str]
    permissions: List[str]
    session_id: Optional[str] = None
    is_active: bool = True

class MCPSecurityManager:
    """Security manager for MCP server"""
    
    def __init__(self, db_path: str, secret_key: str):
        self.db_path = db_path
        self.secret_key = secret_key
        self.algorithm = "HS256"
        self.token_expire_hours = 24
        
    def validate_jwt_token(self, token: str) -> Optional[UserContext]:
        """Validate JWT token and return user context"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            user_id = payload.get("user_id")
            username = payload.get("username")
            
            if not user_id or not username:
                logger.warning("Invalid JWT token: missing user_id or username")
                return None
            
            # Get user details from database
            return self._get_user_context(user_id, username)
            
        except jwt.ExpiredSignatureError:
            logger.warning("JWT token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid JWT token: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Error validating JWT token: {str(e)}")
            return None
    
    def validate_session_id(self, session_id: str) -> Optional[UserContext]:
        """Validate session ID and return user context"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT us.session_id, us.user_id, us.expires_at, us.is_active,
                       u.username, u.email, u.role, u.permissions, u.is_active as user_active
                FROM user_sessions us
                JOIN users u ON us.user_id = u.user_id
                WHERE us.session_id = ? AND us.is_active = 1 AND u.is_active = 1
            """, (session_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                logger.warning(f"Invalid or expired session: {session_id}")
                return None
            
            session_id, user_id, expires_at, session_active, username, email, role, permissions_json, user_active = row
            
            # Check if session is expired
            if datetime.now() >= datetime.fromisoformat(expires_at):
                logger.warning(f"Session expired: {session_id}")
                return None
            
            # Parse permissions
            permissions = json.loads(permissions_json) if permissions_json else []
            roles = [role] if role else ['user']
            
            return UserContext(
                user_id=user_id,
                username=username,
                email=email,
                roles=roles,
                permissions=permissions,
                session_id=session_id,
                is_active=user_active
            )
            
        except Exception as e:
            logger.error(f"Error validating session: {str(e)}")
            return None
    
    def _get_user_context(self, user_id: str, username: str) -> Optional[UserContext]:
        """Get user context from database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT user_id, username, email, role, permissions, is_active
                FROM users
                WHERE user_id = ? AND username = ? AND is_active = 1
            """, (user_id, username))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                logger.warning(f"User not found or inactive: {user_id}")
                return None
            
            user_id, username, email, role, permissions_json, is_active = row
            
            # Parse permissions
            permissions = json.loads(permissions_json) if permissions_json else []
            roles = [role] if role else ['user']
            
            return UserContext(
                user_id=user_id,
                username=username,
                email=email,
                roles=roles,
                permissions=permissions,
                is_active=is_active
            )
            
        except Exception as e:
            logger.error(f"Error getting user context: {str(e)}")
            return None
    
    def check_permission(self, user_context: UserContext, permission: str) -> bool:
        """Check if user has specific permission"""
        if not user_context or not user_context.is_active:
            return False
        
        # Admin users have all permissions
        if 'admin' in user_context.roles:
            return True
        
        # Check specific permission
        return permission in user_context.permissions
    
    def check_role(self, user_context: UserContext, required_role: str) -> bool:
        """Check if user has specific role"""
        if not user_context or not user_context.is_active:
            return False
        
        return required_role in user_context.roles
    
    def get_available_tools(self, user_context: UserContext) -> List[str]:
        """Get list of available tools for user"""
        if not user_context or not user_context.is_active:
            return []
        
        # Base tools available to all users
        base_tools = [
            "search_memories",
            "create_person", 
            "create_calendar_event",
            "add_to_list",
            "get_calendar_events",
            "get_lists"
        ]
        
        # Admin tools
        if 'admin' in user_context.roles:
            base_tools.extend([
                "get_developer_tasks",
                "system_config",
                "user_management"
            ])
        
        # Developer tools
        if 'developer' in user_context.roles:
            base_tools.extend([
                "get_developer_tasks",
                "debug_tools",
                "performance_metrics"
            ])
        
        return base_tools
    
    def audit_log(self, user_context: UserContext, tool_name: str, action: str, details: Dict[str, Any]):
        """Log security events for audit"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create audit log table if it doesn't exist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mcp_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    session_id TEXT
                )
            """)
            
            # Insert audit log entry
            cursor.execute("""
                INSERT INTO mcp_audit_log (user_id, username, tool_name, action, details, session_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user_context.user_id,
                user_context.username,
                tool_name,
                action,
                json.dumps(details),
                user_context.session_id
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error writing audit log: {str(e)}")
    
    def create_secure_context(self, user_context: UserContext) -> Dict[str, Any]:
        """Create secure context for tool execution"""
        return {
            "user_id": user_context.user_id,
            "username": user_context.username,
            "roles": user_context.roles,
            "permissions": user_context.permissions,
            "session_id": user_context.session_id,
            "timestamp": datetime.now().isoformat()
        }

class SecureMCPServer:
    """Secure wrapper for MCP server operations"""
    
    def __init__(self, security_manager: MCPSecurityManager):
        self.security_manager = security_manager
    
    def authenticate_request(self, auth_header: Optional[str], session_header: Optional[str]) -> Optional[UserContext]:
        """Authenticate request and return user context"""
        # Try JWT token first
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            user_context = self.security_manager.validate_jwt_token(token)
            if user_context:
                return user_context
        
        # Try session ID
        if session_header:
            user_context = self.security_manager.validate_session_id(session_header)
            if user_context:
                return user_context
        
        logger.warning("Authentication failed: no valid credentials provided")
        return None
    
    def authorize_tool_access(self, user_context: UserContext, tool_name: str) -> bool:
        """Check if user can access specific tool"""
        available_tools = self.security_manager.get_available_tools(user_context)
        
        if tool_name not in available_tools:
            logger.warning(f"User {user_context.username} denied access to tool: {tool_name}")
            return False
        
        return True
    
    def execute_secure_tool(self, user_context: UserContext, tool_name: str, tool_args: Dict[str, Any], tool_func):
        """Execute tool with security context"""
        # Check authorization
        if not self.authorize_tool_access(user_context, tool_name):
            raise PermissionError(f"Access denied to tool: {tool_name}")
        
        # Add user context to tool arguments
        secure_args = tool_args.copy()
        secure_args["_user_context"] = self.security_manager.create_secure_context(user_context)
        
        # Log tool execution
        self.security_manager.audit_log(
            user_context, 
            tool_name, 
            "tool_execution", 
            {"args": tool_args, "timestamp": datetime.now().isoformat()}
        )
        
        # Execute tool
        try:
            result = tool_func(secure_args)
            
            # Log successful execution
            self.security_manager.audit_log(
                user_context,
                tool_name,
                "tool_success",
                {"result_type": type(result).__name__, "timestamp": datetime.now().isoformat()}
            )
            
            return result
            
        except Exception as e:
            # Log failed execution
            self.security_manager.audit_log(
                user_context,
                tool_name,
                "tool_error",
                {"error": str(e), "timestamp": datetime.now().isoformat()}
            )
            raise

# Security configuration
SECURITY_CONFIG = {
    "jwt_secret": "zoe-mcp-secret-key-change-in-production",
    "token_expire_hours": 24,
    "session_expire_hours": 168,  # 7 days
    "max_failed_attempts": 5,
    "rate_limit_per_minute": 60,
    "audit_log_retention_days": 90
}

