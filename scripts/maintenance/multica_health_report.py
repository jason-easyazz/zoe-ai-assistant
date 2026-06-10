#!/usr/bin/env python3
"""Report whether Zoe can use Multica as the engineering ticket source of truth."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))

_HERMES_WORKER_SOULS = (
    Path.home() / ".hermes" / "profiles" / "zoe-coder" / "SOUL.md",
    Path.home() / ".hermes" / "profiles" / "zoe-planner" / "SOUL.md",
    Path.home() / ".hermes" / "profiles" / "zoe-reviewer" / "SOUL.md",
)
_HERMES_KANBAN_WORKER_DROPIN = (
    Path.home() / ".config" / "systemd" / "user" / "hermes-agent.service.d" / "kanban-worker-lean.conf"
)
_HERMES_AGENT_ROOT = Path.home() / ".hermes" / "hermes-agent"
_HERMES_AGENT_PYTHON = _HERMES_AGENT_ROOT / "venv" / "bin" / "python"
_HERMES_SYSTEM_PROMPT_FILE = _HERMES_AGENT_ROOT / "agent" / "system_prompt.py"
_HERMES_KANBAN_TOOLS_FILE = _HERMES_AGENT_ROOT / "tools" / "kanban_tools.py"
_HERMES_WORKER_TOOL_LIMIT = 25
_HERMES_WORKER_MAX_TOKENS = 1024
_HERMES_WORKER_MIN_CONTEXT_LENGTH = 64000
_HERMES_WORKER_MIN_OLLAMA_NUM_CTX = 65536
_HERMES_WORKER_BASE_URL = "http://127.0.0.1:11434/v1"
_HERMES_WORKER_TOOLSETS = ["file", "kanban", "terminal"]
_HERMES_WORKER_FILE_READ_MAX_CHARS = 6000
_HERMES_WORKER_TOOL_OUTPUT = {"max_bytes": 8000, "max_line_length": 300, "max_lines": 120}


def _load_default_env() -> None:
    """Load Zoe env files for cron/plain-shell health checks without overriding exports."""
    root = Path(__file__).resolve().parents[2]
    for env_path in (root / ".env", root / "services" / "zoe-data" / ".env"):
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = value.strip().strip("'\"")


def _uploads_configured() -> bool:
    if os.environ.get("LOCAL_UPLOAD_DIR") and os.environ.get("LOCAL_UPLOAD_BASE_URL"):
        return True
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.modules.yml"
    try:
        compose = compose_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return (
        'LOCAL_UPLOAD_DIR: "/data/uploads"' in compose
        and 'LOCAL_UPLOAD_BASE_URL: "http://localhost:8080"' in compose
        and "multica-uploads:/data/uploads" in compose
    )


def _oidc_client_id_configured() -> bool:
    return bool(os.environ.get("MULTICA_OIDC_CLIENT_ID"))


def _runtime_cli_version(runtime: dict) -> str:
    metadata = runtime.get("metadata") if isinstance(runtime.get("metadata"), dict) else {}
    return str(metadata.get("cli_version") or "")


def _zoe_tools_configured(agent: dict) -> bool:
    mcp_config = agent.get("mcp_config") if isinstance(agent.get("mcp_config"), dict) else {}
    servers = mcp_config.get("servers") if isinstance(mcp_config.get("servers"), dict) else {}
    zoe_tools = servers.get("zoe-tools") if isinstance(servers.get("zoe-tools"), dict) else {}
    return str(zoe_tools.get("url") or "").rstrip("/") == "http://zoe-ui/api/mcp"


def _agent_runtime_contracts(agents: list[dict], runtimes: list[dict]) -> dict:
    runtime_by_id = {str(rt.get("id")): rt for rt in runtimes if rt.get("id")}
    required = {
        "Hermes": {"provider": "hermes", "model": "main"},
        "OpenClaw": {"provider": "openclaw", "model": "main"},
    }
    contracts: dict[str, dict] = {}
    for name, expected in required.items():
        agent = next((a for a in agents if a.get("name") == name), None)
        runtime = runtime_by_id.get(str((agent or {}).get("runtime_id"))) if agent else None
        contracts[name] = {
            "agent_exists": bool(agent),
            "runtime_exists": bool(runtime),
            "runtime_provider": (runtime or {}).get("provider"),
            "runtime_online": (runtime or {}).get("status") == "online",
            "runtime_cli_version": _runtime_cli_version(runtime or {}),
            "agent_status": (agent or {}).get("status"),
            "max_concurrent_tasks": (agent or {}).get("max_concurrent_tasks"),
            "model": (agent or {}).get("model"),
            "zoe_tools_mcp": _zoe_tools_configured(agent or {}),
            "ok": bool(
                agent
                and runtime
                and (runtime or {}).get("provider") == expected["provider"]
                and (runtime or {}).get("status") == "online"
                and _runtime_cli_version(runtime or {})
                and (agent or {}).get("max_concurrent_tasks") == 1
                and (agent or {}).get("model") == expected["model"]
                and _zoe_tools_configured(agent or {})
            ),
        }
    return contracts


def _workspace_contract(workspaces: list[dict]) -> dict:
    """Report whether the workspace context describes Zoe's active Multica workflow."""
    workspace = workspaces[0] if workspaces else {}
    context = str(workspace.get("context") or "")
    description = str(workspace.get("description") or "")
    repos = workspace.get("repos") if isinstance(workspace.get("repos"), list) else []
    github_repo = "github.com/jason-easyazz/zoe-ai-assistant"
    required_terms = {
        "multica_source_of_truth": "multica source of truth" in context.lower(),
        "simple_english_issue_capture": "simple-english issue capture" in context.lower(),
        "one_ticket_at_a_time": "one approved ticket at a time" in context.lower(),
        "github_repo": github_repo in context.lower() or any(github_repo in str(repo).lower() for repo in repos),
    }
    return {
        "ok": bool(workspace) and workspace.get("issue_prefix") == "ZOE" and all(required_terms.values()),
        "name": workspace.get("name"),
        "issue_prefix": workspace.get("issue_prefix"),
        "description": description,
        "repos_count": len(repos),
        **required_terms,
    }


def _worker_profile_contexts() -> dict[str, dict]:
    """Report whether Hermes Kanban worker profiles stay lean and task-scoped."""
    contexts: dict[str, dict] = {}
    for soul_path in _HERMES_WORKER_SOULS:
        name = soul_path.parent.name
        try:
            content = soul_path.read_text(encoding="utf-8")
        except OSError as exc:
            contexts[name] = {"exists": False, "ok": False, "error": str(exc)}
            continue
        contexts[name] = {
            "exists": True,
            "bytes": len(content.encode("utf-8")),
            "has_zoe_self": "ZOE_SELF_BEGIN" in content,
            "has_kanban_contract": all(token in content for token in ("kanban_show", "kanban_complete", "kanban_block")),
            "ok": (
                len(content.encode("utf-8")) <= 2000
                and "ZOE_SELF_BEGIN" not in content
                and all(token in content for token in ("kanban_show", "kanban_complete", "kanban_block"))
            ),
        }
    return contexts


def _worker_dispatch_contract() -> dict:
    """Report whether Hermes service uses lean context/tool settings for Kanban workers."""
    try:
        content = _HERMES_KANBAN_WORKER_DROPIN.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "dropin_exists": False, "error": str(exc)}
    ignore_rules = "HERMES_KANBAN_WORKER_IGNORE_RULES=true" in content
    toolsets = "HERMES_KANBAN_WORKER_TOOLSETS=terminal,file,kanban" in content
    no_skills_toolset = "HERMES_KANBAN_WORKER_TOOLSETS=terminal,file,kanban,skills" not in content
    lean_env = "HERMES_KANBAN_LEAN_SYSTEM=true" in content
    auto_skill_off = "HERMES_KANBAN_WORKER_AUTO_SKILL=false" in content
    compact_show = "HERMES_KANBAN_COMPACT_SHOW=true" in content
    try:
        system_prompt = _HERMES_SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    except OSError:
        system_prompt = ""
    try:
        kanban_tools = _HERMES_KANBAN_TOOLS_FILE.read_text(encoding="utf-8")
    except OSError:
        kanban_tools = ""
    lean_system_patch = "HERMES_KANBAN_LEAN_SYSTEM" in system_prompt and "HERMES_KANBAN_TASK" in system_prompt
    compact_show_patch = "HERMES_KANBAN_COMPACT_SHOW" in kanban_tools and "_compact_show_for_worker" in kanban_tools
    return {
        "ok": ignore_rules and toolsets and no_skills_toolset and lean_env and auto_skill_off and compact_show and lean_system_patch and compact_show_patch,
        "dropin_exists": True,
        "ignore_rules": ignore_rules,
        "lean_system_env": lean_env,
        "auto_skill_off": auto_skill_off,
        "compact_show": compact_show,
        "lean_system_patch": lean_system_patch,
        "compact_show_patch": compact_show_patch,
        "no_skills_toolset": no_skills_toolset,
        "toolsets": "terminal,file,kanban" if toolsets else "",
    }


def _worker_tool_envelope() -> dict[str, dict]:
    """Report the actual tool schemas Hermes worker profiles would send."""
    code = r"""
import json
from cli import CLI_CONFIG, get_tool_definitions
from hermes_cli.tools_config import _get_platform_tools

toolsets = sorted(_get_platform_tools(CLI_CONFIG, "cli"))
model_config = CLI_CONFIG.get("model") if isinstance(CLI_CONFIG.get("model"), dict) else {}
tools = get_tool_definitions(enabled_toolsets=toolsets, quiet_mode=True)
names = [((tool.get("function") or {}).get("name") or tool.get("name") or "") for tool in tools]
print(json.dumps({"toolsets": toolsets, "tool_count": len(tools), "tools": names, "max_tokens": model_config.get("max_tokens"), "context_length": model_config.get("context_length"), "ollama_num_ctx": model_config.get("ollama_num_ctx"), "base_url": model_config.get("base_url"), "file_read_max_chars": CLI_CONFIG.get("file_read_max_chars"), "tool_output": CLI_CONFIG.get("tool_output")}))
"""
    envelopes: dict[str, dict] = {}
    for soul_path in _HERMES_WORKER_SOULS:
        profile_dir = soul_path.parent
        name = profile_dir.name
        env = os.environ.copy()
        env["PYTHONPATH"] = str(_HERMES_AGENT_ROOT)
        env["HERMES_HOME"] = str(profile_dir)
        try:
            completed = subprocess.run(
                [str(_HERMES_AGENT_PYTHON), "-c", code],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
        except Exception as exc:
            envelopes[name] = {"ok": False, "error": str(exc)}
            continue
        if completed.returncode != 0:
            envelopes[name] = {"ok": False, "error": completed.stderr.strip() or completed.stdout.strip()}
            continue
        try:
            data = json.loads(completed.stdout.strip().splitlines()[-1])
        except Exception as exc:
            envelopes[name] = {"ok": False, "error": f"invalid JSON: {exc}", "stdout": completed.stdout[-1000:]}
            continue
        tools = [str(tool) for tool in data.get("tools") or []]
        disallowed = [
            tool
            for tool in tools
            if tool.startswith("mcp_")
            or tool.startswith("memory")
            or tool in {"clarify", "cronjob", "execute_code", "image_generate"}
        ]
        max_tokens = data.get("max_tokens")
        max_tokens_ok = isinstance(max_tokens, int) and 0 < max_tokens <= _HERMES_WORKER_MAX_TOKENS
        context_length = data.get("context_length")
        context_length_ok = isinstance(context_length, int) and context_length >= _HERMES_WORKER_MIN_CONTEXT_LENGTH
        ollama_num_ctx = data.get("ollama_num_ctx")
        ollama_num_ctx_ok = isinstance(ollama_num_ctx, int) and ollama_num_ctx >= _HERMES_WORKER_MIN_OLLAMA_NUM_CTX
        base_url = str(data.get("base_url") or "").rstrip("/")
        base_url_ok = base_url == _HERMES_WORKER_BASE_URL
        toolsets_ok = sorted(data.get("toolsets") or []) == sorted(_HERMES_WORKER_TOOLSETS)
        file_read_ok = data.get("file_read_max_chars") == _HERMES_WORKER_FILE_READ_MAX_CHARS
        tool_output = data.get("tool_output") if isinstance(data.get("tool_output"), dict) else {}
        tool_output_ok = all(tool_output.get(key) == value for key, value in _HERMES_WORKER_TOOL_OUTPUT.items())
        envelopes[name] = {
            "ok": data.get("tool_count", 9999) <= _HERMES_WORKER_TOOL_LIMIT and not disallowed and max_tokens_ok and context_length_ok and ollama_num_ctx_ok and base_url_ok and toolsets_ok and file_read_ok and tool_output_ok,
            "toolsets": data.get("toolsets") or [],
            "tool_count": data.get("tool_count"),
            "max_tokens": max_tokens,
            "max_tokens_ok": max_tokens_ok,
            "context_length": context_length,
            "context_length_ok": context_length_ok,
            "ollama_num_ctx": ollama_num_ctx,
            "ollama_num_ctx_ok": ollama_num_ctx_ok,
            "base_url": base_url,
            "base_url_ok": base_url_ok,
            "toolsets_ok": toolsets_ok,
            "file_read_max_chars": data.get("file_read_max_chars"),
            "file_read_ok": file_read_ok,
            "tool_output": tool_output,
            "tool_output_ok": tool_output_ok,
            "disallowed_tools": disallowed,
            "tools": tools,
        }
    return envelopes


async def _probe(url: str, *, headers: dict[str, str] | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
        return {
            "ok": 200 <= response.status_code < 300,
            "status": response.status_code,
            "url": url,
        }
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc)}


async def _get_json(url: str, *, headers: dict[str, str], params: dict[str, str]) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers, params=params)
        if not 200 <= response.status_code < 300:
            return {"ok": False, "status": response.status_code, "url": url}
        return {"ok": True, "status": response.status_code, "url": url, "data": response.json()}
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc)}


async def run(*, ensure_shape: bool = False) -> dict:
    _load_default_env()
    from multica_client import get_engineering_multica_agent_id, get_multica_client

    client = get_multica_client()
    report: dict = {
        "configured": client.is_configured(),
        "base_url": bool(os.environ.get("MULTICA_BASE_URL")),
        "workspace_id": bool(os.environ.get("MULTICA_WORKSPACE_ID")),
        "api_token": bool(os.environ.get("MULTICA_API_TOKEN")),
        "webhook_secret": bool(os.environ.get("MULTICA_WEBHOOK_SECRET")),
        "oidc_client_id": _oidc_client_id_configured(),
        "oidc_client_secret": bool(os.environ.get("MULTICA_OIDC_CLIENT_SECRET")),
        "uploads_configured": _uploads_configured(),
        "email_configured": bool(
            os.environ.get("RESEND_API_KEY") or os.environ.get("SMTP_HOST")
        ),
        "hermes_agent_id": get_engineering_multica_agent_id(),
        "checks": {},
    }
    if not client.is_configured():
        report["ok"] = False
        return report

    issues = await client.list_issues(status="todo")
    labels = await client.list_labels()
    projects = await client.list_projects()
    api_headers = client._headers()
    api_params = {"workspace_id": client._workspace}
    agents_resp = await _get_json(f"{client._base}/api/agents", headers=api_headers, params=api_params)
    runtimes_resp = await _get_json(f"{client._base}/api/runtimes", headers=api_headers, params=api_params)
    workspaces_resp = await _get_json(f"{client._base}/api/workspaces", headers=api_headers, params=api_params)
    agents = agents_resp.get("data") if isinstance(agents_resp.get("data"), list) else []
    runtimes_raw = runtimes_resp.get("data")
    runtimes = runtimes_raw if isinstance(runtimes_raw, list) else (runtimes_raw or {}).get("runtimes", [])
    workspaces = workspaces_resp.get("data") if isinstance(workspaces_resp.get("data"), list) else []
    report["checks"]["issues_api"] = isinstance(issues, list)
    report["checks"]["labels_api"] = isinstance(labels, list)
    report["checks"]["projects_api"] = isinstance(projects, list)
    report["checks"]["agents_api"] = agents_resp.get("ok") is True
    report["checks"]["runtimes_api"] = runtimes_resp.get("ok") is True
    report["checks"]["workspaces_api"] = workspaces_resp.get("ok") is True
    report["checks"]["workspace_contract"] = _workspace_contract(workspaces)
    report["checks"]["agent_runtime_contracts"] = _agent_runtime_contracts(agents, runtimes)
    report["checks"]["worker_profile_contexts"] = _worker_profile_contexts()
    report["checks"]["worker_dispatch_contract"] = _worker_dispatch_contract()
    report["checks"]["worker_tool_envelope"] = _worker_tool_envelope()
    base_url = os.environ.get("MULTICA_BASE_URL", "").rstrip("/")
    report["checks"]["backend_health"] = await _probe(f"{base_url}/health")
    report["checks"]["web_direct"] = await _probe(
        os.environ.get("MULTICA_WEB_URL", "http://127.0.0.1:3000")
    )
    report["checks"]["web_proxy"] = await _probe(
        os.environ.get("MULTICA_PROXY_URL", "http://127.0.0.1/multica/")
    )
    report["checks"]["api_proxy"] = await _probe(
        os.environ.get("MULTICA_API_PROXY_HEALTH_URL", "http://127.0.0.1/multica-api/health")
    )
    report["checks"]["oidc_discovery"] = await _probe(
        os.environ.get(
            "MULTICA_OIDC_DISCOVERY_URL",
            "http://127.0.0.1/.well-known/openid-configuration",
        )
    )

    required_labels = [
        "needs-split",
        "blocked-external",
        "in-review",
        "greptile",
        "ci-failed",
        "audit-only",
        "user-feedback",
        "harness-fix",
        "operator-task",
    ]
    if ensure_shape:
        for label in required_labels:
            await client.ensure_label(label, existing=labels)
        labels = await client.list_labels()
    present_labels = {str(label.get("name") or "").lower() for label in labels}
    report["checks"]["required_labels"] = {
        label: label.lower() in present_labels for label in required_labels
    }
    report["ok"] = (
        report["configured"]
        and report["checks"]["issues_api"]
        and report["checks"]["labels_api"]
        and report["checks"]["projects_api"]
        and report["checks"]["agents_api"]
        and report["checks"]["runtimes_api"]
        and report["checks"]["workspaces_api"]
        and report["checks"]["workspace_contract"].get("ok") is True
        and all(
            contract["ok"]
            for contract in report["checks"]["agent_runtime_contracts"].values()
        )
        and all(
            context["ok"]
            for context in report["checks"]["worker_profile_contexts"].values()
        )
        and report["checks"]["worker_dispatch_contract"].get("ok") is True
        and all(
            envelope["ok"]
            for envelope in report["checks"]["worker_tool_envelope"].values()
        )
        and all(report["checks"]["required_labels"].values())
        and report["webhook_secret"]
        and all(
            report["checks"][name]["ok"]
            for name in (
                "backend_health",
                "web_direct",
                "web_proxy",
                "api_proxy",
                "oidc_discovery",
            )
        )
        and report["uploads_configured"]
        and (
            report["email_configured"]
            or os.environ.get("MULTICA_REQUIRE_EMAIL", "false").lower() != "true"
        )
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Zoe/Multica product surface health.")
    parser.add_argument("--ensure-shape", action="store_true", help="Create missing canonical labels.")
    args = parser.parse_args(argv)
    report = asyncio.run(run(ensure_shape=args.ensure_shape))
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
