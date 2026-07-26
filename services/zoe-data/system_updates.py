"""
system_updates.py — Aggregated version + health snapshot for all Zoe components.

Provides the four symbols imported by services/zoe-data/routers/system.py:
  build_updates_snapshot, install_component, installable_components, invalidate_cache

Also exports:
  start_zoe_update_background_tasks  — called from main.py lifespan
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

import httpx


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPOSE_FILE = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "docker-compose.yml"))
COMPOSE_PROJECT_DIR = os.path.dirname(COMPOSE_FILE)
VERSION_FILE = os.path.join(COMPOSE_PROJECT_DIR, "VERSION")

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------
_CACHE: dict | None = None
_CACHE_AT: float = 0.0
_CACHE_TTL: int = 300  # seconds

# Cached latest GitHub release tag (refreshed with snapshot)
_GITHUB_LATEST: str | None = None
_GITHUB_LATEST_AT: float = 0.0


def invalidate_cache() -> None:
    global _CACHE, _CACHE_AT, _GITHUB_LATEST, _GITHUB_LATEST_AT
    _CACHE = None
    _CACHE_AT = 0.0
    _GITHUB_LATEST = None
    _GITHUB_LATEST_AT = 0.0


# ---------------------------------------------------------------------------
# VERSION file helpers
# ---------------------------------------------------------------------------

def _read_local_version() -> str:
    """Read the installed Zoe platform version from the VERSION file."""
    try:
        with open(VERSION_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return "dev"


def _write_local_version(version: str) -> None:
    """Write a new version to the VERSION file after a successful update."""
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(version.strip() + "\n")


def _is_dev_mode() -> bool:
    """
    True when VERSION does not start with 'v' (e.g. 'dev', 'unknown', '').
    In dev mode: no GitHub release notifications fire and git-backed tiles
    show update_available=False so the development unit is never nagged.
    """
    return not _read_local_version().startswith("v")


# ---------------------------------------------------------------------------
# Semver helpers
# ---------------------------------------------------------------------------

def _semver_key(tag: str) -> tuple[int, ...]:
    """Convert 'v2.1.0' → (2, 1, 0) for sorting. Unknown parts become 0."""
    nums = re.findall(r"\d+", tag)
    return tuple(int(n) for n in nums) if nums else (0,)


def _latest_semver(tags: list[str]) -> str | None:
    """Return the highest vX.Y.Z tag from a list, or None."""
    valid = [t for t in tags if t.startswith("v")]
    return max(valid, key=_semver_key) if valid else None


def _version_newer(latest: str | None, installed: str) -> bool:
    """True if latest > installed by semver comparison."""
    if not latest or not installed.startswith("v"):
        return False
    return _semver_key(latest) > _semver_key(installed)


# ---------------------------------------------------------------------------
# Component definitions
# ---------------------------------------------------------------------------
# Tuple: (id, display_name, detail, container_name, image_ref, local_build)
# local_build=True  → no registry digest check; install = rebuild from source
# local_build=False → registry digest comparison; install = docker compose pull + up -d
#
# Git-backed components share the Zoe platform VERSION and are updated atomically
# via _install_zoe_platform(). Identified by membership in _GIT_BACKED_IDS.
_DOCKER_SERVICES: list[tuple[str, str, str, str, str, bool]] = [
    (
        "zoe-ui",
        "Touch UI",
        "Web interface (content from git, served by nginx)",
        "zoe-ui",
        "nginx:alpine",
        False,
    ),
    (
        "zoe-auth",
        "Auth Service",
        "Authentication and session management",
        "zoe-auth",
        "",
        True,
    ),
    (
        "homeassistant",
        "Home Assistant",
        "Home automation hub",
        "homeassistant",
        "ghcr.io/home-assistant/home-assistant:stable",
        False,
    ),
    (
        "homeassistant-mcp-bridge",
        "HA MCP Bridge",
        "Home Assistant → MCP tool bridge",
        "homeassistant-mcp-bridge",
        "",
        True,
    ),
    (
        "keeper",
        "Keeper",
        "Scheduled task runner",
        "zoe-keeper",
        "ghcr.io/ridafkih/keeper.sh:latest",
        False,
    ),
    (
        "cloudflared",
        "Cloudflared Tunnel",
        "Cloudflare secure tunnel (optional profile)",
        "zoe-cloudflared",
        "cloudflare/cloudflared:latest",
        False,
    ),
]

# These Docker service IDs are built from the git repo and updated atomically
# via _install_zoe_platform() (git pull + docker rebuild).
_GIT_BACKED_IDS: frozenset[str] = frozenset({"zoe-ui", "zoe-auth", "homeassistant-mcp-bridge"})


def installable_components() -> list[str]:
    """Synchronous — called twice by system.py for validation and error messages."""
    docker_ids = [s[0] for s in _DOCKER_SERVICES]
    return docker_ids + ["zoe-data"]


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------

async def _run_cmd(*args: str, timeout: float = 15.0, cwd: str | None = None) -> tuple[int, str]:
    """Run an arbitrary command; return (returncode, combined_stdout_stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace")
    except asyncio.TimeoutError:
        return -1, f"Timed out after {timeout}s"
    except FileNotFoundError as exc:
        return -1, f"Command not found: {exc}"
    except Exception as exc:
        return -1, str(exc)


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

async def _docker_inspect(container_name: str) -> dict[str, Any] | None:
    """Return parsed docker inspect JSON for a single container, or None on failure."""
    rc, out = await _run_cmd(
        "docker", "inspect", container_name, "--format", "{{json .}}",
        timeout=10.0,
    )
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out.strip())
    except json.JSONDecodeError:
        return None


def _short_digest(sha256: str) -> str:
    """Return a 12-char short digest suitable for display."""
    if sha256.startswith("sha256:"):
        sha256 = sha256[7:]
    return sha256[:12]


# ---------------------------------------------------------------------------
# GitHub release fetch
# ---------------------------------------------------------------------------

async def _fetch_github_latest_release() -> str | None:
    """
    Return the highest vX.Y.Z tag from origin using git ls-remote.
    Uses the git credentials already embedded in the remote URL.
    Returns None on any failure (network, no tags, etc.).
    """
    rc, out = await _run_cmd(
        "git", "-C", COMPOSE_PROJECT_DIR,
        "ls-remote", "--tags", "origin", "refs/tags/v*",
        timeout=15.0,
    )
    if rc != 0 or not out.strip():
        return None
    # Each line: "<sha>\trefs/tags/v2.1.0"  or  "<sha>\trefs/tags/v2.1.0^{}"
    tags = [
        line.split("refs/tags/")[-1].strip()
        for line in out.strip().splitlines()
        if "refs/tags/v" in line and "^{}" not in line
    ]
    return _latest_semver(tags)


# ---------------------------------------------------------------------------
# Registry digest lookup (no layer download — read-only metadata only)
# ---------------------------------------------------------------------------

async def _registry_latest_digest(image: str) -> str | None:
    try:
        if image.startswith("ghcr.io/"):
            return await _ghcr_digest(image)
        return await _dockerhub_digest(image)
    except Exception as exc:
        logger.debug("_registry_latest_digest(%s) failed: %s", image, exc)
        return None


async def _dockerhub_digest(image: str) -> str | None:
    """Docker Hub REST API — no auth needed for public images."""
    tag = "latest"
    if ":" in image.split("/")[-1]:
        image_base, tag = image.rsplit(":", 1)
    else:
        image_base = image

    parts = image_base.split("/")
    if len(parts) == 1:
        namespace, repo = "library", parts[0]
    else:
        namespace, repo = parts[0], "/".join(parts[1:])

    url = f"https://hub.docker.com/v2/repositories/{namespace}/{repo}/tags/{tag}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            if r.status_code != 200:
                return None
            data = r.json()
            digest = data.get("digest")
            if not digest:
                images = data.get("images", [])
                if images:
                    digest = images[0].get("digest")
            return digest or None
    except Exception as exc:
        logger.debug("_dockerhub_digest(%s) failed: %s", image, exc)
        return None


async def _ghcr_digest(image: str) -> str | None:
    """GitHub Container Registry — unauthenticated for public packages."""
    without_host = image[len("ghcr.io/"):]
    tag = "latest"
    if ":" in without_host.split("/")[-1]:
        without_host, tag = without_host.rsplit(":", 1)

    url = f"https://ghcr.io/v2/{without_host}/manifests/{tag}"
    headers = {
        "Accept": (
            "application/vnd.docker.distribution.manifest.v2+json,"
            "application/vnd.oci.image.manifest.v1+json,"
            "application/vnd.docker.distribution.manifest.list.v2+json"
        )
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, headers=headers)
            if r.status_code == 200:
                digest = r.headers.get("Docker-Content-Digest") or r.headers.get("etag")
                return digest.strip('"') if digest else None
            if r.status_code == 401:
                token = await _ghcr_anon_token(without_host, client)
                if not token:
                    return None
                r2 = await client.get(url, headers={**headers, "Authorization": f"Bearer {token}"})
                if r2.status_code == 200:
                    digest = r2.headers.get("Docker-Content-Digest") or r2.headers.get("etag")
                    return digest.strip('"') if digest else None
            return None
    except Exception as exc:
        logger.debug("_ghcr_digest(%s) failed: %s", image, exc)
        return None


async def _ghcr_anon_token(scope_path: str, client: httpx.AsyncClient) -> str | None:
    try:
        r = await client.get(
            "https://ghcr.io/token",
            params={"scope": f"repository:{scope_path}:pull", "service": "ghcr.io"},
            timeout=5.0,
        )
        if r.status_code == 200:
            return r.json().get("token")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Per-component check functions
# ---------------------------------------------------------------------------

async def _check_docker_service(
    component_id: str,
    name: str,
    detail: str,
    container_name: str,
    image_ref: str,
    local_build: bool,
    github_latest_tag: str | None = None,
) -> dict:
    """Build a component dict for a Docker service."""
    info = await _docker_inspect(container_name)
    is_git_backed = component_id in _GIT_BACKED_IDS

    if info is None:
        if is_git_backed:
            local_ver = _read_local_version()
            return {
                "id": component_id,
                "name": name,
                "detail": detail,
                "version": local_ver,
                "latest": github_latest_tag or "—",
                "health": "offline",
                "update_available": (not _is_dev_mode()) and _version_newer(github_latest_tag, local_ver),
                "installable": True,
            }
        return {
            "id": component_id,
            "name": name,
            "detail": detail,
            "version": "not found",
            "latest": "local build" if local_build else "—",
            "health": "offline",
            "update_available": False,
            "installable": True,
        }

    state_status = info.get("State", {}).get("Status", "unknown")
    running = state_status == "running"
    running_image_id = info.get("Image", "")
    created = info.get("Created", "")[:10]

    # Git-backed components: version from VERSION file, latest from GitHub tags
    if is_git_backed:
        local_ver = _read_local_version()
        in_dev = _is_dev_mode()
        update_avail = (not in_dev) and _version_newer(github_latest_tag, local_ver)
        return {
            "id": component_id,
            "name": name,
            "detail": detail,
            "version": local_ver if not in_dev else f"dev ({created})",
            "latest": github_latest_tag if (not in_dev and github_latest_tag) else ("—" if in_dev else "—"),
            "health": "healthy" if running else ("warning" if state_status == "exited" else "offline"),
            "update_available": update_avail,
            "installable": not in_dev,
        }

    # Registry-tracked components
    config_image = info.get("Config", {}).get("Image", image_ref or container_name)
    tag_part = config_image.split(":")[-1] if ":" in config_image else config_image
    installed_version = tag_part if tag_part not in ("latest", "stable", "") else f"{tag_part} ({created})"

    if local_build:
        # local build, not git-backed — shouldn't normally happen with current config
        return {
            "id": component_id,
            "name": name,
            "detail": detail,
            "version": f"local build ({created})",
            "latest": "local build",
            "health": "healthy" if running else "offline",
            "update_available": False,
            "installable": True,
        }

    latest_digest = await _registry_latest_digest(image_ref)
    repo_digests = info.get("RepoDigests", [])
    running_digest = ""
    for rd in repo_digests:
        if "@sha256:" in rd:
            running_digest = rd.split("@sha256:")[-1]
            break
    running_short = running_digest[:12] if running_digest else _short_digest(running_image_id)

    if latest_digest is None:
        return {
            "id": component_id,
            "name": name,
            "detail": detail,
            "version": installed_version,
            "latest": "registry unavailable",
            "health": "healthy" if running else ("warning" if state_status == "exited" else "offline"),
            "update_available": False,
            "installable": True,
        }

    latest_short = _short_digest(latest_digest)
    update_available = latest_short != running_short

    return {
        "id": component_id,
        "name": name,
        "detail": detail,
        "version": installed_version,
        "latest": latest_short,
        "health": "healthy" if running else ("warning" if state_status == "exited" else "offline"),
        "update_available": update_available,
        "installable": True,
    }


async def _check_zoe_data(github_latest_tag: str | None = None) -> dict:
    """zoe-data host Python service — VERSION file + GitHub release tracking."""
    local_ver = _read_local_version()
    in_dev = _is_dev_mode()

    rc2, branch = await _run_cmd(
        "git", "-C", COMPOSE_PROJECT_DIR, "rev-parse", "--abbrev-ref", "HEAD",
        timeout=5.0,
    )
    branch_name = branch.strip() if rc2 == 0 else ""

    if in_dev:
        rc, sha = await _run_cmd(
            "git", "-C", COMPOSE_PROJECT_DIR, "rev-parse", "--short", "HEAD",
            timeout=8.0,
        )
        sha_str = sha.strip() if rc == 0 else "unknown"
        display = f"dev ({sha_str}"
        if branch_name and branch_name != "HEAD":
            display += f" on {branch_name}"
        display += ")"
    else:
        display = local_ver
        if branch_name and branch_name not in ("main", "HEAD"):
            display += f" (on {branch_name})"

    update_avail = (not in_dev) and _version_newer(github_latest_tag, local_ver)

    return {
        "id": "zoe-data",
        "name": "Zoe Brain API",
        "detail": "Core FastAPI service (host-run, not in Docker) — restart required after update",
        "version": display,
        "latest": github_latest_tag if (not in_dev and github_latest_tag) else "—",
        "health": "healthy",
        "update_available": update_avail,
        "installable": not in_dev,
    }


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------

async def build_updates_snapshot(db: Any, force: bool = False) -> dict:
    """
    Aggregate version + health for every tracked Zoe component.
    Returns {"fetched_at": <unix_seconds>, "components": [...]}.
    Cache TTL is 5 minutes; bypassed when force=True.
    """
    global _CACHE, _CACHE_AT, _GITHUB_LATEST, _GITHUB_LATEST_AT

    now = time.time()
    if not force and _CACHE is not None and (now - _CACHE_AT) < _CACHE_TTL:
        return _CACHE

    # Fetch latest GitHub release tag once for all git-backed components.
    # In dev mode we skip the network call entirely.
    github_latest: str | None = None
    if not _is_dev_mode():
        github_latest = await _fetch_github_latest_release()
        _GITHUB_LATEST = github_latest
        _GITHUB_LATEST_AT = now

    tasks = [
        _check_docker_service(svc_id, name, detail, container, image, local_build, github_latest)
        for svc_id, name, detail, container, image, local_build in _DOCKER_SERVICES
    ]
    tasks.append(_check_zoe_data(github_latest))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    n_docker = len(_DOCKER_SERVICES)
    components = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            if i < n_docker:
                label = _DOCKER_SERVICES[i][0]
            else:
                label = "zoe-data"
            logger.warning("build_updates_snapshot: check for %s raised: %s", label, r)
            components.append({
                "id": label,
                "name": label,
                "detail": "Check failed",
                "version": "—",
                "latest": "—",
                "health": "unknown",
                "update_available": False,
                "installable": False,
            })
        else:
            components.append(r)

    snapshot = {"fetched_at": int(now), "components": components}
    _CACHE = snapshot
    _CACHE_AT = now
    return snapshot


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

async def install_component(db: Any, user: dict, component: str) -> dict:
    """
    Install/upgrade a component. Returns {"ok": bool, "log": str}.
    Writes a row to update_history regardless of outcome.
    """
    # All git-backed components (and zoe-data) use the platform installer
    if component in _GIT_BACKED_IDS or component == "zoe-data":
        return await _install_zoe_platform(db, user)

    # Registry-tracked Docker services
    service = next((s for s in _DOCKER_SERVICES if s[0] == component), None)
    if service is None:
        raise ValueError(f"Unknown component: {component}")

    _svc_id, _name, _detail, container_name, image_ref, local_build = service

    info_before = await _docker_inspect(container_name)
    version_before: str | None = None
    if info_before:
        created = info_before.get("Created", "")[:10]
        ci = info_before.get("Config", {}).get("Image", "")
        tag = ci.split(":")[-1] if ":" in ci else ci
        version_before = tag if tag not in ("latest", "stable", "") else f"{tag} ({created})"

    ok, log = await _run_docker_install(container_name, component, local_build)

    info_after = await _docker_inspect(container_name)
    version_after: str | None = None
    if info_after:
        created = info_after.get("Created", "")[:10]
        ci = info_after.get("Config", {}).get("Image", "")
        tag = ci.split(":")[-1] if ":" in ci else ci
        version_after = tag if tag not in ("latest", "stable", "") else f"{tag} ({created})"

    await _write_update_history(db, component, version_before, version_after, ok, log, user)
    invalidate_cache()
    return {"ok": ok, "log": log}


async def _install_zoe_platform(db: Any, user: dict) -> dict:
    """
    Atomic update for all git-backed Zoe components:
      1. Pre-flight safety checks
      2. git fetch + merge --ff-only origin/main
      3. docker compose up --build -d for git-backed Docker services
      4. Write VERSION, update_history; return restart_required=True for zoe-data
    """
    logs: list[str] = []
    version_before = _read_local_version()

    # ── Pre-flight checks ────────────────────────────────────────────────────
    # 1. Working tree must be clean
    rc_st, out_st = await _run_cmd(
        "git", "-C", COMPOSE_PROJECT_DIR, "status", "--porcelain",
        timeout=8.0,
    )
    if rc_st != 0:
        raise ValueError(f"git status failed: {out_st.strip()}")
    if out_st.strip():
        raise ValueError(
            "There are uncommitted changes in the repository. "
            "Commit or stash them before installing an update.\n"
            + out_st.strip()[:500]
        )

    # 2. Must be on main branch
    rc_br, out_br = await _run_cmd(
        "git", "-C", COMPOSE_PROJECT_DIR, "rev-parse", "--abbrev-ref", "HEAD",
        timeout=5.0,
    )
    current_branch = out_br.strip() if rc_br == 0 else "unknown"
    if current_branch != "main":
        raise ValueError(
            f"Device is on branch '{current_branch}', not 'main'. "
            "Switch to main before installing updates: git checkout main"
        )

    # 3. Verify a newer release tag exists on the remote
    latest_tag = await _fetch_github_latest_release()
    if not latest_tag:
        raise ValueError("No release tags found on origin. Create a GitHub Release first.")
    if not _version_newer(latest_tag, version_before):
        raise ValueError(
            f"Already up to date ({version_before}). "
            "Force a re-install via git reset if needed."
        )

    logs.append(f"Pre-flight OK. Updating {version_before} → {latest_tag}\n")

    # ── Git update ────────────────────────────────────────────────────────────
    rc_fetch, out_fetch = await _run_cmd(
        "git", "-C", COMPOSE_PROJECT_DIR,
        "fetch", "--tags", "origin",
        timeout=30.0,
    )
    logs.append(f"--- git fetch ---\n{out_fetch}")
    if rc_fetch != 0:
        ok = False
        log = "\n".join(logs)[-8000:]
        await _write_update_history(db, "zoe-platform", version_before, None, ok, log, user)
        return {"ok": False, "log": log}

    rc_merge, out_merge = await _run_cmd(
        "git", "-C", COMPOSE_PROJECT_DIR,
        "merge", "--ff-only", "origin/main",
        timeout=60.0,
    )
    logs.append(f"--- git merge --ff-only ---\n{out_merge}")
    if rc_merge != 0:
        ok = False
        log = "\n".join(logs)[-8000:]
        await _write_update_history(db, "zoe-platform", version_before, None, ok, log, user)
        return {"ok": False, "log": log}

    # ── Docker rebuild for git-backed services ────────────────────────────────
    git_docker_services = [s[0] for s in _DOCKER_SERVICES if s[0] in _GIT_BACKED_IDS and s[5]]  # local_build only
    if git_docker_services:
        rc_build, out_build = await _run_cmd(
            "docker", "compose", "-f", COMPOSE_FILE,
            "up", "--build", "-d", *git_docker_services,
            timeout=300.0,
            cwd=COMPOSE_PROJECT_DIR,
        )
        logs.append(f"--- docker compose up --build ---\n{out_build}")
        if rc_build != 0:
            log = "\n".join(logs)[-8000:]
            await _write_update_history(db, "zoe-platform", version_before, None, False, log, user)
            return {"ok": False, "log": log}

    # zoe-ui: nginx serves bind-mounted dist/ so no rebuild needed; just reload
    rc_reload, out_reload = await _run_cmd(
        "docker", "exec", "zoe-ui", "nginx", "-s", "reload",
        timeout=15.0,
    )
    if rc_reload == 0:
        logs.append("--- nginx reload: OK ---")
    else:
        logs.append(f"--- nginx reload: skipped or failed (non-fatal) ---\n{out_reload}")

    # ── Write VERSION file ────────────────────────────────────────────────────
    try:
        _write_local_version(latest_tag)
        logs.append(f"VERSION updated to {latest_tag}")
    except Exception as exc:
        logs.append(f"WARNING: Could not write VERSION file: {exc}")

    log = "\n".join(logs)[-8000:]
    await _write_update_history(db, "zoe-platform", version_before, latest_tag, True, log, user)
    invalidate_cache()

    return {
        "ok": True,
        "log": log,
        "restart_required": True,
        "restart_cmd": "systemctl --user restart zoe-data.service",
        "message": (
            f"Zoe updated to {latest_tag}. "
            "Run 'systemctl --user restart zoe-data.service' to complete the update."
        ),
    }


async def _run_docker_install(container_name: str, service_name: str, local_build: bool) -> tuple[bool, str]:
    """Pull (or rebuild) and restart a registry-tracked Docker Compose service."""
    logs: list[str] = []

    if local_build:
        rc, out = await _run_cmd(
            "docker", "compose", "-f", COMPOSE_FILE,
            "up", "--build", "-d", service_name,
            timeout=300.0,
            cwd=COMPOSE_PROJECT_DIR,
        )
        logs.append(out)
        return rc == 0, "\n".join(logs)[-8000:]

    rc_pull, out_pull = await _run_cmd(
        "docker", "compose", "-f", COMPOSE_FILE,
        "pull", service_name,
        timeout=180.0,
        cwd=COMPOSE_PROJECT_DIR,
    )
    logs.append(f"--- pull ---\n{out_pull}")
    if rc_pull != 0:
        return False, "\n".join(logs)[-8000:]

    rc_up, out_up = await _run_cmd(
        "docker", "compose", "-f", COMPOSE_FILE,
        "up", "-d", service_name,
        timeout=60.0,
        cwd=COMPOSE_PROJECT_DIR,
    )
    logs.append(f"--- up ---\n{out_up}")
    return rc_up == 0, "\n".join(logs)[-8000:]


async def _write_update_history(
    db: Any,
    component: str,
    version_before: str | None,
    version_after: str | None,
    ok: bool,
    log: str,
    user: dict,
) -> None:
    try:
        initiated_by = user.get("username") or user.get("user_id") or "unknown"
        await db.execute(
            """INSERT INTO update_history
               (component, version_before, version_after, ok, log_excerpt, initiated_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, NOW())""",
            (
                component,
                version_before,
                version_after,
                1 if ok else 0,
                log[-4000:] if log else None,
                initiated_by,
            ),
        )
        await db.commit()
    except Exception as exc:
        logger.warning("_write_update_history failed: %s", exc)


# ---------------------------------------------------------------------------
# Background update notification loop
# ---------------------------------------------------------------------------

async def _check_and_notify_zoe_release(db: Any) -> None:
    """
    Check GitHub for a newer release and insert a notification if one is found.
    Skipped entirely in dev mode so the development unit is never nagged.
    """
    if _is_dev_mode():
        return

    latest = await _fetch_github_latest_release()
    current = _read_local_version()

    if not latest or not _version_newer(latest, current):
        return

    # Dedup: don't insert if there's already an unread zoe_update notification
    try:
        row = await (await db.execute(
            "SELECT id FROM notifications WHERE type = 'zoe_update' AND delivered = 0 LIMIT 1"
        )).fetchone()
        if row:
            return

        await db.execute(
            """INSERT INTO notifications
               (user_id, type, title, message, data, delivered, created_at)
               VALUES (?, ?, ?, ?, ?, 0, NOW())""",
            (
                "system",
                "zoe_update",
                f"Zoe {latest} available",
                "A new version of Zoe is ready to install.",
                json.dumps({"kind": "zoe_update", "latest": latest, "current": current}),
            ),
        )
        await db.commit()

        from push import broadcaster
        await broadcaster.broadcast("all", "notification_created", {
            "type": "zoe_update",
            "title": f"Zoe {latest} available",
            "message": "A new version of Zoe is ready to install.",
        })
        logger.info("zoe_update notification created for %s → %s", current, latest)

    except Exception as exc:
        logger.warning("_check_and_notify_zoe_release failed: %s", exc)


async def _zoe_update_background_loop() -> None:
    """Daily background loop: check GitHub releases and notify if newer version exists."""
    await asyncio.sleep(180)  # 3-minute startup delay, matching OpenClaw pattern
    while True:
        try:
            # get_db_ctx, not `async for db in get_db()`: the `break` leaked
            # the pooled connection (#953 / the 2026-07-03 pool drain).
            from db_pool import get_db_ctx
            async with get_db_ctx() as db:
                await _check_and_notify_zoe_release(db)
        except Exception as exc:
            logger.warning("_zoe_update_background_loop: %s", exc)
        await asyncio.sleep(86400)  # daily


def start_zoe_update_background_tasks() -> asyncio.Task | None:
    """
    Start the daily Zoe release check loop.
    Controlled by ZOE_UPDATE_CHECK_ENABLED env var (default: true).
    Returns the Task so main.py can cancel it on shutdown.
    """
    if os.environ.get("ZOE_UPDATE_CHECK_ENABLED", "true").lower() != "true":
        logger.info("Zoe update background check disabled (ZOE_UPDATE_CHECK_ENABLED=false)")
        return None
    return asyncio.create_task(_zoe_update_background_loop(), name="zoe_update_check")
