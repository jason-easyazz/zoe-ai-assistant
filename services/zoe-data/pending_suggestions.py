"""Store and resolve proactive save offers (list, reminder, calendar, etc.)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def store_suggestions(
    user_id: str,
    session_id: str,
    suggestions: list[dict],
) -> int:
    if not suggestions or user_id in ("guest", ""):
        return 0
    now = datetime.now(timezone.utc).isoformat()
    stored = 0
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            for s in suggestions[:3]:
                sid = str(uuid.uuid4())
                slots = s.get("pre_filled_slots") or {}
                await db.execute(
                    """INSERT INTO pending_suggestions
                       (id, user_id, session_id, action_type, description, list_type,
                        when_hint, amount_hint, offer_phrase, pre_filled_slots,
                        created_at, turns_elapsed, expire_after_turns, resolved)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 2, 0)""",
                    (
                        sid,
                        user_id,
                        session_id,
                        s.get("action_type", ""),
                        s.get("description", "")[:500],
                        s.get("list_type"),
                        s.get("when_hint"),
                        s.get("amount_hint"),
                        s.get("offer_phrase", "")[:300],
                        json.dumps(slots),
                        now,
                    ),
                )
                stored += 1
            await db.commit()
    except Exception as exc:
        logger.debug("pending_suggestions.store failed: %s", exc)
    return stored


async def load_for_prompt(user_id: str, session_id: str, *, limit: int = 3) -> str:
    if user_id in ("guest", ""):
        return ""
    lines: list[str] = []
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            async with db.execute(
                """SELECT id, offer_phrase, turns_elapsed, expire_after_turns
                   FROM pending_suggestions
                   WHERE user_id = ? AND session_id = ? AND resolved = 0
                   ORDER BY created_at ASC LIMIT ?""",
                (user_id, session_id, limit),
            ) as cur:
                rows = await cur.fetchall()
            for row in rows:
                turns = int(row["turns_elapsed"] or 0) + 1
                expire = int(row["expire_after_turns"] or 2)
                if turns > expire:
                    await db.execute(
                        "UPDATE pending_suggestions SET resolved = 1 WHERE id = ?",
                        (row["id"],),
                    )
                    continue
                await db.execute(
                    "UPDATE pending_suggestions SET turns_elapsed = ? WHERE id = ?",
                    (turns, row["id"]),
                )
                lines.append(f"- {row['offer_phrase']}")
            await db.commit()
    except Exception as exc:
        logger.debug("pending_suggestions.load failed: %s", exc)
    if not lines:
        return ""
    return "\n".join(lines)


async def list_active(user_id: str, session_id: str) -> list[dict]:
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            async with db.execute(
                """SELECT id, action_type, description, offer_phrase, pre_filled_slots
                   FROM pending_suggestions
                   WHERE user_id = ? AND session_id = ? AND resolved = 0
                   ORDER BY created_at ASC LIMIT 5""",
                (user_id, session_id),
            ) as cur:
                rows = await cur.fetchall()
        out = []
        for r in rows:
            slots = {}
            try:
                slots = json.loads(r["pre_filled_slots"] or "{}")
            except json.JSONDecodeError:
                pass
            out.append({
                "id": r["id"],
                "action_type": r["action_type"],
                "description": r["description"],
                "offer_phrase": r["offer_phrase"],
                "pre_filled_slots": slots,
            })
        return out
    except Exception as exc:
        logger.debug("pending_suggestions.list_active failed: %s", exc)
        return []


def ui_components_for_suggestions(suggestions: list[dict]) -> list[dict]:
    """Build AG-UI confirm cards for active suggestions."""
    comps = []
    for s in suggestions:
        comps.append({
            "type": "action_card",
            "title": s.get("offer_phrase") or "Save this?",
            "actions": [{
                "label": "Save",
                "action": "pending_suggestion_accept",
                "suggestion_id": s["id"],
            }],
        })
    return comps


async def mark_resolved(suggestion_id: str, user_id: str) -> bool:
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            await db.execute(
                "UPDATE pending_suggestions SET resolved = 1 WHERE id = ? AND user_id = ?",
                (suggestion_id, user_id),
            )
            await db.commit()
        return True
    except Exception as exc:
        logger.debug("pending_suggestions.mark_resolved failed: %s", exc)
        return False


async def execute_suggestion(suggestion_id: str, user_id: str) -> dict:
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            async with db.execute(
                """SELECT action_type, pre_filled_slots FROM pending_suggestions
                   WHERE id = ? AND user_id = ? AND resolved = 0""",
                (suggestion_id, user_id),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return {"ok": False, "error": "not_found"}
            action = row["action_type"]
            slots = json.loads(row["pre_filled_slots"] or "{}")

            if action == "list_add":
                lt = slots.get("list_type", "shopping")
                text = slots.get("item") or slots.get("description", "")
                ln = lt.capitalize()
                async with db.execute(
                    "SELECT id FROM lists WHERE list_type=? AND name=? AND deleted=0"
                    " AND (user_id=? OR visibility='family')"
                    " ORDER BY CASE WHEN visibility='family' THEN 0 ELSE 1 END LIMIT 1",
                    (lt, ln, user_id),
                ) as cur2:
                    lrow = await cur2.fetchone()
                if lrow:
                    list_id = lrow["id"]
                else:
                    list_id = str(uuid.uuid4())
                    await db.execute(
                        "INSERT INTO lists (id, user_id, name, list_type, visibility) VALUES (?,?,?,?,?)",
                        (list_id, user_id, ln, lt, "family"),
                    )
                item_id = str(uuid.uuid4())
                await db.execute(
                    "INSERT INTO list_items (id, list_id, text) VALUES (?,?,?)",
                    (item_id, list_id, text),
                )
                result = {"item_id": item_id, "list_id": list_id, "text": text}
            elif action == "reminder_create":
                rid = str(uuid.uuid4())
                title = slots.get("title") or slots.get("description", "Reminder")
                await db.execute(
                    """INSERT INTO reminders (id, user_id, title, description, reminder_type,
                       category, priority, due_date, due_time, is_active, acknowledged, visibility, deleted)
                       VALUES (?, ?, ?, '', 'general', 'general', 'medium', NULL, NULL, 1, 0, 'personal', 0)""",
                    (rid, user_id, title),
                )
                result = {"reminder_id": rid, "title": title}
            elif action == "note_create":
                nid = str(uuid.uuid4())
                title = slots.get("title") or "Note"
                content = slots.get("content") or slots.get("description", "")
                await db.execute(
                    "INSERT INTO notes (id, user_id, title, content, category, visibility, deleted) VALUES (?,?,?,?,?,?,0)",
                    (nid, user_id, title, content, "general", "personal"),
                )
                result = {"note_id": nid, "title": title}
            else:
                return {"ok": False, "error": f"unsupported_action:{action}"}
            await db.commit()
        await mark_resolved(suggestion_id, user_id)
        return {"ok": True, "action": action, "result": result}
    except Exception as exc:
        logger.warning("pending_suggestions.execute failed: %s", exc)
        return {"ok": False, "error": str(exc)}
