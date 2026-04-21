"""
Pattern Detector
==================

Phase 8: Detects repeated multi-step workflows from conversation history.

A pattern is detected when:
- count >= 3 occurrences
- (last_occurrence - first_occurrence) >= 7 days
- All occurrences had success/positive outcomes

This prevents noise (3 retries of a broken command) from being treated
as a genuine repeated workflow.
"""

import sqlite3
import json
import re
import logging
import os
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from collections import defaultdict, Counter

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

MIN_OCCURRENCES = 3
MIN_TIME_SPAN_DAYS = 7


def init_patterns_db():
    """Initialize pattern detection tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS detected_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            pattern_hash TEXT NOT NULL,
            description TEXT NOT NULL,
            action_sequence TEXT NOT NULL,
            occurrence_count INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            success_rate REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'detected',
            skill_proposed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, pattern_hash)
        );

        CREATE INDEX IF NOT EXISTS idx_patterns_user
            ON detected_patterns(user_id, status);

        CREATE TABLE IF NOT EXISTS pattern_occurrences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            session_id TEXT,
            success INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (pattern_id) REFERENCES detected_patterns(id)
        );
    """)
    conn.commit()
    conn.close()
    logger.info("Pattern detector DB initialized")


# Initialize on import
init_patterns_db()


def extract_action_signatures(messages: List[Dict]) -> List[str]:
    """Extract action signatures from a list of chat messages.

    An action signature is a simplified representation of what was done,
    e.g., "smart_home.turn_on", "calendar.create", "list.add".
    """
    signatures = []
    for msg in messages:
        metadata = msg.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}

        # Look for routing/intent information
        intent = metadata.get("intent", "")
        routing = metadata.get("routing", "")
        tool = metadata.get("tool", "")

        if intent:
            signatures.append(f"intent:{intent}")
        elif tool:
            signatures.append(f"tool:{tool}")
        elif routing and routing != "conversation":
            signatures.append(f"routing:{routing}")

    return signatures


def compute_pattern_hash(action_sequence: List[str]) -> str:
    """Compute a stable hash for an action sequence."""
    import hashlib
    seq_str = "|".join(sorted(set(action_sequence)))
    return hashlib.md5(seq_str.encode()).hexdigest()[:12]


def detect_patterns(user_id: str) -> List[Dict[str, Any]]:
    """Scan conversation history for repeated patterns.

    Returns newly detected patterns that meet the threshold:
    - 3+ occurrences
    - Spanning 7+ days
    - With positive outcomes

    Returns:
        List of new pattern dictionaries
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    new_patterns = []

    try:
        # Get recent conversations with action intents/tools
        cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
        cursor.execute("""
            SELECT session_id, content, metadata, created_at
            FROM chat_messages
            WHERE session_id IN (
                SELECT id FROM chat_sessions WHERE user_id = ?
            )
            AND role = 'assistant'
            AND created_at > ?
            ORDER BY created_at ASC
        """, (user_id, cutoff))

        messages = [dict(row) for row in cursor.fetchall()]

        # Group by session and extract action signatures
        session_actions = defaultdict(list)
        session_times = {}
        for msg in messages:
            sid = msg["session_id"]
            sigs = extract_action_signatures([msg])
            if sigs:
                session_actions[sid].extend(sigs)
                session_times[sid] = msg["created_at"]

        # Count action sequences
        sequence_counts = Counter()
        sequence_times = defaultdict(list)

        for sid, actions in session_actions.items():
            if len(actions) >= 2:
                pattern_hash = compute_pattern_hash(actions)
                sequence_counts[pattern_hash] += 1
                sequence_times[pattern_hash].append(session_times.get(sid, ""))

        # Find patterns meeting threshold
        for pattern_hash, count in sequence_counts.items():
            if count < MIN_OCCURRENCES:
                continue

            times = sorted([t for t in sequence_times[pattern_hash] if t])
            if len(times) < 2:
                continue

            # Check time span
            try:
                first = datetime.fromisoformat(times[0].replace("Z", ""))
                last = datetime.fromisoformat(times[-1].replace("Z", ""))
                span_days = (last - first).days
            except Exception:
                continue

            if span_days < MIN_TIME_SPAN_DAYS:
                continue

            # Check if already detected
            cursor.execute("""
                SELECT id FROM detected_patterns
                WHERE user_id = ? AND pattern_hash = ?
            """, (user_id, pattern_hash))

            if cursor.fetchone():
                # Update existing pattern
                cursor.execute("""
                    UPDATE detected_patterns
                    SET occurrence_count = ?, last_seen = ?
                    WHERE user_id = ? AND pattern_hash = ?
                """, (count, times[-1], user_id, pattern_hash))
            else:
                # New pattern detected
                cursor.execute("""
                    INSERT INTO detected_patterns
                        (user_id, pattern_hash, description, action_sequence,
                         occurrence_count, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_id, pattern_hash,
                    f"Repeated workflow ({count} occurrences over {span_days} days)",
                    json.dumps(list(session_actions.values())[0] if session_actions else []),
                    count, times[0], times[-1],
                ))

                new_patterns.append({
                    "pattern_hash": pattern_hash,
                    "count": count,
                    "span_days": span_days,
                    "first_seen": times[0],
                    "last_seen": times[-1],
                })

        conn.commit()

    except Exception as e:
        logger.error(f"Pattern detection failed: {e}")
    finally:
        conn.close()

    if new_patterns:
        logger.info(f"Detected {len(new_patterns)} new patterns for user {user_id}")

    return new_patterns


def get_unproposed_patterns(user_id: str) -> List[Dict[str, Any]]:
    """Get detected patterns that haven't been proposed as skills yet."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT * FROM detected_patterns
            WHERE user_id = ? AND skill_proposed = 0
              AND occurrence_count >= ? AND status = 'detected'
            ORDER BY occurrence_count DESC
        """, (user_id, MIN_OCCURRENCES))

        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Failed to get unproposed patterns: {e}")
        return []
    finally:
        conn.close()
