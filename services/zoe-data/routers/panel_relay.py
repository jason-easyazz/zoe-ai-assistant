"""Bind a panel's relay client certificate to the panel: the registry behind ``relay_ingress`` (panel-anywhere section 6.1, R1).

  GET    /api/panels/PANEL_ID/client-certs               - list the fingerprints bound to the panel (admin)
  PUT    /api/panels/PANEL_ID/client-certs/FINGERPRINT   - bind a certificate (admin); body optional ``{"note": "..."}``
  DELETE /api/panels/PANEL_ID/client-certs/FINGERPRINT   - revoke it (admin); takes effect at once, in this process

The operator gets FINGERPRINT from ``scripts/setup/relay/household_pki.py sign-csr`` (lower-case hex SHA-1 of the certificate - what
nginx forwards as ``X-Zoe-Client-Cert-Fp``). A fingerprint is bound to ONE panel for good: it is never moved between panels and a
revoked one is never un-revoked (issue a new certificate). All three 404 while ``ZOE_RELAY_INGRESS`` is off, so a box that does not
run the relay exposes nothing new. The rule that uses the binding is ``relay_ingress.authenticate``.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

import relay_ingress
from auth import require_admin
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/panels", tags=["panel-relay"])

_FP_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _require_enabled() -> None:
    if not relay_ingress.enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def _fingerprint(raw: str) -> str:
    fp = str(raw or "").strip().lower().replace(":", "")
    if not _FP_RE.match(fp):
        raise HTTPException(status_code=422, detail="fingerprint must be 40 or 64 hex characters")
    return fp


async def _panel_exists(db, panel_id: str) -> bool:
    cur = await db.execute("SELECT 1 FROM panels WHERE panel_id = ?", (panel_id,))
    return await cur.fetchone() is not None


@router.get("/{panel_id}/client-certs")
async def list_client_certs(panel_id: str, admin: dict = Depends(require_admin), db=Depends(get_db)):
    _require_enabled()
    cur = await db.execute(
        "SELECT fingerprint, note, created_at, revoked_at FROM panel_client_certs WHERE panel_id = ? ORDER BY created_at",
        (panel_id,),
    )
    rows = await cur.fetchall()
    return {"panel_id": panel_id, "certs": [dict(r) for r in rows]}


@router.put("/{panel_id}/client-certs/{fingerprint}")
async def bind_client_cert(
    panel_id: str, fingerprint: str, payload: dict | None = None, admin: dict = Depends(require_admin), db=Depends(get_db)
):
    _require_enabled()
    fp = _fingerprint(fingerprint)
    if not await _panel_exists(db, panel_id):
        raise HTTPException(status_code=404, detail=f"Panel '{panel_id}' not found")
    cur = await db.execute("SELECT panel_id, revoked_at FROM panel_client_certs WHERE fingerprint = ?", (fp,))
    existing = await cur.fetchone()
    if existing:
        if str(existing["panel_id"]) != panel_id:
            raise HTTPException(status_code=409, detail="That certificate is already bound to another panel")
        if existing["revoked_at"]:
            raise HTTPException(status_code=409, detail="That certificate was revoked; issue a new one")
        return {"ok": True, "panel_id": panel_id, "fingerprint": fp, "already_bound": True}
    note = str((payload or {}).get("note") or "")[:200]
    await db.execute(
        "INSERT INTO panel_client_certs (fingerprint, panel_id, note, created_at) VALUES (?, ?, ?, ?)",
        (fp, panel_id, note, datetime.now(tz=timezone.utc).isoformat()),
    )
    await db.commit()
    relay_ingress.invalidate_cache()
    logger.info("panel_relay: bound relay client certificate %s... to panel %s", fp[:12], panel_id)
    return {"ok": True, "panel_id": panel_id, "fingerprint": fp}


@router.delete("/{panel_id}/client-certs/{fingerprint}")
async def revoke_client_cert(panel_id: str, fingerprint: str, admin: dict = Depends(require_admin), db=Depends(get_db)):
    _require_enabled()
    fp = _fingerprint(fingerprint)
    cur = await db.execute("SELECT revoked_at FROM panel_client_certs WHERE fingerprint = ? AND panel_id = ?", (fp, panel_id))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Certificate not bound to this panel")
    if not row["revoked_at"]:
        await db.execute(
            "UPDATE panel_client_certs SET revoked_at = ? WHERE fingerprint = ? AND panel_id = ?",
            (datetime.now(tz=timezone.utc).isoformat(), fp, panel_id),
        )
        await db.commit()
    relay_ingress.invalidate_cache()
    logger.info("panel_relay: revoked relay client certificate %s... for panel %s", fp[:12], panel_id)
    return {"ok": True, "panel_id": panel_id, "fingerprint": fp, "revoked": True}
