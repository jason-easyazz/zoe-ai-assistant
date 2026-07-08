"""Store and resolve proactive save offers (list, reminder, calendar, etc.)."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date, datetime, timezone

from db_pool import get_db_ctx, get_pool

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def person_suggestions_enabled() -> bool:
    """Contact suggestions (`person_create` action) — default OFF.

    Gates both the executor branch below and the Phase-2 emitters, so the whole
    contacts-from-known-people bridge is dark until turned on. See
    docs/adr/ADR-contacts-from-known-people.md.
    """
    return os.environ.get("ZOE_PERSON_SUGGEST_ENABLED", "").strip().lower() in _TRUTHY


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
        async with get_db_ctx() as db:
            for s in suggestions[:3]:
                sid = str(uuid.uuid4())
                slots = s.get("pre_filled_slots") or {}
                await db.execute(
                    """INSERT INTO pending_suggestions
                       (id, user_id, session_id, action_type, description, list_type,
                        when_hint, amount_hint, offer_phrase, pre_filled_slots,
                        created_at, turns_elapsed, expire_after_turns, resolved)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 0, 2, 0)""",
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
                )
                stored += 1
    except Exception as exc:
        logger.debug("pending_suggestions.store failed: %s", exc)
    return stored


async def load_for_prompt(user_id: str, session_id: str, *, limit: int = 3) -> str:
    if user_id in ("guest", ""):
        return ""
    lines: list[str] = []
    try:
        async with get_db_ctx() as db:
            rows = await db.fetch(
                """SELECT id, offer_phrase, turns_elapsed, expire_after_turns
                   FROM pending_suggestions
                   WHERE user_id = $1 AND session_id = $2 AND resolved = 0
                   ORDER BY created_at ASC LIMIT $3""",
                user_id,
                session_id,
                limit,
            )
            for row in rows:
                turns = int(row["turns_elapsed"] or 0) + 1
                expire = int(row["expire_after_turns"] or 2)
                if turns > expire:
                    await db.execute(
                        "UPDATE pending_suggestions SET resolved = 1 WHERE id = $1",
                        row["id"],
                    )
                    continue
                await db.execute(
                    "UPDATE pending_suggestions SET turns_elapsed = $1 WHERE id = $2",
                    turns,
                    row["id"],
                )
                lines.append(f"- {row['offer_phrase']}")
    except Exception as exc:
        logger.debug("pending_suggestions.load failed: %s", exc)
    if not lines:
        return ""
    return "\n".join(lines)


async def list_active(user_id: str, session_id: str) -> list[dict]:
    try:
        async with get_db_ctx() as db:
            rows = await db.fetch(
                """SELECT id, action_type, description, offer_phrase, pre_filled_slots
                   FROM pending_suggestions
                   WHERE user_id = $1 AND session_id = $2 AND resolved = 0
                   ORDER BY created_at ASC LIMIT 5""",
                user_id,
                session_id,
            )
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
        async with get_db_ctx() as db:
            await db.execute(
                "UPDATE pending_suggestions SET resolved = 1 WHERE id = $1 AND user_id = $2",
                suggestion_id,
                user_id,
            )
        return True
    except Exception as exc:
        logger.debug("pending_suggestions.mark_resolved failed: %s", exc)
        return False


async def _execute_action(conn, action: str, slots: dict, user_id: str) -> dict:
    if action == "list_add":
        lt = slots.get("list_type", "shopping")
        text = slots.get("item") or slots.get("description", "")
        ln = lt.capitalize()
        lrow = await conn.fetchrow(
            "SELECT id FROM lists WHERE list_type=$1 AND name=$2 AND deleted=0"
            " AND (user_id=$3 OR visibility='family')"
            " ORDER BY CASE WHEN visibility='family' THEN 0 ELSE 1 END LIMIT 1",
            lt,
            ln,
            user_id,
        )
        if lrow:
            list_id = lrow["id"]
        else:
            list_id = str(uuid.uuid4())
            visibility = "personal" if lt in ("personal", "tasks") else "family"
            await conn.execute(
                "INSERT INTO lists (id, user_id, name, list_type, visibility) VALUES ($1,$2,$3,$4,$5)",
                list_id,
                user_id,
                ln,
                lt,
                visibility,
            )
        item_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO list_items (id, list_id, text) VALUES ($1,$2,$3)",
            item_id,
            list_id,
            text,
        )
        return {"item_id": item_id, "list_id": list_id, "text": text}

    if action == "reminder_create":
        rid = str(uuid.uuid4())
        title = slots.get("title") or slots.get("description", "Reminder")
        await conn.execute(
            """INSERT INTO reminders (id, user_id, title, description, reminder_type,
               category, priority, due_date, due_time, is_active, acknowledged, visibility, deleted)
               VALUES ($1, $2, $3, '', 'general', 'general', 'medium', NULL, NULL, 1, 0, 'personal', 0)""",
            rid,
            user_id,
            title,
        )
        return {"reminder_id": rid, "title": title}

    if action == "calendar_create":
        eid = str(uuid.uuid4())
        title = slots.get("title") or slots.get("description", "Event")
        start_date = slots.get("start_date") or date.today().isoformat()
        await conn.execute(
            """INSERT INTO events (id, user_id, title, start_date, category, all_day, visibility, deleted)
               VALUES ($1, $2, $3, $4, 'general', 0, 'family', 0)""",
            eid,
            user_id,
            title,
            start_date,
        )
        return {"event_id": eid, "title": title, "start_date": start_date}

    if action == "note_create":
        nid = str(uuid.uuid4())
        title = slots.get("title") or "Note"
        content = slots.get("content") or slots.get("description", "")
        await conn.execute(
            "INSERT INTO notes (id, user_id, title, content, category, visibility, deleted) VALUES ($1,$2,$3,$4,$5,$6,0)",
            nid,
            user_id,
            title,
            content,
            "general",
            "personal",
        )
        return {"note_id": nid, "title": title}

    if action == "person_create":
        # Turn a known-but-not-a-contact person into a full, editable contact row.
        # Fails closed when the feature is off, and rejects pronoun/junk names.
        if not person_suggestions_enabled():
            raise ValueError("unsupported_action:person_create")
        name = (slots.get("name") or "").strip()
        from person_extractor import _looks_like_person_name  # tiny pure guard; lazy import
        if not name or not _looks_like_person_name(name):
            raise ValueError("invalid_person_name")
        # Dedup: never mint a second row for a name the user already has.
        existing = await conn.fetchrow(
            "SELECT id FROM people WHERE user_id=$1 AND lower(name)=lower($2) AND deleted=0 LIMIT 1",
            user_id,
            name,
        )
        if existing:
            return {"person_id": existing["id"], "name": name, "created": False}
        pid = str(uuid.uuid4())
        relationship = (slots.get("relationship") or "").strip() or None
        # circle: NULL unless a real category is supplied (never the column-name
        # literal, which would land the contact in an undefined UI bucket).
        circle = (slots.get("circle") or "").strip() or None
        # visibility: default PRIVATE — a contact Zoe proposes from conversation
        # may be personal (a therapist, a work colleague); don't auto-share it
        # with the whole family. Owner still sees it (people query is OR user_id).
        visibility = (slots.get("visibility") or "").strip() or "personal"
        await conn.execute(
            "INSERT INTO people (id, user_id, name, relationship, circle, context, visibility, is_partial)"
            " VALUES ($1,$2,$3,$4,$5,$6,$7,0)",
            pid,
            user_id,
            name,
            relationship,
            circle,
            "suggested",
            visibility,
        )
        return {"person_id": pid, "name": name, "relationship": relationship, "created": True}

    raise ValueError(f"unsupported_action:{action}")


async def execute_suggestion(suggestion_id: str, user_id: str) -> dict:
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """SELECT action_type, pre_filled_slots FROM pending_suggestions
                       WHERE id = $1 AND user_id = $2 AND resolved = 0""",
                    suggestion_id,
                    user_id,
                )
                if not row:
                    return {"ok": False, "error": "not_found"}
                action = row["action_type"]
                slots = json.loads(row["pre_filled_slots"] or "{}")
                result = await _execute_action(conn, action, slots, user_id)
                await conn.execute(
                    "UPDATE pending_suggestions SET resolved = 1 WHERE id = $1 AND user_id = $2",
                    suggestion_id,
                    user_id,
                )
        return {"ok": True, "action": action, "result": result}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.warning("pending_suggestions.execute failed: %s", exc)
        return {"ok": False, "error": str(exc)}
