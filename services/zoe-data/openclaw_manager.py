"""
openclaw_manager.py — OpenClaw plugin and skill management interface.

Wraps `openclaw plugin` and `openclaw skills` CLI commands and exposes
the data needed by the `openclaw_manager` and `skills_manager` AG-UI components.

Security model
--------------
- Bundled skills (source == "openclaw-bundled", shipped inside the npm package) are
  always allowed — they are reviewed by the OpenClaw maintainer team.
- Workspace skills already present on disk are treated as trusted (already installed).
- ClawHub installs must be in _SKILLS_ALLOWLIST. Add a name only after reviewing the
  SKILL.md content yourself or via the preview endpoint.
- Every install candidate is scanned for BLOCK/WARN patterns; BLOCK-level findings
  prevent installation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
_OPENCLAW_DIR = Path(os.environ.get("OPENCLAW_DIR", Path.home() / ".openclaw"))


# ── Static plugin catalogue ───────────────────────────────────────────────────

_PLUGIN_META: dict[str, dict[str, str]] = {
    "memory-memu": {
        "icon": "🧠",
        "description": "Persistent memory store — lets OpenClaw remember facts across sessions.",
    },
    "browser": {
        "icon": "🌐",
        "description": "Headless browser for web scraping, screenshots, and automation.",
        "docs_url": "https://docs.openclaw.ai/plugins/browser",
    },
    "web_search": {
        "icon": "🔍",
        "description": "Search the web using DuckDuckGo or Brave Search API.",
        "docs_url": "https://docs.openclaw.ai/plugins/web-search",
    },
    "home_assistant": {
        "icon": "🏠",
        "description": "Control Home Assistant devices: lights, switches, climate.",
        "docs_url": "https://docs.openclaw.ai/plugins/home-assistant",
    },
    "telegram": {
        "icon": "📱",
        "description": "Connect Zoe to Telegram for chat and push notifications.",
        "docs_url": "https://docs.openclaw.ai/plugins/telegram",
    },
    "calendar": {
        "icon": "📅",
        "description": "Read and create Google Calendar or CalDAV events.",
        "docs_url": "https://docs.openclaw.ai/plugins/calendar",
    },
    "code_interpreter": {
        "icon": "💻",
        "description": "Run Python code snippets inside a sandboxed environment.",
        "docs_url": "https://docs.openclaw.ai/plugins/code-interpreter",
    },
    "email": {
        "icon": "📧",
        "description": "Send and read emails via SMTP / IMAP.",
        "docs_url": "https://docs.openclaw.ai/plugins/email",
    },
    "shopping": {
        "icon": "🛒",
        "description": "Search and compare prices across major retailers.",
        "docs_url": "https://docs.openclaw.ai/plugins/shopping",
    },
    "weather": {
        "icon": "🌤️",
        "description": "Fetch current conditions and forecasts from Open-Meteo.",
        "docs_url": "https://docs.openclaw.ai/plugins/weather",
    },
}


# ── Skills security ───────────────────────────────────────────────────────────

# Maintainer-curated allowlist for ClawHub installs.
# Bundled skills (source == "openclaw-bundled") bypass this check.
# Workspace skills already on disk are trusted as-is.
# To add a new ClawHub skill: review SKILL.md via /api/openclaw/skills/{name}/preview,
# confirm no BLOCK findings, then add the name here.
_SKILLS_ALLOWLIST: frozenset[str] = frozenset({
    # Currently workspace-installed — treated as trusted
    "briefing", "browser", "dynamic-widgets", "family-data",
    "grocery-meal", "ha-patterns", "home-assistant", "journal",
    "memory-consolidation", "proactive", "research", "self-improvement",
    "summarize", "touch-panel", "transactions", "weather",
    "zoe-setup", "zoe-ui",
    # Eligible bundled skills reviewed by OpenClaw maintainers
    "gh-issues", "github", "healthcheck", "mcporter", "node-connect",
    "skill-creator", "taskflow", "taskflow-inbox-triage",
    # User-requested additions
    "openai-whisper-api", "video-frames", "session-logs", "himalaya",
})

# Security patterns applied to SKILL.md content.
# BLOCK findings disable install entirely; WARN requires user confirmation.
_SECURITY_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("BLOCK",
     re.compile(r"~\/\.(ssh|aws|gnupg)|secret|credential|private[_\s]?key", re.I),
     "References sensitive credential paths"),
    ("BLOCK",
     re.compile(r"(modify|write|overwrite|edit).*(openclaw\.json|config\.json)", re.I),
     "Attempts to modify OpenClaw config"),
    ("WARN",
     re.compile(r"\b(exec|subprocess|spawn|bash|sh -c)\b", re.I),
     "References shell/exec execution"),
    ("WARN",
     re.compile(r"https?://(?!docs\.openclaw\.ai|wttr\.in|github\.com|clawhub\.com)", re.I),
     "External URLs outside trusted domains"),
    ("WARN",
     re.compile(r"\b(curl|wget|netcat|nc )\b", re.I),
     "References network transfer tools"),
]


def _scan_skill_content(content: str) -> dict:
    """Run automated security scan on SKILL.md text. Returns verdict + findings."""
    findings = []
    for level, pat, desc in _SECURITY_PATTERNS:
        matches = pat.findall(content)
        if matches:
            findings.append({
                "level": level,
                "description": desc,
                "matches": list(set(str(m) for m in matches[:3])),
            })
    blocked = any(f["level"] == "BLOCK" for f in findings)
    verdict = "BLOCK" if blocked else ("WARN" if findings else "CLEAN")
    return {"verdict": verdict, "findings": findings}


def _skill_is_allowed(name: str, source: str) -> bool:
    """Return True if a skill may be installed.

    Bundled skills are always allowed (OpenClaw-reviewed).
    ClawHub / unknown sources must be in _SKILLS_ALLOWLIST.
    """
    if source == "openclaw-bundled":
        return True
    return name in _SKILLS_ALLOWLIST


# ── Core CLI runner ───────────────────────────────────────────────────────────

def _build_openclaw_env() -> dict[str, str]:
    """Build env for OpenClaw subprocesses.

    - Ensures ~/.local/bin is in PATH (user-installed tools: jq, rg, ffmpeg, himalaya)
    - Merges any missing vars from ~/.env so eligibility checks see API keys
    """
    env = os.environ.copy()
    # Prepend ~/.local/bin so user-installed tools are found
    local_bin = str(Path.home() / ".local" / "bin")
    path = env.get("PATH", "")
    if local_bin not in path.split(":"):
        env["PATH"] = f"{local_bin}:{path}"
    # Load missing vars from .env file (assistant root)
    env_file = Path.home() / "assistant" / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in env:
                    env[key] = val
        except OSError:
            pass
    return env


async def _run_openclaw(*args: str, timeout: int = 15) -> tuple[int, str, str]:
    """Run an openclaw CLI command and return (returncode, stdout, stderr)."""
    cmd = [_OPENCLAW_BIN, *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_OPENCLAW_DIR),
            env=_build_openclaw_env(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(), stderr.decode()
    except FileNotFoundError:
        return 1, "", f"openclaw binary not found at {_OPENCLAW_BIN}"
    except asyncio.TimeoutError:
        return 1, "", "openclaw command timed out"
    except Exception as exc:
        return 1, "", str(exc)


# ── Plugin functions ──────────────────────────────────────────────────────────

def _installed_plugins_from_config() -> set[str]:
    """Read installed plugins from openclaw.json without shelling out."""
    cfg_path = _OPENCLAW_DIR / "openclaw.json"
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        plugins_obj = cfg.get("plugins", {})
        if isinstance(plugins_obj, dict):
            # plugins.entries keys = actually installed/configured plugins
            entries = set(plugins_obj.get("entries", {}).keys())
            # plugins.allow may list additional permitted-but-not-configured plugins,
            # but exclude "plugin" which is the CLI surface key, not a real plugin.
            allow = {p for p in plugins_obj.get("allow", []) if p != "plugin"}
            return entries | allow
    except Exception as exc:
        logger.debug("Could not read openclaw.json: %s", exc)
    return set()


async def list_plugins() -> list[dict[str, Any]]:
    """Return a merged list of installed + known-available plugins."""
    installed = _installed_plugins_from_config()

    # Try `openclaw plugins list --json` for richer data
    # CLI returns {"workspaceDir": "...", "plugins": [...]}
    rc, out, _ = await _run_openclaw("plugins", "list", "--json")
    cli_plugins: list[dict] = []
    if rc == 0 and out.strip():
        try:
            raw = json.loads(out)
            if isinstance(raw, list):
                cli_plugins = raw
            elif isinstance(raw, dict):
                cli_plugins = raw.get("plugins", [])
        except json.JSONDecodeError:
            pass

    seen: set[str] = set()
    result: list[dict[str, Any]] = []

    for p in cli_plugins:
        name = p.get("name", "")
        if not name:
            continue
        seen.add(name)
        meta = _PLUGIN_META.get(name, {})
        result.append({
            "name": name,
            "icon": meta.get("icon", p.get("icon", "🔌")),
            "description": meta.get("description", p.get("description", "")),
            "installed": p.get("installed", name in installed),
            "docs_url": meta.get("docs_url", ""),
        })

    # Add catalogue items not returned by CLI
    for name, meta in _PLUGIN_META.items():
        if name in seen:
            continue
        result.append({
            "name": name,
            "icon": meta.get("icon", "🔌"),
            "description": meta.get("description", ""),
            "installed": name in installed,
            "docs_url": meta.get("docs_url", ""),
        })

    result.sort(key=lambda x: (not x["installed"], x["name"]))
    return result


async def install_plugin(name: str) -> dict[str, Any]:
    """Install an OpenClaw plugin by name."""
    rc, out, err = await _run_openclaw("plugins", "install", name, timeout=60)
    if rc != 0:
        raise RuntimeError(err.strip() or f"install failed (exit {rc})")
    return {"status": "installed", "name": name, "output": out.strip()}


async def remove_plugin(name: str) -> dict[str, Any]:
    """Remove an OpenClaw plugin by name."""
    rc, out, err = await _run_openclaw("plugins", "remove", name, timeout=30)
    if rc != 0:
        raise RuntimeError(err.strip() or f"remove failed (exit {rc})")
    return {"status": "removed", "name": name, "output": out.strip()}


# ── Skill functions ───────────────────────────────────────────────────────────

async def list_skills() -> list[dict[str, Any]]:
    """Return workspace-installed skills + eligible bundled skills.

    Workspace skills at ~/.openclaw/workspace/skills/ are Zoe-custom SKILL.md
    files managed outside the `openclaw skills` CLI. They are enumerated directly
    from the filesystem and merged with the CLI output for bundled/eligible skills.
    """
    # 1. Enumerate workspace skills from filesystem directly
    workspace_skills_dir = _OPENCLAW_DIR / "workspace" / "skills"
    workspace_names: set[str] = set()
    result: list[dict[str, Any]] = []

    if workspace_skills_dir.exists():
        for entry in sorted(workspace_skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            workspace_names.add(name)
            # Try to read a one-line description from SKILL.md
            description = ""
            for fname in ("SKILL.md", "skill.md"):
                skill_md = entry / fname
                if skill_md.exists():
                    try:
                        first_line = skill_md.read_text(encoding="utf-8", errors="replace").splitlines()
                        for line in first_line:
                            line = line.strip().lstrip("#").strip()
                            if line:
                                description = line[:120]
                                break
                    except OSError:
                        pass
                    break
            result.append({
                "name": name,
                "description": description,
                "icon": "📁",
                "installed": True,
                "source": "workspace",
                "eligible": True,
                "missing": {},
                "homepage": "",
                "allowed": True,  # already installed; the allowlist gate is irrelevant
            })

    # 2. Get eligible bundled skills from CLI (exclude any already in workspace)
    rc, out, _ = await _run_openclaw("skills", "list", "--json")
    if rc == 0 and out.strip():
        try:
            raw = json.loads(out)
            cli_skills = raw if isinstance(raw, list) else raw.get("skills", [])
            for s in cli_skills:
                name = s.get("name", "")
                if not name or name in workspace_names:
                    continue
                source = s.get("source", "openclaw-bundled")
                is_eligible = s.get("eligible", False)
                if not is_eligible:
                    continue
                raw_missing = s.get("missing", {})
                result.append({
                    "name": name,
                    "description": s.get("description", ""),
                    "icon": s.get("emoji", "🔧"),
                    "installed": False,
                    "source": source,
                    "eligible": True,
                    "missing": {k: v for k, v in raw_missing.items() if v},
                    "homepage": s.get("homepage", ""),
                    "allowed": _skill_is_allowed(name, source),
                })
        except json.JSONDecodeError:
            pass

    result.sort(key=lambda x: (not x["installed"], x["name"]))
    return result


async def install_skill(
    name: str,
    version: str | None = None,
    force: bool = False,
    source: str = "clawhub",
) -> dict[str, Any]:
    """Install a skill. Raises PermissionError if not in allowlist; RuntimeError on CLI failure."""
    if not _skill_is_allowed(name, source):
        raise PermissionError(
            f"'{name}' is not in the Zoe skill allowlist. "
            "Use the preview endpoint to review SKILL.md, then add the name to "
            "_SKILLS_ALLOWLIST in openclaw_manager.py to proceed."
        )

    # Bundled skills live inside the OpenClaw package; `openclaw skills install`
    # fetches from ClawHub and always returns 404 for them.  Copy locally instead.
    if source == "openclaw-bundled":
        rc, out, _ = await _run_openclaw("skills", "info", name, "--json", timeout=10)
        if rc != 0:
            raise RuntimeError(f"Cannot find bundled skill '{name}'")
        try:
            info = json.loads(out)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Malformed info for bundled skill '{name}'") from exc
        base_dir = info.get("baseDir")
        if not base_dir or not Path(base_dir).is_dir():
            raise RuntimeError(f"Bundled skill '{name}' has no baseDir")
        dest = _OPENCLAW_DIR / "workspace" / "skills" / name
        if dest.exists():
            if not force:
                shutil.rmtree(dest)  # treat re-install as force-update
            else:
                shutil.rmtree(dest)
        try:
            shutil.copytree(base_dir, dest)
        except OSError as exc:
            raise RuntimeError(f"Failed to copy bundled skill '{name}': {exc}") from exc
        return {"status": "installed", "name": name, "version": None, "output": f"Copied from {base_dir}"}

    args: list[str] = ["skills", "install", name]
    if version:
        args += ["--version", version]
    if force:
        args += ["--force"]
    rc, out, err = await _run_openclaw(*args, timeout=60)
    if rc != 0:
        err_msg = err.strip() or f"install failed (exit {rc})"
        # Skill already in workspace — auto-retry with --force so Install = install-or-update
        if "already exists" in err_msg.lower() and "--force" not in args:
            args += ["--force"]
            rc, out, err = await _run_openclaw(*args, timeout=60)
            if rc != 0:
                raise RuntimeError(err.strip() or f"force-reinstall failed (exit {rc})")
            return {"status": "reinstalled", "name": name, "version": version, "output": out.strip()}
        raise RuntimeError(err_msg)
    return {"status": "installed", "name": name, "version": version, "output": out.strip()}


async def update_skill(name: str) -> dict[str, Any]:
    """Update a workspace skill from ClawHub."""
    rc, out, err = await _run_openclaw("skills", "update", name, timeout=60)
    if rc != 0:
        raise RuntimeError(err.strip() or f"update failed (exit {rc})")
    return {"status": "updated", "name": name, "output": out.strip()}


async def remove_skill(name: str) -> dict[str, Any]:
    """Remove a workspace skill by deleting its directory."""
    skill_dir = _OPENCLAW_DIR / "workspace" / "skills" / name
    if not skill_dir.exists():
        raise RuntimeError(f"Skill '{name}' not found in workspace")
    try:
        shutil.rmtree(skill_dir)
    except OSError as exc:
        raise RuntimeError(f"Failed to remove skill '{name}': {exc}") from exc
    return {"status": "removed", "name": name}


async def preview_skill(name: str) -> dict[str, Any]:
    """Read a skill's SKILL.md and run an automated security scan.

    Sources checked in order:
    1. Workspace skill  — ~/.openclaw/workspace/skills/{name}/SKILL.md
    2. Bundled skill    — filePath from `openclaw skills info {name} --json`
    """
    content: str | None = None
    source = "unknown"

    # 1. Workspace skill — ~/.openclaw/workspace/skills/{name}/SKILL.md
    skill_ws_dir = _OPENCLAW_DIR / "workspace" / "skills" / name
    if skill_ws_dir.exists():
        for fname in ("SKILL.md", "skill.md"):
            p = skill_ws_dir / fname
            if p.exists():
                content = p.read_text(encoding="utf-8", errors="replace")
                source = "workspace"
                break

    # 2. Bundled skill via CLI info
    if content is None:
        rc, out, _ = await _run_openclaw("skills", "info", name, "--json", timeout=10)
        if rc == 0 and out.strip():
            try:
                info = json.loads(out)
                fp = info.get("filePath")
                if fp:
                    skill_md = Path(fp)
                    # filePath may point to the directory; look for SKILL.md inside
                    if skill_md.is_dir():
                        for fname in ("SKILL.md", "skill.md"):
                            candidate = skill_md / fname
                            if candidate.exists():
                                skill_md = candidate
                                break
                    if skill_md.is_file():
                        content = skill_md.read_text(encoding="utf-8", errors="replace")
                        source = "openclaw-bundled"
            except (json.JSONDecodeError, OSError):
                pass

    if content is None:
        return {
            "name": name,
            "content": None,
            "source": source,
            "security": None,
            "error": "SKILL.md not found locally — ClawHub preview requires network access",
        }

    security = _scan_skill_content(content)
    return {
        "name": name,
        "content": content,
        "source": source,
        "security": security,
        "error": None,
    }


async def search_clawhub_skills(query: str) -> dict[str, Any]:
    """Search ClawHub registry. Returns results or a graceful offline error."""
    rc, out, err = await _run_openclaw("skills", "search", query, "--json", timeout=10)
    if rc != 0:
        return {
            "results": [],
            "error": "ClawHub search requires internet access",
            "offline": True,
        }
    try:
        data = json.loads(out)
        results = data if isinstance(data, list) else data.get("results", data)
        return {"results": results, "offline": False, "error": None}
    except (json.JSONDecodeError, Exception):
        return {
            "results": [],
            "error": "Could not parse ClawHub results",
            "offline": True,
        }
