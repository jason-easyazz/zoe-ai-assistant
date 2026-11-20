"""The ONE sender of commands to Home Assistant: every member-driven change to a light, switch, scene, cover, lock, climate
or media player in the house leaves zoe-data through ``send``.

Nothing about the house is stored in Zoe. A command is a POST to the Home Assistant bridge (``ZOE_HA_BRIDGE_URL``): the device
command path with ``{"entity_id", "action", "data"}``, or the scene path with ``{"scene_id"}``. Four entry points send one: the
spoken / Skybridge resolver (``smart_home_service``), the panel's control route (``routers/ha_control``), the brain's
``ha_control`` tool (``zoe_agent``) and music played through a house media player (``intent_router``). Each keeps its own
checks, its own timeout and its own handling of a failure: this function only sends, exactly as each of them sent it.

READS of the house (entity lists, states) are not commands and do not come through here.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

#: the bridge's two command paths (closed: ``send`` takes nothing else)
DEVICE, SCENE = "/devices/control", "/scenes/activate"
PATHS = (DEVICE, SCENE)


async def send(base_url: str, path: str, body: dict, *, timeout: Any = None, client: Optional[Any] = None):
    """POST one command to the bridge and hand back the ``httpx`` response. Raises what ``httpx`` raises (a caller that must
    never raise catches it, as it always did). ``client``: the caller's own open client (music through the house keeps one)."""
    if path not in PATHS:
        raise ValueError("not a Home Assistant command path")
    if client is not None:
        return await client.post(f"{base_url}{path}", json=body)
    async with httpx.AsyncClient(timeout=timeout) as c:
        return await c.post(f"{base_url}{path}", json=body)
