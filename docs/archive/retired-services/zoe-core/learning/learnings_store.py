"""
Learnings Store
================

Phase 2: Store and retrieve learnings in SQLite + RAG memory.

Learnings are:
- Per-user (Alice's corrections don't affect Bob)
- Trust-gated (owner corrections auto-confirmed, trusted contacts need review)
- Searchable (used to enrich LLM context before generating responses)
- Expiring (pending learnings expire after 30 days if not confirmed)

Storage is SQLite for structured data, with optional forwarding to
the RAG memory service (zoe-mem-agent) for semantic search.
"""

import json
import sqlite3
import logging
import os
import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from learning.reflector import Learning

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
MEM_AGENT_URL = os.getenv("MEM_AGENT_URL", "http://zoe-mem-agent:11435")

EXPIRY_DAYS = 30  # Pending learnings expire after this many days


def init_learnings_db():
    """Initialize the learnings table."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS learnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            context TEXT,
            source_message TEXT,
            confidence REAL NOT NULL DEFAULT 0.0,
            trust_level TEXT NOT NULL DEFAULT 'owner',
            status TEXT NOT NULL DEFAULT 'confirmed',
            detected_at TEXT NOT NULL,
            confirmed_at TEXT,
            expires_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_learnings_user
            ON learnings(user_id, status);

        CREATE INDEX IF NOT EXISTS idx_learnings_category
            ON learnings(user_id, category, status);
    """)
    conn.commit()
    conn.close()
    logger.info("Learnings DB initialized")


# Initialize on import
init_learnings_db()


def store_learning(learning: Learning) -> Dict[str, Any]:
    """Store a learning in the database.

    Owner learnings are auto-confirmed.
    Trusted contact learnings are pending_review with 30-day expiry.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    expires_at = None
    confirmed_at = None

    if learning.status == "confirmed":
        confirmed_at = datetime.utcnow().isoformat() + "Z"
    else:
        expires_at = (datetime.utcnow() + timedelta(days=EXPIRY_DAYS)).isoformat() + "Z"

    try:
        cursor.execute("""
            INSERT INTO learnings
                (user_id, category, content, context, source_message,
                 confidence, trust_level, status, detected_at, confirmed_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            learning.user_id,
            learning.category,
            learning.content,
            learning.context,
            learning.source_message,
            learning.confidence,
            learning.trust_level,
            learning.status,
            learning.detected_at,
            confirmed_at,
            expires_at,
        ))
        conn.commit()
        learning_id = cursor.lastrowid

        logger.info(f"Stored learning #{learning_id}: {learning.category} for user {learning.user_id} ({learning.status})")
        return {"success": True, "id": learning_id, "status": learning.status}

    except Exception as e:
        logger.error(f"Failed to store learning: {e}")
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def get_relevant_learnings(
    user_id: str,
    query: str = "",
    category: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Get relevant learnings for a user.

    Returns confirmed + pending_review learnings (both are used for context).
    Expired pending learnings are excluded.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        now = datetime.utcnow().isoformat() + "Z"

        if category:
            cursor.execute("""
                SELECT * FROM learnings
                WHERE user_id = ?
                  AND category = ?
                  AND status IN ('confirmed', 'pending_review')
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY confidence DESC, created_at DESC
                LIMIT ?
            """, (user_id, category, now, limit))
        else:
            cursor.execute("""
                SELECT * FROM learnings
                WHERE user_id = ?
                  AND status IN ('confirmed', 'pending_review')
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY confidence DESC, created_at DESC
                LIMIT ?
            """, (user_id, now, limit))

        return [dict(row) for row in cursor.fetchall()]

    except Exception as e:
        logger.error(f"Failed to get learnings: {e}")
        return []
    finally:
        conn.close()


def confirm_learning(user_id: str, learning_id: int) -> Dict[str, Any]:
    """Confirm a pending learning (primary user approval)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE learnings
            SET status = 'confirmed',
                confirmed_at = datetime('now'),
                expires_at = NULL
            WHERE id = ? AND user_id = ? AND status = 'pending_review'
        """, (learning_id, user_id))
        conn.commit()

        if cursor.rowcount == 0:
            return {"success": False, "error": "Learning not found or already confirmed"}

        return {"success": True, "message": "Learning confirmed"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def dismiss_learning(user_id: str, learning_id: int) -> Dict[str, Any]:
    """Dismiss a pending learning."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE learnings SET status = 'rejected'
            WHERE id = ? AND user_id = ? AND status = 'pending_review'
        """, (learning_id, user_id))
        conn.commit()

        if cursor.rowcount == 0:
            return {"success": False, "error": "Learning not found"}

        return {"success": True, "message": "Learning dismissed"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def get_pending_count(user_id: str) -> int:
    """Get count of pending learnings for a user."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT COUNT(*) FROM learnings
            WHERE user_id = ? AND status = 'pending_review'
              AND (expires_at IS NULL OR expires_at > datetime('now'))
        """, (user_id,))
        return cursor.fetchone()[0]
    except Exception:
        return 0
    finally:
        conn.close()


def build_learnings_context(user_id: str, message: str = "", limit: int = 5) -> str:
    """Build a learnings context string for injection into LLM prompt.

    Returns a formatted string of relevant learnings for the user.
    """
    learnings = get_relevant_learnings(user_id, query=message, limit=limit)

    if not learnings:
        return ""

    lines = ["\n## Things I've Learned About You\n"]
    for l in learnings:
        status_marker = " (pending confirmation)" if l["status"] == "pending_review" else ""
        lines.append(f"- [{l['category']}] {l['content']}{status_marker}")

    return "\n".join(lines)
