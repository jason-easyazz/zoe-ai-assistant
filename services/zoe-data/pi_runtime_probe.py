"""Read-only Pi runtime readiness probe for Zoe.

This module detects whether Pi is available as an external runtime. It does not
install Pi, run agent tasks, or invoke models. Execution remains behind explicit
operator configuration and Zoe governance.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


PI_MIN_NODE_VERSION = (22, 19, 0)
PI_INSTALL_PACKAGE = "@earendil-works/pi-coding-agent"
PI_INSTALL_COMMAND = "npm install -g --ignore-scripts @earendil-works/pi-coding-agent"


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
    tool_versions: dict[str, str | None] | None = None
    requirements: dict[str, Any] | None = None
    install_plan: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "acceptable": self.acceptable,
            "status": self.status,
            "config": self.config,
            "tools": self.tools,
            "tool_versions": self.tool_versions or {},
            "requirements": self.requirements or _pi_requirements_snapshot(),
            "install_plan": self.install_plan or _pi_install_plan_snapshot(),
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
            requirements=_pi_requirements_snapshot(),
            install_plan=_pi_install_plan_snapshot(),
            agent_files=(),
            reason=str(exc),
        )

    tools = _tool_snapshot(config.command, env)
    tool_versions = _tool_version_snapshot(tools, env, timeout_seconds=config.timeout_seconds)
    requirements = _pi_requirements_snapshot(node_version=tool_versions.get("node"), node_found=bool(tools.get("node")))
    install_plan = _pi_install_plan_snapshot()

    if not config.enabled:
        return PiRuntimeProbeResult(
            ok=False,
            acceptable=True,
            status="disabled",
            config=config.to_dict(),
            tools=tools,
            tool_versions=tool_versions,
            requirements=requirements,
            install_plan=install_plan,
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
            tool_versions=tool_versions,
            requirements=requirements,
            install_plan=install_plan,
            agent_files=agent_files,
            reason="node is required before Pi can run",
        )

    if requirements["node"].get("status") == "too_old":
        return PiRuntimeProbeResult(
            ok=False,
            acceptable=False,
            status="node_version_too_old",
            config=config.to_dict(),
            tools=tools,
            tool_versions=tool_versions,
            requirements=requirements,
            install_plan=install_plan,
            agent_files=agent_files,
            reason=f"node >= {requirements['node']['minimum']} is required for current Pi",
        )

    if not tools.get("npm"):
        return PiRuntimeProbeResult(
            ok=False,
            acceptable=False,
            status="missing_npm",
            config=config.to_dict(),
            tools=tools,
            tool_versions=tool_versions,
            requirements=requirements,
            install_plan=install_plan,
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
            tool_versions=tool_versions,
            requirements=requirements,
            install_plan=install_plan,
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
        tool_versions=tool_versions,
        requirements=requirements,
        install_plan=install_plan,
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
    if env is None:
        path = _path_with_nvm_node_bin(path or "")
    return {
        "node": shutil.which("node", path=path),
        "npm": shutil.which("npm", path=path),
        "pi": shutil.which(pi_command, path=path),
    }


def _path_with_nvm_node_bin(path: str) -> str:
    parts = [part for part in path.split(os.pathsep) if part]
    joined = os.pathsep.join(parts)
    if shutil.which("node", path=joined) and shutil.which("npm", path=joined) and shutil.which("pi", path=joined):
        return joined
    node_bin = _discover_nvm_node_bin()
    if node_bin and node_bin not in parts:
        parts.append(node_bin)
    return os.pathsep.join(parts)


def _discover_nvm_node_bin() -> str | None:
    nvm_versions = Path.home() / ".nvm" / "versions" / "node"
    if not nvm_versions.is_dir():
        return None
    candidates = [path / "bin" for path in nvm_versions.iterdir() if (path / "bin").is_dir()]
    candidates = [path for path in candidates if (path / "node").exists() and (path / "npm").exists()]
    if not candidates:
        return None

    def version_key(path: Path) -> tuple[int, ...]:
        version = path.parent.name.lstrip("v")
        try:
            return tuple(int(part) for part in version.split("."))
        except ValueError:
            return (0,)

    return str(sorted(candidates, key=version_key)[-1])


def _tool_version_snapshot(
    tools: Mapping[str, str | None],
    env: Mapping[str, str] | None = None,
    *,
    timeout_seconds: float = 1.0,
) -> dict[str, str | None]:
    return {
        "node": _command_version(tools.get("node"), env, timeout_seconds=timeout_seconds),
        "npm": _command_version(tools.get("npm"), env, timeout_seconds=timeout_seconds),
        "pi": None,
    }


def _command_version(
    command_path: str | None,
    env: Mapping[str, str] | None = None,
    *,
    timeout_seconds: float = 1.0,
) -> str | None:
    if not command_path:
        return None
    values = dict(os.environ if env is None else env)
    command_dir = str(Path(command_path).parent)
    path_parts = [part for part in values.get("PATH", "").split(os.pathsep) if part]
    if command_dir not in path_parts:
        values["PATH"] = os.pathsep.join([command_dir, *path_parts])
    try:
        completed = subprocess.run(
            [command_path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=values,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    return output[0].strip() if output else None


def _pi_requirements_snapshot(*, node_version: str | None = None, node_found: bool = False) -> dict[str, Any]:
    node_status = "unknown" if node_found else "missing"
    parsed = _parse_semver(node_version)
    if parsed is not None:
        node_status = "ok" if parsed >= PI_MIN_NODE_VERSION else "too_old"
    return {
        "node": {
            "minimum": _format_semver(PI_MIN_NODE_VERSION),
            "detected": node_version,
            "status": node_status,
        },
        "npm": {"required": True},
        "pi": {"required": True, "package": PI_INSTALL_PACKAGE},
        "local_model": {"required_for_execution": True},
        "offline_only": {"required_for_execution": True},
    }


def _pi_install_plan_snapshot() -> dict[str, Any]:
    return {
        "source": "https://pi.dev/docs/latest/quickstart",
        "node_minimum_source": "https://pi.dev/news",
        "install_command": PI_INSTALL_COMMAND,
        "uninstall_command": f"npm uninstall -g {PI_INSTALL_PACKAGE}",
        "requires_multica_approval": True,
        "probe_executes_pi": False,
    }


def _parse_semver(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        return None
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch))


def _format_semver(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


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
    "PI_INSTALL_COMMAND",
    "PI_INSTALL_PACKAGE",
    "PI_MIN_NODE_VERSION",
    "PiRuntimeConfig",
    "PiRuntimeConfigError",
    "PiRuntimeProbeResult",
    "probe_pi_runtime",
]
