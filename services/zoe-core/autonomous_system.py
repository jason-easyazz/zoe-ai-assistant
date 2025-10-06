"""Autonomous System with proper Docker handling"""
import os
import sys
import subprocess
import json
import sqlite3
import psutil
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class AutonomousSystem:
    """Complete autonomous development system"""
    
    def __init__(self):
        self.root = Path("/home/pi/zoe")
        self.app_root = Path("/app")
        # Don't initialize Docker here - it's not accessible from container
        self.knowledge = self.load_full_knowledge()
        
    def load_full_knowledge(self) -> Dict:
        """Load everything about the project"""
        return {
            "project_structure": self.scan_entire_project(),
            "documentation": self.load_all_docs(),
            "current_state": self.get_complete_state(),
            "capabilities": self.list_capabilities()
        }
    
    def scan_entire_project(self) -> Dict:
        """Complete project scan"""
        structure = {}
        try:
            for ext in ['.py', '.html', '.js', '.sh', '.yml', '.md']:
                files = list(self.app_root.rglob(f'*{ext}'))
                structure[ext] = [str(f) for f in files[:50]]  # Limit to 50 per type
        except Exception as e:
            logger.error(f"Scan error: {e}")
        return structure
    
    def load_all_docs(self) -> Dict:
        """Load all documentation"""
        docs = {}
        # These docs are in /home/pi/zoe but we're in container at /app
        # Skip for now or mount them
        return docs
    
    def get_complete_state(self) -> Dict:
        """Everything about current state"""
        return {
            "containers": self.get_containers_via_cli(),
            "services": self.check_all_services(),
            "database": self.inspect_database(),
            "api_routes": self.list_all_routes(),
            "errors": self.find_all_errors(),
            "performance": self.get_performance_metrics()
        }
    
    def get_containers_via_cli(self) -> List[Dict]:
        """Get containers using CLI instead of Docker SDK"""
        containers = []
        try:
            result = subprocess.run(
                "docker ps -a --format '{{.Names}}|{{.Status}}|{{.Image}}'",
                shell=True, capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split('\n'):
                if 'zoe-' in line:
                    parts = line.split('|')
                    if len(parts) >= 3:
                        containers.append({
                            "name": parts[0],
                            "status": parts[1],
                            "image": parts[2]
                        })
        except Exception as e:
            logger.error(f"Container check error: {e}")
        return containers
    
    def check_all_services(self) -> Dict:
        """Check service status"""
        services = {}
        # These checks work from inside container
        endpoints = [
            ("api", "http://localhost:8000/health"),
            ("chat", "http://localhost:8000/api/chat/"),
        ]
        for name, url in endpoints:
            try:
                result = subprocess.run(
                    f"curl -s -o /dev/null -w '%{{http_code}}' {url}",
                    shell=True, capture_output=True, text=True, timeout=5
                )
                services[name] = result.stdout.strip() == "200"
            except:
                services[name] = False
        return services
    
    def inspect_database(self) -> Dict:
        """Check database"""
        try:
            db_path = "/app/data/zoe.db"
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [t[0] for t in cursor.fetchall()]
                conn.close()
                return {"tables": tables}
            else:
                return {"error": "Database not found"}
        except Exception as e:
            return {"error": str(e)}
    
    def list_all_routes(self) -> List[str]:
        """List API routes"""
        try:
            result = subprocess.run(
                "grep -r '@router' /app/routers/ 2>/dev/null | head -20",
                shell=True, capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip().split('\n')[:20]
        except:
            return []
    
    def find_all_errors(self) -> List[str]:
        """Find recent errors"""
        errors = []
        try:
            result = subprocess.run(
                "tail -50 /proc/1/fd/1 2>/dev/null | grep -i error | tail -5",
                shell=True, capture_output=True, text=True, timeout=5
            )
            if result.stdout:
                errors = result.stdout.strip().split('\n')
        except:
            pass
        return errors
    
    def get_performance_metrics(self) -> Dict:
        """System metrics"""
        return {
            "cpu": psutil.cpu_percent(interval=1),
            "memory": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage('/').percent
        }
    
    def list_capabilities(self) -> List[str]:
        """What Zack can do"""
        return [
            "Read and analyze all project files",
            "Execute shell commands",
            "Check container status",
            "Monitor system performance",
            "Access database",
            "Find and fix errors",
            "Create new features",
            "Generate complete implementations"
        ]
    
    async def execute_development_task(self, task: str) -> Dict:
        """Execute development tasks"""
        return {
            "task": task,
            "status": "analyzed",
            "can_execute": True
        }

autonomous = AutonomousSystem()
