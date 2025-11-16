"""
Docker Management & Monitoring for Zoe Developer Environment
=============================================================

Provides comprehensive Docker container management with real-time monitoring:
- Container stats (CPU, memory, network, disk I/O)
- Container logs with filtering
- Image management
- Network inspection
- Volume management
- Docker events stream
"""

import docker
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import asyncio

logger = logging.getLogger(__name__)

class DockerManager:
    """Manages Docker operations and monitoring"""
    
    def __init__(self):
        try:
            self.client = docker.from_env()
            self.client.ping()
            logger.info("✅ Docker client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Docker client: {e}")
            self.client = None
    
    def is_available(self) -> bool:
        """Check if Docker is available"""
        return self.client is not None
    
    def list_containers(self, all: bool = True) -> List[Dict[str, Any]]:
        """List all containers with their status"""
        if not self.is_available():
            return []
        
        try:
            containers = self.client.containers.list(all=all)
            result = []
            
            for container in containers:
                result.append({
                    "id": container.id[:12],
                    "name": container.name,
                    "image": container.image.tags[0] if container.image.tags else "unknown",
                    "status": container.status,
                    "state": container.attrs["State"],
                    "created": container.attrs["Created"],
                    "ports": container.attrs.get("NetworkSettings", {}).get("Ports", {}),
                    "networks": list(container.attrs.get("NetworkSettings", {}).get("Networks", {}).keys())
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to list containers: {e}")
            return []
    
    def get_container_stats(self, container_id: str) -> Optional[Dict[str, Any]]:
        """Get real-time stats for a container"""
        if not self.is_available():
            return None
        
        try:
            container = self.client.containers.get(container_id)
            stats = container.stats(stream=False)
            
            # Parse stats
            cpu_stats = stats.get("cpu_stats", {})
            precpu_stats = stats.get("precpu_stats", {})
            memory_stats = stats.get("memory_stats", {})
            
            # Calculate CPU percentage
            cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - \
                       precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
            system_delta = cpu_stats.get("system_cpu_usage", 0) - \
                          precpu_stats.get("system_cpu_usage", 0)
            cpu_count = cpu_stats.get("online_cpus", 1)
            
            cpu_percent = 0.0
            if system_delta > 0:
                cpu_percent = (cpu_delta / system_delta) * cpu_count * 100.0
            
            # Calculate memory percentage
            mem_usage = memory_stats.get("usage", 0)
            mem_limit = memory_stats.get("limit", 1)
            mem_percent = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0.0
            
            # Network stats
            networks = stats.get("networks", {})
            net_rx = sum(net.get("rx_bytes", 0) for net in networks.values())
            net_tx = sum(net.get("tx_bytes", 0) for net in networks.values())
            
            # Block I/O stats
            blkio_stats = stats.get("blkio_stats", {}).get("io_service_bytes_recursive", [])
            blk_read = sum(item.get("value", 0) for item in blkio_stats if item.get("op") == "Read")
            blk_write = sum(item.get("value", 0) for item in blkio_stats if item.get("op") == "Write")
            
            return {
                "container_id": container_id,
                "container_name": container.name,
                "timestamp": datetime.now().isoformat(),
                "cpu_percent": round(cpu_percent, 2),
                "memory_usage_mb": round(mem_usage / (1024 * 1024), 2),
                "memory_limit_mb": round(mem_limit / (1024 * 1024), 2),
                "memory_percent": round(mem_percent, 2),
                "network_rx_mb": round(net_rx / (1024 * 1024), 2),
                "network_tx_mb": round(net_tx / (1024 * 1024), 2),
                "block_read_mb": round(blk_read / (1024 * 1024), 2),
                "block_write_mb": round(blk_write / (1024 * 1024), 2)
            }
            
        except Exception as e:
            logger.error(f"Failed to get container stats: {e}")
            return None
    
    def get_all_stats(self) -> List[Dict[str, Any]]:
        """Get stats for all running containers"""
        if not self.is_available():
            return []
        
        containers = self.list_containers(all=False)  # Only running
        stats = []
        
        for container in containers:
            container_stats = self.get_container_stats(container["id"])
            if container_stats:
                stats.append(container_stats)
        
        return stats
    
    def start_container(self, container_id: str) -> bool:
        """Start a container"""
        if not self.is_available():
            return False
        
        try:
            container = self.client.containers.get(container_id)
            container.start()
            logger.info(f"✅ Started container: {container.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to start container: {e}")
            return False
    
    def stop_container(self, container_id: str, timeout: int = 10) -> bool:
        """Stop a container"""
        if not self.is_available():
            return False
        
        try:
            container = self.client.containers.get(container_id)
            container.stop(timeout=timeout)
            logger.info(f"✅ Stopped container: {container.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to stop container: {e}")
            return False
    
    def restart_container(self, container_id: str, timeout: int = 10) -> bool:
        """Restart a container"""
        if not self.is_available():
            return False
        
        try:
            container = self.client.containers.get(container_id)
            container.restart(timeout=timeout)
            logger.info(f"✅ Restarted container: {container.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to restart container: {e}")
            return False
    
    def get_container_logs(self, container_id: str, tail: int = 100, since: Optional[str] = None) -> str:
        """Get container logs"""
        if not self.is_available():
            return ""
        
        try:
            container = self.client.containers.get(container_id)
            logs = container.logs(
                tail=tail,
                since=since,
                timestamps=True
            ).decode('utf-8', errors='replace')
            return logs
        except Exception as e:
            logger.error(f"Failed to get container logs: {e}")
            return f"Error: {str(e)}"
    
    def exec_command(self, container_id: str, command: str) -> Dict[str, Any]:
        """Execute a command inside a container"""
        if not self.is_available():
            return {"success": False, "error": "Docker not available"}
        
        try:
            container = self.client.containers.get(container_id)
            result = container.exec_run(command)
            
            return {
                "success": result.exit_code == 0,
                "exit_code": result.exit_code,
                "output": result.output.decode('utf-8', errors='replace')
            }
        except Exception as e:
            logger.error(f"Failed to exec command: {e}")
            return {"success": False, "error": str(e)}
    
    def list_images(self) -> List[Dict[str, Any]]:
        """List all Docker images"""
        if not self.is_available():
            return []
        
        try:
            images = self.client.images.list()
            result = []
            
            for image in images:
                result.append({
                    "id": image.id[:12],
                    "tags": image.tags,
                    "created": image.attrs.get("Created"),
                    "size_mb": round(image.attrs.get("Size", 0) / (1024 * 1024), 2)
                })
            
            return result
        except Exception as e:
            logger.error(f"Failed to list images: {e}")
            return []
    
    def pull_image(self, image_name: str, tag: str = "latest") -> bool:
        """Pull a Docker image"""
        if not self.is_available():
            return False
        
        try:
            self.client.images.pull(image_name, tag=tag)
            logger.info(f"✅ Pulled image: {image_name}:{tag}")
            return True
        except Exception as e:
            logger.error(f"Failed to pull image: {e}")
            return False
    
    def remove_image(self, image_id: str, force: bool = False) -> bool:
        """Remove a Docker image"""
        if not self.is_available():
            return False
        
        try:
            self.client.images.remove(image_id, force=force)
            logger.info(f"✅ Removed image: {image_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove image: {e}")
            return False
    
    def list_networks(self) -> List[Dict[str, Any]]:
        """List Docker networks"""
        if not self.is_available():
            return []
        
        try:
            networks = self.client.networks.list()
            result = []
            
            for network in networks:
                result.append({
                    "id": network.id[:12],
                    "name": network.name,
                    "driver": network.attrs.get("Driver"),
                    "scope": network.attrs.get("Scope"),
                    "containers": len(network.attrs.get("Containers", {}))
                })
            
            return result
        except Exception as e:
            logger.error(f"Failed to list networks: {e}")
            return []
    
    def list_volumes(self) -> List[Dict[str, Any]]:
        """List Docker volumes"""
        if not self.is_available():
            return []
        
        try:
            volumes = self.client.volumes.list()
            result = []
            
            for volume in volumes:
                result.append({
                    "name": volume.name,
                    "driver": volume.attrs.get("Driver"),
                    "mountpoint": volume.attrs.get("Mountpoint"),
                    "created": volume.attrs.get("CreatedAt")
                })
            
            return result
        except Exception as e:
            logger.error(f"Failed to list volumes: {e}")
            return []
    
    def get_system_df(self) -> Dict[str, Any]:
        """Get Docker disk usage"""
        if not self.is_available():
            return {}
        
        try:
            df = self.client.df()
            
            return {
                "images": {
                    "count": len(df.get("Images", [])),
                    "size_mb": round(sum(img.get("Size", 0) for img in df.get("Images", [])) / (1024 * 1024), 2)
                },
                "containers": {
                    "count": len(df.get("Containers", [])),
                    "size_mb": round(sum(c.get("SizeRw", 0) for c in df.get("Containers", [])) / (1024 * 1024), 2)
                },
                "volumes": {
                    "count": len(df.get("Volumes", [])),
                    "size_mb": round(sum(v.get("UsageData", {}).get("Size", 0) for v in df.get("Volumes", [])) / (1024 * 1024), 2)
                }
            }
        except Exception as e:
            logger.error(f"Failed to get system df: {e}")
            return {}
    
    async def stream_events(self, filters: Optional[Dict] = None):
        """Stream Docker events"""
        if not self.is_available():
            return
        
        try:
            for event in self.client.events(decode=True, filters=filters):
                yield event
        except Exception as e:
            logger.error(f"Failed to stream events: {e}")

# Global instance
docker_manager = DockerManager()


