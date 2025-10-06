#!/usr/bin/env python3
"""
ZACK'S TRUE INTELLIGENCE CORE
This is the essential foundation that makes Zack truly intelligent.
Preserves all developments since August 31st while maintaining real intelligence.
"""

import subprocess
import psutil
import sqlite3
import json
import docker
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path

# ============================================
# CORE INTELLIGENCE FUNCTIONS
# ============================================

def execute_command(cmd: str, timeout: int = 10) -> dict:
    """Execute system commands and return real results"""
    try:
        result = subprocess.run(
            cmd, 
            shell=True, 
            capture_output=True, 
            text=True, 
            timeout=timeout, 
            cwd="/app"
        )
        return {
            "stdout": result.stdout[:5000],
            "stderr": result.stderr[:1000],
            "code": result.returncode,
            "success": result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Command timed out", "code": -1, "success": False}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "code": -1, "success": False}

def get_real_system_metrics() -> dict:
    """Get ACTUAL system metrics using psutil"""
    metrics = {}
    
    try:
        # CPU metrics
        metrics["cpu_percent"] = psutil.cpu_percent(interval=1)
        metrics["cpu_cores"] = psutil.cpu_count()
        
        # Memory metrics
        mem = psutil.virtual_memory()
        metrics["memory_percent"] = round(mem.percent, 1)
        metrics["memory_used_gb"] = round(mem.used / (1024**3), 2)
        metrics["memory_total_gb"] = round(mem.total / (1024**3), 2)
        metrics["memory_available_gb"] = round(mem.available / (1024**3), 2)
        
        # Disk metrics
        disk = psutil.disk_usage("/")
        metrics["disk_percent"] = round(disk.percent, 1)
        metrics["disk_free_gb"] = round(disk.free / (1024**3), 2)
        metrics["disk_total_gb"] = round(disk.total / (1024**3), 2)
        
        # Temperature (Raspberry Pi specific)
        try:
            temp_result = execute_command("vcgencmd measure_temp")
            if temp_result["success"] and "temp=" in temp_result["stdout"]:
                temp_str = temp_result["stdout"].split("temp=")[1].split("'")[0]
                metrics["temperature_c"] = float(temp_str)
        except:
            metrics["temperature_c"] = None
            
    except Exception as e:
        print(f"Error getting metrics: {e}")
        
    return metrics

def analyze_for_optimization() -> dict:
    """Analyze system and provide REAL, PRACTICAL recommendations"""
    analysis = {
        "metrics": get_real_system_metrics(),
        "recommendations": [],
        "issues": [],
        "health_score": 100
    }
    
    metrics = analysis["metrics"]
    
    # Check CPU
    if metrics.get("cpu_percent", 0) > 80:
        analysis["issues"].append(f"High CPU usage: {metrics['cpu_percent']}%")
        analysis["recommendations"].append("Consider stopping unused containers: docker stop [container]")
        analysis["health_score"] -= 20
    elif metrics.get("cpu_percent", 0) > 60:
        analysis["recommendations"].append("CPU moderate. Monitor for spikes: watch docker stats")
        analysis["health_score"] -= 10
        
    # Check Memory
    if metrics.get("memory_percent", 0) > 85:
        analysis["issues"].append(f"High memory usage: {metrics['memory_percent']}%")
        analysis["recommendations"].append("Free memory: docker system prune -a")
        analysis["health_score"] -= 25
    elif metrics.get("memory_percent", 0) > 70:
        analysis["recommendations"].append("Memory usage moderate. Consider: docker restart zoe-ollama")
        analysis["health_score"] -= 10
        
    # Check Disk
    if metrics.get("disk_percent", 0) > 90:
        analysis["issues"].append(f"Critical disk usage: {metrics['disk_percent']}%")
        analysis["recommendations"].append("Urgent: Clean logs: find /var/log -type f -name '*.log' -delete")
        analysis["health_score"] -= 30
    elif metrics.get("disk_percent", 0) > 75:
        analysis["recommendations"].append("Disk filling up. Run: docker system prune --volumes")
        analysis["health_score"] -= 15
        
    # If no issues found, provide proactive suggestions
    if not analysis["issues"]:
        analysis["recommendations"] = [
            "System healthy! Consider these optimizations:",
            "• Set up daily backups: crontab -e → 0 2 * * * /home/pi/zoe/scripts/backup.sh",
            "• Enable log rotation: sudo logrotate /etc/logrotate.conf",
            "• Monitor trends: docker stats --no-stream > /tmp/stats.log"
        ]
        
    return analysis

# Export for use
__all__ = ['execute_command', 'get_real_system_metrics', 'analyze_for_optimization']
