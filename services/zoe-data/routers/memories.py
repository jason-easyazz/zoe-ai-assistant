"""Semantic-first memories API with review workflow."""

import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_db
from memory_gateway import MemoryGateway
from models import MemoryProposalCreate, MemoryReviewBody

router = APIRouter(prefix="/api/memories", tags=["memories"])
gateway = MemoryGateway()


def _visibility_filter_sql() -> str:
    return "(visibility = 'family' OR user_id = ?)"


def _row_to_memory(row) -> dict:
    provenance = row["provenance_json"]
    if provenance and isinstance(provenance, str):
        try:
            provenance = json.loads(provenance)
        except json.JSONDecodeError:
            provenance = None
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "memory_type": row["memory_type"],
        "title": row["title"],
        "content": row["content"],
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "confidence": row["confidence"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "source_excerpt": row["source_excerpt"],
        "provenance": provenance,
        "visibility": row["visibility"],
        "status": row["status"],
        "observed_at": row["observed_at"],
        "last_verified_at": row["last_verified_at"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
        "review_note": row["review_note"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def create_memory_item(
    db,
    user_id: str,
    proposal: MemoryProposalCreate,
    default_status: str = "pending_review",
) -> dict:
    memory_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO memory_items
           (id, user_id, memory_type, title, content, entity_type, entity_id, confidence,
            source_type, source_id, source_excerpt, provenance_json, visibility, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            memory_id,
            user_id,
            proposal.memory_type,
            proposal.title,
            proposal.content,
            proposal.entity_type,
            proposal.entity_id,
            proposal.confidence,
            proposal.source_type,
            proposal.source_id,
            proposal.source_excerpt,
            json.dumps(proposal.provenance) if proposal.provenance else None,
            proposal.visibility,
            default_status,
        ),
    )
    await db.execute(
        """INSERT INTO memory_audit (id, memory_id, user_id, action, new_value, reason)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            memory_id,
            user_id,
            "create",
            json.dumps(proposal.model_dump()),
            "proposal_created",
        ),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM memory_items WHERE id = ?", (memory_id,))
    row = await cur.fetchone()
    return _row_to_memory(row)


@router.get("/")
async def list_memories(
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    user_id = user["user_id"]
    where = ["deleted = 0", _visibility_filter_sql()]
    params = [user_id]
    if status:
        where.append("status = ?")
        params.append(status)
    sql = f"""SELECT * FROM memory_items
              WHERE {' AND '.join(where)}
              ORDER BY observed_at DESC
              LIMIT ? OFFSET ?"""
    params.extend([limit, offset])
    cur = await db.execute(sql, params)
    rows = await cur.fetchall()
    items = [_row_to_memory(r) for r in rows]
    return {"memories": items, "count": len(items)}


@router.post("/proposals")
async def create_memory_proposal(
    body: MemoryProposalCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    status = "approved" if body.confidence >= 0.9 and body.memory_type == "preference" else "pending_review"
    memory = await create_memory_item(db, user["user_id"], body, default_status=status)
    if memory["status"] == "approved":
        await gateway.ingest_memory(
            {
                "title": memory.get("title"),
                "content": memory.get("content"),
                "metadata": {
                    "memory_id": memory["id"],
                    "entity_type": memory.get("entity_type"),
                    "entity_id": memory.get("entity_id"),
                    "source_type": memory.get("source_type"),
                    "confidence": memory.get("confidence"),
                },
                "tags": ["zoe-memory", memory.get("memory_type", "fact")],
            }
        )
    return memory


@router.get("/review")
async def list_review_queue(
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    cur = await db.execute(
        """SELECT * FROM memory_items
           WHERE deleted = 0 AND status = 'pending_review'
             AND (visibility = 'family' OR user_id = ?)
           ORDER BY observed_at DESC
           LIMIT ?""",
        (user["user_id"], limit),
    )
    rows = await cur.fetchall()
    items = [_row_to_memory(r) for r in rows]
    return {"items": items, "count": len(items)}


@router.post("/{memory_id}/review")
async def review_memory(
    memory_id: str,
    body: MemoryReviewBody,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    action = body.action.lower().strip()
    if action not in {"approve", "reject", "edit"}:
        raise HTTPException(status_code=400, detail="action must be approve|reject|edit")
    cur = await db.execute(
        "SELECT * FROM memory_items WHERE id = ? AND deleted = 0",
        (memory_id,),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Memory not found")
    old_content = row["content"]
    new_status = "approved" if action in {"approve", "edit"} else "rejected"
    new_content = body.content if action == "edit" and body.content else old_content
    await db.execute(
        """UPDATE memory_items
           SET status = ?, content = ?, review_note = ?, reviewed_by = ?, reviewed_at = datetime('now'),
               last_verified_at = CASE WHEN ? = 'approved' THEN datetime('now') ELSE last_verified_at END,
               updated_at = datetime('now')
           WHERE id = ?""",
        (new_status, new_content, body.note, user["user_id"], new_status, memory_id),
    )
    await db.execute(
        """INSERT INTO memory_audit (id, memory_id, user_id, action, old_value, new_value, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            memory_id,
            user["user_id"],
            action,
            old_content,
            new_content,
            body.note or "review_action",
        ),
    )
    await db.commit()
    if new_status == "approved":
        await gateway.ingest_memory(
            {
                "title": row["title"],
                "content": new_content,
                "metadata": {
                    "memory_id": memory_id,
                    "entity_type": row["entity_type"],
                    "entity_id": row["entity_id"],
                    "source_type": row["source_type"],
                    "confidence": row["confidence"],
                },
                "tags": ["zoe-memory", row["memory_type"] or "fact"],
            }
        )
    cur = await db.execute("SELECT * FROM memory_items WHERE id = ?", (memory_id,))
    updated = await cur.fetchone()
    return _row_to_memory(updated)


@router.get("/search")
async def search_memories(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    user_id = user["user_id"]
    pattern = f"%{q}%"
    cur = await db.execute(
        """SELECT * FROM memory_items
           WHERE deleted = 0 AND status = 'approved'
             AND (visibility = 'family' OR user_id = ?)
             AND (content LIKE ? OR title LIKE ? OR source_excerpt LIKE ?)
           ORDER BY confidence DESC, observed_at DESC
           LIMIT ?""",
        (user_id, pattern, pattern, pattern, limit),
    )
    rows = await cur.fetchall()
    lexical = [
        {
            **_row_to_memory(r),
            "source": "local",
            "score": float(r["confidence"] or 0.0),
        }
        for r in rows
    ]
    semantic = await gateway.semantic_search(q, limit=limit)
    return {"query": q, "results": lexical + semantic, "count": len(lexical) + len(semantic)}


@router.post("/link-preview")
async def link_preview(
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
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
