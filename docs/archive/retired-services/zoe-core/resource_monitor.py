"""
Resource Monitoring System for Zoe AI Assistant
Monitors CPU/RAM/Disk usage and throttles high resource tasks
"""
import psutil
import time
import threading
import logging
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
import json

logger = logging.getLogger(__name__)

class ResourceLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class ResourceThresholds:
    """Resource usage thresholds"""
    cpu_percent: float = 80.0
    memory_percent: float = 85.0
    disk_percent: float = 90.0
    memory_mb: float = 1000.0  # 1GB limit
    temperature_celsius: float = 80.0  # For Pi temperature monitoring

@dataclass
class ResourceMetrics:
    """Current resource metrics"""
    timestamp: datetime
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    disk_percent: float
    disk_used_gb: float
    disk_free_gb: float
    temperature: Optional[float] = None
    load_average: Optional[float] = None

class ResourceMonitor:
    """Comprehensive resource monitoring system"""
    
    def __init__(self, thresholds: ResourceThresholds = None):
        self.thresholds = thresholds or ResourceThresholds()
        self.metrics_history: List[ResourceMetrics] = []
        self.max_history = 1000  # Keep last 1000 measurements
        self.monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.alert_callbacks: List[Callable] = []
        self.throttled_tasks: Dict[str, float] = {}  # task_id -> throttle_factor
        
        # Resource levels
        self.current_level = ResourceLevel.LOW
        self.last_alert_time = {}
        
    def start_monitoring(self, interval: float = 5.0):
        """Start continuous resource monitoring"""
        if self.monitoring:
            return
        
        self.monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True
        )
        self.monitor_thread.start()
        logger.info(f"Resource monitoring started with {interval}s interval")
    
    def stop_monitoring(self):
        """Stop resource monitoring"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=1.0)
        logger.info("Resource monitoring stopped")
    
    def _monitor_loop(self, interval: float):
        """Main monitoring loop"""
        while self.monitoring:
            try:
                metrics = self._collect_metrics()
                self._update_metrics_history(metrics)
                self._check_thresholds(metrics)
                self._update_resource_level(metrics)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
            
            time.sleep(interval)
    
    def _collect_metrics(self) -> ResourceMetrics:
        """Collect current resource metrics"""
        # CPU usage
        cpu_percent = psutil.cpu_percent(interval=1)
        
        # Memory usage
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_used_mb = memory.used / (1024 * 1024)
        memory_available_mb = memory.available / (1024 * 1024)
        
        # Disk usage
        disk = psutil.disk_usage('/')
        disk_percent = (disk.used / disk.total) * 100
        disk_used_gb = disk.used / (1024 * 1024 * 1024)
        disk_free_gb = disk.free / (1024 * 1024 * 1024)
        
        # Temperature (for Raspberry Pi)
        temperature = self._get_cpu_temperature()
        
        # Load average
        load_average = psutil.getloadavg()[0] if hasattr(psutil, 'getloadavg') else None
        
        return ResourceMetrics(
            timestamp=datetime.now(),
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            memory_used_mb=memory_used_mb,
            memory_available_mb=memory_available_mb,
            disk_percent=disk_percent,
            disk_used_gb=disk_used_gb,
            disk_free_gb=disk_free_gb,
            temperature=temperature,
            load_average=load_average
        )
    
    def _get_cpu_temperature(self) -> Optional[float]:
        """Get CPU temperature (Raspberry Pi specific)"""
        try:
            # Try to read from thermal zone
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp_millicelsius = int(f.read().strip())
                return temp_millicelsius / 1000.0
        except (FileNotFoundError, ValueError, PermissionError):
            return None
    
    def _update_metrics_history(self, metrics: ResourceMetrics):
        """Update metrics history"""
        self.metrics_history.append(metrics)
        
        # Keep only recent metrics
        if len(self.metrics_history) > self.max_history:
            self.metrics_history = self.metrics_history[-self.max_history:]
    
    def _check_thresholds(self, metrics: ResourceMetrics):
        """Check if any thresholds are exceeded"""
        alerts = []
        
        # CPU threshold
        if metrics.cpu_percent > self.thresholds.cpu_percent:
            alerts.append(f"High CPU usage: {metrics.cpu_percent:.1f}%")
        
        # Memory threshold
        if metrics.memory_percent > self.thresholds.memory_percent:
            alerts.append(f"High memory usage: {metrics.memory_percent:.1f}%")
        
        # Memory absolute threshold
        if metrics.memory_used_mb > self.thresholds.memory_mb:
            alerts.append(f"High memory usage: {metrics.memory_used_mb:.1f}MB")
        
        # Disk threshold
        if metrics.disk_percent > self.thresholds.disk_percent:
            alerts.append(f"High disk usage: {metrics.disk_percent:.1f}%")
        
        # Temperature threshold
        if metrics.temperature and metrics.temperature > self.thresholds.temperature_celsius:
            alerts.append(f"High temperature: {metrics.temperature:.1f}°C")
        
        # Send alerts
        for alert in alerts:
            self._send_alert(alert, metrics)
    
    def _send_alert(self, message: str, metrics: ResourceMetrics):
        """Send resource alert"""
        alert_key = message.split(':')[0]  # Use first part as key
        
        # Throttle alerts (max once per minute per type)
        now = time.time()
        if alert_key in self.last_alert_time:
            if now - self.last_alert_time[alert_key] < 60:  # 1 minute
                return
        
        self.last_alert_time[alert_key] = now
        
        # Call alert callbacks
        for callback in self.alert_callbacks:
            try:
                callback(message, metrics)
            except Exception as e:
                logger.error(f"Alert callback failed: {e}")
        
        logger.warning(f"Resource alert: {message}")
    
    def _update_resource_level(self, metrics: ResourceMetrics):
        """Update current resource level based on metrics"""
        old_level = self.current_level
        
        # Determine resource level
        if (metrics.cpu_percent > 90 or 
            metrics.memory_percent > 95 or 
            metrics.disk_percent > 95 or
            (metrics.temperature and metrics.temperature > 85)):
            self.current_level = ResourceLevel.CRITICAL
        elif (metrics.cpu_percent > 80 or 
              metrics.memory_percent > 85 or 
              metrics.disk_percent > 90 or
              (metrics.temperature and metrics.temperature > 80)):
            self.current_level = ResourceLevel.HIGH
        elif (metrics.cpu_percent > 60 or 
              metrics.memory_percent > 70 or 
              metrics.disk_percent > 80):
            self.current_level = ResourceLevel.MEDIUM
        else:
            self.current_level = ResourceLevel.LOW
        
        # Log level changes
        if old_level != self.current_level:
            logger.info(f"Resource level changed: {old_level.value} -> {self.current_level.value}")
    
    def add_alert_callback(self, callback: Callable[[str, ResourceMetrics], None]):
        """Add alert callback function"""
        self.alert_callbacks.append(callback)
    
    def get_current_metrics(self) -> Optional[ResourceMetrics]:
        """Get most recent metrics"""
        return self.metrics_history[-1] if self.metrics_history else None
    
    def get_metrics_summary(self, hours: int = 1) -> Dict[str, Any]:
        """Get metrics summary for specified time period"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_metrics = [m for m in self.metrics_history if m.timestamp >= cutoff_time]
        
        if not recent_metrics:
            return {"error": "No recent metrics available"}
        
        # Calculate averages
        avg_cpu = sum(m.cpu_percent for m in recent_metrics) / len(recent_metrics)
        avg_memory = sum(m.memory_percent for m in recent_metrics) / len(recent_metrics)
        avg_disk = sum(m.disk_percent for m in recent_metrics) / len(recent_metrics)
        
        # Find peaks
        max_cpu = max(m.cpu_percent for m in recent_metrics)
        max_memory = max(m.memory_percent for m in recent_metrics)
        max_disk = max(m.disk_percent for m in recent_metrics)
        
        return {
            "period_hours": hours,
            "measurements": len(recent_metrics),
            "current_level": self.current_level.value,
            "averages": {
                "cpu_percent": round(avg_cpu, 1),
                "memory_percent": round(avg_memory, 1),
                "disk_percent": round(avg_disk, 1)
            },
            "peaks": {
                "cpu_percent": round(max_cpu, 1),
                "memory_percent": round(max_memory, 1),
                "disk_percent": round(max_disk, 1)
            },
            "latest": {
                "cpu_percent": recent_metrics[-1].cpu_percent,
                "memory_percent": recent_metrics[-1].memory_percent,
                "memory_used_mb": round(recent_metrics[-1].memory_used_mb, 1),
                "disk_percent": recent_metrics[-1].disk_percent,
                "temperature": recent_metrics[-1].temperature
            }
        }
    
    def should_throttle_task(self, task_id: str, estimated_memory_mb: float = 100) -> bool:
        """Check if task should be throttled based on current resources"""
        current_metrics = self.get_current_metrics()
        if not current_metrics:
            return False
        
        # Throttle if resources are high
        if self.current_level in [ResourceLevel.HIGH, ResourceLevel.CRITICAL]:
            return True
        
        # Throttle if estimated memory would exceed threshold
        if current_metrics.memory_used_mb + estimated_memory_mb > self.thresholds.memory_mb:
            return True
        
        return False
    
    def get_throttle_factor(self, task_id: str) -> float:
        """Get throttle factor for task (0.0 = no throttling, 1.0 = maximum throttling)"""
        if self.current_level == ResourceLevel.CRITICAL:
            return 1.0
        elif self.current_level == ResourceLevel.HIGH:
            return 0.7
        elif self.current_level == ResourceLevel.MEDIUM:
            return 0.3
        else:
            return 0.0
    
    def cleanup_old_metrics(self, days: int = 7):
        """Clean up old metrics data"""
        cutoff_time = datetime.now() - timedelta(days=days)
        self.metrics_history = [m for m in self.metrics_history if m.timestamp >= cutoff_time]
        logger.info(f"Cleaned up metrics older than {days} days")
    
    def get_system_info(self) -> Dict[str, Any]:
        """Get system information"""
        try:
            # CPU info
            cpu_count = psutil.cpu_count()
            cpu_freq = psutil.cpu_freq()
            
            # Memory info
            memory = psutil.virtual_memory()
            
            # Disk info
            disk = psutil.disk_usage('/')
            
            # Boot time
            boot_time = datetime.fromtimestamp(psutil.boot_time())
            
            return {
                "cpu": {
                    "count": cpu_count,
                    "frequency_mhz": cpu_freq.current if cpu_freq else None,
                    "max_frequency_mhz": cpu_freq.max if cpu_freq else None
                },
                "memory": {
                    "total_mb": round(memory.total / (1024 * 1024), 1),
                    "available_mb": round(memory.available / (1024 * 1024), 1),
                    "percent": memory.percent
                },
                "disk": {
                    "total_gb": round(disk.total / (1024 * 1024 * 1024), 1),
                    "free_gb": round(disk.free / (1024 * 1024 * 1024), 1),
                    "percent": round((disk.used / disk.total) * 100, 1)
                },
                "system": {
                    "boot_time": boot_time.isoformat(),
                    "uptime_hours": round((datetime.now() - boot_time).total_seconds() / 3600, 1)
                },
                "thresholds": {
                    "cpu_percent": self.thresholds.cpu_percent,
                    "memory_percent": self.thresholds.memory_percent,
                    "memory_mb": self.thresholds.memory_mb,
                    "disk_percent": self.thresholds.disk_percent,
                    "temperature_celsius": self.thresholds.temperature_celsius
                }
            }
        except Exception as e:
            logger.error(f"Failed to get system info: {e}")
            return {"error": str(e)}

# Global instance
resource_monitor = ResourceMonitor()
