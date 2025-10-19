from fastapi import APIRouter
import sqlite3
import os

router = APIRouter(prefix="/api/setup/multiuser", tags=["setup", "multiuser"])

DB_PATH = "/app/data/zoe.db"


def _ensure_data_dir() -> None:
    os.makedirs("/app/data", exist_ok=True)


def _run_sql_statements(cursor: sqlite3.Cursor, statements: list[str]) -> None:
    for stmt in statements:
        if not stmt.strip():
            continue
        try:
            cursor.execute(stmt)
        except Exception:
            # Idempotent best-effort: ignore if statement fails (e.g., column exists)
            pass


@router.post("/run")
async def run_multiuser_migration():
    """Run idempotent migration to enable multi-user support."""
    _ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    statements = [
        # User management tables
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            is_admin BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            settings_json TEXT DEFAULT '{}'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            service TEXT NOT NULL,
            encrypted_key TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            UNIQUE(user_id, service)
        )
        """,
        # Add user_id columns to existing tables if they exist
        "ALTER TABLE events ADD COLUMN user_id TEXT DEFAULT 'default'",
        "ALTER TABLE lists ADD COLUMN user_id TEXT DEFAULT 'default'",
        "ALTER TABLE memories ADD COLUMN user_id TEXT DEFAULT 'default'",
        "ALTER TABLE tasks ADD COLUMN user_id TEXT DEFAULT 'default'",
        # Indexes
        "CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_lists_user ON lists(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id)",
    ]

    _run_sql_statements(cur, statements)

    # Ensure default admin user exists
    try:
        cur.execute("SELECT 1 FROM users WHERE user_id = ?", ("default",))
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO users (user_id, email, username, password_hash, is_admin) VALUES (?, ?, ?, ?, 1)",
                ("default", "admin@local", "admin", "CHANGE_ME"),
            )
    except Exception:
        pass

    conn.commit()
    conn.close()

    # Create on-disk folder structure
    try:
        os.makedirs("/app/data/system/models", exist_ok=True)
        os.makedirs("/app/data/system/backups", exist_ok=True)
        for sub in ["backups", "exports", "uploads"]:
            os.makedirs(f"/app/data/users/default/{sub}", exist_ok=True)
    except Exception:
        pass

    return {"status": "ok", "message": "Multi-user schema ensured", "default_user": "created_or_exists"}






