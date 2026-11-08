"""Panel speaker-model health: "the panel cannot score voices at all" is an owner ALARM, never a silent guest night.

Every panel voice turn carries ``speaker_model_state`` (``zoe_voice_daemon._speaker_model_state``)::

    {"model": "<id>", "state": "ready" | "loading" | "refused" | "missing" | "off", "down_s": 123, "attempts": 2, ...}

Without it the server cannot tell "the gate ran and nobody matched" from "the panel could not score": on 2026-11-07 the panel's
ResNet34 self-test was refused at boot, every turn went up with no claim, ``_speaker_verdict`` was None and the owner was a guest
for ~8 h (no memory, no private context) while nothing said so.

``note_turn`` (called by /turn and /turn_stream for device callers) tracks each panel. A state that is neither ``ready`` nor ``off``
for ``ZOE_PANEL_SPEAKER_ALARM_MIN`` minutes (default 10) raises the alarm, ONCE per outage (re-logged hourly):

* an ``ALARM:`` ERROR line in the zoe-data log,
* ``/health`` ``panel_speaker`` (state + minutes only, no panel id),
* one bell ``notifications`` row (type ``panel_speaker``) for each admin,
* the admin morning brief line (``brief_line``), which also reports an outage that ended in the last 24 h.

The outage start comes from the panel's own ``down_s`` when it sends one, so a zoe-data restart (the night window restarts it) does
not reset the clock. State is in memory, mirrored best-effort to ``ZOE_PANEL_SPEAKER_HEALTH_PATH`` (default
``~/.zoe/panel_speaker_health.json``) so the brief can still name an outage that ended before a restart. No household data:
model ids, states, timestamps and the refusal's own text. Never raises.
"""
from __future__ import annotations

import json
import logging
import os

from typed_env import env_float, env_str
import threading
import time
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

HEALTHY = frozenset({"ready", "off"})
KNOWN = frozenset({"ready", "off", "loading", "refused", "missing"})
REALARM_S = 3600.0
RECENT_OUTAGE_S = 24 * 3600.0
BELL_TITLE = "Panel speaker ID is down"

_LOCK = threading.Lock()
_PANELS: dict[str, dict] = {}          # panel_id -> {"state", "model", "since", "reason", "seen", "alarmed_at", "belled"}
_LAST_OUTAGE: dict[str, dict] = {}     # panel_id -> {"state", "since", "end", "minutes"}
_LOADED = False


def alarm_after_s() -> float:
    v = env_float("ZOE_PANEL_SPEAKER_ALARM_MIN", 10.0)
    return max(0.0, v) * 60.0


def _store_path() -> str:
    return env_str("ZOE_PANEL_SPEAKER_HEALTH_PATH", "") or os.path.expanduser("~/.zoe/panel_speaker_health.json")


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        with open(_store_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for k, v in (data.get("last_outage") or {}).items():
                if isinstance(v, dict):
                    _LAST_OUTAGE[str(k)] = v
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("panel_speaker_health: store unreadable (%s)", type(exc).__name__)


def _save() -> None:
    try:
        path = _store_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"last_outage": _LAST_OUTAGE}, fh)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("panel_speaker_health: store unwritable (%s)", type(exc).__name__)


def _parse(block: Any) -> Optional[dict]:
    if not isinstance(block, Mapping):
        return None
    state = str(block.get("state") or "").strip().lower()[:16]
    if state not in KNOWN:
        return None
    out = dict(state=state, model=str(block.get("model") or "")[:64], reason=str(block.get("reason") or "")[:160])
    try:
        down = float(block.get("down_s"))
        out["down_s"] = down if 0 <= down < 400 * 86400 else None
    except (TypeError, ValueError):
        out["down_s"] = None
    return out


def note_turn(panel_id: str, block: Any, *, now: Optional[float] = None) -> Optional[str]:
    """Record one turn's ``speaker_model_state``. Returns the ALARM text when this turn raised (or re-raised) the alarm, else None.
    A turn without the block (an older daemon) changes nothing."""
    try:
        return _note(str(panel_id or "")[:64] or "panel", _parse(block), time.time() if now is None else now)
    except Exception as exc:  # noqa: BLE001 - health bookkeeping never breaks a turn
        logger.debug("panel_speaker_health: note failed (%s)", type(exc).__name__)
        return None


def _note(panel: str, st: Optional[dict], now: float) -> Optional[str]:
    if st is None:
        return None
    with _LOCK:
        _load()
        cur = _PANELS.get(panel)
        if st["state"] in HEALTHY:
            if cur is not None:
                minutes = (now - cur["since"]) / 60.0
                _LAST_OUTAGE[panel] = dict(state=cur["state"], since=cur["since"], end=now, minutes=round(minutes, 1),
                                           model=cur["model"])
                del _PANELS[panel]
                _save()
                logger.warning("panel %s speaker model is %s again after %.0f min %s", panel, st["state"], minutes, cur["state"])
            return None
        since = now - st["down_s"] if st.get("down_s") else (cur["since"] if cur else now)
        if cur is None:
            cur = _PANELS[panel] = dict(since=since, alarmed_at=None, belled=False)
            logger.warning("panel %s speaker model is %s (%s): its turns carry NO speaker claim - every voice is a guest until it "
                           "is ready", panel, st["state"], st["reason"] or "no reason given")
        else:
            cur["since"] = min(cur["since"], since)
        cur.update(state=st["state"], model=st["model"], reason=st["reason"], seen=now)
        down = now - cur["since"]
        if down < alarm_after_s():
            return None
        if cur["alarmed_at"] is not None and now - cur["alarmed_at"] < REALARM_S:
            return None
        cur["alarmed_at"] = now
        text = (f"panel {panel} speaker model {cur['model'] or '?'} has been {cur['state']} for {down / 60.0:.0f} min - every voice "
                f"turn is a GUEST turn (no memory, no private context) until it loads: {cur['reason'] or 'no reason given'}")
        logger.error("ALARM: %s", text)
        ring = not cur["belled"]
        cur["belled"] = True
    if ring:
        _schedule_bell(panel, down)
    return text


def snapshot(*, now: Optional[float] = None) -> dict:
    """For ``/health``: ``{"alarm": bool, "panels": [{"state", "down_min", "alarm"}]}`` - no panel ids, no reasons."""
    t = time.time() if now is None else now
    with _LOCK:
        rows = [dict(state=p["state"], down_min=round((t - p["since"]) / 60.0, 1), alarm=(t - p["since"]) >= alarm_after_s())
                for p in _PANELS.values()]
    return dict(alarm=any(r["alarm"] for r in rows), panels=rows)


def _hours(minutes: float) -> str:
    if minutes < 90:
        return f"{minutes:.0f} minutes"
    return f"{minutes / 60.0:.1f} hours"


def brief_line(*, now: Optional[float] = None) -> str:
    """ONE plain line for the admin morning brief, or "": a live outage past the alarm bar, else one that ENDED in the last 24 h
    and lasted past the bar."""
    t = time.time() if now is None else now
    with _LOCK:
        _load()
        live = [p for p in _PANELS.values() if t - p["since"] >= alarm_after_s()]
        if live:
            p = max(live, key=lambda p: t - p["since"])
            return (f"Panel speaker ID has been down for {_hours((t - p['since']) / 60.0)} ({p['state']}): every voice at the panel is "
                    "treated as a guest until it loads - check the panel's /health.")
        recent = [o for o in _LAST_OUTAGE.values()
                  if t - float(o.get("end") or 0) <= RECENT_OUTAGE_S and float(o.get("minutes") or 0) * 60.0 >= alarm_after_s()]
        if recent:
            o = max(recent, key=lambda o: float(o.get("end") or 0))
            return (f"Panel speaker ID was down for {_hours(float(o['minutes']))} ({o.get('state')}) and is back now; voices at the "
                    "panel were treated as guests meanwhile.")
    return ""


def _schedule_bell(panel: str, down_s: float) -> None:
    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_ring_admins(panel, down_s), name="panel_speaker_bell")


async def _ring_admins(panel: str, down_s: float) -> int:
    """One bell row per admin, once per outage (best effort; the ALARM log line and /health stand without it)."""
    try:
        from db_compat import get_compat_db

        msg = (f"Speaker ID on the panel has been down for {_hours(down_s / 60.0)}: everyone at the panel is treated as a guest "
               "(no memory, no private answers) until it loads.")
        async with get_compat_db() as db:
            cur = await db.execute("SELECT id FROM users WHERE LOWER(role) = 'admin'")
            admins = [r["id"] for r in await cur.fetchall()]
            with _LOCK:
                since = int((_PANELS.get(panel) or {}).get("since") or 0)
            for uid in admins:
                await db.execute("INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at) "
                                 "VALUES (?, ?, 'panel_speaker', ?, ?, ?, 0, NOW()) ON CONFLICT (id) DO NOTHING",
                                 (f"panel_speaker:{panel}:{since // 600}:{uid}", uid, BELL_TITLE, msg, json.dumps({"panel": panel})))
            await db.commit()
        return len(admins)
    except Exception as exc:  # noqa: BLE001
        logger.warning("panel_speaker_health: admin bell failed (%s)", type(exc).__name__)
        return 0


def _reset_for_tests() -> None:
    global _LOADED
    with _LOCK:
        _PANELS.clear()
        _LAST_OUTAGE.clear()
        _LOADED = False
