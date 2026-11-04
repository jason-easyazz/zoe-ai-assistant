"""zoe_agent_registry.py — Agent registry loader for zoe_agent.py.

Loads agents_registry.yml and looks up agent info by ID (multica_client reads it).
The brain prompt no longer renders any agent-routing section: there is no peer
agent to escalate to.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "agents_registry.yml")


def load_agent_registry(path: str = _REGISTRY_PATH) -> dict[str, Any]:
    """Load agents_registry.yml. Returns empty dict on failure (agent still works)."""
    try:
        import yaml  # type: ignore[import]
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("zoe_agent_registry: could not load %s: %s", path, exc)
        return {}


def get_agent_info(registry: dict[str, Any], agent_id: str) -> dict[str, Any]:
    """Return the registry entry for a named agent, or empty dict."""
    return registry.get("agents", {}).get(agent_id, {})
