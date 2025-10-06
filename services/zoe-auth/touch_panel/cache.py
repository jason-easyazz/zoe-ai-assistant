"""
Touch Panel Local Caching System
Optimized for fast authentication on touch panels with offline support
"""

import sqlite3
import json
import hashlib
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import threading
import logging

logger = logging.getLogger(__name__)

@dataclass
class CachedUser:
    """Cached user information for touch panels"""
    user_id: str
    username: str
    passcode_hash: Optional[str]
    role: str
    permissions: List[str]
    last_sync: datetime
    expires_at: datetime

@dataclass
class CachedSession:
    """Cached session for offline use"""
    session_id: str
    user_id: str
    permissions: List[str]
    expires_at: datetime
    device_id: str

class TouchPanelCache:
    """Local caching system for touch panels"""
    
    def __init__(self, device_id: str, cache_dir: str = "/app/data/touch_cache"):
        self.device_id = device_id
        self.cache_dir = cache_dir
        self.db_path = os.path.join(cache_dir, f"touch_cache_{device_id}.db")
        self.lock = threading.RLock()
        
        # Cache duration settings
        self.user_cache_duration = timedelta(hours=24)
        self.session_cache_duration = timedelta(hours=2)
        self.offline_grace_period = timedelta(minutes=30)
        
        self._ensure_cache_directory()
        self._init_cache_database()

    def _ensure_cache_directory(self):
        """Ensure cache directory exists"""
        os.makedirs(self.cache_dir, exist_ok=True)

    def _init_cache_database(self):
        """Initialize local cache database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cached_users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    passcode_hash TEXT,
                    role TEXT NOT NULL,
                    permissions TEXT NOT NULL,
                    last_sync TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS cached_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    permissions TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_status (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cached_users_username ON cached_users(username)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cached_sessions_user_id ON cached_sessions(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cached_sessions_device ON cached_sessions(device_id)")

    def sync_from_server(self, server_data: Dict[str, Any]) -> bool:
        """
        Sync data from central auth server
        
        Args:
            server_data: Dictionary containing users, roles, and permissions
            
        Returns:
            True if sync successful
        """
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    # Clear old data
                    conn.execute("DELETE FROM cached_users WHERE expires_at < ?", 
                               (datetime.now().isoformat(),))

                    # Cache users
                    users_synced = 0
                    for user_data in server_data.get('users', []):
                        expires_at = datetime.now() + self.user_cache_duration
                        
                        conn.execute("""
                            INSERT OR REPLACE INTO cached_users 
                            (user_id, username, passcode_hash, role, permissions, last_sync, expires_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            user_data['user_id'],
                            user_data['username'],
                            user_data.get('passcode_hash'),
                            user_data['role'],
                            json.dumps(user_data.get('permissions', [])),
                            datetime.now().isoformat(),
                            expires_at.isoformat()
                        ))
                        users_synced += 1

                    # Update sync status
                    conn.execute("""
                        INSERT OR REPLACE INTO sync_status (key, value, updated_at)
                        VALUES (?, ?, ?)
                    """, ("last_sync", datetime.now().isoformat(), datetime.now().isoformat()))

                    logger.info(f"Touch panel {self.device_id} synced {users_synced} users")
                    return True

        except Exception as e:
            logger.error(f"Failed to sync touch panel cache: {e}")
            return False

    def verify_passcode_offline(self, username: str, passcode: str) -> Tuple[bool, Optional[CachedUser]]:
        """
        Verify passcode using cached data (offline mode)
        
        Args:
            username: Username to verify
            passcode: Passcode to verify
            
        Returns:
            Tuple of (success, cached_user_info)
        """
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.execute("""
                        SELECT user_id, username, passcode_hash, role, permissions, last_sync, expires_at
                        FROM cached_users 
                        WHERE username = ? AND expires_at > ?
                    """, (username, datetime.now().isoformat()))

                    row = cursor.fetchone()
                    if not row:
                        return False, None

                    user_id, username, passcode_hash, role, permissions_json, last_sync, expires_at = row

                    if not passcode_hash:
                        return False, None

                    # Verify passcode (using simple hash for cache - not as secure as server-side)
                    # In production, you might want a more sophisticated offline verification
                    passcode_check = hashlib.sha256(f"{passcode}{user_id}".encode()).hexdigest()
                    if passcode_check != passcode_hash:
                        return False, None

                    cached_user = CachedUser(
                        user_id=user_id,
                        username=username,
                        passcode_hash=passcode_hash,
                        role=role,
                        permissions=json.loads(permissions_json),
                        last_sync=datetime.fromisoformat(last_sync),
                        expires_at=datetime.fromisoformat(expires_at)
                    )

                    return True, cached_user

        except Exception as e:
            logger.error(f"Offline passcode verification error: {e}")
            return False, None

    def cache_session(self, session_id: str, user_id: str, permissions: List[str]) -> bool:
        """
        Cache session for offline use
        
        Args:
            session_id: Session identifier
            user_id: User identifier
            permissions: User permissions
            
        Returns:
            True if cached successfully
        """
        try:
            with self.lock:
                expires_at = datetime.now() + self.session_cache_duration
                
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO cached_sessions 
                        (session_id, user_id, permissions, expires_at, device_id)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        session_id, user_id, json.dumps(permissions),
                        expires_at.isoformat(), self.device_id
                    ))

                return True

        except Exception as e:
            logger.error(f"Failed to cache session: {e}")
            return False

    def get_cached_session(self, session_id: str) -> Optional[CachedSession]:
        """
        Get cached session information
        
        Args:
            session_id: Session identifier
            
        Returns:
            CachedSession if found and valid, None otherwise
        """
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.execute("""
                        SELECT session_id, user_id, permissions, expires_at, device_id
                        FROM cached_sessions 
                        WHERE session_id = ? AND expires_at > ? AND device_id = ?
                    """, (session_id, datetime.now().isoformat(), self.device_id))

                    row = cursor.fetchone()
                    if row:
                        return CachedSession(
                            session_id=row[0],
                            user_id=row[1],
                            permissions=json.loads(row[2]),
                            expires_at=datetime.fromisoformat(row[3]),
                            device_id=row[4]
                        )

        except Exception as e:
            logger.error(f"Failed to get cached session: {e}")

        return None

    def cleanup_expired_cache(self) -> int:
        """
        Clean up expired cache entries
        
        Returns:
            Number of entries cleaned up
        """
        try:
            with self.lock:
                cleaned = 0
                now = datetime.now().isoformat()
                
                with sqlite3.connect(self.db_path) as conn:
                    # Clean expired users
                    cursor = conn.execute("DELETE FROM cached_users WHERE expires_at <= ?", (now,))
                    cleaned += cursor.rowcount

                    # Clean expired sessions
                    cursor = conn.execute("DELETE FROM cached_sessions WHERE expires_at <= ?", (now,))
                    cleaned += cursor.rowcount

                if cleaned > 0:
                    logger.info(f"Cleaned up {cleaned} expired cache entries")

                return cleaned

        except Exception as e:
            logger.error(f"Cache cleanup error: {e}")
            return 0

    def get_last_sync_time(self) -> Optional[datetime]:
        """Get timestamp of last successful sync"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT value FROM sync_status WHERE key = 'last_sync'")
                row = cursor.fetchone()
                if row:
                    return datetime.fromisoformat(row[0])
        except Exception as e:
            logger.error(f"Failed to get last sync time: {e}")

        return None

    def is_sync_stale(self) -> bool:
        """Check if cache sync is stale and needs refresh"""
        last_sync = self.get_last_sync_time()
        if not last_sync:
            return True

        # Consider sync stale after 4 hours
        return datetime.now() - last_sync > timedelta(hours=4)

    def get_cached_user_count(self) -> int:
        """Get number of cached users"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM cached_users WHERE expires_at > ?", 
                                    (datetime.now().isoformat(),))
                return cursor.fetchone()[0]
        except Exception:
            return 0

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        try:
            stats = {
                "device_id": self.device_id,
                "cached_users": self.get_cached_user_count(),
                "last_sync": self.get_last_sync_time(),
                "sync_stale": self.is_sync_stale(),
                "cache_size_mb": 0
            }

            # Get cache file size
            if os.path.exists(self.db_path):
                stats["cache_size_mb"] = round(os.path.getsize(self.db_path) / (1024 * 1024), 2)

            # Get session count
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM cached_sessions WHERE expires_at > ?", 
                                    (datetime.now().isoformat(),))
                stats["cached_sessions"] = cursor.fetchone()[0]

            return stats

        except Exception as e:
            logger.error(f"Failed to get cache stats: {e}")
            return {"error": str(e)}

    def clear_cache(self) -> bool:
        """Clear all cached data"""
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("DELETE FROM cached_users")
                    conn.execute("DELETE FROM cached_sessions")
                    conn.execute("DELETE FROM sync_status")

                logger.info(f"Cleared all cache for device {self.device_id}")
                return True

        except Exception as e:
            logger.error(f"Failed to clear cache: {e}")
            return False

class TouchPanelCacheManager:
    """Manages caches for multiple touch panels"""
    
    def __init__(self):
        self.caches: Dict[str, TouchPanelCache] = {}
        self.lock = threading.RLock()

    def get_cache(self, device_id: str) -> TouchPanelCache:
        """Get or create cache for device"""
        with self.lock:
            if device_id not in self.caches:
                self.caches[device_id] = TouchPanelCache(device_id)
            return self.caches[device_id]

    def sync_all_caches(self, server_data: Dict[str, Any]) -> Dict[str, bool]:
        """Sync all device caches with server data"""
        results = {}
        with self.lock:
            for device_id, cache in self.caches.items():
                results[device_id] = cache.sync_from_server(server_data)
        return results

    def cleanup_all_caches(self) -> Dict[str, int]:
        """Cleanup expired entries from all caches"""
        results = {}
        with self.lock:
            for device_id, cache in self.caches.items():
                results[device_id] = cache.cleanup_expired_cache()
        return results

    def get_all_cache_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all caches"""
        stats = {}
        with self.lock:
            for device_id, cache in self.caches.items():
                stats[device_id] = cache.get_cache_stats()
        return stats

# Global cache manager instance
cache_manager = TouchPanelCacheManager()

