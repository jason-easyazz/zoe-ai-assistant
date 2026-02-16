"""
Trust Gate Allowlist CRUD
=========================

Phase 0: Per-user allowlist of trusted contacts/sources.
Only contacts on the allowlist can trigger Zoe to execute actions (ACT mode).
All other sources are read-only (READ mode).

Each user manages their own allowlist independently.
"""

import json
import sqlite3
import logging
import os
from datetime import datetime
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# Valid contact types
VALID_CONTACT_TYPES = {
    "phone", "email", "telegram_id", "discord_id", "whatsapp", "service", "web"
}

# Valid permission scopes
VALID_PERMISSIONS = {
    "all",           # Full access
    "smart_home",    # Control HA devices
    "memory",        # Store/search memories
    "workflows",     # Trigger N8N workflows
    "research",      # Trigger Agent Zero research
    "lists",         # Manage lists
    "calendar",      # Manage calendar
    "reminders",     # Manage reminders
}


def init_trust_gate_db():
    """Initialize Trust Gate tables. Idempotent -- safe to call on every startup."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Read and execute the schema file
    schema_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "db", "schema", "trust_gate.sql"
    )
    if os.path.exists(schema_path):
        with open(schema_path, "r") as f:
            cursor.executescript(f.read())
        logger.info("Trust Gate DB schema applied")
    else:
        # Inline fallback if schema file not found
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS trust_allowlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                contact_type TEXT NOT NULL,
                contact_value TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                permissions TEXT NOT NULL DEFAULT '["all"]',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, contact_type, contact_value)
            );
            CREATE TABLE IF NOT EXISTS trust_gate_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                source_type TEXT NOT NULL,
                source_value TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'web',
                content_summary TEXT,
                decision TEXT NOT NULL,
                reason TEXT,
                action_requested TEXT,
                permissions_matched TEXT
            );
        """)
        logger.warning("Trust Gate schema file not found, used inline fallback")

    conn.commit()
    conn.close()


# Initialize on import
init_trust_gate_db()


# ---- Allowlist CRUD ----

def add_contact(
    user_id: str,
    contact_type: str,
    contact_value: str,
    label: str = "",
    permissions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Add a trusted contact to a user's allowlist.

    Args:
        user_id: The Zoe user who owns this entry
        contact_type: Type of contact (phone, email, telegram_id, etc.)
        contact_value: The contact identifier
        label: Human-friendly name
        permissions: List of permission scopes (default: ["all"])

    Returns:
        Dict with success status and entry data
    """
    if contact_type not in VALID_CONTACT_TYPES:
        return {"success": False, "error": f"Invalid contact_type: {contact_type}. Valid: {VALID_CONTACT_TYPES}"}

    perms = permissions or ["all"]
    invalid_perms = set(perms) - VALID_PERMISSIONS
    if invalid_perms:
        return {"success": False, "error": f"Invalid permissions: {invalid_perms}. Valid: {VALID_PERMISSIONS}"}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO trust_allowlist (user_id, contact_type, contact_value, label, permissions)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, contact_type, contact_value.strip().lower(), label, json.dumps(perms)))
        conn.commit()

        entry_id = cursor.lastrowid
        logger.info(f"Allowlist: added {contact_type}:{contact_value} for user {user_id} (perms: {perms})")

        return {
            "success": True,
            "entry": {
                "id": entry_id,
                "user_id": user_id,
                "contact_type": contact_type,
                "contact_value": contact_value.strip().lower(),
                "label": label,
                "permissions": perms,
            }
        }
    except sqlite3.IntegrityError:
        # Already exists -- update instead
        cursor.execute("""
            UPDATE trust_allowlist
            SET label = ?, permissions = ?, active = 1, updated_at = datetime('now')
            WHERE user_id = ? AND contact_type = ? AND contact_value = ?
        """, (label, json.dumps(perms), user_id, contact_type, contact_value.strip().lower()))
        conn.commit()

        logger.info(f"Allowlist: updated existing {contact_type}:{contact_value} for user {user_id}")
        return {
            "success": True,
            "entry": {
                "user_id": user_id,
                "contact_type": contact_type,
                "contact_value": contact_value.strip().lower(),
                "label": label,
                "permissions": perms,
            },
            "updated": True
        }
    except Exception as e:
        logger.error(f"Allowlist add failed: {e}")
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def remove_contact(user_id: str, entry_id: int) -> Dict[str, Any]:
    """Soft-delete a contact from the allowlist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE trust_allowlist
            SET active = 0, updated_at = datetime('now')
            WHERE id = ? AND user_id = ?
        """, (entry_id, user_id))
        conn.commit()

        if cursor.rowcount == 0:
            return {"success": False, "error": "Entry not found or not owned by user"}

        logger.info(f"Allowlist: removed entry {entry_id} for user {user_id}")
        return {"success": True, "message": f"Entry {entry_id} removed"}
    except Exception as e:
        logger.error(f"Allowlist remove failed: {e}")
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def list_contacts(user_id: str) -> List[Dict[str, Any]]:
    """List all active contacts on a user's allowlist."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT id, user_id, contact_type, contact_value, label, permissions, created_at, updated_at
            FROM trust_allowlist
            WHERE user_id = ? AND active = 1
            ORDER BY created_at DESC
        """, (user_id,))

        entries = []
        for row in cursor.fetchall():
            entries.append({
                "id": row["id"],
                "user_id": row["user_id"],
                "contact_type": row["contact_type"],
                "contact_value": row["contact_value"],
                "label": row["label"],
                "permissions": json.loads(row["permissions"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return entries
    except Exception as e:
        logger.error(f"Allowlist list failed: {e}")
        return []
    finally:
        conn.close()


def check_contact(
    user_id: str,
    contact_type: str,
    contact_value: str,
    required_permission: Optional[str] = None,
) -> Dict[str, Any]:
    """Check if a contact is on the user's allowlist and has the required permission.

    This is the core lookup used by the Trust Gate engine.

    Args:
        user_id: The Zoe user to check against
        contact_type: Type of contact
        contact_value: The contact identifier
        required_permission: Optional specific permission to check

    Returns:
        Dict with:
        - allowed: bool -- whether the contact is trusted
        - permissions: list -- the permissions the contact has
        - label: str -- human-friendly name
        - reason: str -- explanation for the decision
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT id, label, permissions
            FROM trust_allowlist
            WHERE user_id = ? AND contact_type = ? AND contact_value = ? AND active = 1
        """, (user_id, contact_type, contact_value.strip().lower()))

        row = cursor.fetchone()

        if not row:
            return {
                "allowed": False,
                "permissions": [],
                "label": "",
                "reason": "Sender not on allowlist"
            }

        perms = json.loads(row["permissions"])

        # Check specific permission if required
        if required_permission and required_permission != "all":
            if "all" not in perms and required_permission not in perms:
                return {
                    "allowed": False,
                    "permissions": perms,
                    "label": row["label"],
                    "reason": f"Contact lacks '{required_permission}' permission (has: {perms})"
                }

        return {
            "allowed": True,
            "permissions": perms,
            "label": row["label"],
            "reason": f"Allowlisted as '{row['label']}' with permissions: {perms}"
        }

    except Exception as e:
        logger.error(f"Allowlist check failed: {e}")
        # Fail closed -- deny access on error
        return {
            "allowed": False,
            "permissions": [],
            "label": "",
            "reason": f"Allowlist check error: {str(e)}"
        }
    finally:
        conn.close()


def update_permissions(
    user_id: str,
    entry_id: int,
    permissions: List[str],
) -> Dict[str, Any]:
    """Update the permissions for an allowlist entry."""
    invalid_perms = set(permissions) - VALID_PERMISSIONS
    if invalid_perms:
        return {"success": False, "error": f"Invalid permissions: {invalid_perms}"}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE trust_allowlist
            SET permissions = ?, updated_at = datetime('now')
            WHERE id = ? AND user_id = ? AND active = 1
        """, (json.dumps(permissions), entry_id, user_id))
        conn.commit()

        if cursor.rowcount == 0:
            return {"success": False, "error": "Entry not found"}

        return {"success": True, "permissions": permissions}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()
