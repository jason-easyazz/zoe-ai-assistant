"""``GET /api/presence/observed`` - hashed keys + states of the OBSERVED HA presence rows (flag ``ZOE_HA_PRESENCE=on``).

A signed-in MEMBER only (``panel_principal.require_member``): never a guest, the panel or an anonymous caller. Flag not ``on`` is a
404 for every caller, like every other flag-dark route: the router-level gate runs before auth and the DB. Nothing here names a
person or an entity; the keys are sha256 hashes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from database import get_db
from panel_principal import require_member
from presence import ha_presence


def _on() -> None:
    if ha_presence.mode() != "on":
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(prefix="/api/presence", tags=["presence"], dependencies=[Depends(_on)])


@router.get("/observed")
async def observed(user: dict = Depends(require_member), db=Depends(get_db)):
    return {"observed": await ha_presence.observed_view(db)}
