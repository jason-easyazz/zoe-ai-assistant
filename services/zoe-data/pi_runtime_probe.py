"""Read-only Pi runtime readiness probe for Zoe.

This module detects whether Pi is available as an external runtime. It does not
install Pi, run agent tasks, or invoke models. Execution remains behind explicit
operator configuration and Zoe governance.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class PiRuntimeConfigError(ValueError):
    """Raised when Pi runtime config would violate Zoe execution policy."""


@dataclass(frozen=True)
class PiRuntimeConfig:
    enabled: bool = False
    allow_execution: bool = False
    offline_only: bool = True
    local_model_required: bool = True
    local_model_configured: bool = False
    command: str = "pi"
    cwd: str = "/home/zoe/assistant"
    agent_dir: str | None = None
    timeout_seconds: float = 2.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PiRuntimeConfig":
        values = env if env is not None else os.environ
        return cls(
            enabled=_env_bool(values.get("ZOE_PI_ENABLED"), default=False),
            allow_execution=_env_bool(values.get("ZOE_PI_ALLOW_EXECUTION"), default=False),
            offline_only=_env_bool(values.get("ZOE_PI_OFFLINE_ONLY"), default=True),
            local_model_required=_env_bool(values.get("ZOE_PI_LOCAL_MODEL_REQUIRED"), default=True),
            local_model_configured=_env_bool(values.get("ZOE_PI_LOCAL_MODEL_CONFIGURED"), default=False),
            command=(values.get("ZOE_PI_COMMAND") or "pi").strip() or "pi",
            cwd=(values.get("ZOE_PI_CWD") or "/home/zoe/assistant").strip() or "/home/zoe/assistant",
            agent_dir=(values.get("ZOE_PI_AGENT_DIR") or None),
            timeout_seconds=float(values.get("ZOE_PI_TIMEOUT_SECONDS") or 2.0),
        )

    def validate(self) -> None:
        if self.timeout_seconds <= 0:
            raise PiRuntimeConfigError("ZOE_PI_TIMEOUT_SECONDS must be positive")
        if self.allow_execution and not self.enabled:
            raise PiRuntimeConfigError("ZOE_PI_ALLOW_EXECUTION requires ZOE_PI_ENABLED")
        if self.enabled and self.allow_execution:
            if not self.offline_only:
                raise PiRuntimeConfigError("Pi execution requires ZOE_PI_OFFLINE_ONLY=true")
            if not self.local_model_required:
                raise PiRuntimeConfigError("Pi execution requires ZOE_PI_LOCAL_MODEL_REQUIRED=true")
            if not self.local_model_configured:
                raise PiRuntimeConfigError("Pi execution requires ZOE_PI_LOCAL_MODEL_CONFIGURED=true")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "allow_execution": self.allow_execution,
            "offline_only": self.offline_only,
            "local_model_required": self.local_model_required,
            "local_model_configured": self.local_model_configured,
            "command": self.command,
            "cwd": self.cwd,
            "agent_dir": self.agent_dir,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class PiRuntimeProbeResult:
    ok: bool
    acceptable: bool
    status: str
    config: dict[str, Any]
    tools: dict[str, str | None]
    agent_files: tuple[dict[str, str | None], ...]
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "acceptable": self.acceptable,
            "status": self.status,
            "config": self.config,
            "tools": self.tools,
            "agent_files": list(self.agent_files),
            "reason": self.reason,
        }


def probe_pi_runtime(env: Mapping[str, str] | None = None) -> PiRuntimeProbeResult:
    try:
        config = PiRuntimeConfig.from_env(env)
        config.validate()
    except (PiRuntimeConfigError, ValueError) as exc:
        snapshot = _config_snapshot(env)
        return PiRuntimeProbeResult(
            ok=False,
            acceptable=False,
            status="misconfigured",
            config=snapshot,
            tools=_tool_snapshot(snapshot.get("command") or "pi", env),
            agent_files=(),
            reason=str(exc),
        )

    tools = _tool_snapshot(config.command, env)

    if not config.enabled:
        return PiRuntimeProbeResult(
            ok=False,
            acceptable=True,
            status="disabled",
            config=config.to_dict(),
            tools=tools,
            agent_files=(),
            reason="ZOE_PI_ENABLED is false",
        )

    agent_files = tuple(_discover_agent_files(config))

    if not tools.get("node"):
        return PiRuntimeProbeResult(
            ok=False,
            acceptable=False,
            status="missing_node",
            config=config.to_dict(),
            tools=tools,
            agent_files=agent_files,
            reason="node is required before Pi can run",
        )

    if not tools.get("npm"):
        return PiRuntimeProbeResult(
            ok=False,
            acceptable=False,
            status="missing_npm",
            config=config.to_dict(),
            tools=tools,
            agent_files=agent_files,
            reason="npm is required before Pi can be installed or updated",
        )

    if not tools.get("pi"):
        return PiRuntimeProbeResult(
            ok=False,
            acceptable=False,
            status="missing_pi",
            config=config.to_dict(),
            tools=tools,
            agent_files=agent_files,
            reason="pi command not found",
        )

    status = "available_execution_disabled"
    ok = False
    reason = "Pi runtime detected; execution is disabled by Zoe policy"
    if config.allow_execution:
        status = "available"
        ok = True
        reason = None

    return PiRuntimeProbeResult(
        ok=ok,
        acceptable=True,
        status=status,
        config=config.to_dict(),
        tools=tools,
        agent_files=agent_files,
        reason=reason,
    )


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Unrecognized boolean env var value: {value!r}")


def _config_snapshot(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    values = env if env is not None else os.environ
    return {
        "enabled": _bool_snapshot(values.get("ZOE_PI_ENABLED"), default=False),
        "allow_execution": _bool_snapshot(values.get("ZOE_PI_ALLOW_EXECUTION"), default=False),
        "offline_only": _bool_snapshot(values.get("ZOE_PI_OFFLINE_ONLY"), default=True),
        "local_model_required": _bool_snapshot(values.get("ZOE_PI_LOCAL_MODEL_REQUIRED"), default=True),
        "local_model_configured": _bool_snapshot(values.get("ZOE_PI_LOCAL_MODEL_CONFIGURED"), default=False),
        "command": (values.get("ZOE_PI_COMMAND") or "pi").strip() or "pi",
        "cwd": (values.get("ZOE_PI_CWD") or "/home/zoe/assistant").strip() or "/home/zoe/assistant",
        "agent_dir": values.get("ZOE_PI_AGENT_DIR") or None,
        "timeout_seconds": _float_snapshot(values.get("ZOE_PI_TIMEOUT_SECONDS"), default=2.0),
    }


def _bool_snapshot(value: str | None, *, default: bool) -> bool | str:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return str(value)


def _float_snapshot(value: str | None, *, default: float) -> float | str:
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return str(value)


def _tool_snapshot(pi_command: str, env: Mapping[str, str] | None = None) -> dict[str, str | None]:
    values = env if env is not None else os.environ
    path = values.get("PATH")
    return {
        "node": shutil.which("node", path=path),
        "npm": shutil.which("npm", path=path),
        "pi": shutil.which(pi_command, path=path),
    }


def _discover_agent_files(config: PiRuntimeConfig) -> list[dict[str, str | None]]:
    agent_dir = Path(config.agent_dir) if config.agent_dir else Path(config.cwd) / ".pi" / "agents"
    if not agent_dir.is_dir():
        return []
    files = []
    for path in sorted(agent_dir.glob("*.md"))[:50]:
        text = path.read_text(encoding="utf-8", errors="replace")
        files.append(
            {
                "path": str(path),
                "name": _frontmatter_value(text, "name") or path.stem,
                "description": _frontmatter_value(text, "description"),
                "model": _frontmatter_value(text, "model"),
            }
        )
    return files


def _frontmatter_value(text: str, key: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    frontmatter = text[3:end]
    match = re.search(rf"^\s*{re.escape(key)}\s*:\s*(.+?)\s*$", frontmatter, re.M)
    if not match:
        return None
    return match.group(1).strip().strip("\"'")


__all__ = [
    "PiRuntimeConfig",
    "PiRuntimeConfigError",
    "PiRuntimeProbeResult",
    "probe_pi_runtime",
]
