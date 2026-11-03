"""Zoe-driven voice enrolment: she knows who has not enrolled, offers it by voice, and runs it from the panel - nobody runs a command.

The owner's requirement (verbatim): "if enrolment hasn't been done, zoe needs to know this, and prompt the user, if they decline that is
fine but they aren't going to be running a piece of code". Three pieces, one flag (``ZOE_ENROLL_OFFER`` = off (DEFAULT) | shadow | on):

  knowledge   ``enrollment_state`` - what each member has set up, what was offered, what they answered, and the offer POLICY (who, when,
              how often, never guests / synthetic ids / children unless ``ZOE_ENROLL_OFFER_KIDS``).
  the talk    this module - ONE deterministic tier in ``fast_tiers.resolve`` (every channel that has a turn gets it, after the
              distress / feedback / provenance tiers and before the self-model and the brain):
                * a spoken yes / no to a PENDING offer ("Want to?" -> "yes" / "not now"), through the existing confirm machinery
                  (``routers.voice_tts._pending_decision``) plus the language file for what it does not know;
                * a spoken request: "learn my voice" starts it; "forget my voice" removes it; "do you know my voice?" is answered from
                  the member's real state ("I don't know your voice yet. Want me to learn it?");
                * the close of a conversation ("thanks", "that's all" - NOT a goodbye, whose reply stays clean) schedules an offer.
              The other natural moment, a PIN step-up on the panel, calls ``schedule_offer_after_pin`` from ``routers/panel_auth.py``.
  the hands   the panel's voice daemon. ``start_enrollment`` queues a COMMAND on the daemon's existing server channel (the
              announcement queue, ``voice_announce.enqueue_command``); the daemon runs the enrolment itself, in-process, as an
              "enrollment mode" (``scripts/setup/zoe_voice_enroll_mode.py``), speaking Zoe's normal voice and uploading to
              ``POST /api/voice/enroll`` with consent=true because the person is performing it.

Offers are SPOKEN through the same queue (``trigger=voice_enroll_offer``; the daemon then opens a listening window so "yes" needs no
wake word). Every offer says what is kept - a voice signature, not recordings - and that it can be deleted. Nothing is offered
mid-task: the PIN offer waits ``ZOE_ENROLL_OFFER_PIN_DELAY_S`` (default 20) so the resumed command's reply lands first, and the daemon
never speaks over a live turn.

Forgetting reaches the box: "forget my voice" (and a profile deleted any other way, via ``forget_voice``) deletes the server row AND
queues a ``voice_profile_forget`` command for EVERY panel that holds a device token, so each daemon drops its copy at once instead of
waiting for the hour-long sync. It also starts the quiet period, so Zoe does not offer again the next morning.

Where the words live: NO English in this file. Cues, replies and the enrolment script are in ``lexicons_data/voice_enrollment_<lang>.json``;
a language with no enabled file contributes nothing. State changes are skipped on a dry run (``allow_writes=False``). Never raises.
Pinned by ``tests/test_voice_enrollment.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import enrollment_state as es
import speaker_models
import voice_announce
from typed_env import env_int

logger = logging.getLogger(__name__)

DIR = Path(__file__).with_name("lexicons_data")
LANGS = ("en", "es")
TIER = "voice_enrollment"
VOICE_CHANNELS = frozenset({"voice", "livekit"})
PENDING_TTL_S = 120.0
#: the offer after a conversation closes is queued this long after the closing turn, so the closing reply is already playing
CLOSE_OFFER_DELAY_S = 3.0
OFFER_TTL_S = 120
#: the daemon's whole enrolment mode is bounded by this (it also sends it back in the result)
ENROLL_MAX_S = 90
_MAX_CHARS_ANY = 140

_PENDING: dict = {}
_TASKS: set = set()


def reset() -> None:
    """Forget every pending offer (tests; a process restart does the same)."""
    _PENDING.clear()
    _TASKS.clear()


# ── language data ─────────────────────────────────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def lex(lang: str) -> dict:
    """``voice_enrollment_<lang>.json`` when it exists AND says ``enabled`` ({} otherwise: that language contributes nothing)."""
    code = (lang or "").strip().lower().split("-")[0]
    path = DIR / f"voice_enrollment_{code}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if code and path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if data.get("enabled") else {}


def languages() -> tuple:
    return tuple(code for code in LANGS if lex(code))


@lru_cache(maxsize=None)
def _cues(lang: str, kind: str) -> tuple:
    return tuple(re.compile(c) for c in (lex(lang).get("cues") or {}).get(kind) or ())


def _frame(text: str, lx: dict) -> str:
    import self_model

    return self_model.strip_frame(self_model.norm(text), lx)


@dataclass(frozen=True)
class Question:
    kind: str       # forget | learn | know_voice | closer
    lang: str


#: forget first (the most consequential reading wins an ambiguous utterance), the closer last (the cheapest, most common turn)
KINDS = ("forget", "learn", "know_voice", "closer")


def classify(text: str) -> Optional[Question]:
    """The enrolment utterance ``text`` is, or None. Pure; no IO. Whole-utterance match over the normalised, frame-stripped text."""
    t = (text or "").strip()
    if not t or t.count("\n") > 1:
        return None
    for lang in languages():
        lx = lex(lang)
        if len(t) > int(lx.get("max_chars") or 100):
            continue
        s = _frame(t, lx)
        if not s:
            continue
        for kind in KINDS:
            for rx in _cues(lang, kind):
                if rx.fullmatch(s):
                    return Question(kind, lang)
    return None


def decision(text: str, lang: str = "en") -> Optional[str]:
    """``"yes"`` | ``"no"`` | None (not an answer) for the reply to an offer.

    The repo's own confirm machinery first (``routers.voice_tts._pending_decision``: a complete affirmative statement, never a question,
    never a yes that carries a "but"), then this language's yes / no list for what that English machinery does not know ("not now",
    "maybe later", every other language). A "drop" from the machinery (the person moved on) is not an answer."""
    t = (text or "").strip()
    if not t or len(t) > _MAX_CHARS_ANY or "?" in t:        # a question ("sure?", "yes?") is never an answer
        return None
    try:
        from routers.voice_tts import _pending_decision

        d = _pending_decision(t)
    except Exception:  # noqa: BLE001 - the machinery is optional here; the language file still answers
        d = None
    if d in ("yes", "no"):
        return d
    if d == "drop":
        return None
    lx = lex(lang) or lex("en")
    s = _frame(t, lx) if lx else ""
    if not s:
        return None
    for kind in ("no", "yes"):
        for rx in _cues(lx.get("lang", lang), kind):
            if rx.fullmatch(s):
                return kind
    return None


# ── pending offers (in process, two minutes: an unanswered offer is simply over) ──────────────────────────────────────────

@dataclass
class _Pending:
    lang: str
    panel_id: str
    expires: float


def arm(user_id: str, lang: str, panel_id: str = "", *, ttl_s: float = PENDING_TTL_S) -> None:
    _PENDING[(user_id or "").strip()] = _Pending(lang=lang, panel_id=panel_id or "", expires=time.monotonic() + ttl_s)


def _pending(user_id: str) -> Optional[_Pending]:
    uid = (user_id or "").strip()
    p = _PENDING.get(uid)
    if p is not None and p.expires <= time.monotonic():
        _PENDING.pop(uid, None)
        return None
    return p


def pending_for(user_id: str) -> bool:
    return _pending(user_id) is not None


# ── the daemon channel ────────────────────────────────────────────────────────────────────────────────────────────────────

async def _display_name(user_id: str) -> str:
    try:
        import self_model

        return await self_model._own_name(user_id)
    except Exception:  # noqa: BLE001
        return ""


def script_for(lang: str, name: str, clips: int = 3) -> dict:
    """The enrolment script the daemon speaks, in the member's language: every sentence, the phrases to say, the words that cancel.
    ``{name}`` is filled here; ``{n}`` and ``{phrase}`` are the daemon's.

    ``clips`` is how many phrases are asked for: 3 (the legacy script, byte for byte) or - for ResNet34, the only model
    now - 5: the lexicon's ``phrases`` plus its ``phrases_extra``, with the spoken intro switched to ``intro_many``."""
    lx = lex(lang) or lex("en")
    out = dict((k, (v.replace("{name}", name or "") if isinstance(v, str) else v)) for k, v in (lx.get("script") or dict()).items())
    phrases = list(lx.get("phrases") or [])
    if clips > len(phrases):
        phrases += list(lx.get("phrases_extra") or [])
        phrases = phrases[:clips]
        many = str(out.get("intro_many") or "")
        if many:
            out["intro"] = many.replace("{count}", str(len(phrases)))
    out["phrases"] = phrases
    return out


async def start_enrollment(user_id: str, panel_id: str, lang: str) -> bool:
    """Queue the enrol-yourself command for THE panel the member spoke at. True when queued. Never raises.

    The row is panel-scoped (``voice_announce.PANEL_SCOPED_TRIGGERS``): only the daemon whose device token IS this panel can claim it, so
    no other panel speaks the member's name and enrols whoever stands there next. That needs a panel that holds a live device token; a
    typed channel, a browser id or a revoked panel gets no command (the caller says it could not start)."""
    try:
        from db_compat import get_compat_db

        if not (panel_id or "").strip():
            return False
        name = await _display_name(user_id)
        async with get_compat_db() as db:
            if panel_id not in await _device_panels(db):
                logger.info("voice_enrollment: no enrol command - %r holds no live device token", "panel")
                return False
            command = dict(name=name, lang=lang, max_total_s=ENROLL_MAX_S)
            # Ask for ResNet34's clip count and name the mode, so the panel builds the right profile.
            # The daemon trusts its own ZOE_SPEAKER_MODEL for WHICH model; the server's word is the clip count and the words.
            clips = speaker_models.clips_needed()
            command.update(script=script_for(lang, name, clips), clips=clips, speaker_model=speaker_models.speaker_mode(),
                           max_total_s=ENROLL_MAX_S + 30)
            await voice_announce.enqueue_command(
                db, user_id=user_id, panel_id=panel_id or None, trigger_type="voice_enroll", command=command,
            )
        # The server-side half of the command: ONLY this member may be enrolled at THIS panel for the command's life (queue
        # TTL + the run + slack). ``/api/voice/enroll`` checks it instead of trusting the daemon's payload (2026-10-19).
        import biometric_scope

        biometric_scope.grant_enrolment(
            panel_id, user_id, kind="voice",
            ttl_s=voice_announce.COMMAND_TTL_S + float(command.get("max_total_s") or ENROLL_MAX_S) + 60.0)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice_enrollment: could not queue the enrol command: %s", type(exc).__name__)
        return False


async def _device_panels(db) -> list:
    async with db.execute("SELECT DISTINCT panel_id FROM device_tokens WHERE revoked = 0 AND panel_id IS NOT NULL") as cur:
        rows = await cur.fetchall()
    return [str(r[0]) for r in rows if r[0]]


async def forget_voice(user_id: str) -> Optional[int]:
    """Delete the member's voice signature on the server AND queue a purge for every panel that holds a copy. Returns how many rows
    were deleted, or None when the delete itself failed (nothing changed). The panel queueing is best effort: a panel that misses
    it still drops the profile at its next sync (the row is gone from ``/profiles/sync``)."""
    from db_compat import get_compat_db

    uid = (user_id or "").strip()
    try:
        async with get_compat_db() as db:
            async with db.execute("DELETE FROM speaker_profiles WHERE user_id = ?", (uid,)) as cur:
                deleted = int(getattr(cur, "rowcount", 0) or 0)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice_enrollment: forget delete failed: %s", type(exc).__name__)
        return None
    await push_forget_to_panels(uid)
    return deleted


async def push_forget_to_panels(user_id: str) -> int:
    """One ``voice_profile_forget`` command per panel holding a device token. Returns how many were queued. Never raises."""
    from db_compat import get_compat_db

    # A deleted voice signature also ends any open panel voice session of that member (ZOE_VOICE_SESSION; a no-op
    # when the flag is off). Both delete paths (the settings route and "forget my voice") come through here.
    try:
        import voice_session

        await voice_session.revoke_member(user_id, "voice_profile_deleted")
    except Exception:  # noqa: BLE001 - the purge below must still be queued
        pass

    queued = 0
    try:
        async with get_compat_db() as db:
            panels = await _device_panels(db)
            for panel in panels:
                try:
                    await voice_announce.enqueue_command(
                        db, user_id=user_id, panel_id=panel, trigger_type="voice_profile_forget", command={})
                    queued += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("voice_enrollment: forget command not queued for one panel: %s", type(exc).__name__)
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice_enrollment: forget push failed: %s", type(exc).__name__)
    return queued


# ── the offer ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _in_quiet_hours(now) -> bool:
    try:
        from proactive.engine import _is_in_quiet_hours

        return bool(_is_in_quiet_hours(now))
    except Exception:  # noqa: BLE001 - the engine is optional here
        return False


async def maybe_offer(user_id: str, moment: str, panel_id: str = "", lang: str = "en") -> str:
    """Decide, and when allowed make, ONE spoken offer. Returns the outcome label (the policy's reason, or ``offered`` / ``shadow`` /
    ``claim_lost`` / ``not_queued``). Never raises. Shadow decides and logs and queues nothing."""
    try:
        flag = es.mode()
        if flag == "off":
            return "flag_off"
        uid = (user_id or "").strip()
        now = es._now()
        audience = await es.audience_of(uid)
        state = await es.get_state(uid)
        verdict = es.decide_offer(state, audience=audience, moment=moment, now=now, flag=flag, kids=es.kids_allowed(),
                                  quiet_hours=_in_quiet_hours(now))
        if not verdict.offer:
            logger.info("ENROLL_OFFER mode=%s moment=%s audience=%s outcome=%s", flag, moment, audience, verdict.reason)
            return verdict.reason
        if flag == "shadow":
            logger.info("ENROLL_OFFER mode=shadow moment=%s audience=%s outcome=would_offer", moment, audience)
            return "shadow"
        if not panel_id:
            logger.info("ENROLL_OFFER mode=on moment=%s audience=%s outcome=no_panel", moment, audience)
            return "no_panel"
        lx = lex(lang) or lex("en")
        if not lx:
            return "no_language"
        if not await es.claim_offer(uid, state, now=now):
            logger.info("ENROLL_OFFER mode=on moment=%s audience=%s outcome=claim_lost", moment, audience)
            return "claim_lost"
        from db_compat import get_compat_db

        text = lx["say"]["offer_pin" if moment == "pin_stepup" else "offer_close"]
        try:
            async with get_compat_db() as db:
                await voice_announce.enqueue_announcement(
                    db, user_id=uid, panel_id=panel_id, message=text, trigger_type="voice_enroll_offer", ttl_s=OFFER_TTL_S)
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice_enrollment: offer not queued: %s", type(exc).__name__)
            # Nothing was offered, so the slot taken above must not stand: it would block tomorrow's offer, count as an ignored offer
            # (three of those are a 60-day "no"), and mark a question pending that was never asked.
            await es.release_offer(uid, state, now=now)
            return "not_queued"
        # NOT armed here: the answer is only meaningful once the offer was HEARD. The daemon's played-ACK
        # (``POST /api/voice/announcements/{id}/played``) calls ``on_announcement_played``, which arms the yes/no.
        logger.info("ENROLL_OFFER mode=on moment=%s audience=%s outcome=offered", moment, audience)
        return "offered"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - an offer is optional; the turn / the PIN is not
        logger.warning("voice_enrollment: offer failed (non-fatal): %s", type(exc).__name__)
        return "error"


def _offer_lang(message: str) -> str:
    """The language whose offer sentence this was (the played row holds the text, not the language); English when none matches."""
    for code in languages():
        say = lex(code).get("say") or {}
        if message in (say.get("offer_pin"), say.get("offer_close")):
            return code
    return "en"


async def on_announcement_played(announcement_id: str) -> bool:
    """The daemon reports an announcement PLAYED. When it was an enrolment OFFER, the member has now heard "Want to?": arm the yes/no
    (two minutes). Before this, an unrelated "yes" cannot be taken as consent to an offer nobody heard. True when armed. Never raises."""
    try:
        if es.mode() != "on":
            return False
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            async with db.execute("SELECT user_id, panel_id, trigger_type, message FROM voice_announcements WHERE id = ?",
                                  (announcement_id,)) as cur:
                row = await cur.fetchone()
        if row is None or (row[2] or "") != "voice_enroll_offer":
            return False
        arm(str(row[0]), _offer_lang(str(row[3] or "")), str(row[1] or ""))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice_enrollment: played hook failed (non-fatal): %s", type(exc).__name__)
        return False


def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _later(delay_s: float, user_id: str, moment: str, panel_id: str, lang: str) -> None:
    try:
        await asyncio.sleep(max(0.0, delay_s))
        await maybe_offer(user_id, moment, panel_id, lang)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice_enrollment: delayed offer failed: %s", type(exc).__name__)


def schedule_offer_after_pin(user_id: str, panel_id: str) -> None:
    """Called by ``routers/panel_auth.py`` after a CORRECT PIN: queue an offer a little later (the resumed command's reply goes first).
    Returns at once; with the flag off it does nothing at all. Never raises."""
    try:
        if es.mode() == "off" or not (user_id or "").strip():
            return
        _spawn(_later(float(env_int("ZOE_ENROLL_OFFER_PIN_DELAY_S", 20)), user_id, "pin_stepup", panel_id, "en"))
    except Exception:  # noqa: BLE001
        pass


# ── the tier ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _say(lx: dict, key: str, **slots) -> str:
    text = (lx.get("say") or {}).get(key, "")
    for k, v in slots.items():
        text = text.replace("{" + k + "}", str(v))
    return text


async def _answer_offer(uid: str, answer: str, p: _Pending, panel_id: str, speaker_verified: Optional[bool], dry: bool) -> Optional[str]:
    lx = lex(p.lang) or lex("en")
    if dry or speaker_verified is False:
        return None
    if answer == "no":
        await es.record_answer(uid, False)
        return _say(lx, "declined")
    audience = await es.audience_of(uid)
    if not es.can_self_request(audience, kids=es.kids_allowed()).offer:
        return _say(lx, "learn_guest" if audience in ("guest", "synthetic", "unknown") else "learn_minor")
    await es.record_answer(uid, True)
    return _say(lx, "start" if await start_enrollment(uid, panel_id or p.panel_id, p.lang) else "start_fail")


async def _handle(q: Question, uid: str, *, channel: Optional[str], speaker_verified: Optional[bool], panel_id: str, dry: bool) -> Optional[str]:
    lx = lex(q.lang)
    audience = await es.audience_of(uid)
    guest = audience in ("guest", "synthetic")
    if q.kind == "forget":
        if guest or audience == "unknown":
            return _say(lx, "forget_guest")
        if speaker_verified is False:
            return _say(lx, "forget_unverified")
        if dry:
            return None
        deleted = await forget_voice(uid)
        if deleted is None:
            return _say(lx, "forget_fail")
        await es.record_answer(uid, False)         # asked to forget: do not offer again tomorrow
        _PENDING.pop(uid, None)
        return _say(lx, "forget_done" if deleted else "forget_none")
    if q.kind == "know_voice":
        if guest or audience == "unknown":
            return _say(lx, "know_guest")
        if speaker_verified is False:
            return _say(lx, "know_unknown")
        state = await es.get_state(uid)
        if state.voice_enrolled is None:
            return _say(lx, "know_unknown")
        if state.voice_enrolled or state.voice_upgrade:
            return _say(lx, "know_yes", n=max(1, state.voice_samples))
        if audience == "minor" and not es.kids_allowed():
            return _say(lx, "know_no_minor")
        if state.declined_now(es._now()):
            return _say(lx, "know_no_declined")
        if not dry and (channel or "") in VOICE_CHANNELS and panel_id:
            arm(uid, q.lang, panel_id)
        return _say(lx, "know_no")
    if q.kind == "learn":
        if not es.can_self_request(audience, kids=es.kids_allowed()).offer:
            return _say(lx, "learn_guest" if audience in ("guest", "synthetic", "unknown") else "learn_minor")
        if speaker_verified is False:
            return _say(lx, "learn_unverified")
        state = await es.get_state(uid)
        if state.voice_enrolled is None:
            return _say(lx, "know_unknown")
        if state.voice_enrolled:
            return _say(lx, "already")
        if (channel or "") not in VOICE_CHANNELS or not panel_id:
            return _say(lx, "learn_no_panel")
        if dry:
            return None
        await es.record_answer(uid, True)
        return _say(lx, "start" if await start_enrollment(uid, panel_id, q.lang) else "start_fail")
    return None


async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None,
               speaker_verified: Optional[bool] = None, panel_id: Optional[str] = None, allow_writes: bool = True):
    """The enrolment tier: a ``DispatchResult`` for a spoken yes/no to a pending offer or a recognised request, else None. The close of
    a conversation may SCHEDULE an offer (a side effect; the turn's own reply is untouched). ``shadow`` logs what it would do and
    returns None; ``off`` returns before any work. NEVER raises."""
    try:
        m = es.mode()
        if m == "off":
            return None
        uid = (user_id or "").strip()
        t = (text or "").strip()
        if not uid or not t or len(t) > _MAX_CHARS_ANY:
            return None
        pend = _pending(uid)
        pid = (panel_id or "").strip()
        dry = allow_writes is False
        reply: Optional[str] = None
        kind = "-"
        if pend is not None and m == "on":
            answer = decision(t, pend.lang)
            if answer is not None:
                _PENDING.pop(uid, None)
                kind = "answer_" + answer
                reply = await _answer_offer(uid, answer, pend, pid, speaker_verified, dry)
            else:
                _PENDING.pop(uid, None)          # anything else is not an answer: the offer is over, and the words go on
        q = None if reply is not None else classify(t)
        if q is not None and q.kind != "closer":
            kind = q.kind
            if m == "shadow":
                logger.info("ENROLL_OFFER mode=shadow kind=%s lang=%s would_answer=1", q.kind, q.lang)
                return None
            reply = await _handle(q, uid, channel=channel, speaker_verified=speaker_verified, panel_id=pid, dry=dry)
        elif q is not None and reply is None:
            if (channel or "") == "voice" and pid and not dry:
                _spawn(_later(CLOSE_OFFER_DELAY_S, uid, "conversation_end", pid, q.lang))
            return None
        if not reply:
            return None
        try:
            import memory_provenance as mp

            mp.note_direct_reply(uid, TIER, session_id)
        except Exception:  # noqa: BLE001
            pass
        logger.info("ENROLL_OFFER mode=on kind=%s answered=1 chars=%d", kind, len(reply))
        import expert_dispatch as _xd

        return _xd.DispatchResult(domain="identity", reply=reply, intent=f"voice_enroll_{kind}", tier=TIER)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a turn is never broken by this tier
        logger.warning("voice_enrollment: tier failed (non-fatal): %s", type(exc).__name__)
        return None
