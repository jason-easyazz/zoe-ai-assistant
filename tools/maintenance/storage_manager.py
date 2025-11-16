#!/usr/bin/env python3
"""
Storage Manager for Zoe (Phase 4: memvid concepts)
Monitors storage usage - NO MODEL DELETION per user requirement
Focus on monitoring, reporting, and safe optimization only
"""

import subprocess
import sqlite3
import os
from typing import Dict, Any, List
from datetime import datetime


class StorageManager:
    """Storage monitoring and safe optimization"""
    
    def __init__(self, zoe_root: str = None):
        # Auto-detect project root if not provided (works for both Pi and Nano)
        if zoe_root is None:
            from pathlib import Path
            zoe_root = str(Path(__file__).parent.parent.parent.resolve())
        self.zoe_root = zoe_root
        self.data_dir = f"{zoe_root}/data"
    
    def analyze_usage(self) -> Dict[str, Any]:
        """
        Analyze storage usage - MONITORING ONLY, NO DELETION
        
        Returns:
            Dictionary with storage breakdown and recommendations
        """
        analysis = {
            "timestamp": datetime.now().isoformat(),
            "docker_images": self._get_docker_usage(),
            "databases": self._get_database_sizes(),
            "ollama_models": self._get_model_usage(),  # JUST REPORT
            "logs": self._get_log_sizes(),
            "total_disk": self._get_disk_usage(),
            "recommendations": []
        }
        
        # Add recommendations (human decides what to do)
        if analysis["databases"]["total_size_mb"] > 500:
            analysis["recommendations"].append("Consider running VACUUM on databases")
        
        if analysis["logs"]["total_size_mb"] > 1000:
            analysis["recommendations"].append("Consider archiving old application logs")
        
        return analysis
    
    def _get_docker_usage(self) -> Dict[str, Any]:
        """Get Docker images usage"""
        try:
            result = subprocess.run(
                ["docker", "system", "df", "--format", "{{json .}}"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                return {
                    "raw_output": lines,
                    "available": True
                }
        except:
            pass
        
        return {"available": False}
    
    def _get_database_sizes(self) -> Dict[str, Any]:
        """Get database file sizes"""
        databases = {}
        total_size = 0
        
        for db_file in ["zoe.db", "memory.db", "training.db"]:
            db_path = f"{self.data_dir}/{db_file}"
            if os.path.exists(db_path):
                size_bytes = os.path.getsize(db_path)
                size_mb = size_bytes / (1024 * 1024)
                databases[db_file] = {
                    "size_bytes": size_bytes,
                    "size_mb": round(size_mb, 2)
                }
                total_size += size_mb
        
        return {
            "databases": databases,
            "total_size_mb": round(total_size, 2)
        }
    
    def _get_model_usage(self) -> Dict[str, Any]:
        """
        Get Ollama model usage - MONITORING ONLY
        NO DELETION per user requirement
        """
        try:
            import httpx
            response = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                models = data.get("models", [])
                
                total_size = 0
                model_list = []
                
                for model in models:
                    size = model.get("size", 0)
                    total_size += size
                    model_list.append({
                        "name": model.get("name"),
                        "size_gb": round(size / (1024**3), 2),
                        "modified": model.get("modified_at")
                    })
                
                return {
                    "count": len(models),
                    "models": model_list,
                    "total_size_gb": round(total_size / (1024**3), 2),
                    "note": "Monitoring only - NO deletion (user requirement)"
                }
        except:
            pass
        
        return {"available": False, "note": "Ollama not accessible"}
    
    def _get_log_sizes(self) -> Dict[str, Any]:
        """Get application log sizes"""
        log_files = []
        total_size = 0
        
        # Check /tmp for Zoe logs
        try:
            for file in os.listdir("/tmp"):
                if file.startswith("zoe-") and file.endswith(".log"):
                    path = f"/tmp/{file}"
                    size = os.path.getsize(path)
                    total_size += size
                    log_files.append({
                        "file": file,
                        "size_mb": round(size / (1024 * 1024), 2)
                    })
        except:
            pass
        
        return {
            "log_files": log_files,
            "total_size_mb": round(total_size / (1024 * 1024), 2)
        }
    
    def _get_disk_usage(self) -> Dict[str, Any]:
        """Get overall disk usage"""
        try:
            result = subprocess.run(
                ["df", "-h", self.zoe_root],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if len(lines) > 1:
                    parts = lines[1].split()
                    return {
                        "filesystem": parts[0],
                        "size": parts[1],
                        "used": parts[2],
                        "available": parts[3],
                        "use_percent": parts[4]
                    }
        except:
            pass
        
        return {"available": False}
    
    def compress_databases(self, dry_run: bool = True) -> Dict[str, Any]:
        """
        SAFE: Run VACUUM on databases (reversible optimization)
        
        Args:
            dry_run: If True, only simulate
            
        Returns:
            Results of VACUUM operations
        """
        results = []
        
        for db_file in ["zoe.db", "memory.db", "training.db"]:
            db_path = f"{self.data_dir}/{db_file}"
            if not os.path.exists(db_path):
                continue
            
            size_before = os.path.getsize(db_path)
            
            if dry_run:
                results.append({
                    "database": db_file,
                    "dry_run": True,
                    "size_mb": round(size_before / (1024 * 1024), 2),
                    "action": "Would run VACUUM"
                })
            else:
                try:
                    # Backup first
                    backup_path = f"{db_path}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                    subprocess.run(["cp", db_path, backup_path], check=True)
                    
                    # Run VACUUM
                    conn = sqlite3.connect(db_path)
                    conn.execute("VACUUM")
                    conn.close()
                    
                    size_after = os.path.getsize(db_path)
                    saved = size_before - size_after
                    
                    results.append({
                        "database": db_file,
                        "dry_run": False,
                        "size_before_mb": round(size_before / (1024 * 1024), 2),
                        "size_after_mb": round(size_after / (1024 * 1024), 2),
                        "saved_mb": round(saved / (1024 * 1024), 2),
                        "backup": backup_path,
                        "action": "VACUUM completed"
                    })
                except Exception as e:
                    results.append({
                        "database": db_file,
                        "error": str(e)
                    })
        
        return {"results": results}
    
    def rotate_logs(self, days_to_keep: int = 30, dry_run: bool = True) -> Dict[str, Any]:
        """
        SAFE: Archive old application logs
        
        Args:
            days_to_keep: Keep logs from last N days
            dry_run: If True, only simulate
            
        Returns:
            Results of log rotation
        """
        # Implementation would archive old logs
        # For now, just report
        return {
            "dry_run": dry_run,
            "message": "Log rotation not yet implemented",
            "note": "Would archive logs older than {} days".format(days_to_keep)
        }


if __name__ == "__main__":
    manager = StorageManager()
    
    print("=" * 80)
    print("ZOE STORAGE ANALYSIS")
    print("=" * 80)
    
    analysis = manager.analyze_usage()
    
    print(f"\nTimestamp: {analysis['timestamp']}")
    
    print("\n## Databases")
    for db, info in analysis['databases']['databases'].items():
        print(f"  {db}: {info['size_mb']} MB")
    print(f"  Total: {analysis['databases']['total_size_mb']} MB")
    
    print("\n## Ollama Models")
    if analysis['ollama_models'].get('available'):
        print(f"  Count: {analysis['ollama_models']['count']}")
        print(f"  Total: {analysis['ollama_models']['total_size_gb']} GB")
        print(f"  Note: {analysis['ollama_models']['note']}")
        for model in analysis['ollama_models']['models'][:5]:
            print(f"    - {model['name']}: {model['size_gb']} GB")
    else:
        print(f"  {analysis['ollama_models']['note']}")
    
    print("\n## Disk Usage")
    if analysis['total_disk'].get('available'):
        disk = analysis['total_disk']
        print(f"  Used: {disk['used']} / {disk['size']} ({disk['use_percent']})")
        print(f"  Available: {disk['available']}")
    
    print("\n## Recommendations")
    if analysis['recommendations']:
        for rec in analysis['recommendations']:
            print(f"  - {rec}")
    else:
        print("  No recommendations at this time")
    
    print("\n" + "=" * 80)




