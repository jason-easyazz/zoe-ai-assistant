"""Semantic-first memories API.

Every operation here lives in MemPalace, reached through `MemoryService`. The
earlier SQLite `memory_items` mirror has been retired: proposals land as
`status='pending'` rows in MemPalace, review flips the status, and
search/list read back through the service with per-user scoping.

See `docs/architecture/memory.md` for the full design.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user, require_admin
from database import get_db
from guest_policy import require_feature_access
from memory_service import (
    MemoryRef,
    MemoryService,
    MemoryServiceError,
    get_memory_service,
)
from models import MemoryProposalCreate, MemoryReviewBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memories", tags=["memories"])


# ─── Helpers ─────────────────────────────────────────────────────────────

_STATUS_ALIASES = {
    # Accept both the new canonical statuses and the legacy `memory_items`
    # values so the review UI doesn't need to change in lockstep.
    "pending_review": "pending",
    "pending": "pending",
    "approved": "approved",
    "rejected": "rejected",
    "archived": "archived",
    "superseded": "superseded",
}


def _ref_to_dict(ref: MemoryRef) -> dict[str, Any]:
    """Serialise MemoryRef for HTTP, keeping the legacy shape where practical.

    The journal / memories UIs expect `id`, `content`, `memory_type`, and a
    few other fields that used to come from `memory_items`. We map MemPalace
    metadata back into that shape so we don't have to rev every consumer in
    the same PR.
    """
    meta = ref.metadata or {}
    return {
        "id": ref.id,
        "user_id": meta.get("user_id") or meta.get("wing"),
        "memory_type": meta.get("memory_type", "fact"),
        "content": ref.text,
        "title": meta.get("title"),
        "entity_type": meta.get("entity_type"),
        "entity_id": meta.get("entity_id"),
        "confidence": float(meta.get("confidence", 0.0) or 0.0),
        "source_type": meta.get("source"),
        "source_id": meta.get("session_id") or meta.get("user_turn_id"),
        "source_excerpt": meta.get("source_excerpt"),
        "visibility": meta.get("visibility", "personal"),
        "status": meta.get("status", "approved"),
        "tags": [t for t in str(meta.get("tags", "") or "").split(",") if t],
        "observed_at": meta.get("added_at"),
        "last_verified_at": meta.get("reviewed_at"),
        "reviewed_by": meta.get("reviewed_by"),
        "reviewed_at": meta.get("reviewed_at"),
        "review_note": meta.get("review_note"),
        "created_at": meta.get("added_at"),
        "updated_at": meta.get("reviewed_at") or meta.get("added_at"),
        "expires_at": meta.get("expires_at"),
        "supersedes_id": meta.get("supersedes_id"),
        "superseded_by_id": meta.get("superseded_by_id"),
        "access_count": int(meta.get("access_count", 0) or 0),
        "source": "mempalace",
    }


def _normalise_status(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    key = raw.lower().strip()
    return _STATUS_ALIASES.get(key, key)


def _svc() -> MemoryService:
    return get_memory_service()


# ─── Endpoints ───────────────────────────────────────────────────────────


@router.get("/")
async def list_memories(
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List memories for the caller, optionally filtered by status.

    Status defaults to `approved` so the UI "my memories" tab doesn't see
    pending / rejected rows unless it asks.
    """
    await require_feature_access(db, user, feature="memories", action="read")
    svc = _svc()
    filter_status = _normalise_status(status) or "approved"
    rows = await svc.list_by_status(
        user_id=user["user_id"],
        status=filter_status,
        limit=limit,
        offset=offset,
    )
    memories = [_ref_to_dict(r) for r in rows]
    return {"memories": memories, "count": len(memories)}


@router.post("/proposals")
async def create_memory_proposal(
    body: MemoryProposalCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a memory proposal.

    High-confidence preferences auto-approve (same heuristic as before); all
    other proposals land as `status='pending'` and surface in the review
    queue. The write goes straight into MemPalace — no SQLite mirror.
    """
    await require_feature_access(db, user, feature="memories", action="write")
    svc = _svc()
    auto_approve = body.confidence >= 0.9 and body.memory_type == "preference"
    status = "approved" if auto_approve else "pending"
    tags = ["zoe-memory", body.memory_type or "fact"]
    if body.source_type:
        tags.append(f"src:{body.source_type}")
    try:
        ref = await svc.ingest(
            body.content,
            user_id=user["user_id"],
            source=body.source_type or "proposal",
            memory_type=body.memory_type or "fact",
            confidence=float(body.confidence or 0.5),
            status=status,
            tags=tags,
            entity_type=body.entity_type,
            entity_id=body.entity_id,
        )
    except MemoryServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if ref is None:
        # Silent drops (PII / dedup / opt-out) return 202 so the caller can
        # distinguish "we took no action" from a hard failure.
        return {"status": "dropped", "reason": "pii_or_dedup"}, 202
    return _ref_to_dict(ref)


@router.get("/review")
async def list_review_queue(
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Surface rows awaiting human review for the current user."""
    await require_feature_access(db, user, feature="memories", action="review")
    svc = _svc()
    rows = await svc.list_by_status(
        user_id=user["user_id"], status="pending", limit=limit
    )
    items = [_ref_to_dict(r) for r in rows]
    return {"items": items, "count": len(items)}


@router.post("/{memory_id}/review")
async def review_memory(
    memory_id: str,
    body: MemoryReviewBody,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="memories", action="review")
    action = (body.action or "").lower().strip()
    if action not in {"approve", "reject", "edit"}:
        raise HTTPException(status_code=400, detail="action must be approve|reject|edit")
    svc = _svc()
    # Safety: callers can only review their own memories unless they're admin.
    current = await svc.get(memory_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    owner = current.metadata.get("user_id") or current.metadata.get("wing")
    is_admin = (user.get("role") or "").lower() == "admin"
    if owner and owner != user["user_id"] and not is_admin:
        raise HTTPException(status_code=403, detail="Cannot review another user's memory")
    try:
        ref = await svc.review(
            memory_id,
            decision=action,
            actor=user["user_id"],
            edits=body.content,
            note=body.note,
        )
    except MemoryServiceError as exc:
        # ValueErrors from bad input become 400, missing-row becomes 404.
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return _ref_to_dict(ref)


@router.get("/search")
async def search_memories(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Semantic search over MemPalace scoped to the caller's user_id."""
    await require_feature_access(db, user, feature="memories", action="read")
    svc = _svc()
    hits = await svc.search(q, user_id=user["user_id"], limit=limit)
    results = []
    for ref in hits:
        row = _ref_to_dict(ref)
        row["score"] = ref.score
        results.append(row)
    return {"query": q, "results": results, "count": len(results)}


@router.get("/people")
async def people_with_memories(
    limit: int = Query(100, ge=1, le=500),
    q: Optional[str] = Query(None, description="Optional name filter"),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return people the journal UI can tag.

    Implements the endpoint `journal-ui-enhancements.js` has always called
    but which previously 404ed. Response shape matches the consumer:
    `{people: [{id,name,relationship,avatar_url}], count}`.
    """
    await require_feature_access(db, user, feature="memories", action="read")
    user_id = user["user_id"]
    params: list = [user_id]
    where = "WHERE deleted = 0 AND (visibility = 'family' OR user_id = ?)"
    if q:
        where += " AND name LIKE ?"
        params.append(f"%{q}%")
    params.append(limit)
    cur = await db.execute(
        f"""SELECT id, name, relationship, visibility, user_id, preferences
            FROM people
            {where}
            ORDER BY name COLLATE NOCASE
            LIMIT ?""",
        params,
    )
    rows = await cur.fetchall()
    people = []
    for r in rows:
        avatar = None
        try:
            pref = json.loads(r["preferences"]) if r["preferences"] else None
            if isinstance(pref, dict):
                avatar = pref.get("avatar_url")
        except (json.JSONDecodeError, TypeError):
            pass
        people.append({
            "id": r["id"],
            "name": r["name"],
            "relationship": r["relationship"],
            "avatar_url": avatar,
            "visibility": r["visibility"],
        })
    return {"people": people, "count": len(people)}


@router.get("/export")
async def export_user_memories(
    user_id: Optional[str] = Query(
        None,
        description="User to export. Defaults to the caller. Admins may specify any user.",
    ),
    admin: dict = Depends(require_admin),
):
    """Full MemPalace dump for a user. Admin-only."""
    target = user_id or admin["user_id"]
    try:
        payload = await _svc().export_user(target)
    except MemoryServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return payload


@router.post("/users/{target_user}/forget")
async def forget_user(
    target_user: str,
    admin: dict = Depends(require_admin),
):
    """Right-to-be-forgotten: delete all MemPalace rows for a user.

    Audited to `mempalace_audit`. Idempotent — a second call returns
    `{removed: 0}`.
    """
    try:
        removed = await _svc().delete_user(target_user, actor=admin["user_id"])
    except MemoryServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"user_id": target_user, "removed": removed}


@router.post("/link-preview")
async def link_preview(
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Best-effort title/content preview by substring match over notes.

    Still pulls from the `notes` table because notes haven't migrated yet;
    it's a read-only convenience endpoint used by the journal UI when the
    user types a URL or keyword.
    """
    await require_feature_access(db, user, feature="memories", action="read")
    query = (payload or {}).get("query") or (payload or {}).get("url") or ""
    if not query:
        return {"preview": [], "count": 0}
    pattern = f"%{query}%"
    cur = await db.execute(
        """SELECT id, title, content, category, updated_at
           FROM notes
           WHERE deleted = 0 AND (visibility = 'family' OR user_id = ?)
             AND (title LIKE ? OR content LIKE ?)
           ORDER BY updated_at DESC
           LIMIT 10""",
        (user["user_id"], pattern, pattern),
    )
    rows = await cur.fetchall()
    return {
        "preview": [dict(r) for r in rows],
        "count": len(rows),
    }


# ─── Opt-out preference ─────────────────────────────────────────────────


@router.get("/opt-out")
async def get_memory_opt_out(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return the caller's memory opt-out flag. Default False."""
    await require_feature_access(db, user, feature="memories", action="read")
    from user_prefs import is_memory_opted_out
    flag = await is_memory_opted_out(user["user_id"])
    return {"user_id": user["user_id"], "memory_opt_out": flag}


@router.put("/opt-out")
async def set_memory_opt_out(
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Toggle the caller's memory opt-out flag.

    When set, the post-turn extractor silently drops new chat-derived
    memories for this user (PII scrubber / idempotency logic stays intact
    for explicit ingest paths so the right-to-be-forgotten flow still
    works). Flipping the flag does NOT purge past memories — use
    `POST /api/users/{id}/forget` for that.
    """
    await require_feature_access(db, user, feature="memories", action="write")
    value = bool((payload or {}).get("memory_opt_out"))
    from user_prefs import KEY_MEMORY_OPT_OUT, set_pref
    await set_pref(user["user_id"], KEY_MEMORY_OPT_OUT, value)
    return {"user_id": user["user_id"], "memory_opt_out": value}
