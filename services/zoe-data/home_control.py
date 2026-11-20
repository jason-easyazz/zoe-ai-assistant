"""The ONE sender of commands to Home Assistant: every member-driven change to a light, switch, scene, cover, lock, climate
or media player in the house leaves zoe-data through ``send``.

Nothing about the house is stored in Zoe. A command is a POST to the Home Assistant bridge (``ZOE_HA_BRIDGE_URL``): the device
command path with ``{"entity_id", "action", "data"}``, or the scene path with ``{"scene_id"}``. Four entry points send one: the
spoken / Skybridge resolver (``smart_home_service``), the panel's control route (``routers/ha_control``), the brain's
``ha_control`` tool (``zoe_agent``) and music played through a house media player (``intent_router``). Each keeps its own
checks, its own timeout and its own handling of a failure: this function only sends, exactly as each of them sent it.

READS of the house (entity lists, states) are not commands and do not come through here.

The Zoe OS home door (``ZOE_OS_HOME=kernel``; ``action_doors``) sits here: the one place a command leaves. Flag off: a flag read.
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
    import action_doors

    verb, entity = action_doors.home_verb(path, body)
    asked = await action_doors.before("home", verb, entity, quiet=verb in action_doors.HOME_QUIET,
                                      secure=action_doors.home_secure(verb), refused=action_doors.HomeRefused)
    try:
        if client is not None:
            r = await client.post(f"{base_url}{path}", json=body)
        else:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(f"{base_url}{path}", json=body)
    except BaseException:
        action_doors.after(asked, False)
        raise
    action_doors.after(asked, getattr(r, "status_code", 500) < 400)
    return r
