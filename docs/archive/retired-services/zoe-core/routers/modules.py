"""
Modules Management Router
===========================

Phase 1b: API endpoints for module management.

Provides:
- GET /api/modules/enabled    -- List enabled modules with ports and widget support
- GET /api/modules/{name}     -- Get module details and health
- POST /api/modules/{name}/enable  -- Enable a module (requires restart)
- POST /api/modules/{name}/disable -- Disable a module (requires restart)
"""

import os
import logging
import httpx
import yaml
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, List, Optional, Any
from auth_integration import validate_session, AuthenticatedSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/modules", tags=["modules"])

MODULES_CONFIG = os.getenv("MODULES_CONFIG", "/app/config/modules.yaml")
MODULES_DIR = os.getenv("MODULES_DIR", "/app/modules")


def load_modules_config() -> Dict[str, Any]:
    """Load modules.yaml configuration."""
    try:
        if not os.path.exists(MODULES_CONFIG):
            return {"enabled_modules": [], "module_config": {}}
        with open(MODULES_CONFIG, "r") as f:
            return yaml.safe_load(f) or {"enabled_modules": [], "module_config": {}}
    except Exception as e:
        logger.error(f"Failed to load modules config: {e}")
        return {"enabled_modules": [], "module_config": {}}


def get_module_info(module_name: str) -> Dict[str, Any]:
    """Get module info from its directory and compose file."""
    module_dir = os.path.join(MODULES_DIR, module_name)
    info = {
        "name": module_name,
        "path": module_dir,
        "has_intents": False,
        "has_skills": False,
        "has_widgets": False,
        "has_compose": False,
        "services": [],
        "port": None,
    }

    if not os.path.isdir(module_dir):
        return info

    # Check for intents
    intents_dir = os.path.join(module_dir, "intents")
    info["has_intents"] = os.path.isdir(intents_dir)

    # Check for skills
    skills_dir = os.path.join(module_dir, "skills")
    info["has_skills"] = os.path.isdir(skills_dir)

    # Check for widgets (look for manifest.json in widget/)
    widget_dir = os.path.join(module_dir, "widget")
    if os.path.isdir(widget_dir):
        manifest = os.path.join(widget_dir, "manifest.json")
        info["has_widgets"] = os.path.isfile(manifest)

    # Check for docker-compose
    compose_file = os.path.join(module_dir, "docker-compose.module.yml")
    info["has_compose"] = os.path.isfile(compose_file)

    # Parse compose to get port and service names
    if info["has_compose"]:
        try:
            with open(compose_file, "r") as f:
                compose = yaml.safe_load(f)
            services = compose.get("services", {})
            info["services"] = list(services.keys())
            # Try to find the main port
            for svc_name, svc_config in services.items():
                ports = svc_config.get("ports", [])
                if ports:
                    # Parse "8100:8100" -> 8100
                    port_str = str(ports[0]).split(":")[0]
                    try:
                        info["port"] = int(port_str)
                    except ValueError:
                        pass
                    break
        except Exception as e:
            logger.warning(f"Failed to parse module compose for {module_name}: {e}")

    return info


@router.get("/enabled")
async def get_enabled_modules(session: AuthenticatedSession = Depends(validate_session)):
    """Return enabled modules with their ports and widget support.

    Used by the frontend widget loader for dynamic discovery.
    """
    config = load_modules_config()
    enabled = config.get("enabled_modules", [])

    modules = []
    for module_name in enabled:
        info = get_module_info(module_name)
        modules.append({
            "name": info["name"],
            "port": info["port"],
            "has_widgets": info["has_widgets"],
            "has_intents": info["has_intents"],
            "has_skills": info["has_skills"],
            "services": info["services"],
        })

    return {"modules": modules, "count": len(modules)}


@router.get("/available")
async def get_available_modules(session: AuthenticatedSession = Depends(validate_session)):
    """List all available modules (enabled and disabled)."""
    config = load_modules_config()
    enabled = set(config.get("enabled_modules", []))

    modules = []
    modules_path = MODULES_DIR

    if os.path.isdir(modules_path):
        for entry in sorted(os.listdir(modules_path)):
            module_dir = os.path.join(modules_path, entry)
            if os.path.isdir(module_dir):
                info = get_module_info(entry)
                info["enabled"] = entry in enabled
                modules.append(info)

    return {"modules": modules, "count": len(modules)}


@router.get("/{module_name}")
async def get_module_detail(
    module_name: str,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Get detailed information about a specific module, including health."""
    info = get_module_info(module_name)

    config = load_modules_config()
    info["enabled"] = module_name in config.get("enabled_modules", [])

    # Check service health if enabled
    health_status = {}
    if info["enabled"]:
        for svc_name in info["services"]:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"http://{svc_name}:80/health")
                    health_status[svc_name] = "healthy" if resp.status_code < 400 else "unhealthy"
            except Exception:
                # Try common alternate ports
                for port in [8000, 8100, 8101]:
                    try:
                        async with httpx.AsyncClient(timeout=3.0) as client:
                            resp = await client.get(f"http://{svc_name}:{port}/health")
                            health_status[svc_name] = "healthy" if resp.status_code < 400 else "unhealthy"
                            break
                    except Exception:
                        continue
                if svc_name not in health_status:
                    health_status[svc_name] = "unreachable"

    info["health"] = health_status
    return info


@router.post("/{module_name}/enable")
async def enable_module(
    module_name: str,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Enable a module. Requires service restart to take effect."""
    module_dir = os.path.join(MODULES_DIR, module_name)
    if not os.path.isdir(module_dir):
        raise HTTPException(status_code=404, detail=f"Module '{module_name}' not found")

    config = load_modules_config()
    enabled = config.get("enabled_modules", [])

    if module_name in enabled:
        return {"success": True, "message": f"Module '{module_name}' is already enabled"}

    enabled.append(module_name)
    config["enabled_modules"] = enabled

    try:
        with open(MODULES_CONFIG, "w") as f:
            yaml.dump(config, f, default_flow_style=False)

        logger.info(f"Module {module_name} enabled in config")
        return {
            "success": True,
            "message": f"Module '{module_name}' enabled. Run 'docker compose restart' to apply.",
            "restart_required": True,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{module_name}/disable")
async def disable_module(
    module_name: str,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Disable a module. Requires service restart to take effect."""
    config = load_modules_config()
    enabled = config.get("enabled_modules", [])

    if module_name not in enabled:
        return {"success": True, "message": f"Module '{module_name}' is already disabled"}

    enabled.remove(module_name)
    config["enabled_modules"] = enabled

    try:
        with open(MODULES_CONFIG, "w") as f:
            yaml.dump(config, f, default_flow_style=False)

        logger.info(f"Module {module_name} disabled in config")
        return {
            "success": True,
            "message": f"Module '{module_name}' disabled. Run 'docker compose restart' to apply.",
            "restart_required": True,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
