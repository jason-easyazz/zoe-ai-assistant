"""
Session Management System for Zoe AI Assistant
Handles user sessions, tokens, timeouts, and concurrent session support
"""

import uuid
import time
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any
from dataclasses import dataclass, asdict
import threading
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class Session:
    """Represents a user session"""
    session_id: str
    user_id: str
    created_at: datetime
    last_activity: datetime
    expires_at: datetime
    is_active: bool = True
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

class SessionManager:
    """Manages user sessions with token-based authentication"""
    
    def __init__(self, db_path: str = "data/sessions.db", default_timeout: int = 3600):
        """
        Initialize session manager
        
        Args:
            db_path: Path to SQLite database for session storage
            default_timeout: Default session timeout in seconds (1 hour)
        """
        self.db_path = db_path
        self.default_timeout = default_timeout
        self.active_sessions: Dict[str, Session] = {}
        self.lock = threading.RLock()
        
        # Initialize database
        self._init_database()
        
        # Start cleanup thread
        self._start_cleanup_thread()
    
    def _init_database(self):
        """Initialize the sessions database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        last_activity TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        metadata TEXT
                    )
                """)
                
                # Create index for faster lookups
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_sessions_user_id 
                    ON sessions(user_id)
                """)
                
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_sessions_expires_at 
                    ON sessions(expires_at)
                """)
                
                conn.commit()
                logger.info("Session database initialized successfully")
                
        except Exception as e:
            logger.error(f"Failed to initialize session database: {e}")
            raise
    
    def create_session(self, user_id: str, timeout: Optional[int] = None, 
                      metadata: Optional[Dict[str, Any]] = None) -> Session:
        """
        Create a new session for a user
        
        Args:
            user_id: Unique identifier for the user
            timeout: Session timeout in seconds (uses default if None)
            metadata: Additional session metadata
            
        Returns:
            Session object
        """
        with self.lock:
            session_id = str(uuid.uuid4())
            now = datetime.now()
            timeout_seconds = timeout or self.default_timeout
            expires_at = now + timedelta(seconds=timeout_seconds)
            
            session = Session(
                session_id=session_id,
                user_id=user_id,
                created_at=now,
                last_activity=now,
                expires_at=expires_at,
                is_active=True,
                metadata=metadata or {}
            )
            
            # Store in memory
            self.active_sessions[session_id] = session
            
            # Store in database
            self._save_session_to_db(session)
            
            logger.info(f"Created session {session_id} for user {user_id}")
            return session
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """
        Get a session by ID
        
        Args:
            session_id: Session identifier
            
        Returns:
            Session object or None if not found/expired
        """
        with self.lock:
            # Check memory first
            if session_id in self.active_sessions:
                session = self.active_sessions[session_id]
                if self._is_session_valid(session):
                    return session
                else:
                    # Session expired, remove it
                    self._remove_session(session_id)
                    return None
            
            # Check database
            session = self._load_session_from_db(session_id)
            if session and self._is_session_valid(session):
                self.active_sessions[session_id] = session
                return session
            elif session:
                # Session exists but is expired
                self._remove_session(session_id)
            
            return None
    
    def update_session_activity(self, session_id: str) -> bool:
        """
        Update session last activity time
        
        Args:
            session_id: Session identifier
            
        Returns:
            True if session was updated, False if not found
        """
        with self.lock:
            session = self.get_session(session_id)
            if not session:
                return False
            
            session.last_activity = datetime.now()
            self._save_session_to_db(session)
            return True
    
    def extend_session(self, session_id: str, additional_seconds: int) -> bool:
        """
        Extend session expiration time
        
        Args:
            session_id: Session identifier
            additional_seconds: Seconds to add to expiration
            
        Returns:
            True if session was extended, False if not found
        """
        with self.lock:
            session = self.get_session(session_id)
            if not session:
                return False
            
            session.expires_at += timedelta(seconds=additional_seconds)
            self._save_session_to_db(session)
            logger.info(f"Extended session {session_id} by {additional_seconds} seconds")
            return True
    
    def invalidate_session(self, session_id: str) -> bool:
        """
        Invalidate a session
        
        Args:
            session_id: Session identifier
            
        Returns:
            True if session was invalidated, False if not found
        """
        with self.lock:
            return self._remove_session(session_id)
    
    def invalidate_user_sessions(self, user_id: str) -> int:
        """
        Invalidate all sessions for a user
        
        Args:
            user_id: User identifier
            
        Returns:
            Number of sessions invalidated
        """
        with self.lock:
            count = 0
            sessions_to_remove = []
            
            # Find sessions to remove
            for session_id, session in self.active_sessions.items():
                if session.user_id == user_id:
                    sessions_to_remove.append(session_id)
            
            # Remove sessions
            for session_id in sessions_to_remove:
                if self._remove_session(session_id):
                    count += 1
            
            # Also remove from database
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.execute(
                        "UPDATE sessions SET is_active = 0 WHERE user_id = ?",
                        (user_id,)
                    )
                    count += cursor.rowcount
                    conn.commit()
            except Exception as e:
                logger.error(f"Failed to invalidate user sessions in database: {e}")
            
            logger.info(f"Invalidated {count} sessions for user {user_id}")
            return count
    
    def get_user_sessions(self, user_id: str) -> List[Session]:
        """
        Get all active sessions for a user
        
        Args:
            user_id: User identifier
            
        Returns:
            List of active sessions
        """
        with self.lock:
            sessions = []
            for session in self.active_sessions.values():
                if session.user_id == user_id and self._is_session_valid(session):
                    sessions.append(session)
            
            # Also check database for sessions not in memory
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.execute("""
                        SELECT session_id, user_id, created_at, last_activity, 
                               expires_at, is_active, metadata
                        FROM sessions 
                        WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                    """, (user_id, datetime.now().isoformat()))
                    
                    for row in cursor.fetchall():
                        session_id = row[0]
                        if session_id not in self.active_sessions:
                            session = self._row_to_session(row)
                            if self._is_session_valid(session):
                                sessions.append(session)
                                self.active_sessions[session_id] = session
            except Exception as e:
                logger.error(f"Failed to load user sessions from database: {e}")
            
            return sessions
    
    def get_session_stats(self) -> Dict[str, Any]:
        """
        Get session statistics
        
        Returns:
            Dictionary with session statistics
        """
        with self.lock:
            now = datetime.now()
            active_count = 0
            expired_count = 0
            user_sessions = {}
            
            for session in self.active_sessions.values():
                if self._is_session_valid(session):
                    active_count += 1
                    user_sessions[session.user_id] = user_sessions.get(session.user_id, 0) + 1
                else:
                    expired_count += 1
            
            return {
                "active_sessions": active_count,
                "expired_sessions": expired_count,
                "unique_users": len(user_sessions),
                "sessions_per_user": user_sessions,
                "total_sessions": len(self.active_sessions)
            }
    
    def _is_session_valid(self, session: Session) -> bool:
        """Check if a session is valid (not expired)"""
        return session.is_active and datetime.now() < session.expires_at
    
    def _save_session_to_db(self, session: Session):
        """Save session to database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO sessions 
                    (session_id, user_id, created_at, last_activity, expires_at, is_active, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    session.session_id,
                    session.user_id,
                    session.created_at.isoformat(),
                    session.last_activity.isoformat(),
                    session.expires_at.isoformat(),
                    1 if session.is_active else 0,
                    json.dumps(session.metadata)
                ))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to save session to database: {e}")
    
    def _load_session_from_db(self, session_id: str) -> Optional[Session]:
        """Load session from database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT session_id, user_id, created_at, last_activity, 
                           expires_at, is_active, metadata
                    FROM sessions 
                    WHERE session_id = ?
                """, (session_id,))
                
                row = cursor.fetchone()
                if row:
                    return self._row_to_session(row)
        except Exception as e:
            logger.error(f"Failed to load session from database: {e}")
        
        return None
    
    def _row_to_session(self, row) -> Session:
        """Convert database row to Session object"""
        return Session(
            session_id=row[0],
            user_id=row[1],
            created_at=datetime.fromisoformat(row[2]),
            last_activity=datetime.fromisoformat(row[3]),
            expires_at=datetime.fromisoformat(row[4]),
            is_active=bool(row[5]),
            metadata=json.loads(row[6]) if row[6] else {}
        )
    
    def _remove_session(self, session_id: str) -> bool:
        """Remove session from memory and mark as inactive in database"""
        removed = False
        
        # Remove from memory
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]
            removed = True
        
        # Mark as inactive in database
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE sessions SET is_active = 0 WHERE session_id = ?",
                    (session_id,)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to remove session from database: {e}")
        
        return removed
    
    def _start_cleanup_thread(self):
        """Start background thread for cleaning up expired sessions"""
        def cleanup_expired_sessions():
            while True:
                try:
                    time.sleep(300)  # Run every 5 minutes
                    self._cleanup_expired_sessions()
                except Exception as e:
                    logger.error(f"Error in session cleanup thread: {e}")
        
        cleanup_thread = threading.Thread(target=cleanup_expired_sessions, daemon=True)
        cleanup_thread.start()
        logger.info("Session cleanup thread started")
    
    def _cleanup_expired_sessions(self):
        """Clean up expired sessions"""
        with self.lock:
            now = datetime.now()
            expired_sessions = []
            
            # Find expired sessions in memory
            for session_id, session in self.active_sessions.items():
                if not self._is_session_valid(session):
                    expired_sessions.append(session_id)
            
            # Remove expired sessions
            for session_id in expired_sessions:
                self._remove_session(session_id)
            
            if expired_sessions:
                logger.info(f"Cleaned up {len(expired_sessions)} expired sessions")
    
    def close(self):
        """Close the session manager and cleanup resources"""
        with self.lock:
            # Save all active sessions to database
            for session in self.active_sessions.values():
                self._save_session_to_db(session)
            
            self.active_sessions.clear()
            logger.info("Session manager closed")

# Global session manager instance
session_manager = SessionManager()
