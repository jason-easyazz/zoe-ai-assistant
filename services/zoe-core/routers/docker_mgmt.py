"""
Docker Management API Router
=============================

Provides REST API for Docker container management:
- Container control (start/stop/restart)
- Real-time stats monitoring
- Logs streaming
- Image management
- Network and volume inspection
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from auth_integration import validate_session, AuthenticatedSession
import logging
import sys
import os

# Add parent directory to path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from developer.docker.manager import docker_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/docker", tags=["docker"])

class CommandExec(BaseModel):
    command: str

@router.get("/containers")
async def list_containers(all: bool = True, session: AuthenticatedSession = Depends(validate_session)):
    """List all Docker containers"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    containers = docker_manager.list_containers(all=all)
    return {
        "containers": containers,
        "count": len(containers)
    }

@router.get("/containers/{container_id}/stats")
async def get_container_stats(container_id: str):
    """Get real-time stats for a container"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    stats = docker_manager.get_container_stats(container_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Container not found or stats unavailable")
    
    return stats

@router.get("/stats")
async def get_all_stats():
    """Get stats for all running containers"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    stats = docker_manager.get_all_stats()
    return {
        "stats": stats,
        "count": len(stats)
    }

@router.post("/containers/{container_id}/start")
async def start_container(container_id: str):
    """Start a container"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    success = docker_manager.start_container(container_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to start container")
    
    return {"success": True, "message": f"Container {container_id} started"}

@router.post("/containers/{container_id}/stop")
async def stop_container(container_id: str, timeout: int = 10):
    """Stop a container"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    success = docker_manager.stop_container(container_id, timeout)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to stop container")
    
    return {"success": True, "message": f"Container {container_id} stopped"}

@router.post("/containers/{container_id}/restart")
async def restart_container(container_id: str, timeout: int = 10):
    """Restart a container"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    success = docker_manager.restart_container(container_id, timeout)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to restart container")
    
    return {"success": True, "message": f"Container {container_id} restarted"}

@router.get("/containers/{container_id}/logs")
async def get_container_logs(
    container_id: str,
    tail: int = Query(default=100, ge=1, le=1000),
    since: Optional[str] = None
):
    """Get container logs"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    logs = docker_manager.get_container_logs(container_id, tail, since)
    return {
        "container_id": container_id,
        "logs": logs,
        "lines": len(logs.split('\n'))
    }

@router.post("/containers/{container_id}/exec")
async def exec_command_in_container(container_id: str, exec_request: CommandExec):
    """Execute a command inside a container"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    result = docker_manager.exec_command(container_id, exec_request.command)
    if not result.get("success"):
        return {"success": False, "error": result.get("error")}
    
    return result

@router.get("/images")
async def list_images():
    """List all Docker images"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    images = docker_manager.list_images()
    return {
        "images": images,
        "count": len(images)
    }

@router.post("/images/pull")
async def pull_image(image_name: str, tag: str = "latest"):
    """Pull a Docker image"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    success = docker_manager.pull_image(image_name, tag)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to pull image")
    
    return {"success": True, "message": f"Image {image_name}:{tag} pulled successfully"}

@router.delete("/images/{image_id}")
async def remove_image(image_id: str, force: bool = False):
    """Remove a Docker image"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    success = docker_manager.remove_image(image_id, force)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to remove image")
    
    return {"success": True, "message": f"Image {image_id} removed"}

@router.get("/networks")
async def list_networks():
    """List Docker networks"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    networks = docker_manager.list_networks()
    return {
        "networks": networks,
        "count": len(networks)
    }

@router.get("/volumes")
async def list_volumes():
    """List Docker volumes"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    volumes = docker_manager.list_volumes()
    return {
        "volumes": volumes,
        "count": len(volumes)
    }

@router.get("/system/df")
async def get_system_disk_usage():
    """Get Docker disk usage"""
    if not docker_manager.is_available():
        raise HTTPException(status_code=503, detail="Docker service unavailable")
    
    df = docker_manager.get_system_df()
    return df

@router.get("/status")
async def get_docker_status():
    """Get Docker service status"""
    return {
        "available": docker_manager.is_available(),
        "service": "Docker Management API",
        "version": "3.0.0"
    }

