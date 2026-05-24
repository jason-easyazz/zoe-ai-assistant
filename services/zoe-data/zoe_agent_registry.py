"""zoe_agent_registry.py — Agent registry loader for zoe_agent.py.

Loads agents_registry.yml once at startup and provides helpers for:
- Generating the AGENT TEAM routing section of the system prompt
- Generating escalation tool descriptions from registry skills
- Looking up agent info by ID

Extracted from zoe_agent.py to keep the registry logic isolated and testable.
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


def build_agent_team_prompt(registry: dict[str, Any]) -> str:
    """Generate AGENT TEAM routing guidance from registry data.

    Returns an empty string if no agents are registered (safe fallback).
    """
    agents = registry.get("agents", {})
    if not agents:
        return ""
    _TOOL_MAP = {
        "hermes": "escalate_to_hermes",
        "openclaw": "escalate_to_openclaw",
    }
    lines = ["", "AGENT TEAM (routing guide — use these tools to delegate):"]
    for agent_id, info in agents.items():
        tool_name = _TOOL_MAP.get(agent_id)
        if not tool_name:
            continue
        skills = ", ".join(info.get("skills", []))
        desc = info.get("description", agent_id)
        lines.append(f"- {desc}: skills={skills} → use {tool_name}")
    lines.append(
        "Delegate to Hermes by default for complex tasks, engineering, planning, review, and repair. "
        "Use Hermes plus Zoe CloakBrowser tools for browser/session workflows. "
        "OpenClaw remains available as an explicit fallback but is not the default route. "
        "Do NOT escalate for general knowledge, maths, history, or simple web lookups."
    )
    return "\n".join(lines)


def registry_tool_description(registry: dict[str, Any], agent_id: str, fallback: str) -> str:
    """Build a tool description from registry skills, falling back to static text."""
    info = registry.get("agents", {}).get(agent_id, {})
    if not info:
        return fallback
    skills = info.get("skills", [])
    desc = info.get("description", agent_id)
    skills_str = ", ".join(skills) if skills else "general tasks"
    latency = info.get("latency", "")
    latency_note = f" ({latency})" if latency else ""
    return f"{desc}{latency_note}. Skills: {skills_str}. {fallback}"


def get_agent_info(registry: dict[str, Any], agent_id: str) -> dict[str, Any]:
    """Return the registry entry for a named agent, or empty dict."""
    return registry.get("agents", {}).get(agent_id, {})
