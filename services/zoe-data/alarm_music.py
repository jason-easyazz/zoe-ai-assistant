"""alarm_music - the clock-radio half of an alarm: start Music Assistant playback on the panel when a music alarm rings (flag-dark
``ZOE_ALARMS``; design ``docs/architecture/alarms.md`` section "Sounds and the clock radio").

An alarm must never be silent, so this module only ever STARTS music and REPORTS how it went; the panel owns the guarantee. The ring
payload carries ``music_state`` (``starting`` | ``playing`` | ``failed``, empty = unknown, e.g. zoe-data restarted mid-ring) and the
panel plays the alarm's tone unless music is ``playing`` within ``START_DEADLINE_S`` of the ring start (or the moment it is
``failed``). So a down Music Assistant, a provider that needs re-auth, a missing panel player, a stale playlist name or a slow
start all end in a tone, never in silence. A failed start is logged (``music_fallback`` event).

Lifecycle: ``on_event`` is an ``alarms.HOOKS`` listener. ``rang`` starts the source on the panel's player at a low volume and ramps it
to ``TARGET_VOLUME`` over the alarm's ``ramp_seconds``; ``snoozed`` / ``dismissed`` / ``auto_stopped`` / ``updated`` / ``deleted`` stop
it, so music can never keep playing under a silenced alarm. Sources (``sound_id``): ``last`` (the last thing played), ``playlist:<name>``,
``radio:<station or artist>``, ``search:<free text>``.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional

import alarms

logger = logging.getLogger(__name__)

START_DEADLINE_S = 10.0
STOP_TIMEOUT_S = 3.0  # a hung Music Assistant must never hold up a dismiss / snooze
START_VOLUME = 5
TARGET_VOLUME = 60
RAMP_STEP_S = 3.0
POLL_S = 1.0
STATE: dict[str, dict[str, Any]] = {}
_tasks: dict[str, "asyncio.Task[None]"] = {}


def _ms() -> Any:
    import music_service

    return music_service


def player_name() -> str:
    from typed_env import env_str

    return env_str("ZOE_ALARM_MUSIC_PLAYER", "Zoe Panel").strip().lower()


def state_of(alarm_id: str) -> str:
    return str(STATE.get(alarm_id, {}).get("state", ""))


async def _panel_player(ms: Any) -> str:
    """The panel's own speaker, by name. Never another room's speaker: a clock radio plays where the clock is."""
    want = player_name()
    for p in await ms.get_players() or []:
        names = (str(p.get("display_name") or ""), str(p.get("name") or ""))
        if any(want and want == n.strip().lower() for n in names) and p.get("player_id") and p.get("available", True):
            return str(p["player_id"])
    return ""


async def _start_source(ms: Any, sound_id: str, pid: str) -> bool:
    kind, _, arg = sound_id.partition(":")
    arg = arg.strip()
    if sound_id == "last":
        items = (await ms.get_recently_played(limit=1)).get("items") or []
        return bool(items) and bool((await ms.play_media(items[0]["uri"], player_id=pid)).get("ok"))
    if kind == "playlist" and arg:
        want = re.sub(r"\s+", " ", arg).lower()
        hit = next((p for p in await ms.list_playlists() if str(p.get("name", "")).strip().lower() == want and p.get("uri")), None)
        return bool(hit) and bool((await ms.play_media(hit["uri"], player_id=pid)).get("ok"))
    if kind in ("radio", "search") and arg:
        return bool(await ms.search_and_play(arg, player_id=pid, radio_mode=(kind == "radio")))
    return False


async def _log(alarm_id: str, event: str, detail: str) -> None:
    try:
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            await alarms.log_event(db, alarm_id, event, detail)
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - a log line never breaks the alarm
        logger.warning("alarm music log failed (%s)", type(exc).__name__)


async def _fail(ms: Any, alarm_id: str, pid: str, why: str) -> None:
    STATE[alarm_id] = {"state": "failed", "since": time.time(), "reason": why}
    logger.warning("alarm %s: music did not start (%s); the panel plays the tone", alarm_id, why)
    await _log(alarm_id, "music_fallback", why)
    if pid:
        try:
            await ms.control("pause", pid)
        except Exception:  # noqa: BLE001
            pass


async def _run(alarm: dict) -> None:
    ms, aid, pid = _ms(), alarm["id"], ""
    t0 = time.time()
    STATE[aid] = {"state": "starting", "since": t0}
    try:
        deadline = time.monotonic() + START_DEADLINE_S
        pid = await asyncio.wait_for(_panel_player(ms), max(0.1, deadline - time.monotonic()))
        if not pid:
            return await _fail(ms, aid, pid, "no panel player")
        ramp = int(alarm.get("ramp_seconds") or 0)
        await ms.control("volume_set", pid, START_VOLUME if ramp else TARGET_VOLUME)
        started = await asyncio.wait_for(_start_source(ms, str(alarm["sound_id"]), pid), max(0.1, deadline - time.monotonic()))
        if not started:
            return await _fail(ms, aid, pid, "source did not start")
        while True:
            np = await asyncio.wait_for(ms.now_playing(pid), max(0.1, deadline - time.monotonic()))
            if np and str(np.get("state")) == "playing":
                break
            if time.monotonic() >= deadline:
                return await _fail(ms, aid, pid, "not playing within 10 s")
            await asyncio.sleep(min(POLL_S, max(0.0, deadline - time.monotonic())))
        if time.monotonic() > deadline:  # the panel already fell back to its tone: late music must not join it
            return await _fail(ms, aid, pid, "started after the 10 s deadline")
        STATE[aid] = {"state": "playing", "since": time.time()}
        if ramp:
            began = float(alarm.get("ring_started_at") or t0)  # the SERVER ring start, like the tone's ramp: slow providers do not restart it
            while True:
                frac = min(1.0, max(0.0, (time.time() - began) / ramp))
                await ms.control("volume_set", pid, round(START_VOLUME + (TARGET_VOLUME - START_VOLUME) * frac))
                if frac >= 1.0:
                    break
                await asyncio.sleep(RAMP_STEP_S)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - timeouts included: any failure is a tone, never silence
        if STATE.get(aid, {}).get("state") != "playing":
            await _fail(ms, aid, pid, type(exc).__name__)
        else:
            logger.warning("alarm %s: volume ramp stopped (%s)", aid, type(exc).__name__)


async def stop(alarm_id: str) -> None:
    """Stop the music for this alarm (cancel the start/ramp, pause the panel player). Idempotent."""
    task = _tasks.pop(alarm_id, None)
    if task and not task.done():
        task.cancel()
    had = STATE.pop(alarm_id, None)
    if had and had.get("state") in ("starting", "playing"):
        ms = _ms()
        try:
            pid = await asyncio.wait_for(_panel_player(ms), STOP_TIMEOUT_S)
            if pid:
                await asyncio.wait_for(ms.control("pause", pid), STOP_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            logger.warning("alarm %s: could not pause the music (%s)", alarm_id, type(exc).__name__)


async def on_event(event: str, alarm: dict) -> None:
    if event == "rang" and alarm.get("sound_kind") == "music":
        await stop(alarm["id"])  # a re-ring after snooze starts clean
        _tasks[alarm["id"]] = asyncio.get_running_loop().create_task(_run(alarm), name=f"alarm_music_{alarm['id']}")
    elif event in ("snoozed", "dismissed", "auto_stopped", "updated", "deleted"):
        await stop(alarm["id"])


def register() -> None:
    if on_event not in alarms.HOOKS:
        alarms.HOOKS.append(on_event)
