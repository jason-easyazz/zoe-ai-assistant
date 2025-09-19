from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import sqlite3
import json
import os
import shutil
import hashlib
import hmac
import time
import threading
import schedule
from datetime import datetime, timedelta
import logging
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import subprocess
import psutil

router = APIRouter(prefix="/api/snapshots", tags=["encrypted-snapshots"])

logger = logging.getLogger(__name__)

class SnapshotConfig(BaseModel):
    enabled: bool = True
    schedule_interval: str = "daily"  # daily, weekly, monthly
    schedule_time: str = "02:00"  # HH:MM format
    retention_days: int = 30
    compression: bool = True
    encryption_enabled: bool = True
    backup_path: str = "/backups"
    exclude_patterns: List[str] = ["*.tmp", "*.log", "node_modules", ".git"]

class SnapshotInfo(BaseModel):
    snapshot_id: str
    created_at: str
    size_bytes: int
    encrypted: bool
    compression_ratio: Optional[float] = None
    checksum: str
    status: str  # created, verified, failed
    backup_path: str

class SnapshotManager:
    def __init__(self):
        self.db_path = "/app/data/snapshots.db"
        self.config = SnapshotConfig()
        self.encryption_key = None
        self.is_running = False
        self.scheduler_thread = None
        
        # Initialize database
        self.init_database()
        
        # Load or generate encryption key
        self.load_encryption_key()
        
        # Start scheduler
        self.start_scheduler()
    
    def init_database(self):
        """Initialize snapshots database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create snapshots table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    encrypted BOOLEAN NOT NULL,
                    compression_ratio REAL,
                    checksum TEXT NOT NULL,
                    status TEXT NOT NULL,
                    backup_path TEXT NOT NULL,
                    metadata TEXT
                )
            """)
            
            # Create config table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS snapshot_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            
            # Insert default config if not exists
            cursor.execute("SELECT COUNT(*) FROM snapshot_config")
            if cursor.fetchone()[0] == 0:
                default_config = {
                    "enabled": "true",
                    "schedule_interval": "daily",
                    "schedule_time": "02:00",
                    "retention_days": "30",
                    "compression": "true",
                    "encryption_enabled": "true",
                    "backup_path": "/backups",
                    "exclude_patterns": json.dumps(["*.tmp", "*.log", "node_modules", ".git"])
                }
                
                for key, value in default_config.items():
                    cursor.execute("INSERT INTO snapshot_config (key, value) VALUES (?, ?)", (key, value))
            
            conn.commit()
            conn.close()
            
            logger.info("✅ Snapshots database initialized")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize snapshots database: {e}")
    
    def load_encryption_key(self):
        """Load or generate encryption key"""
        try:
            key_file = "/app/data/snapshot_key.key"
            
            if os.path.exists(key_file):
                with open(key_file, 'rb') as f:
                    self.encryption_key = f.read()
                logger.info("✅ Encryption key loaded")
            else:
                # Generate new key
                self.encryption_key = Fernet.generate_key()
                with open(key_file, 'wb') as f:
                    f.write(self.encryption_key)
                # Set secure permissions
                os.chmod(key_file, 0o600)
                logger.info("✅ New encryption key generated")
                
        except Exception as e:
            logger.error(f"❌ Failed to load/generate encryption key: {e}")
            self.encryption_key = None
    
    def start_scheduler(self):
        """Start the backup scheduler"""
        if not self.is_running:
            self.is_running = True
            self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
            self.scheduler_thread.start()
            logger.info("✅ Snapshot scheduler started")
    
    def stop_scheduler(self):
        """Stop the backup scheduler"""
        self.is_running = False
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
        logger.info("✅ Snapshot scheduler stopped")
    
    def _scheduler_loop(self):
        """Main scheduler loop"""
        while self.is_running:
            try:
                # Load current config
                self.load_config()
                
                # Schedule backup based on config
                if self.config.enabled:
                    if self.config.schedule_interval == "daily":
                        schedule.every().day.at(self.config.schedule_time).do(self._run_scheduled_backup)
                    elif self.config.schedule_interval == "weekly":
                        schedule.every().week.at(self.config.schedule_time).do(self._run_scheduled_backup)
                    elif self.config.schedule_interval == "monthly":
                        schedule.every().month.at(self.config.schedule_time).do(self._run_scheduled_backup)
                
                # Run pending jobs
                schedule.run_pending()
                
                # Sleep for 1 minute
                time.sleep(60)
                
            except Exception as e:
                logger.error(f"❌ Error in scheduler loop: {e}")
                time.sleep(60)
    
    def _run_scheduled_backup(self):
        """Run a scheduled backup"""
        try:
            logger.info("🔄 Running scheduled backup...")
            snapshot_id = self.create_snapshot()
            if snapshot_id:
                logger.info(f"✅ Scheduled backup completed: {snapshot_id}")
            else:
                logger.error("❌ Scheduled backup failed")
        except Exception as e:
            logger.error(f"❌ Error in scheduled backup: {e}")
    
    def load_config(self):
        """Load configuration from database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT key, value FROM snapshot_config")
            config_data = dict(cursor.fetchall())
            
            self.config = SnapshotConfig(
                enabled=config_data.get("enabled", "true").lower() == "true",
                schedule_interval=config_data.get("schedule_interval", "daily"),
                schedule_time=config_data.get("schedule_time", "02:00"),
                retention_days=int(config_data.get("retention_days", "30")),
                compression=config_data.get("compression", "true").lower() == "true",
                encryption_enabled=config_data.get("encryption_enabled", "true").lower() == "true",
                backup_path=config_data.get("backup_path", "/backups"),
                exclude_patterns=json.loads(config_data.get("exclude_patterns", "[]"))
            )
            
            conn.close()
            
        except Exception as e:
            logger.error(f"❌ Failed to load config: {e}")
    
    def save_config(self, config: SnapshotConfig):
        """Save configuration to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            config_data = {
                "enabled": str(config.enabled).lower(),
                "schedule_interval": config.schedule_interval,
                "schedule_time": config.schedule_time,
                "retention_days": str(config.retention_days),
                "compression": str(config.compression).lower(),
                "encryption_enabled": str(config.encryption_enabled).lower(),
                "backup_path": config.backup_path,
                "exclude_patterns": json.dumps(config.exclude_patterns)
            }
            
            for key, value in config_data.items():
                cursor.execute("""
                    INSERT OR REPLACE INTO snapshot_config (key, value) 
                    VALUES (?, ?)
                """, (key, value))
            
            conn.commit()
            conn.close()
            
            self.config = config
            logger.info("✅ Configuration saved")
            
        except Exception as e:
            logger.error(f"❌ Failed to save config: {e}")
    
    def create_snapshot(self) -> Optional[str]:
        """Create a new encrypted snapshot"""
        try:
            snapshot_id = f"snapshot_{int(time.time())}"
            timestamp = datetime.now().isoformat()
            
            # Create backup directory
            backup_dir = os.path.join(self.config.backup_path, snapshot_id)
            os.makedirs(backup_dir, exist_ok=True)
            
            # Create tar archive
            archive_path = os.path.join(backup_dir, f"{snapshot_id}.tar")
            if self.config.compression:
                archive_path += ".gz"
            
            # Build tar command with exclusions
            tar_cmd = ["tar", "-cf", archive_path]
            if self.config.compression:
                tar_cmd = ["tar", "-czf", archive_path]
            
            # Add exclusions
            for pattern in self.config.exclude_patterns:
                tar_cmd.extend(["--exclude", pattern])
            
            # Add source directory
            tar_cmd.append("/app/data")
            
            # Run tar command
            result = subprocess.run(tar_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"❌ Tar command failed: {result.stderr}")
                return None
            
            # Get file size
            file_size = os.path.getsize(archive_path)
            
            # Calculate checksum
            checksum = self._calculate_checksum(archive_path)
            
            # Encrypt if enabled
            if self.config.encryption_enabled and self.encryption_key:
                encrypted_path = archive_path + ".enc"
                self._encrypt_file(archive_path, encrypted_path)
                
                # Remove original file
                os.remove(archive_path)
                archive_path = encrypted_path
                
                # Update file size
                file_size = os.path.getsize(archive_path)
            
            # Calculate compression ratio
            compression_ratio = None
            if self.config.compression:
                original_size = self._calculate_directory_size("/app/data")
                compression_ratio = file_size / original_size if original_size > 0 else 1.0
            
            # Store snapshot info
            snapshot_info = SnapshotInfo(
                snapshot_id=snapshot_id,
                created_at=timestamp,
                size_bytes=file_size,
                encrypted=self.config.encryption_enabled,
                compression_ratio=compression_ratio,
                checksum=checksum,
                status="created",
                backup_path=archive_path
            )
            
            self._store_snapshot_info(snapshot_info)
            
            # Verify snapshot
            if self._verify_snapshot(snapshot_info):
                self._update_snapshot_status(snapshot_id, "verified")
                logger.info(f"✅ Snapshot created and verified: {snapshot_id}")
            else:
                self._update_snapshot_status(snapshot_id, "failed")
                logger.error(f"❌ Snapshot verification failed: {snapshot_id}")
            
            return snapshot_id
            
        except Exception as e:
            logger.error(f"❌ Failed to create snapshot: {e}")
            return None
    
    def _calculate_checksum(self, file_path: str) -> str:
        """Calculate SHA-256 checksum of a file"""
        try:
            hash_sha256 = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_sha256.update(chunk)
            return hash_sha256.hexdigest()
        except Exception as e:
            logger.error(f"❌ Failed to calculate checksum: {e}")
            return ""
    
    def _calculate_directory_size(self, directory: str) -> int:
        """Calculate total size of a directory"""
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(directory):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    if os.path.exists(filepath):
                        total_size += os.path.getsize(filepath)
        except Exception as e:
            logger.error(f"❌ Failed to calculate directory size: {e}")
        return total_size
    
    def _encrypt_file(self, input_path: str, output_path: str):
        """Encrypt a file using Fernet"""
        try:
            fernet = Fernet(self.encryption_key)
            
            with open(input_path, 'rb') as f:
                data = f.read()
            
            encrypted_data = fernet.encrypt(data)
            
            with open(output_path, 'wb') as f:
                f.write(encrypted_data)
                
        except Exception as e:
            logger.error(f"❌ Failed to encrypt file: {e}")
            raise
    
    def _decrypt_file(self, input_path: str, output_path: str):
        """Decrypt a file using Fernet"""
        try:
            fernet = Fernet(self.encryption_key)
            
            with open(input_path, 'rb') as f:
                encrypted_data = f.read()
            
            decrypted_data = fernet.decrypt(encrypted_data)
            
            with open(output_path, 'wb') as f:
                f.write(decrypted_data)
                
        except Exception as e:
            logger.error(f"❌ Failed to decrypt file: {e}")
            raise
    
    def _store_snapshot_info(self, snapshot_info: SnapshotInfo):
        """Store snapshot information in database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO snapshots 
                (snapshot_id, created_at, size_bytes, encrypted, compression_ratio, 
                 checksum, status, backup_path, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snapshot_info.snapshot_id,
                snapshot_info.created_at,
                snapshot_info.size_bytes,
                snapshot_info.encrypted,
                snapshot_info.compression_ratio,
                snapshot_info.checksum,
                snapshot_info.status,
                snapshot_info.backup_path,
                json.dumps({})
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"❌ Failed to store snapshot info: {e}")
    
    def _verify_snapshot(self, snapshot_info: SnapshotInfo) -> bool:
        """Verify snapshot integrity"""
        try:
            # Check if file exists
            if not os.path.exists(snapshot_info.backup_path):
                return False
            
            # Verify checksum
            current_checksum = self._calculate_checksum(snapshot_info.backup_path)
            if current_checksum != snapshot_info.checksum:
                logger.error("❌ Checksum verification failed")
                return False
            
            # If encrypted, try to decrypt
            if snapshot_info.encrypted and self.encryption_key:
                try:
                    temp_path = snapshot_info.backup_path + ".temp"
                    self._decrypt_file(snapshot_info.backup_path, temp_path)
                    os.remove(temp_path)
                except Exception as e:
                    logger.error(f"❌ Decryption verification failed: {e}")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Snapshot verification failed: {e}")
            return False
    
    def _update_snapshot_status(self, snapshot_id: str, status: str):
        """Update snapshot status"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE snapshots 
                SET status = ? 
                WHERE snapshot_id = ?
            """, (status, snapshot_id))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"❌ Failed to update snapshot status: {e}")
    
    def get_snapshots(self) -> List[SnapshotInfo]:
        """Get all snapshots"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT snapshot_id, created_at, size_bytes, encrypted, 
                       compression_ratio, checksum, status, backup_path
                FROM snapshots 
                ORDER BY created_at DESC
            """)
            
            snapshots = []
            for row in cursor.fetchall():
                snapshots.append(SnapshotInfo(
                    snapshot_id=row['snapshot_id'],
                    created_at=row['created_at'],
                    size_bytes=row['size_bytes'],
                    encrypted=bool(row['encrypted']),
                    compression_ratio=row['compression_ratio'],
                    checksum=row['checksum'],
                    status=row['status'],
                    backup_path=row['backup_path']
                ))
            
            conn.close()
            return snapshots
            
        except Exception as e:
            logger.error(f"❌ Failed to get snapshots: {e}")
            return []
    
    def cleanup_old_snapshots(self):
        """Remove old snapshots based on retention policy"""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.config.retention_days)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get old snapshots
            cursor.execute("""
                SELECT snapshot_id, backup_path FROM snapshots 
                WHERE created_at < ?
            """, (cutoff_date.isoformat(),))
            
            old_snapshots = cursor.fetchall()
            
            # Remove files and database entries
            for snapshot_id, backup_path in old_snapshots:
                try:
                    # Remove backup file
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                    
                    # Remove database entry
                    cursor.execute("DELETE FROM snapshots WHERE snapshot_id = ?", (snapshot_id,))
                    
                    logger.info(f"✅ Removed old snapshot: {snapshot_id}")
                    
                except Exception as e:
                    logger.error(f"❌ Failed to remove snapshot {snapshot_id}: {e}")
            
            conn.commit()
            conn.close()
            
            logger.info(f"✅ Cleanup completed: {len(old_snapshots)} snapshots removed")
            
        except Exception as e:
            logger.error(f"❌ Failed to cleanup old snapshots: {e}")

# Initialize snapshot manager
snapshot_manager = SnapshotManager()

@router.get("/config")
async def get_snapshot_config():
    """Get current snapshot configuration"""
    return snapshot_manager.config.dict()

@router.post("/config")
async def update_snapshot_config(config: SnapshotConfig):
    """Update snapshot configuration"""
    try:
        snapshot_manager.save_config(config)
        return {"message": "Configuration updated", "config": config.dict()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {str(e)}")

@router.post("/create")
async def create_snapshot(background_tasks: BackgroundTasks):
    """Create a new snapshot"""
    try:
        snapshot_id = snapshot_manager.create_snapshot()
        if snapshot_id:
            # Schedule cleanup in background
            background_tasks.add_task(snapshot_manager.cleanup_old_snapshots)
            return {"message": "Snapshot created", "snapshot_id": snapshot_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to create snapshot")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create snapshot: {str(e)}")

@router.get("/list")
async def list_snapshots():
    """List all snapshots"""
    try:
        snapshots = snapshot_manager.get_snapshots()
        return {
            "snapshots": [snapshot.dict() for snapshot in snapshots],
            "total_count": len(snapshots)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list snapshots: {str(e)}")

@router.get("/status")
async def get_snapshot_status():
    """Get snapshot system status"""
    return {
        "is_running": snapshot_manager.is_running,
        "encryption_enabled": snapshot_manager.config.encryption_enabled,
        "encryption_key_loaded": snapshot_manager.encryption_key is not None,
        "next_backup": "Scheduled" if snapshot_manager.config.enabled else "Disabled",
        "backup_path": snapshot_manager.config.backup_path
    }

@router.post("/verify/{snapshot_id}")
async def verify_snapshot(snapshot_id: str):
    """Verify a specific snapshot"""
    try:
        snapshots = snapshot_manager.get_snapshots()
        snapshot = next((s for s in snapshots if s.snapshot_id == snapshot_id), None)
        
        if not snapshot:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        
        is_valid = snapshot_manager._verify_snapshot(snapshot)
        
        if is_valid:
            snapshot_manager._update_snapshot_status(snapshot_id, "verified")
            return {"message": "Snapshot verified", "valid": True}
        else:
            snapshot_manager._update_snapshot_status(snapshot_id, "failed")
            return {"message": "Snapshot verification failed", "valid": False}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to verify snapshot: {str(e)}")

@router.delete("/cleanup")
async def cleanup_old_snapshots():
    """Manually trigger cleanup of old snapshots"""
    try:
        snapshot_manager.cleanup_old_snapshots()
        return {"message": "Cleanup completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cleanup: {str(e)}")

@router.get("/stats")
async def get_snapshot_stats():
    """Get snapshot statistics"""
    try:
        snapshots = snapshot_manager.get_snapshots()
        
        total_size = sum(s.size_bytes for s in snapshots)
        verified_count = sum(1 for s in snapshots if s.status == "verified")
        failed_count = sum(1 for s in snapshots if s.status == "failed")
        
        return {
            "total_snapshots": len(snapshots),
            "total_size_bytes": total_size,
            "total_size_gb": round(total_size / (1024**3), 2),
            "verified_count": verified_count,
            "failed_count": failed_count,
            "success_rate": round((verified_count / len(snapshots)) * 100, 2) if snapshots else 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")




