---
type: Reference
title: Zoe OS music and home-control doors - a member's ACTION asks the kernel at the one sender
description: ZOE_OS_MUSIC=kernel and ZOE_OS_HOME=kernel (both default off). Two action areas, nothing stored. Every path to Music Assistant and to Home Assistant, the one sender of each, what is asked first, what is sent first and told afterwards (stop, turn down, lights off), what is never done unchecked (locks and covers), a child's home-control LEVEL and how an adult sets it, what crosses the socket, and the switch-on steps.
tags: [zoe-os, kernel, music, home, home-assistant, levels, door, safety, flag-dark]
timestamp: 2026-11-20T00:00:00Z
---

# Zoe OS music and home-control doors

**Status 2026-11-20: built, flag-dark (`ZOE_OS_MUSIC` and `ZOE_OS_HOME` unset = `off`). Nothing on this page is live.** These
are ACTION areas: nothing is stored in Zoe for either. The kernel decides and records the action; the command goes to Music
Assistant or Home Assistant as before. Same mechanism as the other areas
([zoe-os-alarms-door.md](zoe-os-alarms-door.md) for "told afterwards").

## 1. Every way out, and the one sender of each [code]

| Area | The one sender | Who reaches it |
|---|---|---|
| Music | `music_service._ma_response` (every command to Music Assistant) | the screen (`routers/music.py`), the spoken resolver (`music_service.resolve_music`), the intent executor (`intent_router`), alarm music and the discovery jobs (automatic) |
| Home | `home_control.send` (the bridge's device and scene command paths) | the spoken / Skybridge resolver (`smart_home_service`), the panel's control route (`routers/ha_control`), the brain's `ha_control` tool (`zoe_agent`), music through a house media player (`intent_router`) |

`tests/test_actions_one_door.py` pins both over every module; `tests/test_actions_same_as_main.py` pins every outbound call of
every entry point against a record captured from main (a fake Music Assistant and a fake bridge).

**Outside both doors, by design:** reads (what is playing, device states); the provider sign-in (`music_oauth.py`); the
admin-gated proxy of Music Assistant's own UI (`routers/music_ma_ui.py`: whoever may open that UI can do anything in it);
the panel's AirPlay speaker (shairport on the panel: Zoe sends it no commands). The Home Assistant MCP bridge is a separate
service: an agent that calls it directly, not through zoe-data, is not behind this door.

**Who is acting.** Every authenticated entry point notes its caller for the doors (`kernel_door.note_actor`: the turn recorder
`panel_principal.record_turn_home_control`, `require_member_or_panel`, the jukebox dependency, `/api/ha`'s own check, the
brain's dispatch, the typed Skybridge route, the A2A stream, the card / form / suggestion actions, the PIN-resumed voice
command, the lab's operator check, and each LiveKit room turn). A command sent with nobody noted is AUTOMATIC (an alarm's
music, the discovery jobs, a routine Zoe runs herself): not asked, not told. Which entry points must note is DERIVED from the
code (`tests/actor_reach.py`: the call graph walked back from the two senders to every route, websocket handler and spawned
task); one that reaches a sender without noting fails `test_actions_kernel_door.py` unless it is listed with its reason
(provider set-up, a read, automatic work, a task of a turn that already noted). Voice turns from the panel's device token
act as THE PANEL even when the voice names a member (the scope member can be a bound or recent session, not the speaker), so
a child speaking at the panel is held to the panel's rule, not their own level: an open question, not changed here.

## 2. Three kinds of command [code]

| Kind | Which | Kernel |
|---|---|---|
| **Quiet** | music: pause, stop, mute, volume down, and a set volume (its direction is not known); home: a light or a media player OFF | **sent first, told afterwards**: the kernel can neither delay nor refuse it |
| **Secure** | home: a lock or a cover (garage doors and blinds are covers; an alarm panel counts as a lock), and any act but closing or locking it. An unknown service on one counts as opening | **always asked first, and a kernel that is down, old or slow means NO** |
| the rest | everything else | asked first; a refusal is final (nothing is sent); kernel down or slow = sent, `not_checked` |

"Kernel down means NO" is true ONLY for the secure kind. It is the one place in Zoe where an unreachable kernel refuses.

**The service's own adults-only check stays on, whatever the flag says.** Since #2715 zoe-data itself sends a command that
unlocks or opens only for an adult member, or for the panel while an adult's verified voice session is open on it
(`home_opening.py`): the bare panel, a guest and a child are refused. The home door does not replace that check. Each sender
runs it first and the door (in `home_control.send`) is asked only for a command it let through, so with `ZOE_OS_HOME=kernel`
BOTH must agree before anything that opens the house is sent:

| The service's check | The kernel | Result |
|---|---|---|
| refuses | not asked, and told nothing (the command never reaches the door) | not sent; the service's sentence is said |
| allows | refuses | not sent; the kernel's sentence is said |
| allows | down, old or slow | not sent ("I can't check who may open that right now") |
| allows | allows | sent, and the outcome is reported |

So a child's level that lists `cover` or `lock` does not open anything by itself: the service's check still refuses a child.
A typed Skybridge command is not a chat or voice turn for that check, so today it refuses an opening command there for
everyone; the kernel is not asked. A command that names a lock or a cover as its service target (`data.entity_id`) is asked
about as that lock or cover, not as the entity in the path. Scenes are not covered by the service's check yet (#2716).

How a refusal reaches the person: the screen gets 403 with a plain sentence; a spoken turn says it; the brain's tool returns
an error result with `status: refused`. A refused music play does not fall back to a house speaker.

**Flag off is main exactly:** a flag read; no kernel call; all 68 scenarios send what main sent. With the doors on and the
kernel allowing, the outbound calls are the same.

## 3. Music: who may [code; `labs/zoe-os/zoe_os/music_door.py`]

Anyone on the roster and the shared panel may control the music. Not on the roster: refused. The point is the record.

## 4. Home control: who may, and a child's LEVEL [code; `labs/zoe-os/zoe_os/home_door.py`]

The ask carries the CLASS of thing and what is done to it as closed codes (`light.on`, `lock.open`, ...): the class is the
entity id's domain, so the kernel decides by class and never learns a device's name.

| Who | May control |
|---|---|
| an adult | everything |
| a child | only the classes their level allows |
| the shared panel, a guest on the roster | everything except opening the house (a lock or a cover that is not being closed) |
| not on the roster | nothing |

**A child's level** is a set of classes, stated per child:

| Level | A child may control |
|---|---|
| `none` | nothing |
| `lights` (the default when nothing is stated) | lights, and media players |
| `comfort` | lights, media players, switches and plugs (fans, helper toggles), scenes (scripts and automations count as scenes) |
| `most` | everything in `comfort`, and the heating and cooling |
| a list of classes | exactly those classes. This is the only way a child is given covers or locks: an adult has to list them on purpose |

Media players are in the default because music is open to everyone. Locks, covers and (below `most`) the heating are
adult-only unless an adult states otherwise.

**Where a level lives.** `home-levels.json` beside the kernel's roster (`ZOE_OS_SHADOW_DIR`, mode 0600):
`{"levels": {"<child id>": "comfort"}}` or `{"levels": {"<child id>": ["light", "switch"]}}`. The kernel reads it and follows
it without a restart. A file others can read, or that is malformed, states nothing: every child then has the default, never more.

**Setting a level** is a change like any other: asked first (`level.set`), an adult only, and with the door on it is never
changed while the kernel cannot be asked. There is no settings screen yet; the setter is a command line:

```bash
cd ~/assistant/services/zoe-data
python3 home_level.py --show
python3 home_level.py --by <adult member id> <child id>=comfort
python3 home_level.py --by <adult member id> <child id>=light,switch,cover
```

**What the data model knows.** Classes: yes (the entity id's domain). Rooms: only for PANELS (a panel's room and its devices,
used to resolve "the light" at that panel); there is no link from a member to a room, so "a child's own room" cannot be used
and is not part of the level.

## 5. What crosses the socket and reaches the log [code]

One ask per command: `door_ask` with `area=music` or `home`, the verb (a closed code), the acting member's id (or
`via=panel`), the speaker's or entity's id, `household`, and the actor's erase stamp. Never a track, an artist, a search, a
device's friendly name, a room, a scene's name or a brightness. One log line:
`ZOE_OS_ACT area=music|home verb=... decision=... reason=... ask_ms=...`.

## 6. Switch-on (owner steps; nothing here is done by the PR)

1. This change deployed and **the kernel unit restarted on it** (an older kernel has neither area).
2. The roster current, with roles. A child must be STATED as a child: a member whose role is not stated is a guest to the kernel, and a guest may do anything in the house but open it (more than a child at the default level).
3. State each child's level (section 4), or accept the default (lights and media).
4. `ZOE_OS_MUSIC=kernel` and / or `ZOE_OS_HOME=kernel` in zoe-data's environment, then a zoe-data restart.
5. Live check with a demo adult and a demo child: the child turns a light on (allowed) and tries a plug (refused, "needs a
   grown-up"); an adult unlocks a test lock (allowed); stop the kernel unit and try the unlock again (refused, nothing sent);
   with it still stopped, turn a light off and pause the music (both happen at once).

Turn off: remove the flag (or set `off`), restart zoe-data.

## 7. Not done, not proven

* Not run live; the voice replay gate has not been run (the spoken music and home paths are touched).
* A spoken command that names several devices ("all the lights") is asked per device: a refusal part-way leaves the earlier
  ones done.
* Some music functions catch every error and answer "failed": a refusal there is said as a failure, not as the sentence
  (the command is still not sent).
* No settings screen for levels (section 4).
