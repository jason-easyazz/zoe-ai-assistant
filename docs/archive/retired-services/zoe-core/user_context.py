"""
User Context System for Zoe AI Assistant
Prepare for multi-user support with user management and context isolation
"""
import sqlite3
import json
import uuid
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class UserContext:
    """User context management system"""
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        self.db_path = db_path
        self.default_user_id = "system"
        self._initialize_database()
    
    def _initialize_database(self):
        """Initialize database with user context tables"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT,
                    display_name TEXT,
                    role TEXT DEFAULT 'user',
                    preferences TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1
                )
            ''')
            
            # Create user_sessions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_data TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')
            
            # Add user_id columns to existing tables if they don't exist
            self._add_user_columns_if_missing(cursor)
            
            # Create default system user if it doesn't exist
            cursor.execute('SELECT COUNT(*) FROM users WHERE id = ?', (self.default_user_id,))
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    INSERT INTO users (id, username, display_name, role, preferences)
                    VALUES (?, ?, ?, ?, ?)
                ''', (self.default_user_id, "system", "System User", "admin", "{}"))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to initialize user context database: {e}")
    
    def _add_user_columns_if_missing(self, cursor):
        """Add user_id columns to existing tables if they don't exist"""
        tables_to_update = [
            "dynamic_tasks",
            "backups",
            "learning_records",
            "code_reviews",
            "api_docs",
            "test_results"
        ]
        
        for table in tables_to_update:
            try:
                # Check if user_id column exists
                cursor.execute(f"PRAGMA table_info({table})")
                columns = [column[1] for column in cursor.fetchall()]
                
                if "user_id" not in columns:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT DEFAULT '{self.default_user_id}'")
                    logger.info(f"Added user_id column to {table}")
            except Exception as e:
                logger.warning(f"Could not add user_id column to {table}: {e}")
    
    def create_user(self, username: str, email: str = None, display_name: str = None, 
                   role: str = "user", preferences: Dict[str, Any] = None) -> Dict[str, Any]:
        """Create a new user"""
        try:
            user_id = str(uuid.uuid4())[:8]
            preferences = preferences or {}
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO users (id, username, email, display_name, role, preferences)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, username, email, display_name, role, json.dumps(preferences)))
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "user_id": user_id,
                "username": username,
                "message": f"User {username} created successfully"
            }
            
        except sqlite3.IntegrityError:
            return {
                "success": False,
                "error": f"Username {username} already exists"
            }
        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            return {
                "success": False,
                "error": f"Failed to create user: {str(e)}"
            }
    
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, username, email, display_name, role, preferences, 
                       created_at, last_active, is_active
                FROM users WHERE id = ?
            ''', (user_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    "id": row[0],
                    "username": row[1],
                    "email": row[2],
                    "display_name": row[3],
                    "role": row[4],
                    "preferences": json.loads(row[5]) if row[5] else {},
                    "created_at": row[6],
                    "last_active": row[7],
                    "is_active": bool(row[8])
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get user: {e}")
            return None
    
    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Get user by username"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, username, email, display_name, role, preferences, 
                       created_at, last_active, is_active
                FROM users WHERE username = ?
            ''', (username,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    "id": row[0],
                    "username": row[1],
                    "email": row[2],
                    "display_name": row[3],
                    "role": row[4],
                    "preferences": json.loads(row[5]) if row[5] else {},
                    "created_at": row[6],
                    "last_active": row[7],
                    "is_active": bool(row[8])
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get user by username: {e}")
            return None
    
    def update_user(self, user_id: str, updates: Dict[str, Any]) -> bool:
        """Update user information"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Build update query dynamically
            update_fields = []
            values = []
            
            for field, value in updates.items():
                if field in ["username", "email", "display_name", "role"]:
                    update_fields.append(f"{field} = ?")
                    values.append(value)
                elif field == "preferences":
                    update_fields.append(f"{field} = ?")
                    values.append(json.dumps(value))
            
            if not update_fields:
                return False
            
            # Add last_active update
            update_fields.append("last_active = CURRENT_TIMESTAMP")
            
            query = f"UPDATE users SET {', '.join(update_fields)} WHERE id = ?"
            values.append(user_id)
            
            cursor.execute(query, values)
            conn.commit()
            conn.close()
            
            return cursor.rowcount > 0
            
        except Exception as e:
            logger.error(f"Failed to update user: {e}")
            return False
    
    def create_session(self, user_id: str, session_data: Dict[str, Any] = None, 
                      expires_hours: int = 24) -> str:
        """Create a new user session"""
        try:
            session_id = str(uuid.uuid4())
            session_data = session_data or {}
            expires_at = datetime.now().timestamp() + (expires_hours * 3600)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO user_sessions (id, user_id, session_data, expires_at)
                VALUES (?, ?, ?, ?)
            ''', (session_id, user_id, json.dumps(session_data), expires_at))
            
            conn.commit()
            conn.close()
            
            return session_id
            
        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            return None
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session by ID"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, user_id, session_data, created_at, last_accessed, 
                       expires_at, is_active
                FROM user_sessions 
                WHERE id = ? AND is_active = 1
            ''', (session_id,))
            
            row = cursor.fetchone()
            
            if row:
                # Check if session is expired
                expires_at = row[5]
                if expires_at and datetime.now().timestamp() > expires_at:
                    # Mark session as inactive
                    cursor.execute('''
                        UPDATE user_sessions 
                        SET is_active = 0 
                        WHERE id = ?
                    ''', (session_id,))
                    conn.commit()
                    conn.close()
                    return None
                
                # Update last_accessed
                cursor.execute('''
                    UPDATE user_sessions 
                    SET last_accessed = CURRENT_TIMESTAMP 
                    WHERE id = ?
                ''', (session_id,))
                conn.commit()
                
                conn.close()
                
                return {
                    "id": row[0],
                    "user_id": row[1],
                    "session_data": json.loads(row[2]) if row[2] else {},
                    "created_at": row[3],
                    "last_accessed": row[4],
                    "expires_at": row[5],
                    "is_active": bool(row[6])
                }
            
            conn.close()
            return None
            
        except Exception as e:
            logger.error(f"Failed to get session: {e}")
            return None
    
    def get_user_tasks(self, user_id: str, status: str = None) -> List[Dict[str, Any]]:
        """Get tasks for a specific user"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if status:
                cursor.execute('''
                    SELECT id, title, objective, requirements, constraints, 
                           acceptance_criteria, priority, status, assigned_to,
                           created_at, last_executed_at, execution_count
                    FROM dynamic_tasks 
                    WHERE user_id = ? AND status = ?
                    ORDER BY created_at DESC
                ''', (user_id, status))
            else:
                cursor.execute('''
                    SELECT id, title, objective, requirements, constraints, 
                           acceptance_criteria, priority, status, assigned_to,
                           created_at, last_executed_at, execution_count
                    FROM dynamic_tasks 
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                ''', (user_id,))
            
            tasks = []
            for row in cursor.fetchall():
                tasks.append({
                    "id": row[0],
                    "title": row[1],
                    "objective": row[2],
                    "requirements": json.loads(row[3]) if row[3] else [],
                    "constraints": json.loads(row[4]) if row[4] else [],
                    "acceptance_criteria": json.loads(row[5]) if row[5] else [],
                    "priority": row[6],
                    "status": row[7],
                    "assigned_to": row[8],
                    "created_at": row[9],
                    "last_executed_at": row[10],
                    "execution_count": row[11]
                })
            
            conn.close()
            return tasks
            
        except Exception as e:
            logger.error(f"Failed to get user tasks: {e}")
            return []
    
    def get_user_context(self, user_id: str) -> Dict[str, Any]:
        """Get comprehensive user context"""
        try:
            user = self.get_user(user_id)
            if not user:
                return {"error": "User not found"}
            
            # Get user's tasks
            tasks = self.get_user_tasks(user_id)
            
            # Get user's active sessions
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT COUNT(*) FROM user_sessions 
                WHERE user_id = ? AND is_active = 1
            ''', (user_id,))
            
            active_sessions = cursor.fetchone()[0]
            conn.close()
            
            # Calculate user statistics
            task_stats = {
                "total_tasks": len(tasks),
                "completed_tasks": len([t for t in tasks if t["status"] == "completed"]),
                "pending_tasks": len([t for t in tasks if t["status"] == "pending"]),
                "in_progress_tasks": len([t for t in tasks if t["status"] == "in_progress"])
            }
            
            return {
                "user": user,
                "tasks": tasks,
                "active_sessions": active_sessions,
                "task_stats": task_stats,
                "context_ready": True
            }
            
        except Exception as e:
            logger.error(f"Failed to get user context: {e}")
            return {"error": f"Failed to get user context: {str(e)}"}
    
    def migrate_existing_data(self) -> Dict[str, Any]:
        """Migrate existing data to use default user context"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Update existing tasks without user_id
            cursor.execute('''
                UPDATE dynamic_tasks 
                SET user_id = ? 
                WHERE user_id IS NULL OR user_id = ''
            ''', (self.default_user_id,))
            
            updated_tasks = cursor.rowcount
            
            # Update other tables if they exist
            tables_to_migrate = ["backups", "learning_records", "code_reviews", "api_docs", "test_results"]
            migration_results = {"tasks": updated_tasks}
            
            for table in tables_to_migrate:
                try:
                    cursor.execute(f'''
                        UPDATE {table} 
                        SET user_id = ? 
                        WHERE user_id IS NULL OR user_id = ''
                    ''', (self.default_user_id,))
                    migration_results[table] = cursor.rowcount
                except Exception as e:
                    migration_results[table] = f"Error: {str(e)}"
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "migration_results": migration_results,
                "message": "Data migration completed successfully"
            }
            
        except Exception as e:
            logger.error(f"Failed to migrate existing data: {e}")
            return {
                "success": False,
                "error": f"Failed to migrate data: {str(e)}"
            }
    
    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, username, email, display_name, role, preferences, 
                       created_at, last_active, is_active
                FROM users 
                ORDER BY created_at DESC
            ''')
            
            users = []
            for row in cursor.fetchall():
                users.append({
                    "id": row[0],
                    "username": row[1],
                    "email": row[2],
                    "display_name": row[3],
                    "role": row[4],
                    "preferences": json.loads(row[5]) if row[5] else {},
                    "created_at": row[6],
                    "last_active": row[7],
                    "is_active": bool(row[8])
                })
            
            conn.close()
            return users
            
        except Exception as e:
            logger.error(f"Failed to get all users: {e}")
            return []
    
    def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE user_sessions 
                SET is_active = 0 
                WHERE expires_at < ? AND is_active = 1
            ''', (datetime.now().timestamp(),))
            
            cleaned = cursor.rowcount
            conn.commit()
            conn.close()
            
            return cleaned
            
        except Exception as e:
            logger.error(f"Failed to cleanup expired sessions: {e}")
            return 0

# Global instance
user_context = UserContext()
