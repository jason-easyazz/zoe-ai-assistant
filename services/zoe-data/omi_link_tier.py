"""The spoken half of linking a pendant: "Zoe, link my pendant" / "unlink my pendant" / "is my pendant linked?" (record
``docs/knowledge/omi-linking.md``; the pairing window itself is ``omi_linking.py``).

ONE deterministic tier in ``fast_tiers._resolve_tiers``, after the named-guest tiers. It answers only a whole-utterance cue from
``lexicons_data/omi_linking_en.json`` (no English here), so every other turn goes on untouched (None).

  link     a member, positively an adult, whose voice the server ACCEPTED (``speaker_verified is True``) or who is signed in with a fresh
           PIN at THIS panel, on the panel's voice channel. Opens the pairing window on that panel; the reply is the first instruction
           (with the one-sentence notice in front of it the first time). Guest / child / unverified / typed / no panel: a spoken reason.
  unlink   the member's OWN pendant, the same proof; refused while it is known to hold unsynced audio; otherwise done, with the warning.
  status   the member's own state, read-only (a verified voice or the panel PIN, like the rest: it names their pendant).

With the operator's linking off it says so plainly ("ask the household admin to turn on pendant linking"). Dry run
(``allow_writes=False``): no window and no unlink. NEVER raises.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import omi_linking

logger = logging.getLogger(__name__)

TIER = "omi_link"
VOICE_CHANNELS = frozenset({"voice", "livekit"})
_MAX_CHARS = 120


def classify(text: str) -> Optional[str]:
    """``link`` | ``unlink`` | ``status`` for a whole-utterance cue, else None. Pure."""
    lx = omi_linking.lex("en")
    s = re.sub(r"[?!.,;:]+", " ", str(text or "").lower()).strip()
    s = re.sub(r"\s+", " ", s)
    if not s or len(s) > _MAX_CHARS:
        return None
    for _ in range(3):                                     # "hey zoe could you ..." strips in a few passes
        for rx in lx.get("strip_lead") or ():
            s = re.sub("^(?:" + rx + ")", "", s).strip()
    for kind in ("unlink", "link", "status"):            # unlink first: the more consequential reading of an ambiguous utterance
        for rx in (lx.get("cues") or {}).get(kind) or ():
            if re.fullmatch(rx, s):
                return kind
    return None


async def _verified_here(uid: str, speaker_verified: Optional[bool], panel_id: str) -> bool:
    """POSITIVE proof the asker is this member at this panel (fails closed; the same rule as ``named_guest_enrol``)."""
    if speaker_verified is True:
        return True
    try:
        import biometric_scope
        return bool(uid and panel_id) and await biometric_scope._fresh_panel_member(panel_id) == uid
    except Exception:  # noqa: BLE001
        return False


def _ago(iso: Optional[str]) -> str:
    try:
        t = datetime.strptime(str(iso), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return ""
    mins = max(0, int((datetime.now(timezone.utc) - t).total_seconds() // 60))
    if mins < 2:
        return "just now"
    if mins < 90:
        return f"{mins} minutes ago"
    hours = round(mins / 60)
    return f"{hours} hours ago" if hours < 36 else f"{round(hours / 24)} days ago"


async def _status(uid: str) -> str:
    import omi_devices
    import omi_member_settings as ms
    mine = await omi_devices.owned_by(uid)
    if not mine:
        return omi_linking.say("status_none")
    if await ms.sync_paused(uid):
        return omi_linking.say("status_paused")
    try:
        import omi_health
        last = next((p.get("last_sync_at") for p in await omi_health.list_for(uid) if p.get("last_sync_at")), None)
    except Exception:  # noqa: BLE001
        last = None
    ago = _ago(last)
    return omi_linking.say("status_linked_synced", ago=ago) if ago else omi_linking.say("status_linked")


async def _unlink(uid: str, role: str) -> str:
    import omi_devices
    mine = await omi_devices.owned_by(uid)
    if not mine:
        return omi_linking.say("unlink_none")
    if len(mine) > 1:
        return omi_linking.say("unlink_many")
    try:
        out = await omi_devices.self_unassign(mine[0]["device_id"], {"user_id": uid, "role": role})
    except omi_devices.DeviceError as exc:
        return omi_linking.say("unlink_pending" if exc.code == "audio_pending" else "unlink_fail")
    return omi_linking.say("unlink_done_warn" if out.get("pending_audio") is None else "unlink_done")


async def _link(member: dict, panel_id: str) -> str:
    import omi_member_settings as ms
    uid = member["user_id"]
    first = not (await ms.read_uid(uid))["notice_ack_at"]
    try:
        await omi_linking.start(member, panel_id, notice_told=True)
    except omi_linking.LinkError as exc:
        return exc.message
    return omi_linking.say("start_with_notice", notice=omi_linking.notice()) if first else omi_linking.say("start")


async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None,
               speaker_verified: Optional[bool] = None, panel_id: Optional[str] = None, allow_writes: bool = True):
    """A ``DispatchResult`` for a linking cue, else None. NEVER raises."""
    try:
        kind = classify(text)
        if kind is None:
            return None
        uid = (user_id or "").strip()
        pid = (panel_id or "").strip()
        import enrollment_state as es
        audience = await es.audience_of(uid)
        ok, why = omi_linking.available()
        if audience in ("guest", "synthetic", "unknown") or not uid:
            reply = omi_linking.say("guest")
        elif not ok:
            reply = omi_linking.say("off_" + why)
        elif (channel or "") not in VOICE_CHANNELS or not pid:
            reply = omi_linking.say("no_panel")
        elif not await _verified_here(uid, speaker_verified, pid):
            reply = omi_linking.say("unverified")
        elif kind == "status":
            reply = await _status(uid)
        elif allow_writes is False:
            return None
        elif kind == "unlink":
            reply = await _unlink(uid, audience)
        else:
            import manner_block
            if not await manner_block.is_adult(uid):
                reply = omi_linking.say("child")
            else:
                reply = await _link({"user_id": uid, "role": "member"}, pid)
        logger.info("OMI_LINK tier kind=%s audience=%s verified=%s", kind, audience, speaker_verified)
        import expert_dispatch as _xd
        return _xd.DispatchResult(domain="identity", reply=reply, intent=f"omi_link_{kind}", tier=TIER)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the tier never breaks a turn
        logger.warning("omi_link: tier failed (non-fatal): %s", type(exc).__name__)
        return None
