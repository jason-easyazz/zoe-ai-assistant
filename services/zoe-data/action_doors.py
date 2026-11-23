"""The Zoe OS doors for the two ACTION areas, music and home control (``ZOE_OS_MUSIC=kernel``, ``ZOE_OS_HOME=kernel``; default
off; docs/knowledge/zoe-os-music-home-door.md).

Nothing is stored for either area: an action is an outbound command, and each area has ONE sender (``music_service._ma_response``;
``home_control.send``). Each sender calls ``before`` right before its command and ``after`` once it knows how the send ended.

WHO IS ACTING is the turn's (``kernel_door.acting``): every authenticated entry point notes it (``kernel_door.note_actor``).
With nobody noted the command is AUTOMATIC (an alarm's music, the discovery jobs, a routine): not asked, not told.

THREE KINDS OF COMMAND, by the closed verb it maps to (never by a device's or a track's name):
  * QUIET   stopping or turning down music; turning a light or a media player off. MADE FIRST, TOLD AFTERWARDS: the kernel can
            neither delay nor refuse them (``os_shadow.record_door``).
  * SECURE  anything that could open the house: a lock or a cover, and any act but closing it. ALWAYS ASKED FIRST, and a
            kernel that is down or slow means NO (``HomeRefused("unavailable")``). The one place "kernel down" refuses.
  * the rest  asked first; a refusal is final; kernel down or slow = the command proceeds, logged ``not_checked``.

Only ids and closed codes cross: the verb, the acting member's id (or the panel), the speaker's or entity's id. Never a name.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import kernel_door
import os_shadow

_ID = re.compile(r"[A-Za-z0-9._:@_-]{1,80}\Z")         # what the kernel accepts as an id; anything else is not sent
TOLD = "told"                                           # ``before`` answered: made first, tell the kernel afterwards


class MusicRefused(kernel_door.Refused):
    """The kernel refused this music action. FINAL: the command was not sent."""

    area = "music"
    lines = {"unknown_person": "I don't know who is asking, so I can't change the music."}
    fallback = "I'm not allowed to change the music."


class HomeRefused(kernel_door.Refused):
    """The kernel refused this command to the house (or could not be asked about one that opens it). FINAL: nothing was sent."""

    area = "home"
    lines = {
        "adults_only": "That one needs a grown-up, so I haven't done it.",
        "unknown_person": "I don't know who is asking, so I can't control the house.",
        "unavailable": "I can't check who may open that right now, so I haven't done it.",
    }
    fallback = "I'm not allowed to do that in the house."


# ── music: Music Assistant command -> verb ──────────────────────────────────────────────────────────────────────────────────
_MUSIC_EXACT = {
    "player_queues/pause": "music.stop", "player_queues/stop": "music.stop",
    "players/cmd/volume_down": "music.quieter", "players/cmd/volume_set": "music.quieter",      # a set level: direction unknown
    "players/cmd/volume_up": "music.louder",
    "player_queues/play": "music.play", "player_queues/play_pause": "music.play", "player_queues/next": "music.play",
    "player_queues/previous": "music.play", "player_queues/play_media": "music.play", "player_queues/play_index": "music.play",
    "player_queues/seek": "music.play", "player_queues/transfer": "music.play", "player_queues/shuffle": "music.play",
    "player_queues/repeat": "music.play", "player_queues/dont_stop_the_music": "music.play",
    "players/cmd/set_members": "music.play", "players/cmd/group": "music.play", "players/cmd/ungroup": "music.play",
    "player_queues/move_item": "music.queue", "player_queues/delete_item": "music.queue", "player_queues/clear": "music.queue",
    "player_queues/save_as_playlist": "music.library", "music/favorites/add_item": "music.library",
    "music/favorites/remove_item": "music.library", "music/playlists/add_playlist_tracks": "music.library",
    "music/playlists/remove_playlist_tracks": "music.library", "music/playlists/create_playlist": "music.library",
}
MUSIC_QUIET = ("music.stop", "music.quieter")


def music_verb(command: str, args: dict) -> Optional[str]:
    """The verb of a Music Assistant command, or None when it is not an action (a read, a search, provider set-up). A command
    of the two action families that is not listed is still an action: ``music.play`` (asked first), never silently unasked."""
    if command == "players/cmd/volume_mute":
        return "music.stop" if args.get("muted") else "music.louder"
    if command == "players/cmd/power":                 # only switching a speaker OFF is a stop; on (or unsaid) is asked first
        return "music.stop" if args.get("powered") is False else "music.play"
    if command in _MUSIC_EXACT:
        return _MUSIC_EXACT[command]
    if command.startswith("players/cmd/") or (command.startswith("player_queues/") and command not in ("player_queues/all", "player_queues/items")):
        return "music.play"
    return None


# ── home: bridge command -> verb ───────────────────────────────────────────────────────────────────────────────────────────
_CLASS_OF = {"light": "light", "switch": "switch", "input_boolean": "switch", "fan": "switch", "climate": "climate",
             "water_heater": "climate", "humidifier": "climate", "cover": "cover", "valve": "cover", "lock": "lock",
             "alarm_control_panel": "lock", "scene": "scene", "script": "scene", "automation": "scene", "media_player": "media"}
_ACT_OF = {"turn_on": "on", "on": "on", "media_play": "on", "turn_off": "off", "off": "off", "media_pause": "off",
           "media_stop": "off", "volume_down": "off", "volume_mute": "off", "open_cover": "open", "open": "open", "unlock": "open",
           "open_valve": "open", "alarm_disarm": "open", "close_cover": "close", "close": "close", "lock": "close",
           "close_valve": "close", "alarm_arm_away": "close", "alarm_arm_home": "close", "alarm_arm_night": "close"}
HOME_QUIET = ("light.off", "media.off")
SECURE_CLASSES = ("lock", "cover")


def home_verb(path: str, body: dict) -> tuple[str, str]:
    """``(verb, entity id)`` of a bridge command. The class is the entity id's DOMAIN (the part before the dot), the act is the
    service asked for; anything this does not know is ``other`` / ``set`` (asked first, and ``set`` on a lock or cover is
    treated as opening it)."""
    import home_control

    if path == home_control.SCENE:
        return "scene.on", str(body.get("scene_id") or "")
    entity = str(body.get("entity_id") or "")
    cls = _CLASS_OF.get(entity.partition(".")[0], "other")
    act = _ACT_OF.get(str(body.get("action") or ""), "set")
    return f"{cls}.{act}", entity


def home_secure(verb: str) -> bool:
    cls, _, act = verb.partition(".")
    return cls in SECURE_CLASSES and act != "close"


# ── the gate both senders use ────────────────────────────────────────────────────────────────────────────────────────────────
async def before(area: str, verb: str, target: Any, *, quiet: bool, secure: bool = False, refused: type = MusicRefused):
    """Right before a command is sent. None: not asked (flag off, or nobody is acting). ``TOLD``-marked tuple: send it, then
    tell the kernel. A ``kernel_door.Decision``: asked (allowed or not checked). Raises ``refused`` when the kernel said no, or
    when a SECURE command could not be checked."""
    if not os_shadow.area_on(area):
        return None
    who = kernel_door.acting()
    if who is None:
        return None
    actor, stamps_at = who
    panel = kernel_door.is_panel(actor)
    ids = {"panel": panel, "target": target if isinstance(target, str) and _ID.match(target) else None, "owner": None,
           "household": True, "stamps_at": stamps_at if stamps_at is not None else os_shadow.stamp_snapshot()}
    person = "" if panel else str(actor)
    if quiet:
        return (TOLD, area, verb, person, ids)
    decision, reason, ask, stamps = await os_shadow.ask_door(area, verb, person, **ids)
    if decision == "refuse":
        raise refused(reason)
    if secure and decision != "allow":
        raise refused("unavailable")
    return kernel_door.Decision(decision, reason, ask, stamps=stamps, area=area)


def after(asked: Any, ok: bool) -> None:
    """Once the sender knows how its command ended. Never raises, never waits."""
    if asked is None:
        return
    if isinstance(asked, tuple) and asked and asked[0] == TOLD:
        if ok:
            _, area, verb, person, ids = asked
            os_shadow.record_door(area, verb, person, **ids)
        return
    kernel_door.wrote(asked, "done" if ok else "failed")
