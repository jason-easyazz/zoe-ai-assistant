"""
Agent Zero Safety Guardrails
============================

Provides safety controls for Agent Zero capabilities based on user mode.

Modes:
  - grandma: Research and planning only (safe for non-technical users)
  - developer: Full capabilities with sandboxing
  - custom: User-defined restrictions
"""

import logging
from typing import List, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class SafetyMode(Enum):
    """Safety modes for Agent Zero."""
    GRANDMA = "grandma"
    DEVELOPER = "developer"
    CUSTOM = "custom"


class SafetyGuardrails:
    """
    Safety guardrails for Agent Zero capabilities.
    
    Controls what Agent Zero is allowed to do based on user mode.
    """
    
    def __init__(self, mode: str = "grandma"):
        """
        Initialize safety guardrails.
        
        Args:
            mode: Safety mode (grandma, developer, custom)
        """
        try:
            self.mode = SafetyMode(mode.lower())
        except ValueError:
            logger.warning(f"Invalid safety mode '{mode}', defaulting to grandma")
            self.mode = SafetyMode.GRANDMA
        
        logger.info(f"🛡️ Safety guardrails initialized in {self.mode.value} mode")
    
    def allow_research(self) -> bool:
        """Allow research/web search operations."""
        return True  # Always allowed
    
    def allow_planning(self) -> bool:
        """Allow task planning operations."""
        return True  # Always allowed
    
    def allow_code_execution(self) -> bool:
        """Allow code execution operations."""
        return self.mode == SafetyMode.DEVELOPER
    
    def allow_file_operations(self) -> bool:
        """Allow file system operations."""
        return self.mode == SafetyMode.DEVELOPER
    
    def allow_system_commands(self) -> bool:
        """Allow system command execution."""
        return self.mode == SafetyMode.DEVELOPER
    
    def get_enabled_capabilities(self) -> List[str]:
        """
        Get list of enabled capabilities.
        
        Returns:
            List of capability names that are enabled
        """
        capabilities = ["research", "planning", "analysis"]
        
        if self.mode == SafetyMode.DEVELOPER:
            capabilities.extend([
                "code_execution_sandboxed",
                "file_operations_limited",
                "system_commands_whitelisted"
            ])
        
        return capabilities
    
    def validate_request(self, capability: str, params: Dict[str, Any]) -> tuple[bool, str]:
        """
        Validate a request against safety rules.
        
        Args:
            capability: Capability being requested (e.g., "code_execution")
            params: Request parameters
            
        Returns:
            (allowed: bool, reason: str)
        """
        if capability == "research":
            return True, "Research is always allowed"
        
        elif capability == "planning":
            return True, "Planning is always allowed"
        
        elif capability == "code_execution":
            if self.mode == SafetyMode.DEVELOPER:
                return True, "Code execution allowed in developer mode"
            else:
                return False, "Code execution is not allowed in grandma mode. I can create a plan instead."
        
        elif capability == "file_operations":
            if self.mode == SafetyMode.DEVELOPER:
                # Check if path is within allowed directories
                path = params.get("path", "")
                if self._is_safe_path(path):
                    return True, "File operation allowed (sandboxed)"
                else:
                    return False, "File path is outside allowed directories"
            else:
                return False, "File operations are not allowed in grandma mode"
        
        elif capability == "system_commands":
            if self.mode == SafetyMode.DEVELOPER:
                command = params.get("command", "")
                if self._is_whitelisted_command(command):
                    return True, "System command allowed (whitelisted)"
                else:
                    return False, "System command not in whitelist"
            else:
                return False, "System commands are not allowed in grandma mode"
        
        else:
            return False, f"Unknown capability: {capability}"
    
    def _is_safe_path(self, path: str) -> bool:
        """
        Check if file path is within allowed directories.
        
        Args:
            path: File path to check
            
        Returns:
            True if path is safe, False otherwise
        """
        # In developer mode, allow access to project directory only
        allowed_prefixes = [
            "/home/zoe/assistant/",
            "/app/data/",
            "/tmp/zoe-"
        ]
        
        return any(path.startswith(prefix) for prefix in allowed_prefixes)
    
    def _is_whitelisted_command(self, command: str) -> bool:
        """
        Check if system command is whitelisted.
        
        Args:
            command: Command to check
            
        Returns:
            True if command is whitelisted, False otherwise
        """
        # Whitelist of safe commands for developer mode
        whitelisted = [
            "ls", "cat", "grep", "find", "echo",
            "docker ps", "docker logs", "docker inspect",
            "git status", "git log", "git diff",
            "curl", "wget", "ping"
        ]
        
        cmd_base = command.split()[0] if command else ""
        return cmd_base in whitelisted or command in whitelisted
    
    def get_blocked_message(self, capability: str) -> str:
        """
        Get user-friendly message when capability is blocked.
        
        Args:
            capability: Blocked capability name
            
        Returns:
            User-friendly message explaining why it's blocked
        """
        messages = {
            "code_execution": "I can't execute code in this mode, but I can create a detailed plan for you instead.",
            "file_operations": "I can't modify files in this mode, but I can suggest what changes to make.",
            "system_commands": "I can't run system commands in this mode, but I can guide you through the steps.",
        }
        
        return messages.get(
            capability,
            f"The '{capability}' feature is not available in {self.mode.value} mode."
        )
