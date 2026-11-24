"""Unlocking or opening the house needs an ADULT (security fix, owner's rule; on by default, no flag).

A command that could OPEN the house - a lock or a cover (garage doors and blinds are covers), and any service on one but
closing, locking or arming it - is sent to Home Assistant only for:

  * a household member who is POSITIVELY an adult by their account record (``manner_block.is_adult``: a missing account, an
    unknown role, a child, a guest and a failed lookup are all NOT an adult), or
  * the panel while a VERIFIED VOICE SESSION is open on it (``voice_session.active_member``), and that member is an adult.
    WHICH panel a caller is comes from ``panel_principal.panel_of`` (device token, kiosk credential or registered address).

A COMMAND THAT REACHES ENTITIES IT DOES NOT NAME is adult-only too (``reaches_unnamed``): the bridge passes a command's data
to Home Assistant as it is, so an area, a device, a label or a floor in it, an id that is not one plain ``domain.object_id``
(a comma list, ``all``, a template) and the generic ``homeassistant`` / ``group`` domains could all reach a lock unseen.

A COMMAND THAT IS NOT ON THE LIST OF (DOMAIN, SERVICE) PAIRS IS ADULT-ONLY (``unlisted``; 2026-11-24). Away from the four
secure domains a command is for everyone only when its service is KNOWN to change nothing but the named entity's own state
(``OWN_STATE_SERVICES``). A service that transmits something (``remote.send_command`` can send the code that opens a garage or
a gate), presses whatever a button is wired to, makes a device fetch an address (``media_player.play_media`` with a URL), a
service or a domain this list has never heard of, and any data carrying a ``command`` / ``payload`` / ``url`` / ``activity`` /
``extra`` are all adult-only. WHERE THE LINE IS: Zoe judges by what Home Assistant says a thing IS. A plain ``switch`` or an
``input_boolean`` that the household has wired to an opener (directly, or through an automation that watches it) looks like any
other switch and is NOT caught: expose an opener to Home Assistant as a cover or a lock, never as a switch.

RUNNING A SCENE, A SCRIPT OR AN AUTOMATION is the same question (``home_runnables``): one that unlocks or opens anything, one
Zoe cannot read, and every automation run by hand, is adult-only; one that touches nothing of the kind is for anyone.

The shared panel with nobody identified, and a guest, are refused. Lock, close and arm stay open to everyone who may control
the house today. Every member-driven sender asks ``refused(...)`` right before it sends; a refusal sends nothing.

The Zoe OS kernel's home door (``ZOE_OS_HOME=kernel``) decides the same classes when it is on and replaces this check there.
"""
from __future__ import annotations

import contextvars
import logging
import re
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

REFUSAL = "Unlocking or opening that needs a grown-up I can recognise, so I haven't done it."
#: Home Assistant domains whose things open the house: locks and alarm panels; covers (garage doors, blinds) and valves
SECURE_DOMAINS = frozenset({"lock", "alarm_control_panel", "cover", "valve"})
#: the only services on those domains that do NOT open anything. Every other service - unlock, open, disarm, toggle, a set
#: position, a generic turn_on / turn_off, one this list has never heard of - counts as opening.
CLOSING = frozenset({"lock", "close", "close_cover", "close_cover_tilt", "close_valve", "stop_cover", "stop_cover_tilt",
                     "alarm_arm_away", "alarm_arm_home", "alarm_arm_night", "alarm_arm_vacation", "alarm_arm_custom_bypass"})
#: entities that RUN other things: what one does is read from Home Assistant (home_runnables.py)
RUNNABLE_DOMAINS = frozenset({"scene", "script", "automation"})
#: a command aimed at anything but plain entity ids reaches entities this check cannot name, so it is adult-only: an area, a
#: device, a label or a floor in its data, an id that is not one ``domain.object_id`` (a comma list, ``all``, a template), and
#: the generic ``homeassistant`` / ``group`` domains (their services act on whatever the data names or the group holds)
SELECTOR_KEYS = frozenset({"area_id", "device_id", "label_id", "floor_id"})
GENERIC_DOMAINS = frozenset({"homeassistant", "group"})
#: the ONLY (domain, service) PAIRS, away from the secure domains, that are for everyone: each changes the named entity's own
#: state and nothing else, and takes no command, payload, code or address. A domain that is not a key, and a service that is not
#: in its set, is adult-only: a new integration and a new service start closed. Deliberately absent:
#:   remote.turn_on / toggle (they take an ``activity``: a stored command sequence), remote.send_command / learn_command /
#:   delete_command; media_player.play_media (a URL the player fetches; see ``_plays_from_the_library`` for the one direct
#:   exception), media_player.join (reaches players it names in data); vacuum.send_command; camera.snapshot / record /
#:   play_stream (a path, another player); ``button`` and ``input_button`` whole (pressing one triggers whatever it is wired
#:   to: that is all a button is); ``text`` (free text sent to a device); ``notify`` whole (a notify target can be a shell
#:   command, MQTT, or a phone that acts on command messages); shell_command, rest_command, mqtt, every integration's own domain.
_ON_OFF = frozenset({"turn_on", "turn_off", "toggle"})
OWN_STATE_SERVICES: Mapping[str, frozenset] = {
    "light": _ON_OFF, "switch": _ON_OFF, "siren": _ON_OFF, "input_boolean": _ON_OFF,
    "fan": _ON_OFF | {"set_percentage", "set_preset_mode", "set_direction", "oscillate", "increase_speed", "decrease_speed"},
    "media_player": _ON_OFF | {"volume_up", "volume_down", "volume_set", "volume_mute", "media_play", "media_pause",
                               "media_play_pause", "media_stop", "media_next_track", "media_previous_track", "media_seek",
                               "select_source", "select_sound_mode", "shuffle_set", "repeat_set", "clear_playlist", "unjoin"},
    "climate": _ON_OFF | {"set_temperature", "set_hvac_mode", "set_preset_mode", "set_fan_mode", "set_humidity", "set_swing_mode",
                          "set_swing_horizontal_mode"},
    "humidifier": _ON_OFF | {"set_humidity", "set_mode"},
    "water_heater": frozenset({"turn_on", "turn_off", "set_temperature", "set_operation_mode", "set_away_mode"}),
    "vacuum": frozenset({"start", "pause", "stop", "return_to_base", "locate", "clean_spot", "set_fan_speed"}),
    "lawn_mower": frozenset({"start_mowing", "pause", "dock"}),
    "camera": frozenset({"turn_on", "turn_off", "enable_motion_detection", "disable_motion_detection"}),
    "remote": frozenset({"turn_off"}),
    "number": frozenset({"set_value"}), "input_number": frozenset({"set_value", "increment", "decrement"}),
    "select": frozenset({"select_option", "select_first", "select_last", "select_next", "select_previous"}),
    "input_select": frozenset({"select_option", "select_first", "select_last", "select_next", "select_previous"}),
    "input_text": frozenset({"set_value"}), "input_datetime": frozenset({"set_datetime"}),
    "timer": frozenset({"start", "pause", "cancel", "finish", "change"}),
    "counter": frozenset({"increment", "decrement", "reset", "set_value"}),
}
#: the bridge's own action names (``/devices/control`` turns each into ``light.turn_on`` with that one field)
BRIDGE_ACTIONS: Mapping[str, frozenset] = {"light": frozenset({"set_brightness", "set_color"})}
#: data keys that carry something to transmit, run or fetch: adult-only wherever they sit in a command's data
PAYLOAD_KEYS = frozenset({"command", "payload", "url", "activity", "extra"})
#: the only address schemes a directly sent ``play_media`` may name: Home Assistant resolves these itself, nothing is fetched
LIBRARY_SCHEMES = ("media-source://", "library://")
_PLAIN_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
#: the caller of THIS turn (chat, voice, the brain's dispatch), noted where the turn's home-control verdict is recorded
_TURN: contextvars.ContextVar[Optional[Mapping[str, Any]]] = contextvars.ContextVar("zoe_home_opening_turn", default=None)


def note_turn(user: Optional[Mapping[str, Any]], request: Any = None) -> None:
    """Note this turn's caller and the request it came in on. Nothing is looked up here: WHICH panel the turn is from is
    resolved (``panel_principal.panel_of``) only if the turn asks to open something, and then once."""
    _TURN.set({"user": dict(user or {}), "request": request})


def _ids(entity_id: Any, data: Any) -> list:
    ids = [entity_id]
    extra = (data or {}).get("entity_id") if isinstance(data, Mapping) else None      # a service's own target overrides the path's
    ids += list(extra) if isinstance(extra, (list, tuple, set)) else [extra]
    return [str(i).strip().lower() for i in ids if i]


def _domains(entity_id: Any, data: Any) -> set:
    return {i.partition(".")[0].strip() for i in _ids(entity_id, data)}


def _names_a_selector(value: Any, depth: int = 0) -> bool:
    if isinstance(value, Mapping):
        return depth > 8 or any(str(k).strip().lower() in SELECTOR_KEYS or _names_a_selector(v, depth + 1) for k, v in value.items())
    return isinstance(value, (list, tuple, set)) and (depth > 8 or any(_names_a_selector(v, depth + 1) for v in value))


def reaches_unnamed(entity_id: Any, data: Any = None) -> bool:
    """Could this command reach entities it does not name one by one? (See ``SELECTOR_KEYS`` above.)"""
    ids = _ids(entity_id, data)
    if not ids:
        return True      # no id at all (empty, blank, missing): it names nothing, so what it would reach is not known
    return (_names_a_selector(data) or not all(_PLAIN_ID.match(i) for i in ids)
            or bool({i.partition(".")[0] for i in ids} & GENERIC_DOMAINS))


def _names_a_key(value: Any, keys: frozenset, depth: int = 0) -> bool:
    if isinstance(value, Mapping):
        return depth > 8 or any(str(k).strip().lower() in keys or _names_a_key(v, keys, depth + 1) for k, v in value.items())
    return isinstance(value, (list, tuple, set)) and (depth > 8 or any(_names_a_key(v, keys, depth + 1) for v in value))


def _plays_from_the_library(data: Any) -> bool:
    """A directly sent ``play_media`` whose content id is words or a library id, never an address a device would fetch (a
    player told to play ``http://<a relay on the LAN>/open`` requests it). Here the data is in hand; in a script it is not,
    so a script's ``play_media`` is unreadable."""
    cid = data.get("media_content_id") if isinstance(data, Mapping) else None
    return isinstance(cid, str) and ("//" not in cid or cid.strip().lower().startswith(LIBRARY_SCHEMES))


def unlisted(entity_id: Any, action: Any, data: Any = None) -> bool:
    """Is this command anything but a listed (domain, service) pair on each entity it names, or does its data carry a
    payload? The secure, runnable and generic domains are decided by their own rules above and below."""
    name = str(action or "").strip().lower()
    if _names_a_key(data, PAYLOAD_KEYS):
        return True
    for domain in _domains(entity_id, data) - SECURE_DOMAINS - RUNNABLE_DOMAINS - GENERIC_DOMAINS:
        if name in OWN_STATE_SERVICES.get(domain, ()) or name in BRIDGE_ACTIONS.get(domain, ()):
            continue
        if not (domain == "media_player" and name == "play_media" and _plays_from_the_library(data)):
            return True
    return False


async def _runs_adult_only(runnable: list, action: Any) -> bool:
    """Would this run a scene, script or automation that opens something or cannot be read? One read per entity, cached,
    and none after the first adult-only verdict."""
    import home_runnables

    if str(action or "").strip().lower() not in home_runnables.RUN_SERVICES:
        return True
    for i in runnable:
        if await home_runnables.verdict(i) != home_runnables.SAFE:
            return True
    return False


def opens(entity_id: Any, action: Any, data: Any = None) -> bool:
    """Could this command open the house? A secure domain, and any service but one that closes, locks or arms."""
    return bool(_domains(entity_id, data) & SECURE_DOMAINS) and str(action or "").strip().lower() not in CLOSING


async def _adult(user_id: Any) -> bool:
    try:
        import manner_block

        return bool(user_id) and bool(await manner_block.is_adult(str(user_id), quiet=True))     # quiet: no id in the log
    except Exception:  # noqa: BLE001 - not knowing is not an adult
        return False


async def may_open(user: Optional[Mapping[str, Any]], request: Any = None, turn: Optional[dict] = None) -> bool:
    """Is this caller an adult member, or a panel whose open verified voice session belongs to one? Never raises.
    With a ``request`` the panel is whatever ``panel_principal.panel_of`` proves it to be (kept on ``turn``, so a turn resolves
    it once); without one, the record's own ``panel_id``."""
    u = user or {}
    if u.get("unauthenticated_override"):      # a header-less caller promoted by a legacy setting is nobody, whatever its id
        u = {"user_id": "guest", "role": "guest", "panel_id": u.get("panel_id")}
    elif await _adult(u.get("user_id")):
        return True
    try:
        import panel_principal
        import voice_session

        if turn is not None and "panel" in turn:
            panel = turn["panel"]
        elif request is not None:
            panel = await panel_principal.panel_of(request, u)
            if turn is not None:
                turn["panel"] = panel
        else:
            panel = str(u.get("panel_id") or "").strip()
        if not panel:
            return False
        return await _adult(voice_session.active_member(panel))
    except Exception:  # noqa: BLE001 - no session to read is no session
        return False


async def refused(entity_id: Any, action: Any, data: Any = None, *, user: Any = "turn", request: Any = None,
                  via: str = "") -> bool:
    """True when this command would open the house (or run a scene / script / automation that does, or that Zoe cannot read,
    or is not a listed own-state service) and its caller may not. ``user``: the caller's record (with its
    ``request``, which says which panel it is), or (default) the caller of this turn. One log line, closed codes only: the lane, the domain and the service class, never a name or an id."""
    runnable = [i for i in _ids(entity_id, data) if i.partition(".")[0] in RUNNABLE_DOMAINS]
    not_listed = unlisted(entity_id, action, data)
    adult_only = opens(entity_id, action, data) or reaches_unnamed(entity_id, data) or not_listed
    if not runnable and not adult_only:
        return False      # an ordinary command: nothing is read, nobody is looked up
    turn = _TURN.get() if user == "turn" else None
    if user == "turn":
        user, request = (turn or {}).get("user"), (turn or {}).get("request")
    if await may_open(user, request, turn):
        return False
    if not adult_only and not await _runs_adult_only(runnable, action):
        return False      # a scene or script that touches no lock, alarm, cover or valve: anyone who may control the house
    logger.warning("HOME_OPENING refused via=%s domain=%s", via or "-",
                   ",".join(sorted(_domains(entity_id, data) & (SECURE_DOMAINS | RUNNABLE_DOMAINS))) or ("unlisted_service" if not_listed else "unnamed_target"))
    return True
