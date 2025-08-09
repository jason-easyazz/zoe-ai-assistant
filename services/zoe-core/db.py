import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


async def init_database(database_path: str) -> None:
    """Initialize database with enhanced schema."""
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(database_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")

        # Enhanced schema with integration support
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER DEFAULT 0,
                source TEXT DEFAULT 'web',
                user_id TEXT DEFAULT 'default'
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source TEXT DEFAULT 'web',
                metadata TEXT,
                FOREIGN KEY (conversation_id) REFERENCES conversations (id)
            );

            CREATE TABLE IF NOT EXISTS journal_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                content TEXT NOT NULL,
                mood_score REAL,
                word_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source TEXT DEFAULT 'manual',
                user_id TEXT DEFAULT 'default'
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'pending',
                priority TEXT DEFAULT 'medium',
                due_date DATE,
                source TEXT DEFAULT 'manual',
                integration_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT DEFAULT 'default'
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                start_date DATE NOT NULL,
                start_time TIME,
                location TEXT,
                source TEXT DEFAULT 'manual',
                integration_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT DEFAULT 'default'
            );

            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                passcode TEXT
            );

            CREATE TABLE IF NOT EXISTS user_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(category, setting_key)
            );

            CREATE TABLE IF NOT EXISTS integration_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                webhook_type TEXT NOT NULL,
                data TEXT NOT NULL,
                processed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Insert default settings
        default_settings = [
            ('personality', 'fun_level', '7'),
            ('personality', 'empathy_level', '8'),
            ('personality', 'cheeky_level', '6'),
            ('personality', 'formality_level', '3'),
            ('integrations', 'voice_enabled', 'true'),
            ('integrations', 'n8n_enabled', 'true'),
            ('integrations', 'ha_enabled', 'true'),
            ('integrations', 'matrix_enabled', 'false'),
            ('ai', 'active_model', 'llama3.2:3b'),
            ('ai', 'available_models', '["llama3.2:3b", "mistral:7b"]'),
        ]

        for category, key, value in default_settings:
            await db.execute(
                """
                INSERT OR IGNORE INTO user_settings (category, setting_key, setting_value)
                VALUES (?, ?, ?)
                """,
                (category, key, value),
            )

        await db.execute(
            "INSERT OR IGNORE INTO users (id, display_name) VALUES (?, ?)",
            ("default", "Default User"),
        )

        await db.commit()
        logger.info("✅ Enhanced database initialized")
