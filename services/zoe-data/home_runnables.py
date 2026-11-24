"""What a scene or a script DOES, so ``home_opening`` can tell who may run it (owner's rule 2026-11-23; no flag).

A ``scene.*`` or ``script.*`` is read from Home Assistant through the bridge's read-only ``GET /definitions/<entity_id>``
(entity ids, service names and target states only). The verdict for one is:

  * ``safe``       - nothing it does touches a lock, an alarm panel, a cover or a valve: anyone who may control the house runs it;
  * ``opens``      - it unlocks, opens or disarms one (any service or target state that is not lock / close / stop / arm);
  * ``unreadable`` - Zoe cannot tell: the read failed, timed out or was not found (a bridge without the endpoint answers 404),
    the body has a shape this reader does not know, a target or a service is templated, it calls another script, scene or
    automation, it targets a group, or it calls a service that is not a (domain, service) PAIR this reader KNOWS to change
    only the named entity's own state (``home_opening.OWN_STATE_SERVICES``): a shell command, MQTT, an integration's own
    service, a call that names no entity at all, ``remote.send_command`` (it can transmit the code that opens a garage),
    ``button.press``, ``media_player.play_media``, ``notify.*``, and any service this list has never heard of on a domain it
    knows. A scene member of a domain that is not on the list is unreadable too.

The pairs are the SAME list the direct command rule in ``home_opening`` uses (``unlisted``): a listed call on a named entity
is ``safe`` here because the same caller may send it directly, and nothing unlisted is safe on either side.

``opens`` and ``unreadable`` are both ADULT-ONLY. An ``automation.*`` is always ``unreadable``: no reader is built for one.

Verdicts are cached per entity id in process memory (an id and a verdict: nothing about a person) for ``TTL_S``; an
``unreadable`` one only for ``UNREADABLE_TTL_S`` so a bridge that comes back is noticed quickly. Home Assistant gives Zoe no
change notice today (a websocket subscription to its reload events is the proper trigger and is not built), so an edited scene
is re-read at most ``TTL_S`` later.
"""
from __future__ import annotations

import time
from typing import Any, Mapping

import httpx

from typed_env import env_str
from home_opening import CLOSING as CLOSING_SERVICES, OWN_STATE_SERVICES, RUNNABLE_DOMAINS, SECURE_DOMAINS

SAFE, OPENS, UNREADABLE = "safe", "opens", "unreadable"
#: the services that run one as it is defined. Any other service on one (apply, create, reload ...) is adult-only unread.
RUN_SERVICES = frozenset({"turn_on", "turn_off", "toggle"})
CLOSED_STATES = frozenset({"locked", "locking", "closed", "closing"})      # and every ``armed_*`` state of an alarm panel
#: the generic services that act on the named entities in their own domain; every other ``homeassistant.*`` is unreadable
GENERIC_SERVICES = frozenset({"turn_on", "turn_off", "toggle", "update_entity"})
#: the only services that reach no entity, so a call to one may name none. ``notify`` is NOT here: a notify target can be a
#: shell command, MQTT, or a phone that acts on command messages, and this reader cannot see which.
NO_ENTITY_SERVICES: Mapping[str, frozenset] = {
    "tts": frozenset({"speak", "clear_cache"}),
    "persistent_notification": frozenset({"create", "dismiss", "dismiss_all", "mark_read"})}
#: a scene MEMBER is put into a state by its domain's own services. For these domains only the listed states are known to
#: carry nothing more (a remote restored to ``on`` may start an activity: a stored command sequence).
SCENE_MEMBER_STATES: Mapping[str, frozenset] = {"remote": frozenset({"off"})}
TTL_S, UNREADABLE_TTL_S, READ_TIMEOUT_S, MAX_ENTRIES = 120.0, 20.0, 3.0, 512

_CACHE: dict[str, tuple[str, float]] = {}
_clock = time.monotonic


async def _read(entity_id: str) -> Any:
    """The bridge's body for this entity, or None for anything but a 200. Raises on a timeout or no bridge."""
    base = env_str("ZOE_HA_BRIDGE_URL", "http://127.0.0.1:8007").rstrip("/")
    async with httpx.AsyncClient(timeout=READ_TIMEOUT_S) as client:
        r = await client.get(f"{base}/definitions/{entity_id}")
        return r.json() if r.status_code == 200 else None


def _reads_as_its_targets(service_domain: str, name: str, entity_domain: str) -> bool:
    """Is this script call one whose whole effect is on the entity it names? Only then can its target decide the verdict.
    Decided per (domain, service) PAIR (``home_opening.OWN_STATE_SERVICES``): a script's data is not seen here, so a service
    that CAN carry a command, a payload or an address (``remote.send_command``, ``media_player.play_media``, ``button.press``)
    is unreadable whatever it carries, and so is a service this list has never heard of on a domain it knows."""
    if service_domain in NO_ENTITY_SERVICES:
        return name in NO_ENTITY_SERVICES[service_domain]
    if not entity_domain:
        return False      # no entity named: an area in a field this never saw, or all of them
    if entity_domain in SECURE_DOMAINS and service_domain in (entity_domain, "homeassistant"):
        return service_domain != "homeassistant" or name in GENERIC_SERVICES      # read: closing or opening is decided below
    if service_domain == "homeassistant":      # the generic call runs the ENTITY's domain service: that pair must be listed
        own = OWN_STATE_SERVICES.get(entity_domain)
        return own is not None and (name == "update_entity" or (name in GENERIC_SERVICES and name in own))
    return service_domain == entity_domain and name in OWN_STATE_SERVICES.get(service_domain, ())


def _member_is_read(entity_domain: str, state: str) -> bool:
    """A scene member with no service: is setting it to this state known to change only that entity?"""
    if entity_domain in SECURE_DOMAINS:
        return True
    return entity_domain in OWN_STATE_SERVICES and state in SCENE_MEMBER_STATES.get(entity_domain, (state,))


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
        if {entity_domain, service_domain} & (RUNNABLE_DOMAINS | {"group"}):
            return UNREADABLE
        if not (_reads_as_its_targets(service_domain, name, entity_domain) if service else _member_is_read(entity_domain, state)):
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
