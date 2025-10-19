from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import subprocess
import json
import docker
import psutil
import time
from datetime import datetime, timedelta

router = APIRouter(prefix="/api/developer/health-dashboard")

class HealthStatus(BaseModel):
    service_name: str
    status: str
    uptime: str
    cpu_usage: float
    memory_usage: float
    last_restart: Optional[str] = None
    health_score: int

class SystemHealth(BaseModel):
    overall_status: str
    total_services: int
    healthy_services: int
    unhealthy_services: int
    system_cpu: float
    system_memory: float
    system_disk: float
    last_updated: str
    services: List[HealthStatus]

@router.get("/dashboard")
async def get_health_dashboard():
    """Get comprehensive health dashboard for all Zoe services"""
    try:
        import httpx
        
        # Get existing health data from the developer health endpoint
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8000/api/developer/health")
            health_data = response.json()
        
        # Process the health data
        services = []
        healthy_count = 0
        unhealthy_count = 0
        
        if 'services' in health_data:
            for service_name, service_info in health_data['services'].items():
                is_healthy = service_info.get('ok', False)
                status = 'running' if is_healthy else 'stopped'
                
                if is_healthy:
                    healthy_count += 1
                else:
                    unhealthy_count += 1
                
                # Calculate health score
                health_score = 100 if is_healthy else 0
                if 'latency_ms' in service_info:
                    latency = service_info['latency_ms']
                    if latency > 1000:
                        health_score -= 20
                    elif latency > 500:
                        health_score -= 10
                
                service = HealthStatus(
                    service_name=service_name.replace('zoe-', ''),
                    status=status,
                    uptime="unknown",  # We don't have uptime from the basic health check
                    cpu_usage=0.0,  # We don't have CPU data from basic health check
                    memory_usage=0.0,  # We don't have memory data from basic health check
                    last_restart=None,
                    health_score=health_score
                )
                services.append(service)
        
        # Get system stats
        system_cpu = psutil.cpu_percent(interval=1)
        system_memory = psutil.virtual_memory().percent
        system_disk = psutil.disk_usage('/').percent
        
        # Determine overall status
        if unhealthy_count == 0:
            overall_status = "healthy"
        elif healthy_count > unhealthy_count:
            overall_status = "degraded"
        else:
            overall_status = "critical"
        
        dashboard = SystemHealth(
            overall_status=overall_status,
            total_services=len(services),
            healthy_services=healthy_count,
            unhealthy_services=unhealthy_count,
            system_cpu=system_cpu,
            system_memory=system_memory,
            system_disk=system_disk,
            last_updated=datetime.now().isoformat(),
            services=services
        )
        
        return dashboard
        
    except Exception as e:
        # Return basic error response instead of raising exception
        return {
            "overall_status": "error",
            "total_services": 0,
            "healthy_services": 0,
            "unhealthy_services": 0,
            "system_cpu": 0.0,
            "system_memory": 0.0,
            "system_disk": 0.0,
            "last_updated": datetime.now().isoformat(),
            "services": [],
            "error": f"Error fetching health dashboard: {str(e)}"
        }

@router.get("/service/{service_name}")
async def get_service_health(service_name: str):
    """Get detailed health information for a specific service"""
    try:
        client = docker.DockerClient(base_url='unix://var/run/docker.sock')
        container = client.containers.get(f"zoe-{service_name}")
        
        # Get detailed stats
        stats = container.stats(stream=False)
        
        # Calculate detailed metrics
        cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage']['total_usage']
        system_delta = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
        cpu_percent = (cpu_delta / system_delta) * len(stats['cpu_stats']['cpu_usage']['percpu_usage']) * 100.0
        
        memory_usage = stats['memory_stats']['usage']
        memory_limit = stats['memory_stats']['limit']
        memory_percent = (memory_usage / memory_limit) * 100.0
        
        # Get network stats
        network_stats = stats['networks']
        network_io = {
            'rx_bytes': sum(net['rx_bytes'] for net in network_stats.values()),
            'tx_bytes': sum(net['tx_bytes'] for net in network_stats.values())
        }
        
        # Get container logs (last 50 lines)
        logs = container.logs(tail=50, timestamps=True).decode('utf-8')
        
        return {
            "service_name": service_name,
            "status": container.status,
            "cpu_usage": round(cpu_percent, 2),
            "memory_usage": round(memory_percent, 2),
            "memory_used_mb": round(memory_usage / 1024 / 1024, 2),
            "memory_limit_mb": round(memory_limit / 1024 / 1024, 2),
            "network_io": network_io,
            "logs": logs.split('\n')[-50:],  # Last 50 lines
            "created_at": container.attrs['Created'],
            "started_at": container.attrs['State']['StartedAt'],
            "restart_count": container.attrs['RestartCount']
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching service health: {str(e)}")

@router.post("/restart/{service_name}")
async def restart_service(service_name: str):
    """Restart a specific Zoe service"""
    try:
        client = docker.DockerClient(base_url='unix://var/run/docker.sock')
        container = client.containers.get(f"zoe-{service_name}")
        
        container.restart()
        
        return {
            "message": f"Service {service_name} restarted successfully",
            "service_name": service_name,
            "restarted_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error restarting service: {str(e)}")

@router.get("/alerts")
async def get_health_alerts():
    """Get current health alerts and warnings"""
    try:
        client = docker.DockerClient(base_url='unix://var/run/docker.sock')
        zoe_containers = client.containers.list(filters={"name": "zoe-"})
        
        alerts = []
        
        for container in zoe_containers:
            try:
                stats = container.stats(stream=False)
                
                # Check CPU usage
                cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage']['total_usage']
                system_delta = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
                cpu_percent = (cpu_delta / system_delta) * len(stats['cpu_stats']['cpu_usage']['percpu_usage']) * 100.0
                
                # Check memory usage
                memory_usage = stats['memory_stats']['usage']
                memory_limit = stats['memory_stats']['limit']
                memory_percent = (memory_usage / memory_limit) * 100.0
                
                service_name = container.name.replace('zoe-', '')
                
                # Generate alerts
                if container.status != 'running':
                    alerts.append({
                        "service": service_name,
                        "level": "critical",
                        "message": f"Service {service_name} is not running",
                        "timestamp": datetime.now().isoformat()
                    })
                elif cpu_percent > 90:
                    alerts.append({
                        "service": service_name,
                        "level": "critical",
                        "message": f"High CPU usage: {cpu_percent:.1f}%",
                        "timestamp": datetime.now().isoformat()
                    })
                elif cpu_percent > 70:
                    alerts.append({
                        "service": service_name,
                        "level": "warning",
                        "message": f"Elevated CPU usage: {cpu_percent:.1f}%",
                        "timestamp": datetime.now().isoformat()
                    })
                
                if memory_percent > 90:
                    alerts.append({
                        "service": service_name,
                        "level": "critical",
                        "message": f"High memory usage: {memory_percent:.1f}%",
                        "timestamp": datetime.now().isoformat()
                    })
                elif memory_percent > 80:
                    alerts.append({
                        "service": service_name,
                        "level": "warning",
                        "message": f"Elevated memory usage: {memory_percent:.1f}%",
                        "timestamp": datetime.now().isoformat()
                    })
                    
            except Exception as e:
                alerts.append({
                    "service": container.name.replace('zoe-', ''),
                    "level": "error",
                    "message": f"Unable to monitor service: {str(e)}",
                    "timestamp": datetime.now().isoformat()
                })
        
        return {
            "alerts": alerts,
            "total_alerts": len(alerts),
            "critical_alerts": len([a for a in alerts if a['level'] == 'critical']),
            "warning_alerts": len([a for a in alerts if a['level'] == 'warning'])
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching health alerts: {str(e)}")
