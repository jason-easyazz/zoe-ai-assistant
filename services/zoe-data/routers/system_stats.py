"""``GET /api/system/stats`` - the desktop dashboard's System card (Wave 4b, 2026-10-19).

Read-only host numbers (CPU, memory, disk, uptime) and the health of the services the household
depends on (database, brain, voice, Home Assistant). A signed-in MEMBER only
(``panel_principal.require_member``): a guest and the wall panel get 403 - how busy the box is and
which services are down is operator information, and no panel surface shows it. Logic lives in
``system_stats.py``.
"""
from fastapi import APIRouter, Depends

import system_stats
from panel_principal import require_member

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/stats")
async def get_system_stats(_user: dict = Depends(require_member)) -> dict:
    return await system_stats.snapshot()
