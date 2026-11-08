"""Hearing a saved unknown-voice clip and naming it (named guests, spec item 6; flag ``ZOE_NAMED_GUEST_CLIPS``, off).

"Who was that this morning?": a VERIFIED ADULT member at the panel asks, Zoe plays a few seconds of the newest saved clip filed under an
unknown voice (#2343's ``panel_voice_clusters``) through the announcement lane and asks "Who's this?"; "that's Quinn" names THAT cluster via
``named_guest_learning.name_unknown`` (same registry, same refusals, a child guest is marked child-safe). A clip is only a pointer to a
cluster that must still exist: no second registry.

WHO HEARS A CLIP  a member the server verified at this panel (accepted voice or fresh PIN) who is positively an adult. Never a guest, an
unverified voice, a child member or a typed channel; only UNKNOWN voices' clips are offered, never a member's. A child GUEST's clip may be
played to such a member (owner 2026-11-08). The audio never rides the announcement row: it carries a single-use, panel-bound token that the
daemon redeems at ``POST /api/voice/clip-playback``.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import secrets
import time
import wave
from dataclasses import dataclass
from typing import Optional

from typed_env import env_bool

logger = logging.getLogger(__name__)

TIER = "named_guest_clips"
MAX_PLAY_S = 5.0
TOKEN_TTL_S = 180.0
PENDING_TTL_S = 300.0


def enabled() -> bool:
    import named_guests

    return named_guests.mode() == "on" and env_bool("ZOE_NAMED_GUEST_CLIPS", default=False)


@dataclass
class _Pending:
    owner: str
    cluster_id: str
    expires: float


@dataclass
class _Token:
    panel_id: str
    wav: str
    expires: float


_PENDING: dict[str, _Pending] = {}       # panel_id -> the clip just played there
_TOKENS: dict[str, _Token] = {}


def reset() -> None:
    _PENDING.clear()
    _TOKENS.clear()


def _lex() -> dict:
    import named_guest_enrol as nge

    return nge.lex("en").get("clips") or {}


def _norm(text: str) -> str:
    import named_guest_enrol as nge

    s = re.sub(r"[?!.,;:]+", " ", str(text or "").lower()).strip()
    s = re.sub(r"\s+", " ", s)
    for rx in nge.lex("en").get("strip_lead") or ():
        s = re.sub("^(?:" + rx + ")", "", s).strip()
    return s


def classify(text: str, *, pending: bool) -> Optional[tuple[str, str]]:
    """``(kind, name)``: ``play`` ("who was that this morning"), and - only while a clip is pending - ``name`` ("that's Quinn") or
    ``skip``. Pure."""
    lx, s = _lex(), _norm(text)
    if not s or len(s) > 80:
        return None
    if pending:
        for rx in lx.get("skip") or ():
            if re.fullmatch(rx, s):
                return "skip", ""
        for rx in lx.get("name") or ():
            m = re.fullmatch(rx, s)
            if m:
                import named_guest_enrol as nge

                name = nge._clean_name(m.group("name"))
                bad = set(name.lower().split(" ")) & set(nge.lex("en").get("not_names") or ())
                return ("name", name) if name and not bad else None
        return None
    for rx in lx.get("play") or ():
        if re.fullmatch(rx, s):
            return "play", ""
    return None


def _say(key: str, **slots) -> str:
    text = str((_lex().get("say") or {}).get(key) or "")
    for k, v in slots.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def cut_wav(path: str, max_s: float = MAX_PLAY_S) -> Optional[bytes]:
    """The first ``max_s`` seconds of a PCM WAV as WAV bytes, or None (unreadable / off-format / empty)."""
    try:
        with wave.open(path, "rb") as w:
            n = min(w.getnframes(), int(w.getframerate() * max_s))
            if n <= 0:
                return None
            frames = w.readframes(n)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as o:
                o.setnchannels(w.getnchannels())
                o.setsampwidth(w.getsampwidth())
                o.setframerate(w.getframerate())
                o.writeframes(frames)
            return buf.getvalue()
    except (OSError, EOFError, wave.Error):
        return None


def issue_token(panel_id: str, wav: str, *, ttl_s: float = TOKEN_TTL_S) -> str:
    for k in [k for k, t in _TOKENS.items() if t.expires <= time.monotonic() or t.panel_id == panel_id]:
        _TOKENS.pop(k, None)                          # one live clip per panel
    tid = secrets.token_urlsafe(18)
    _TOKENS[tid] = _Token(panel_id, wav, time.monotonic() + ttl_s)
    return tid


def redeem(token: str, panel_id: str) -> Optional[dict]:
    """The daemon's redemption: single use, THIS panel only, unexpired. ``{"audio_base64", "content_type"}`` or None."""
    t = _TOKENS.pop(str(token or ""), None)
    if t is None or t.expires <= time.monotonic() or t.panel_id != (panel_id or ""):
        return None
    audio = cut_wav(t.wav)
    return None if audio is None else dict(audio_base64=base64.b64encode(audio).decode("ascii"), content_type="audio/wav")


async def _live_clusters() -> set:
    from db_compat import get_compat_db

    import named_guest_learning as ngl

    async with get_compat_db() as db:
        await ngl.sweep(db)
        await db.commit()
        rows = await ngl._rows(db, "SELECT id FROM panel_voice_clusters")
    return {r[0] for r in rows}


async def _queue(owner: str, panel_id: str, wav: str) -> bool:
    import voice_announce
    import voice_enrollment as ve
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        if panel_id not in await ve._device_panels(db):
            return False
        await voice_announce.enqueue_command(db, user_id=owner, panel_id=panel_id, trigger_type="voice_clip_play",
                                             command=dict(token=issue_token(panel_id, wav), ask=_say("ask")))
    return True


async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None,
               speaker_verified: Optional[bool] = None, panel_id: Optional[str] = None, allow_writes: bool = True):
    """"Who was that this morning?" / "that's Quinn": a ``DispatchResult`` or None. NEVER raises; any doubt is None (the turn goes on)."""
    try:
        if not enabled() or (channel or "") not in ("voice", "livekit") or not (panel_id or "").strip():
            return None
        panel, uid = panel_id.strip(), (user_id or "").strip()
        pend = _PENDING.get(panel)
        if pend is not None and (pend.expires <= time.monotonic() or pend.owner != uid):
            if pend.expires <= time.monotonic():
                _PENDING.pop(panel, None)
            pend = None                    # someone else's clip is not this speaker's to name
        q = classify(text, pending=pend is not None)
        if q is None or allow_writes is False:
            return None
        kind, name = q
        import enrollment_state as es
        import expert_dispatch as _xd
        import named_guest_enrol as nge

        if await es.audience_of(uid) != "adult" or speaker_verified is False or not await nge._verified_here(uid, speaker_verified, panel):
            # a guest, an unverified voice or a child hears nothing of this: the cue is ordinary speech to them and the turn goes on
            logger.info("NAMED_GUEST clips refused (not a verified adult member) kind=%s", kind)
            return None
        if kind == "skip":
            _PENDING.pop(panel, None)
            for k in [k for k, t in _TOKENS.items() if t.panel_id == panel]:
                _TOKENS.pop(k, None)           # a queued playback must not play the clip the member just cancelled
            reply = _say("skipped")
        elif kind == "name":
            reply = await _name(uid, panel, pend, name)
        else:
            reply = await _play(uid, panel)
        logger.info("NAMED_GUEST clips kind=%s", kind)
        return _xd.DispatchResult(domain="identity", reply=reply, intent=f"named_guest_clip_{kind}", tier=TIER)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("named_guest_clips: tier failed (non-fatal): %s", type(exc).__name__)
        return None


async def _play(uid: str, panel: str) -> str:
    import voice_clips

    clip = voice_clips.newest_unknown_clip(valid=await _live_clusters())
    if clip is None:
        return _say("none")
    if cut_wav(clip[0]) is None or not await _queue(uid, panel, clip[0]):
        return _say("no_audio")
    _PENDING[panel] = _Pending(uid, clip[1], time.monotonic() + PENDING_TTL_S)
    return _say("play")


async def _name(uid: str, panel: str, pend: Optional[_Pending], name: str) -> str:
    import named_guest_learning as ngl
    import voice_clips

    _PENDING.pop(panel, None)
    try:
        out = await ngl.name_unknown(pend.cluster_id, name, uid) if pend is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("named_guest_clips: naming failed: %s", type(exc).__name__)
        out = None
    if out is None:
        return _say("refused")
    voice_clips.relabel_cluster(pend.cluster_id, "guest", out["guest_id"])     # its clips are now that guest's (still on the clock)
    return _say("named", name=out["name"])
