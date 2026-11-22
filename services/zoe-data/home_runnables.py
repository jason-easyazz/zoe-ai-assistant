"""What a scene or a script DOES, so ``home_opening`` can tell who may run it (owner's rule 2026-11-23; no flag).

A ``scene.*`` or ``script.*`` is read from Home Assistant through the bridge's read-only ``GET /definitions/<entity_id>``
(entity ids, service names and target states only). The verdict for one is:

  * ``safe``       - nothing it does touches a lock, an alarm panel, a cover or a valve: anyone who may control the house runs it;
  * ``opens``      - it unlocks, opens or disarms one (any service or target state that is not lock / close / stop / arm);
  * ``unreadable`` - Zoe cannot tell: the read failed, timed out or was not found (a bridge without the endpoint answers 404),
    the body has a shape this reader does not know, a target or a service is templated, it calls another script, scene or
    automation, it targets a group, or it calls a service that acts outside entities (a shell command, MQTT ...).

``opens`` and ``unreadable`` are both ADULT-ONLY. An ``automation.*`` is always ``unreadable``: no reader is built for one.

Verdicts are cached per entity id in process memory (an id and a verdict: nothing about a person) for ``TTL_S``; an
``unreadable`` one only for ``UNREADABLE_TTL_S`` so a bridge that comes back is noticed quickly. Home Assistant gives Zoe no
change notice today (a websocket subscription to its reload events is the proper trigger and is not built), so an edited scene
is re-read at most ``TTL_S`` later.
"""
from __future__ import annotations

import os
import time
from typing import Any, Mapping

import httpx

from home_opening import CLOSING as CLOSING_SERVICES, RUNNABLE_DOMAINS, SECURE_DOMAINS

SAFE, OPENS, UNREADABLE = "safe", "opens", "unreadable"
#: the services that run one as it is defined. Any other service on one (apply, create, reload ...) is adult-only unread.
RUN_SERVICES = frozenset({"turn_on", "turn_off", "toggle"})
CLOSED_STATES = frozenset({"locked", "locking", "closed", "closing"})      # and every ``armed_*`` state of an alarm panel
#: service domains that act outside Home Assistant's entities, so what they reach cannot be read from their targets
OPAQUE_SERVICE_DOMAINS = frozenset({"shell_command", "python_script", "pyscript", "rest_command", "mqtt", "hassio"})
TTL_S, UNREADABLE_TTL_S, READ_TIMEOUT_S, MAX_ENTRIES = 120.0, 20.0, 3.0, 512

_CACHE: dict[str, tuple[str, float]] = {}
_clock = time.monotonic


async def _read(entity_id: str) -> Any:
    """The bridge's body for this entity, or None for anything but a 200. Raises on a timeout or no bridge."""
    base = os.environ.get("ZOE_HA_BRIDGE_URL", "http://127.0.0.1:8007").rstrip("/")
    async with httpx.AsyncClient(timeout=READ_TIMEOUT_S) as client:
        r = await client.get(f"{base}/definitions/{entity_id}")
        return r.json() if r.status_code == 200 else None


def classify(body: Any) -> str:
    """The verdict for the bridge's body. Anything not exactly the known shape is ``unreadable``."""
    actions = body.get("actions") if isinstance(body, Mapping) and body.get("readable") is True else None
    if not isinstance(actions, list) or not actions:
        return UNREADABLE
    verdict = SAFE
    for a in actions:
        fields = [a.get(k) for k in ("entity_id", "service", "state")] if isinstance(a, Mapping) else [None]
        if not all(isinstance(f, str) and "{" not in f for f in fields):
            return UNREADABLE
        entity, service, state = (f.strip().lower() for f in fields)
        entity_domain, dot, _ = entity.partition(".")
        service_domain, _, name = service.rpartition(".")
        if (entity and not dot) or (service and not service_domain) or not (entity or service):
            return UNREADABLE
        if {entity_domain, service_domain} & (RUNNABLE_DOMAINS | OPAQUE_SERVICE_DOMAINS | {"group"}):
            return UNREADABLE
        if {entity_domain, service_domain} & SECURE_DOMAINS:
            closing = name in CLOSING_SERVICES if service else (state in CLOSED_STATES or state.startswith("armed_"))
            if not closing:
                verdict = OPENS
    return verdict


async def verdict(entity_id: Any) -> str:
    """``safe`` / ``opens`` / ``unreadable`` for a scene, a script or an automation entity. Never raises."""
    eid = str(entity_id or "").strip().lower()
    if eid.partition(".")[0] not in ("scene", "script"):
        return UNREADABLE      # an automation run by hand is adult-only always; anything else is not something this reads
    now = _clock()
    hit = _CACHE.get(eid)
    if hit and hit[1] > now:
        return hit[0]
    try:
        v = classify(await _read(eid))
    except Exception:  # noqa: BLE001 - a timeout, no bridge, a body that is not JSON: Zoe cannot tell
        v = UNREADABLE
    if len(_CACHE) >= MAX_ENTRIES:
        _CACHE.clear()
    _CACHE[eid] = (v, now + (UNREADABLE_TTL_S if v == UNREADABLE else TTL_S))
    return v
