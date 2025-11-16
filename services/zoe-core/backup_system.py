"""
Backup System for Zoe AI Assistant
Implements 3-2-1 backup strategy with pre-task snapshots
"""
import os
import shutil
import sqlite3
import json
import tarfile
import gzip
from datetime import datetime
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class BackupSystem:
    """Comprehensive backup system with 3-2-1 strategy"""
    
    def __init__(self):
        self.backup_root = Path(__file__).parent.parent.parent.resolve() / "backups"
        self.backup_root.mkdir(exist_ok=True)
        
        # 3-2-1 strategy: 3 copies, 2 different media, 1 offsite
        self.local_backups = self.backup_root / "local"
        self.local_backups.mkdir(exist_ok=True)
        
        self.remote_backups = self.backup_root / "remote"  # For future cloud integration
        self.remote_backups.mkdir(exist_ok=True)
        
        # Critical paths to backup
        self.critical_paths = [
            str(PROJECT_ROOT / "data"),
            str(PROJECT_ROOT / "services/zoe-core"),
            str(PROJECT_ROOT / "templates"),
            str(PROJECT_ROOT / "scripts"),
            str(PROJECT_ROOT / "documentation")
        ]
        
        # Exclude patterns
        self.exclude_patterns = [
            "*.pyc",
            "__pycache__",
            "*.log",
            "*.tmp",
            ".git",
            "node_modules",
            "*.wav",
            "*.mp3"
        ]
    
    def create_pre_task_snapshot(self, task_id: str, task_description: str) -> Dict:
        """Create snapshot before task execution"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"pre_task_{task_id}_{timestamp}"
        
        try:
            # Create backup directory
            backup_dir = self.local_backups / backup_name
            backup_dir.mkdir(exist_ok=True)
            
            # Create metadata
            metadata = {
                "task_id": task_id,
                "task_description": task_description,
                "timestamp": timestamp,
                "backup_type": "pre_task_snapshot",
                "paths_backed_up": [],
                "size_bytes": 0
            }
            
            total_size = 0
            
            # Backup critical paths
            for path in self.critical_paths:
                if os.path.exists(path):
                    dest_path = backup_dir / Path(path).name
                    if os.path.isdir(path):
                        shutil.copytree(path, dest_path, ignore=self._ignore_patterns)
                    else:
                        shutil.copy2(path, dest_path)
                    
                    # Calculate size
                    path_size = self._get_path_size(dest_path)
                    total_size += path_size
                    metadata["paths_backed_up"].append({
                        "source": path,
                        "destination": str(dest_path),
                        "size_bytes": path_size
                    })
            
            metadata["size_bytes"] = total_size
            
            # Save metadata
            with open(backup_dir / "metadata.json", "w") as f:
                json.dump(metadata, f, indent=2)
            
            # Create compressed archive
            archive_path = self.local_backups / f"{backup_name}.tar.gz"
            self._create_compressed_archive(backup_dir, archive_path)
            
            # Clean up uncompressed directory
            shutil.rmtree(backup_dir)
            
            logger.info(f"Pre-task snapshot created: {archive_path} ({total_size} bytes)")
            
            return {
                "success": True,
                "backup_path": str(archive_path),
                "size_bytes": total_size,
                "metadata": metadata
            }
            
        except Exception as e:
            logger.error(f"Backup creation failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def create_full_backup(self, description: str = "Manual backup") -> Dict:
        """Create full system backup"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"full_backup_{timestamp}"
        
        try:
            backup_dir = self.local_backups / backup_name
            backup_dir.mkdir(exist_ok=True)
            
            metadata = {
                "description": description,
                "timestamp": timestamp,
                "backup_type": "full_backup",
                "paths_backed_up": [],
                "size_bytes": 0
            }
            
            total_size = 0
            
            # Backup entire zoe directory
            zoe_root = PROJECT_ROOT
            for item in zoe_root.iterdir():
                if item.name not in ["backups", "logs", "models"]:  # Exclude large directories
                    dest_path = backup_dir / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest_path, ignore=self._ignore_patterns)
                    else:
                        shutil.copy2(item, dest_path)
                    
                    path_size = self._get_path_size(dest_path)
                    total_size += path_size
                    metadata["paths_backed_up"].append({
                        "source": str(item),
                        "destination": str(dest_path),
                        "size_bytes": path_size
                    })
            
            metadata["size_bytes"] = total_size
            
            # Save metadata
            with open(backup_dir / "metadata.json", "w") as f:
                json.dump(metadata, f, indent=2)
            
            # Create compressed archive
            archive_path = self.local_backups / f"{backup_name}.tar.gz"
            self._create_compressed_archive(backup_dir, archive_path)
            
            # Clean up uncompressed directory
            shutil.rmtree(backup_dir)
            
            logger.info(f"Full backup created: {archive_path} ({total_size} bytes)")
            
            return {
                "success": True,
                "backup_path": str(archive_path),
                "size_bytes": total_size,
                "metadata": metadata
            }
            
        except Exception as e:
            logger.error(f"Full backup creation failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def restore_backup(self, backup_path: str, restore_location: str = None) -> Dict:
        """Restore from backup"""
        try:
            if not os.path.exists(backup_path):
                return {"success": False, "error": "Backup file not found"}
            
            # Extract to temporary directory first
            temp_dir = Path("/tmp/backup_restore")
            temp_dir.mkdir(exist_ok=True)
            
            # Extract archive
            with tarfile.open(backup_path, "r:gz") as tar:
                tar.extractall(temp_dir)
            
            # Find metadata
            metadata_file = None
            for root, dirs, files in os.walk(temp_dir):
                if "metadata.json" in files:
                    metadata_file = os.path.join(root, "metadata.json")
                    break
            
            if not metadata_file:
                return {"success": False, "error": "No metadata found in backup"}
            
            # Load metadata
            with open(metadata_file, "r") as f:
                metadata = json.load(f)
            
            # Restore files
            restored_count = 0
            for path_info in metadata["paths_backed_up"]:
                source = os.path.join(temp_dir, os.path.basename(path_info["destination"]))
                destination = path_info["source"]
                
                if os.path.exists(source):
                    # Create destination directory if needed
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    
                    if os.path.isdir(source):
                        if os.path.exists(destination):
                            shutil.rmtree(destination)
                        shutil.copytree(source, destination)
                    else:
                        shutil.copy2(source, destination)
                    
                    restored_count += 1
            
            # Clean up temp directory
            shutil.rmtree(temp_dir)
            
            logger.info(f"Restored {restored_count} items from {backup_path}")
            
            return {
                "success": True,
                "restored_items": restored_count,
                "metadata": metadata
            }
            
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def list_backups(self) -> List[Dict]:
        """List all available backups"""
        backups = []
        
        for backup_file in self.local_backups.glob("*.tar.gz"):
            try:
                # Extract metadata from filename
                name_parts = backup_file.stem.split("_")
                backup_type = name_parts[0] + "_" + name_parts[1] if len(name_parts) > 1 else "unknown"
                timestamp = name_parts[-1] if len(name_parts) > 2 else "unknown"
                
                # Get file size
                size_bytes = backup_file.stat().st_size
                
                backups.append({
                    "name": backup_file.name,
                    "path": str(backup_file),
                    "type": backup_type,
                    "timestamp": timestamp,
                    "size_bytes": size_bytes,
                    "size_mb": round(size_bytes / (1024 * 1024), 2)
                })
                
            except Exception as e:
                logger.warning(f"Could not process backup file {backup_file}: {e}")
        
        # Sort by timestamp (newest first)
        backups.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return backups
    
    def cleanup_old_backups(self, keep_count: int = 10) -> Dict:
        """Clean up old backups, keeping only the most recent ones"""
        backups = self.list_backups()
        
        if len(backups) <= keep_count:
            return {"success": True, "deleted_count": 0, "message": "No cleanup needed"}
        
        deleted_count = 0
        for backup in backups[keep_count:]:
            try:
                os.remove(backup["path"])
                deleted_count += 1
                logger.info(f"Deleted old backup: {backup['name']}")
            except Exception as e:
                logger.error(f"Failed to delete backup {backup['name']}: {e}")
        
        return {
            "success": True,
            "deleted_count": deleted_count,
            "message": f"Cleaned up {deleted_count} old backups"
        }
    
    def _ignore_patterns(self, dir_path, filenames):
        """Ignore patterns for shutil.copytree"""
        ignored = []
        for filename in filenames:
            for pattern in self.exclude_patterns:
                if filename.endswith(pattern.replace("*", "")) or pattern.replace("*", "") in filename:
                    ignored.append(filename)
                    break
        return ignored
    
    def _get_path_size(self, path: str) -> int:
        """Calculate total size of path"""
        total_size = 0
        if os.path.isfile(path):
            return os.path.getsize(path)
        elif os.path.isdir(path):
            for dirpath, dirnames, filenames in os.walk(path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    try:
                        total_size += os.path.getsize(filepath)
                    except (OSError, FileNotFoundError):
                        pass
        return total_size
    
    def _create_compressed_archive(self, source_dir: Path, archive_path: Path):
        """Create compressed tar.gz archive"""
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(source_dir, arcname=source_dir.name)

# Global instance
backup_system = BackupSystem()
