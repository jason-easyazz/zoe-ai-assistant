"""
Music Auth Manager
==================

Handles encrypted storage and retrieval of music provider credentials.
Uses Fernet symmetric encryption for secure credential storage.
"""

import os
import json
import sqlite3
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")


class MusicAuthManager:
    """
    Manages encrypted authentication credentials for music providers.
    
    Supports:
    - YouTube Music OAuth
    - Spotify OAuth (future)
    - Local file auth (future)
    """
    
    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize the auth manager.
        
        Args:
            encryption_key: Fernet key for encryption. If not provided,
                           reads from MUSIC_AUTH_KEY environment variable.
        """
        key = encryption_key or os.getenv("MUSIC_AUTH_KEY")
        
        if not key:
            # Generate a new key if not provided (dev mode)
            logger.warning("MUSIC_AUTH_KEY not set, generating temporary key")
            key = Fernet.generate_key().decode()
            logger.info("Generated temporary music auth key (will not persist across restarts)")
        
        try:
            self.fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as e:
            logger.error(f"Invalid encryption key: {e}")
            raise ValueError("Invalid MUSIC_AUTH_KEY format. Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
        
        self._init_db()
    
    def _init_db(self):
        """Initialize music auth table."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Create table if not exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS music_auth (
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    auth_data TEXT NOT NULL,
                    auth_type TEXT DEFAULT 'oauth',
                    expires_at TIMESTAMP,
                    refresh_token TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, provider)
                )
            """)
            
            conn.commit()
            conn.close()
            logger.debug("Music auth table initialized")
        except Exception as e:
            logger.error(f"Failed to initialize music auth table: {e}")
    
    def encrypt(self, data: Dict[str, Any]) -> str:
        """
        Encrypt authentication data.
        
        Args:
            data: Dictionary of auth data to encrypt
            
        Returns:
            Encrypted string
        """
        json_str = json.dumps(data)
        encrypted = self.fernet.encrypt(json_str.encode())
        return encrypted.decode()
    
    def decrypt(self, encrypted_data: str) -> Dict[str, Any]:
        """
        Decrypt authentication data.
        
        Args:
            encrypted_data: Encrypted string
            
        Returns:
            Decrypted dictionary
            
        Raises:
            InvalidToken: If decryption fails (wrong key or corrupted data)
        """
        try:
            decrypted = self.fernet.decrypt(encrypted_data.encode())
            return json.loads(decrypted.decode())
        except InvalidToken:
            logger.error("Failed to decrypt auth data - invalid token")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse decrypted auth data: {e}")
            raise
    
    async def store_auth(
        self,
        user_id: str,
        provider: str,
        auth_data: Dict[str, Any],
        auth_type: str = "oauth",
        expires_at: Optional[datetime] = None,
        refresh_token: Optional[str] = None
    ) -> bool:
        """
        Store encrypted authentication credentials.
        
        Args:
            user_id: User identifier
            provider: Provider name (youtube_music, spotify, etc.)
            auth_data: Authentication data to encrypt
            auth_type: Type of auth (oauth, cookie, api_key)
            expires_at: Token expiration time
            refresh_token: Refresh token (will be encrypted)
            
        Returns:
            True if successful
        """
        try:
            encrypted_auth = self.encrypt(auth_data)
            encrypted_refresh = self.encrypt({"token": refresh_token}) if refresh_token else None
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO music_auth 
                (user_id, provider, auth_data, auth_type, expires_at, refresh_token, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                user_id, provider, encrypted_auth, auth_type,
                expires_at.isoformat() if expires_at else None,
                encrypted_refresh
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Stored {provider} auth for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to store auth: {e}")
            return False
    
    async def get_auth(self, user_id: str, provider: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve and decrypt authentication credentials.
        
        Args:
            user_id: User identifier
            provider: Provider name
            
        Returns:
            Decrypted auth data or None if not found
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT auth_data, expires_at FROM music_auth
                WHERE user_id = ? AND provider = ?
            """, (user_id, provider))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return None
            
            # Check if expired
            if row["expires_at"]:
                expires = datetime.fromisoformat(row["expires_at"])
                if datetime.now() > expires:
                    logger.warning(f"{provider} auth expired for user {user_id}")
                    # Could trigger refresh here
                    return None
            
            return self.decrypt(row["auth_data"])
            
        except InvalidToken:
            logger.error(f"Failed to decrypt {provider} auth for user {user_id}")
            return None
        except Exception as e:
            logger.error(f"Failed to get auth: {e}")
            return None
    
    async def get_refresh_token(self, user_id: str, provider: str) -> Optional[str]:
        """Get the refresh token for a provider."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT refresh_token FROM music_auth
                WHERE user_id = ? AND provider = ?
            """, (user_id, provider))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row or not row[0]:
                return None
            
            decrypted = self.decrypt(row[0])
            return decrypted.get("token")
            
        except Exception as e:
            logger.error(f"Failed to get refresh token: {e}")
            return None
    
    async def delete_auth(self, user_id: str, provider: str) -> bool:
        """
        Delete authentication credentials.
        
        Args:
            user_id: User identifier
            provider: Provider name
            
        Returns:
            True if deleted, False if not found
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM music_auth
                WHERE user_id = ? AND provider = ?
            """, (user_id, provider))
            
            deleted = cursor.rowcount > 0
            conn.commit()
            conn.close()
            
            if deleted:
                logger.info(f"Deleted {provider} auth for user {user_id}")
            
            return deleted
            
        except Exception as e:
            logger.error(f"Failed to delete auth: {e}")
            return False
    
    async def check_auth_status(self, user_id: str, provider: str) -> Dict[str, Any]:
        """
        Check authentication status for a provider.
        
        Returns:
            Dict with authenticated status and expiration info
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT auth_type, expires_at, created_at, updated_at
                FROM music_auth
                WHERE user_id = ? AND provider = ?
            """, (user_id, provider))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return {
                    "authenticated": False,
                    "provider": provider,
                    "error": "Not authenticated"
                }
            
            # Check expiration
            expires_at = row["expires_at"]
            is_expired = False
            
            if expires_at:
                expires = datetime.fromisoformat(expires_at)
                is_expired = datetime.now() > expires
            
            return {
                "authenticated": not is_expired,
                "provider": provider,
                "auth_type": row["auth_type"],
                "expires_at": expires_at,
                "is_expired": is_expired,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            }
            
        except Exception as e:
            logger.error(f"Failed to check auth status: {e}")
            return {
                "authenticated": False,
                "provider": provider,
                "error": str(e)
            }


# Singleton instance
_auth_manager: Optional[MusicAuthManager] = None


def get_auth_manager() -> MusicAuthManager:
    """Get the singleton auth manager instance."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = MusicAuthManager()
    return _auth_manager

