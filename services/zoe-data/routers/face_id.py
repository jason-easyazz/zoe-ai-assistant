"""Face-ID profile storage — the server side of on-panel face identification.

Storage + policy only, NO vision models: panels (Pi + USB webcam) detect,
liveness-check, and embed faces locally, enroll the resulting embedding here,
and pull consented profiles back down via /sync to cosine-match on-device.
Raw frames never reach this service — embeddings only, mirroring the
speaker-ID design in routers/voice_tts.py (W5) with two deliberate
differences:

- MULTIPLE rows per user (pose variety; the panel takes the best match)
  instead of one weight-averaged embedding.
- consent is REQUIRED at enroll time (400 without) — there is no
  "enrolled but unconsented" face state (W6).

Everything is gated by ZOE_FACE_ID_ENABLED (default off, read per call) so
the surface doesn't exist until the operator turns the feature on.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from biometric_scope import (
    authorize_profile_access,
    require_person_scope,
    resolve_enroll_target,
)
from routers.voice_tts import _require_voice_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/face", tags=["face-id"])

# Keep at most this many enrolled poses per user; enrolling past the cap
# drops the oldest row so a user can refresh their gallery without deletes.
_MAX_PROFILES_PER_USER = 8

# Embedding size guard: buffalo_sc's recognizer is 512-dim; accept a small
# family of plausible dims so a model upgrade doesn't need a schema change.
_ALLOWED_DIMS = {128, 256, 512}


def _enabled() -> bool:
    return os.environ.get("ZOE_FACE_ID_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def _face_id_threshold() -> float:
    """ArcFace-space cosine acceptance threshold, read per call."""
    try:
        return float(os.environ.get("ZOE_FACE_ID_THRESHOLD", "0.45"))
    except ValueError:
        return 0.45


def _require_enabled() -> None:
    if not _enabled():
        raise HTTPException(status_code=503, detail="face id is disabled (ZOE_FACE_ID_ENABLED)")


@router.post("/enroll")
async def face_enroll(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """Store one face-embedding pose for a user.

    Request: { "embedding_base64": ..., "user_id": ..., "display_name": ...,
               "model_name": ..., "dim": 512, "panel_id": ..., "consent": true }
    """
    _require_enabled()
    import uuid as _uuid
    from db_compat import get_compat_db as _get_compat_db

    payload = payload or {}
    if not bool(payload.get("consent")):
        raise HTTPException(status_code=400, detail="explicit consent is required to enroll a face profile")

    # A session caller may only enrol ITSELF (admins excepted): accepting the
    # payload's user_id verbatim let any household member enrol their own face
    # under someone else's id — identity takeover, not just a bad row.
    user_id = str(resolve_enroll_target(payload.get("user_id"), caller) or "").strip()
    if not user_id or user_id in ("voice-daemon", "guest"):
        raise HTTPException(status_code=400, detail="a real user_id is required")
    display_name = str(payload.get("display_name") or user_id).strip() or user_id
    model_name = str(payload.get("model_name") or "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="model_name is required")
    panel_id = str(payload.get("panel_id") or caller.get("panel_id") or "") or None

    try:
        dim = int(payload.get("dim"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="dim is required")
    if dim not in _ALLOWED_DIMS:
        raise HTTPException(status_code=400, detail=f"unsupported embedding dim {dim}")

    emb_b64 = str(payload.get("embedding_base64") or "").strip()
    if not emb_b64:
        raise HTTPException(status_code=400, detail="embedding_base64 is required")
    try:
        emb = base64.b64decode(emb_b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 embedding") from exc
    if len(emb) != dim * 4:
        raise HTTPException(status_code=400, detail=f"embedding length {len(emb)} != dim {dim} * 4 bytes")

    profile_id = str(_uuid.uuid4())
    try:
        async with _get_compat_db() as db:
            await db.execute(
                """INSERT INTO face_profiles (id, user_id, display_name, embedding_blob,
                       model_name, dim, consent_at, panel_id)
                   VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)""",
                (profile_id, user_id, display_name, emb, model_name, dim, panel_id),
            )
            # Cap the per-user gallery: drop oldest rows past the limit.
            async with db.execute(
                "SELECT id FROM face_profiles WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
            for (old_id,) in rows[_MAX_PROFILES_PER_USER:]:
                await db.execute("DELETE FROM face_profiles WHERE id=?", (old_id,))
            await db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("face/enroll DB error for user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to store face profile") from exc

    return {"ok": True, "profile_id": profile_id, "user_id": user_id, "display_name": display_name}


@router.get("/profiles/sync")
async def face_profiles_sync(caller: dict = Depends(_require_voice_auth)):
    """Panel-facing feed of enrolled face embeddings for ON-DEVICE matching.

    Device-token callers only — this hands out biometric embeddings and must
    never be reachable from a browser session.
    """
    _require_enabled()
    if caller.get("source") != "device":
        raise HTTPException(status_code=403, detail="device token required")
    from db_compat import get_compat_db as _get_compat_db

    try:
        async with _get_compat_db() as db:
            async with db.execute(
                "SELECT user_id, display_name, embedding_blob, model_name, dim FROM face_profiles "
                "WHERE active=1 ORDER BY user_id"
            ) as cur:
                rows = await cur.fetchall()
    except Exception as exc:
        logger.error("face/profiles/sync DB error: %s", exc)
        raise HTTPException(status_code=500, detail="DB error") from exc

    return {
        "ok": True,
        "threshold": _face_id_threshold(),
        "profiles": [
            {
                "user_id": r[0],
                "display_name": r[1],
                "embedding_base64": base64.b64encode(bytes(r[2])).decode("ascii"),
                "model_name": r[3],
                "dim": r[4],
            }
            for r in rows
        ],
    }


@router.get("/profiles")
async def face_profiles(caller: dict = Depends(_require_voice_auth)):
    """List the caller's own enrolled face profiles (metadata only — never embeddings).

    Household-wide only for an admin: who is enrolled is itself biometric
    metadata, so the scope filter is in the SQL, not a post-filter.
    """
    _require_enabled()
    from db_compat import get_compat_db as _get_compat_db

    caller_id, is_admin = require_person_scope(caller)
    if is_admin:
        sql = (
            "SELECT id, user_id, display_name, model_name, dim, panel_id, created_at "
            "FROM face_profiles WHERE active=1 ORDER BY display_name, created_at"
        )
        params: tuple = ()
    else:
        sql = (
            "SELECT id, user_id, display_name, model_name, dim, panel_id, created_at "
            "FROM face_profiles WHERE active=1 AND user_id=? ORDER BY display_name, created_at"
        )
        params = (caller_id,)

    try:
        async with _get_compat_db() as db:
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return {
            "ok": True,
            "profiles": [
                {"id": r[0], "user_id": r[1], "display_name": r[2], "model_name": r[3],
                 "dim": r[4], "panel_id": r[5], "created_at": str(r[6]) if r[6] is not None else None}
                for r in rows
            ],
        }
    except Exception as exc:
        logger.error("face/profiles DB error: %s", exc)
        raise HTTPException(status_code=500, detail="DB error") from exc


@router.delete("/profiles/{profile_id}")
async def face_profile_delete(profile_id: str, caller: dict = Depends(_require_voice_auth)):
    """Delete one enrolled face pose — the caller's own, or any row for an admin.

    Ownership is checked against the row BEFORE the delete. `_require_voice_auth`
    only proves the caller may reach the voice surface, so an unscoped
    `DELETE ... WHERE id=?` let any signed-in household member wipe anyone
    else's faceprint.
    """
    _require_enabled()
    from db_compat import get_compat_db as _get_compat_db

    caller_id, is_admin = require_person_scope(caller)
    try:
        async with _get_compat_db() as db:
            async with db.execute(
                "SELECT user_id FROM face_profiles WHERE id=?", (profile_id,)
            ) as cur:
                row = await cur.fetchone()
            authorize_profile_access(
                row[0] if row else None, caller_id, is_admin, kind="face"
            )
            await db.execute("DELETE FROM face_profiles WHERE id=?", (profile_id,))
            await db.commit()
        return {"ok": True, "deleted": profile_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("face/profiles delete DB error for %s: %s", profile_id, exc)
        raise HTTPException(status_code=500, detail="DB error") from exc
