"""A member links their OWN Omi pendant at the wall panel - by voice ("Zoe, link my pendant") or the Link button in Settings - and nobody
runs code or edits a file. Design, decisions and owner steps: ``docs/knowledge/omi-linking.md``.

PROOF = WHO + WHAT IS IN THEIR HAND.
  who      a VERIFIED member: the voice the server accepted (``speaker_verified is True``) or this member's fresh PIN sign-in at this panel.
           Checked by the caller (the voice tier / the route) before ``start``; ``omi_devices.self_assign`` re-checks member + adult.
  what     the PAIRING WINDOW this module opens on THAT panel: for ``WINDOW_S`` seconds the panel's Bluetooth agent scans (the existing
           Settings > Bluetooth broker, ``bluetooth_broker.BROKER``; nothing new on the Pi) and only an Omi pendant heard NEAR the panel
           (``NEAR_RSSI``) inside the window counts as seen. ``self_assign`` refuses any device this window did not see.
  never    a pendant someone else owns (they unlink first, or the admin moves it); a second pendant equally near (ambiguous: we ask them
           to keep only theirs close); a child's pendant (admin assign only); a guest; another member as the target.

After the claim the panel CONNECTS to the pendant once (an audited broker ``connect``), so the agent's known-devices store remembers
it, and ``omi-sync`` (which now reads that store) starts syncing it with no operator env step. Then it disconnects.

On screen: the EXISTING enrolment overlay (``voice_enroll`` push frame -> ``js/touch-voice-enroll.js``), never a new overlay. Spoken: one
announcement per outcome on THAT panel (``voice_announce``). Every word is in ``lexicons_data/omi_linking_en.json``.

The notice: the first time a member links, they are told in ONE sentence that the pendant records the people around them and how to
pause it; ``omi_member_settings.notice_ack_at`` stores when. Windows are in-process state (one uvicorn process): a restart closes them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

import omi_devices as devices

logger = logging.getLogger(__name__)

DIR = Path(__file__).with_name("lexicons_data")
WINDOW_S = 90.0
#: a pendant heard at or above this strength is "next to the panel"; a pendant across the room is not (the CV1 reads about -45 dBm
#: held at the panel and -80 dBm or weaker from the next room)
NEAR_RSSI = -65
#: how long a claim may follow the last sighting inside the window (the claim runs right after the scan that saw it)
CLAIM_GRACE_S = 15.0
POLL_S = 1.0
STEPS = 3

_WINDOWS: dict[str, "Window"] = {}
_TASKS: set = set()


@lru_cache(maxsize=None)
def lex(lang: str = "en") -> dict:
    path = DIR / f"omi_linking_{(lang or 'en').split('-')[0].lower()}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if data.get("enabled") else {}


def say(key: str, **slots: Any) -> str:
    text = str((lex("en").get("say") or {}).get(key) or "")
    for k, v in slots.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def notice() -> str:
    return say("notice")


@dataclass
class Window:
    panel_id: str
    user_id: str
    role: str
    opened: float
    deadline: float
    seen: dict = field(default_factory=dict)          # device_id -> monotonic time last heard NEAR inside this window
    state: str = "searching"                          # searching | linked | already | failed | closed
    reason: str = ""
    device_id: str = ""
    remembered: Optional[bool] = None
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def public(self, now: float) -> dict:
        return {"panel_id": self.panel_id, "state": self.state, "reason": self.reason, "device_id": self.device_id or None,
                "remembered": self.remembered, "seconds_left": max(0, int(self.deadline - now))}


def reset() -> None:
    _WINDOWS.clear()
    _TASKS.clear()


def available() -> tuple[bool, str]:
    """(True, '') when the operator has everything linking needs on, else (False, reason code) - the UI and the voice say it plainly."""
    import bluetooth_broker
    import omi_recordings
    if not omi_recordings.enabled():
        return False, "sync_off"
    if not devices.enabled():
        return False, "linking_off"
    if not bluetooth_broker.enabled():
        return False, "bluetooth_off"
    return True, ""


def window_saw(panel_id: str, user_id: str, device_id: str, *, now: Optional[float] = None) -> bool:
    """Did THIS panel hear THIS pendant near it inside the window THIS member opened (and is the window still open)?"""
    w = _WINDOWS.get(panel_id or "")
    if w is None:
        return False
    now = w.clock() if now is None else now
    if w.user_id != user_id or now > w.deadline:
        return False
    at = w.seen.get(device_id)
    return at is not None and now - at <= CLAIM_GRACE_S


def status(user_id: str) -> Optional[dict]:
    for w in _WINDOWS.values():
        if w.user_id == user_id:
            return w.public(w.clock())
    return None


class LinkError(Exception):
    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(code)
        self.status, self.code, self.message = status, code, message or code


async def start(member: dict, panel_id: str, *, notice_told: bool, broker: Any = None, sleep: Callable = asyncio.sleep,
                clock: Callable[[], float] = time.monotonic, spawn: bool = True) -> Window:
    """Open the pairing window on ``panel_id`` for the member (the caller has already proved WHO they are). Raises ``LinkError``."""
    ok, why = available()
    if not ok:
        raise LinkError(409, why, say("off_" + why))
    try:
        uid = devices.member_only(member)
    except devices.DeviceError as exc:
        raise LinkError(exc.status, exc.code, say("guest")) from exc
    import manner_block
    if not await manner_block.is_adult(uid):
        raise LinkError(403, "adult_only", say("child"))
    import omi_member_settings as ms
    acked = (await ms.read_uid(uid))["notice_ack_at"]
    if not acked and not notice_told:
        raise LinkError(428, "notice_required", notice())
    import bluetooth_broker as bt
    broker = broker or bt.BROKER
    if not bt.PANEL_ID_RX.match(panel_id or ""):
        raise LinkError(400, "bad_panel", say("no_panel"))
    try:
        st = broker.panel(panel_id)
        if not st.online(broker.clock()):
            raise LinkError(409, "panel_offline", say("panel_offline"))
    except bt.BrokerError as exc:
        raise LinkError(409, "panel_offline", say("panel_offline")) from exc
    now = clock()
    cur = _WINDOWS.get(panel_id)
    if cur is not None and cur.state == "searching" and now <= cur.deadline and cur.user_id != uid:
        raise LinkError(409, "panel_busy", say("panel_busy"))
    w = Window(panel_id=panel_id, user_id=uid, role=str(member.get("role") or ""), opened=now, deadline=now + WINDOW_S,
               clock=clock)
    _WINDOWS[panel_id] = w
    await _screen(panel_id, "phrase", say("screen_title"), say("screen_hold"), 1)
    if spawn:
        try:
            task = asyncio.get_running_loop().create_task(drive(w, broker=broker, sleep=sleep, clock=clock))
            _TASKS.add(task)
            task.add_done_callback(_TASKS.discard)
        except RuntimeError:
            pass
    return w


# --- the window's driver -----------------------------------------------------------------------------------------------------
async def _screen(panel_id: str, state: str, line: str, phrase: str = "", index: int = 0) -> None:
    """One frame on THAT panel's own channel, through the enrolment overlay (best effort; the spoken line is the contract)."""
    try:
        from voice_session import send_to_panel
        frame: dict = {"panel_id": panel_id, "state": state, "line": line[:160]}
        if state in ("phrase", "recording", "accepted", "reask"):
            frame.update(phrase=phrase[:240], index=index, total=STEPS)
        await send_to_panel(panel_id, "voice_enroll", frame)
    except Exception as exc:  # noqa: BLE001
        logger.debug("omi_linking: screen frame not sent (%s)", type(exc).__name__)


async def _speak(panel_id: str, user_id: str, text: str) -> None:
    if not text:
        return
    try:
        import voice_announce
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            await voice_announce.enqueue_announcement(db, user_id=user_id, message=text, panel_id=panel_id, trigger_type="omi_link",
                                                      ttl_s=120)
    except Exception as exc:  # noqa: BLE001
        logger.warning("omi_linking: outcome not spoken (%s)", type(exc).__name__)


async def _run_job(broker: Any, w: Window, op: str, device_id: Optional[str], sleep: Callable, clock: Callable) -> Optional[Any]:
    """Queue one broker job for the member and wait for it to finish (or the window to close). None = not run / did not finish."""
    import bluetooth_broker as bt
    try:
        job = broker.enqueue(w.panel_id, op, device_id, None, {"user_id": w.user_id, "role": w.role})
    except bt.BrokerError:
        return None
    limit = max(clock() + 30.0, w.deadline) if op != "scan" else w.deadline
    while clock() <= limit:
        await sleep(POLL_S)
        try:
            j = broker.get_job(w.panel_id, job.id)
        except bt.BrokerError:
            return None
        if j.state in bt.TERMINAL:
            return j
    return None


async def _owner(device_id: str) -> str:
    return await devices.owner_of(device_id)


async def _finish(w: Window, state: str, reason: str, key: str, **slots: Any) -> None:
    w.state, w.reason = state, reason
    text = say(key, **slots)
    await _screen(w.panel_id, "done" if state in ("linked", "already") else "failed", text)
    await _speak(w.panel_id, w.user_id, text)
    logger.info("OMI_LINK panel=%s state=%s reason=%s", w.panel_id, state, reason or "-")


async def drive(w: Window, *, broker: Any, sleep: Callable = asyncio.sleep, clock: Callable[[], float] = time.monotonic) -> Window:
    """Scan until exactly ONE unowned pendant is heard next to the panel, then claim it for the member. Never raises."""
    try:
        told_crowded = False
        while clock() <= w.deadline and w.state == "searching":
            job = await _run_job(broker, w, "scan", None, sleep, clock)
            if job is None or job.state != "succeeded":
                await sleep(POLL_S)
                continue
            heard = clock()
            near = [d for d in ((job.result or {}).get("devices") or [])
                    if isinstance(d, dict) and d.get("driver") == "omi" and isinstance(d.get("rssi"), (int, float))
                    and d["rssi"] >= NEAR_RSSI and devices.DEVICE_ID_RX.match(str(d.get("id", "")))]
            for d in near:
                w.seen[d["id"]] = heard
            owners = {d["id"]: await _owner(d["id"]) for d in near}
            mine = [i for i, o in owners.items() if o == w.user_id]
            free = [i for i, o in owners.items() if not o]
            if mine and not free:
                w.device_id = mine[0]
                await _finish(w, "already", "", "already")
                break
            if len(free) == 1:
                await _screen(w.panel_id, "accepted", say("screen_title"), say("screen_found"), 2)
                try:
                    await devices.self_assign(free[0], {"user_id": w.user_id, "role": w.role}, panel_id=w.panel_id)
                except devices.DeviceError as exc:
                    await _finish(w, "failed", exc.code, "claim_failed")
                    break
                w.device_id = free[0]
                import omi_member_settings as ms
                from db_compat import get_compat_db
                async with get_compat_db() as db:
                    await ms.acknowledge_notice(db, w.user_id)
                await _screen(w.panel_id, "phrase", say("screen_title"), say("screen_remember"), 3)
                conn = await _run_job(broker, w, "connect", free[0], sleep, clock)
                w.remembered = bool(conn is not None and conn.state == "succeeded")
                if w.remembered:
                    await _run_job(broker, w, "disconnect", free[0], sleep, clock)
                await _finish(w, "linked", "", "linked" if w.remembered else "linked_not_remembered")
                break
            if len(free) > 1 and not told_crowded:
                told_crowded = True
                await _screen(w.panel_id, "reask", say("screen_title"), say("crowded"), 1)
                await _speak(w.panel_id, w.user_id, say("crowded"))
            elif near and not free and not mine:
                await _finish(w, "failed", "owned_by_someone_else", "taken")
                break
        if w.state == "searching":
            await _finish(w, "failed", "not_found", "not_found")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a background window never takes zoe-data down
        logger.warning("omi_linking: window failed (%s)", type(exc).__name__)
        w.state, w.reason = "failed", "internal"
    return w
