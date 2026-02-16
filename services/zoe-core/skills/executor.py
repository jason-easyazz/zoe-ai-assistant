"""
Skills Executor
================

Phase 1a: Safe API-only skill execution with endpoint whitelisting.

The executor enforces security rules:
- Skills can ONLY call API endpoints listed in their allowed_endpoints
- No shell, file, or process access
- Only localhost/Docker network endpoints
- All calls are logged for audit

This is the runtime enforcement layer that prevents a compromised or
malicious skill from accessing APIs it shouldn't.
"""

import re
import logging
import httpx
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse

from skills.loader import Skill

logger = logging.getLogger(__name__)

# Allowed URL hosts (only internal services)
ALLOWED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "zoe-core",
    "zoe-auth",
    "zoe-n8n",
    "zoe-llamacpp",
    "zoe-litellm",
    "zoe-mem-agent",
    "zoe-mcp-server",
    "zoe-agent0",
    "agent-zero-bridge",
    "zoe-ollama",
    "homeassistant",
}

# HTTP methods allowed for skills
ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}


class SkillExecutor:
    """Execute skill API calls with endpoint whitelisting."""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        self._call_log: list = []  # Recent calls for audit

    def check_endpoint_allowed(
        self,
        skill: Skill,
        method: str,
        path: str,
    ) -> Tuple[bool, str]:
        """Check if an API call is allowed by the skill's whitelist.

        Args:
            skill: The skill attempting the call
            method: HTTP method (GET, POST, etc.)
            path: API path (e.g., "/api/homeassistant/control")

        Returns:
            (allowed, reason) tuple
        """
        method = method.upper()

        if method not in ALLOWED_METHODS:
            return False, f"HTTP method {method} not allowed"

        # Check against skill's allowed_endpoints
        endpoint_str = f"{method} {path}"

        for allowed in skill.allowed_endpoints:
            # Parse allowed endpoint: "POST /api/homeassistant/control"
            parts = allowed.strip().split(" ", 1)
            if len(parts) != 2:
                continue

            allowed_method, allowed_path = parts[0].upper(), parts[1]

            # Exact match or prefix match (for /api/homeassistant/* patterns)
            if method == allowed_method:
                if path == allowed_path:
                    return True, f"Exact match: {endpoint_str}"
                if allowed_path.endswith("*") and path.startswith(allowed_path[:-1]):
                    return True, f"Prefix match: {endpoint_str} matches {allowed}"

        return False, (
            f"Endpoint {endpoint_str} not in skill's allowed_endpoints: "
            f"{skill.allowed_endpoints}"
        )

    async def execute_api_call(
        self,
        skill: Skill,
        method: str,
        url: str,
        body: Optional[Dict] = None,
        headers: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Execute an API call on behalf of a skill.

        Enforces:
        1. URL host must be an internal service
        2. Method + path must be in skill's allowed_endpoints
        3. api_only must be true

        Args:
            skill: The skill making the call
            method: HTTP method
            url: Full URL (e.g., "http://localhost:8000/api/homeassistant/control")
            body: Optional request body
            headers: Optional headers

        Returns:
            Dict with success status and response data
        """
        # Enforce api_only
        if not skill.api_only:
            return {
                "success": False,
                "error": "Skill is not API-only -- execution blocked",
                "skill": skill.name,
            }

        # Validate URL host
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if hostname not in ALLOWED_HOSTS:
            logger.warning(
                f"Skill {skill.name} tried to call external host: {hostname} -- BLOCKED"
            )
            self._log_call(skill, method, url, blocked=True, reason=f"External host: {hostname}")
            return {
                "success": False,
                "error": f"Host '{hostname}' is not an allowed internal service",
                "skill": skill.name,
            }

        # Validate endpoint against whitelist
        path = parsed.path
        allowed, reason = self.check_endpoint_allowed(skill, method, path)

        if not allowed:
            logger.warning(
                f"Skill {skill.name} tried to call {method} {path} -- BLOCKED: {reason}"
            )
            self._log_call(skill, method, url, blocked=True, reason=reason)
            return {
                "success": False,
                "error": reason,
                "skill": skill.name,
            }

        # Execute the call
        try:
            response = await self._client.request(
                method=method,
                url=url,
                json=body,
                headers=headers or {},
            )

            self._log_call(
                skill, method, url, blocked=False,
                status_code=response.status_code
            )

            if response.status_code < 400:
                try:
                    data = response.json()
                except Exception:
                    data = response.text

                return {
                    "success": True,
                    "status_code": response.status_code,
                    "data": data,
                    "skill": skill.name,
                }
            else:
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "error": response.text[:500],
                    "skill": skill.name,
                }

        except httpx.TimeoutException:
            logger.error(f"Skill {skill.name} API call timed out: {method} {url}")
            self._log_call(skill, method, url, blocked=False, reason="timeout")
            return {
                "success": False,
                "error": "Request timed out",
                "skill": skill.name,
            }
        except Exception as e:
            logger.error(f"Skill {skill.name} API call failed: {e}")
            self._log_call(skill, method, url, blocked=False, reason=str(e))
            return {
                "success": False,
                "error": str(e),
                "skill": skill.name,
            }

    def _log_call(
        self,
        skill: Skill,
        method: str,
        url: str,
        blocked: bool = False,
        reason: str = "",
        status_code: int = 0,
    ):
        """Log an API call for audit."""
        entry = {
            "skill": skill.name,
            "method": method,
            "url": url,
            "blocked": blocked,
            "reason": reason,
            "status_code": status_code,
        }
        self._call_log.append(entry)

        # Keep last 100 entries
        if len(self._call_log) > 100:
            self._call_log = self._call_log[-100:]

    def get_call_log(self, limit: int = 50) -> list:
        """Get recent API call log for audit."""
        return self._call_log[-limit:]


# Singleton instance
skill_executor = SkillExecutor()
