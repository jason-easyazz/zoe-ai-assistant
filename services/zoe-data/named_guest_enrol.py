"""Enrolling a named guest: "Hey Zoe, learn Quinn's voice" from a VERIFIED adult member at the panel (``named_guests.py`` owns who a
guest is; runbook ``docs/knowledge/named-guests.md``).

  the ask      the spoken tier (``fast_tiers``, after ``voice_enrollment``): a member whose voice the server accepted, or who signed in
               with a fresh PIN (``speaker_verified`` not False), and who is positively an adult. A guest, an unverified voice, a child
               ASKER or a typed channel is refused with a spoken reason. The name of a household member is refused (they enrol themselves).
  the run      the SAME daemon enrolment run members use (``voice_enroll`` command, the member script with the guest's name), carrying
               ``guest.ticket``: a single-use, panel-bound, 4-minute server ticket that names the member and the guest.
  the store    ``POST /api/voice/guests/enroll`` -> ``store_profile``: refused without that ticket, so an unverified voice can never
               create, overwrite or re-enrol a guest (or a member). The guest is the member's OWN person (an existing one of that
               name is linked); the print is a ``learned_voiceprints`` row with ``channel='panel'``, replacing that member's old one.
               A child GUEST is enrolled like anyone (owner 2026-11-08) and marked child-safe at match time; a profile within the FAR-1 % bar of any member's own (the member
               said the phrases, not the guest). A name the member once forgot is released AFTER the store (an explicit
               re-teach, as the forget ledger asks).
Pinned by ``tests/test_named_guest_enrol.py``. Flag: ``ZOE_NAMED_GUESTS`` (the tier answers only when ``on``).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import speaker_models
from named_guests import CHANNEL, mode

logger = logging.getLogger(__name__)

TIER = "named_guests"
DIR = Path(__file__).with_name("lexicons_data")
TICKET_TTL_S = 240.0
_NAME_RX = re.compile(r"^[a-z][a-z'\-]{0,19}(?: [a-z][a-z'\-]{0,19}){0,2}$")


def _clean_name(name: str) -> str:
    """A spoken name as Zoe says it ("quinn o'neil" -> "Quinn O'neil"), or "" when it is not a plain name."""
    s = re.sub(r"\s+", " ", str(name or "").strip().lower())
    if not _NAME_RX.match(s):
        return ""
    return " ".join(w[:1].upper() + w[1:] for w in s.split(" "))


@dataclass
class _Ticket:
    owner_user_id: str
    panel_id: str
    name: str
    expires: float
    forgotten_at_issue: bool = False     # the member asked to learn a name they had forgotten: an explicit re-teach


_TICKETS: dict[str, _Ticket] = {}


def reset() -> None:
    _TICKETS.clear()


def issue_ticket(owner_user_id: str, panel_id: str, name: str, *, ttl_s: float = TICKET_TTL_S,
                 forgotten_at_issue: bool = False) -> str:
    for k in [k for k, t in _TICKETS.items() if t.expires <= time.monotonic() or t.panel_id == panel_id]:
        _TICKETS.pop(k, None)                       # one live ticket per panel; expired ones go
    tid = secrets.token_urlsafe(18)
    _TICKETS[tid] = _Ticket(owner_user_id, panel_id, name, time.monotonic() + ttl_s, forgotten_at_issue)
    return tid


def take_ticket(ticket: str, panel_id: str) -> Optional[_Ticket]:
    """Single use, this panel only, unexpired."""
    t = _TICKETS.pop(str(ticket or ""), None)
    if t is None or t.expires <= time.monotonic() or t.panel_id != (panel_id or ""):
        return None
    return t


class GuestError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


async def _is_member_name(db, name: str) -> bool:
    import contacts_store as cs

    key = cs.fold(name)
    async with db.execute("SELECT name FROM users") as cur:
        rows = await cur.fetchall()
    for (n,) in rows:
        f = cs.fold(n or "")
        if key and f and (f == key or f.split(" ")[0] == key):
            return True
    return False


async def _sounds_like_a_member(db, vec, model_id: str) -> bool:
    """Is this profile within the FAR-1 % bar (long clips) of any consented member profile of the model? Fails CLOSED (True)."""
    import numpy as np

    try:
        bar = speaker_models.RESNET34_THRESHOLDS["far1"]["long"]
        v = np.asarray(vec, dtype=np.float32).reshape(-1)
        v = v / max(float(np.linalg.norm(v)), 1e-12)
        async with db.execute("SELECT embedding_blob FROM speaker_profiles WHERE consent_at IS NOT NULL AND "
                              + speaker_models.MODEL_SQL + " = ?", (model_id,)) as cur:
            rows = await cur.fetchall()
        for (blob,) in rows:
            m = np.frombuffer(bytes(blob), dtype="<f4").astype(np.float32)
            if m.size == v.size and float(m @ v) / max(float(np.linalg.norm(m)), 1e-12) >= bar:
                return True
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("named_guests: member-likeness check failed (refusing the profile): %s", type(exc).__name__)
        return True


async def store_profile(payload: dict, caller: dict) -> dict:
    """``POST /api/voice/guests/enroll``: the panel's finished profile for the ticketed guest. Raises ``GuestError``."""
    if mode() == "off":
        raise GuestError("named guests are off", 404)
    t = take_ticket(str((payload or {}).get("ticket") or ""), str((caller or {}).get("panel_id") or ""))
    if t is None:
        raise GuestError("no live guest enrolment for this panel", 403)
    model_id = str((payload or {}).get("model_id") or "")
    if model_id != speaker_models.acting_model():
        raise GuestError("unknown model_id")
    try:
        vec = speaker_models.validate_embedding(base64.b64decode(str(payload.get("embedding_base64") or ""), validate=True), model_id)
    except Exception:  # noqa: BLE001
        vec = None
    if vec is None:
        raise GuestError("invalid embedding for this model")
    return await store_print(t.owner_user_id, t.name, vec, model_id, sample_count=(payload or {}).get("sample_count"),
                             forgotten_at_issue=t.forgotten_at_issue, ref="named_guest_enrol")


async def store_print(owner: str, name: str, vec, model_id: str, *, sample_count, forgotten_at_issue: bool, ref: str) -> dict:
    """THE one write of a named guest's panel print, shared by the panel's ticketed upload (``store_profile``) and the owner's
    backfill from saved clips (``named_guest_backfill``): the same refusals (a member's name, a profile within the FAR-1 % bar of a
    member's, a name forgotten since the ask began, a ledger outage), the member's OWN person, ``channel='panel'``. Raises
    ``GuestError``."""
    import memory_forgotten as mf

    try:
        forgotten_now = await mf.matches(owner, name, strict=True)
    except Exception as exc:  # noqa: BLE001 - the ledger cannot answer: nothing is stored
        raise GuestError("the forget ledger is unavailable; nothing was stored", 503) from exc
    if forgotten_now and not forgotten_at_issue:
        # The member forgot this name AFTER the enrolment began (review #2341): the pending upload must not undo that forget.
        raise GuestError("that name was forgotten after the enrolment began; nothing was stored", 409)
    import contacts_store as cs
    import omi_voices
    from db_compat import get_compat_db

    blob = vec.astype("<f4").tobytes()
    async with get_compat_db() as db:
        if await _is_member_name(db, name):
            raise GuestError("that is a household member", 409)
        if await _sounds_like_a_member(db, vec, model_id):
            # The member (not the guest) said the phrases: storing it would make the member's own voice "Quinn" and drop their claims.
            raise GuestError("that voice sounds like a household member", 409)
        try:
            person = await omi_voices._resolve_person(db, owner, None, name, ref)
        except omi_voices.VoiceError as exc:
            raise GuestError(str(exc), exc.status) from exc
        now = cs.now_iso()
        await db.execute("DELETE FROM learned_voiceprints WHERE owner_user_id = ? AND people_id = ? AND model_id = ? AND channel = ?",
                         (owner, person["id"], model_id, CHANNEL))
        await db.execute(
            "INSERT INTO learned_voiceprints (id, owner_user_id, people_id, model_id, channel, embedding, n_segments, n_recordings, "
            "status, learning_enabled, created_at, updated_at, last_confirmed_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'confirmed', 1, ?, ?, ?, ?)",
            (str(uuid.uuid4()), owner, person["id"], model_id, CHANNEL, blob,
             max(1, min(int(sample_count or 1), 16)), now, now, now, omi_voices.KEEP_UNTIL_FORGOTTEN))
        await db.commit()
    if forgotten_at_issue:
        try:
            # The member's explicit re-teach: they asked to learn a name they had forgotten (AFTER the store, as the ledger asks).
            await mf.release(owner, person["name"])
        except Exception as exc:  # noqa: BLE001 - the shield stays; the guest is then simply not matched
            logger.warning("named_guests: forget-ledger release failed: %s", type(exc).__name__)
    logger.info("NAMED_GUEST enrolled=1 linked_existing=%d", 0 if person.get("created") else 1)
    return dict(ok=True, guest_id=person["id"], display_name=person["name"], model_id=model_id)


# ── the spoken tier ───────────────────────────────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def lex(lang: str = "en") -> dict:
    path = DIR / f"named_guests_{(lang or 'en').split('-')[0].lower()}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if data.get("enabled") else {}


def classify(text: str) -> Optional[tuple[str, str]]:
    """``(kind, name)`` for "learn Quinn's voice" (``learn``) / "forget Quinn's voice" (``forget``), else None. Pure."""
    lx = lex("en")
    s = re.sub(r"[?!.,;:]+", " ", str(text or "").lower()).strip()
    s = re.sub(r"\s+", " ", s)
    for rx in lx.get("strip_lead") or ():
        s = re.sub("^(?:" + rx + ")", "", s).strip()
    if not s or len(s) > 80:
        return None
    for kind in ("forget", "learn", "forget_bare"):
        for rx in (lx.get("cues") or {}).get(kind) or ():
            m = re.fullmatch(rx, s)
            if m:
                name = _clean_name(m.group("name"))
                words = name.lower().split(" ")
                if name and not set(words) & set(lx.get("not_names") or ()) and "voice" not in words:
                    return kind, name
    return None


def _say(key: str, **slots) -> str:
    text = str((lex("en").get("say") or {}).get(key) or "")
    for k, v in slots.items():
        text = text.replace("{" + k + "}", str(v))
    return text


async def _own_guest(owner: str, name: str) -> Optional[str]:
    import contacts_store as cs

    key = cs.fold(name)
    import named_guest_memory

    for g in await named_guest_memory.list_for(owner):
        if cs.fold(g["name"]) == key:
            return g["guest_id"]
    return None


async def start_enrolment(owner: str, name: str, panel_id: str) -> bool:
    """Queue the panel's enrolment run for a GUEST, under a single-use ticket for this panel. True when queued. Never raises."""
    try:
        import voice_announce
        import voice_enrollment as ve
        from db_compat import get_compat_db

        clips = speaker_models.clips_needed()
        script = ve.script_for("en", name, clips)
        script.update((k, v.replace("{name}", name)) for k, v in (lex("en").get("script") or {}).items())
        import memory_forgotten as mf

        forgotten = await mf.matches(owner, name, strict=True)      # a ledger outage raises: nothing is queued
        async with get_compat_db() as db:
            if panel_id not in await ve._device_panels(db):
                return False
            ticket = issue_ticket(owner, panel_id, name, forgotten_at_issue=forgotten)
            await voice_announce.enqueue_command(
                db, user_id=owner, panel_id=panel_id, trigger_type="voice_enroll",
                command=dict(name=name, lang="en", script=script, clips=clips, speaker_model=speaker_models.speaker_mode(),
                             max_total_s=ve.ENROLL_MAX_S + 30, guest=dict(ticket=ticket)))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("named_guests: enrolment not queued: %s", type(exc).__name__)
        return False


async def _verified_here(uid: str, speaker_verified: Optional[bool], panel_id: str) -> bool:
    """POSITIVE proof the asker is this member at this panel: a voice the server accepted, or this member's FRESH PIN sign-in on
    this panel (review #2341: "no verdict" alone is never authorisation). Fails closed."""
    if speaker_verified is True:
        return True
    try:
        import biometric_scope

        return bool(uid) and await biometric_scope._fresh_panel_member(panel_id) == uid
    except Exception:  # noqa: BLE001
        return False


async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None,
               speaker_verified: Optional[bool] = None, panel_id: Optional[str] = None, allow_writes: bool = True):
    """"Learn Quinn's voice" / "forget Quinn's voice" / "forget Quinn" (only when Quinn is one of my guests): a ``DispatchResult`` or None. NEVER raises."""
    try:
        if mode() != "on":
            return None
        q = classify(text)
        if q is None:
            return None
        kind, name = q
        import enrollment_state as es

        uid = (user_id or "").strip()
        audience = await es.audience_of(uid)
        if kind == "forget_bare" and (audience != "adult" or await _own_guest(uid, name) is None):
            return None                        # not a guest of this member: the turn goes on as it always did
        if audience != "adult":
            reply = _say("guest_cannot" if audience in ("guest", "synthetic", "unknown") else "minor_cannot")
        elif speaker_verified is False:
            reply = _say("unverified")
        elif allow_writes is False:
            return None
        elif kind in ("forget", "forget_bare"):
            gid = await _own_guest(uid, name)
            if gid is None and kind == "forget_bare":
                return None                    # "forget <word>" that names none of my guests is not this tier's
            if gid is None:
                reply = _say("forget_none", name=name)
            elif (channel or "") in ("voice", "livekit") and not await _verified_here(uid, speaker_verified, (panel_id or "").strip()):
                reply = _say("unverified")            # at the panel, erasing a guest needs the same positive proof as learning one
            else:
                try:
                    import named_guest_memory

                    await named_guest_memory.erase(uid, gid)
                    reply = _say("forget_done", name=name)
                except Exception as exc:  # noqa: BLE001 - fail closed: say so, keep nothing pretending
                    logger.warning("named_guests: erase failed: %s", type(exc).__name__)
                    reply = _say("forget_fail", name=name)
        elif (channel or "") not in ("voice", "livekit") or not (panel_id or "").strip():
            reply = _say("no_panel")
        elif not await _verified_here(uid, speaker_verified, (panel_id or "").strip()):
            reply = _say("unverified")
        else:
            from db_compat import get_compat_db

            async with get_compat_db() as db:
                member = await _is_member_name(db, name)
            if member:
                reply = _say("is_member", name=name)
            else:
                reply = _say("start" if await start_enrolment(uid, name, (panel_id or "").strip()) else "start_fail", name=name)
        logger.info("NAMED_GUEST tier kind=%s audience=%s verified=%s", kind, audience, speaker_verified)
        import expert_dispatch as _xd

        return _xd.DispatchResult(domain="identity", reply=reply, intent=f"named_guest_{kind}", tier=TIER)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("named_guests: tier failed (non-fatal): %s", type(exc).__name__)
        return None
