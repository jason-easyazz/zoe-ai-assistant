"""Panel voice session: "an identified voice is a password" (flag-dark, ``ZOE_VOICE_SESSION``).

Owner requirement (2026-10-13): a member the speaker gate has VERIFIED gets their own private data on the panel they are
talking to - calendar, lists, reminders, ordinary writes, memory recall - with no PIN, for as long as they keep talking.
Record + operator steps + security model: ``docs/knowledge/voice-verified-session.md``.

What a session is. One per panel, in process memory (never persisted, never synced): ``member_id``, when it opened,
and when the member was last VERIFIED. It opens on a spoken turn whose panel claim the SERVER accepted (its threshold, a
consent row, ``speaker_verified is True``) and whose speaker model meets the minimum, and it is renewed by every
verified turn of the same member. It CLOSES:

* when an unverified voice speaks on that panel (``speaker_verified is False``), or a device turn carries no verdict at
  all (the gate is off, in shadow or errored: unknown is not the member);
* when a DIFFERENT member's verified voice speaks (that member's own session replaces it);
* ``ZOE_VOICE_SESSION_TTL_S`` (default 120) after the last verified turn;
* on a lock / close phrase (``lexicons_data/<lang>.json`` ``voice_session``);
* when the member's voice consent is revoked or their voice profile is deleted (``close_member``).

What it never does: grant admin (it confers the MEMBER scope a PIN sign-in confers, nothing more); override a fresh PIN
sign-in of a different member (a verified voice of B on a panel A signed into with a PIN gets neither A's data nor a
session); skip the STEP-UP tier (``lexicons_data/voice_session_policy.json`` + the lexicon's ``step_up`` classes):
payments, security / admin / settings, biometrics, forgetting a person, anything for another member still ask for the
PIN, reusing the existing panel PIN challenge.

Model gate. Resemblyzer misses the owner ~11 % of the time at FAR 1 %; WeSpeaker ResNet34 ~0.3 %. A session opens only
when the ACTING speaker model (``ZOE_SPEAKER_MODEL``, ``speaker_models.acting_model`` when PR #2024 is present) is
ResNet34 AND the panel's claim names that model; ``ZOE_VOICE_SESSION_ALLOW_RESEMBLYZER=1`` is the owner's explicit,
loudly documented override. With the flag ON and the gate unmet, a verified claim still attributes the turn (memory
provenance) but no longer unlocks private data by itself.

Audit: every open / renew / close / refusal is one ``zoe.voice_session`` log line with the member id, the panel and the
reason - never audio, never a score.

Flag OFF (default) = nothing here runs and ``routers/voice_tts.voice_command`` is byte-for-byte today's behaviour.
Stdlib + ``lexicons`` + ``typed_env`` at import time (hot voice path; nothing heavy, no DB at import).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import lexicons
from typed_env import env_bool, env_int, env_str

logger = logging.getLogger(__name__)
audit = logging.getLogger("zoe.voice_session")

LEGACY_MODEL_ID = "resemblyzer"
#: mirrors ``speaker_models.RESNET34_MODEL_ID`` (PR #2024); used only when that module is absent.
RESNET34_MODEL_ID = "wespeaker_resnet34+i8qdq-u8s8+c1"

_POLICY_PATH = Path(__file__).with_name("lexicons_data") / "voice_session_policy.json"
_SENTINELS = frozenset({"", "guest", "voice-guest", "voice-daemon", "family-admin"})

# Monkeypatchable clock (tests drive TTL expiry without sleeping).
_clock = time.monotonic


def enabled() -> bool:
    """``ZOE_VOICE_SESSION`` (default OFF), read per call."""
    return env_bool("ZOE_VOICE_SESSION", default=False)


def ttl_s() -> int:
    """Seconds a session stays open after the LAST verified turn (``ZOE_VOICE_SESSION_TTL_S``, default 120, 10..900)."""
    return max(10, min(900, env_int("ZOE_VOICE_SESSION_TTL_S", 120)))


# -- the model gate --------------------------------------------------------------------------------------------------

def acting_model() -> str:
    """The speaker model whose claims the server honours: ``speaker_models.acting_model()`` when PR #2024's module is
    present, else ``ZOE_SPEAKER_MODEL`` read the same way (``resnet34`` -> ResNet34, anything else -> Resemblyzer)."""
    try:
        import speaker_models  # type: ignore[import-not-found]

        return str(speaker_models.acting_model())
    except Exception:
        return RESNET34_MODEL_ID if env_str("ZOE_SPEAKER_MODEL", "resemblyzer").lower() == "resnet34" else LEGACY_MODEL_ID


def claim_model(payload: Optional[dict]) -> str:
    """The embedding space the panel computed its claim in: ``speaker.model_id`` / ``voice_model_id``; a claim that
    names none is the legacy Resemblyzer one (every daemon that predates ``ZOE_SPEAKER_MODEL``)."""
    payload = payload or {}
    block = payload.get("speaker")
    raw = (block.get("model_id") if isinstance(block, dict) else None) or payload.get("voice_model_id")
    s = raw.strip() if isinstance(raw, str) else ""
    return s or LEGACY_MODEL_ID


def model_gate(payload: Optional[dict]) -> tuple[bool, str]:
    """``(ok, reason)``: may a verified claim in this turn open / renew a session?

    The claim must be in the ACTING model's space (a Resemblyzer score on a server acting on ResNet34 is meaningless),
    and that model must be ResNet34 unless the owner set ``ZOE_VOICE_SESSION_ALLOW_RESEMBLYZER=1``."""
    claimed, acting = claim_model(payload), acting_model()
    if claimed != acting:
        return False, "claim_not_acting_model"
    if acting == RESNET34_MODEL_ID:
        return True, "resnet34"
    if acting == LEGACY_MODEL_ID and env_bool("ZOE_VOICE_SESSION_ALLOW_RESEMBLYZER", default=False):
        return True, "resemblyzer_allowed"
    return False, "model_below_minimum"


# -- language data ----------------------------------------------------------------------------------------------------

@lru_cache(maxsize=None)
def policy() -> dict:
    """``lexicons_data/voice_session_policy.json`` (``{}`` when unreadable: the step-up then falls back to phrases)."""
    try:
        return json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - a broken data file is loud, never fatal
        logger.warning("voice_session policy unreadable (%s); intent/skybridge step-up tiers are empty", exc)
        return {}


def _section(lang: str) -> dict:
    sec = lexicons.load(lang).get("voice_session")
    return sec if isinstance(sec, dict) else {}


def _langs(text: str) -> list[str]:
    first = lexicons.detect(text or "")
    order = [first] + [c for c in lexicons.LANGS if c != first]
    return [c for c in order if _section(c)]


def text_for(key: str, *, lang: str = "en", default: str = "") -> Any:
    """A panel / spoken text from the lexicon (``lang`` first, then English); ``default`` when neither has it."""
    for code in (lang, "en"):
        val = _section(code).get(key)
        if val not in (None, ""):
            return val
    return default


def _norm(text: str) -> str:
    t = (text or "").replace("’", "'").strip().lower()
    t = re.sub(r"[\s]+", " ", t)
    return t.strip(" .,!?;:")


@lru_cache(maxsize=None)
def _compiled(lang: str, key: str, whole: bool) -> tuple:
    out = []
    for frag in _section(lang).get(key) or []:
        try:
            out.append(re.compile(rf"(?:{frag})" if whole else rf"(?<!\w)(?:{frag})(?!\w)", re.IGNORECASE))
        except re.error:
            continue
    return tuple(out)


def _strip_fillers(t: str, lang: str) -> str:
    prev = None
    while prev != t:
        prev = t
        for rx in _compiled(lang, "strip", True):
            t = re.sub(rf"^(?:{rx.pattern})\b[\s,]*", "", t, flags=re.IGNORECASE)
            t = re.sub(rf"[\s,]*\b(?:{rx.pattern})$", "", t, flags=re.IGNORECASE)
        t = t.strip(" .,!?;:")
    return t


def phrase_kind(text: str) -> Optional[str]:
    """``"lock"`` | ``"close"`` | None. The WHOLE utterance must be the phrase (fillers at either end removed), so a
    request that merely contains the words ("lock the front door", "that's all the milk") is neither."""
    base = _norm(text)
    if not base:
        return None
    for lang in _langs(base):
        t = _strip_fillers(base, lang)
        if not t:
            continue
        for kind in ("lock", "close"):
            if any(rx.fullmatch(t) for rx in _compiled(lang, kind, True)):
                return kind
    return None


# -- the step-up tier --------------------------------------------------------------------------------------------------

def step_up_reason(
    text: str,
    *,
    intent_name: Optional[str] = None,
    skybridge: Optional[tuple] = None,
    other_member_names: tuple = (),
) -> Optional[str]:
    """Why this turn still needs the PIN inside a voice session, or None. Data-driven (see module docstring)."""
    pol = policy()
    if intent_name and intent_name in set(pol.get("step_up_intents") or ()):
        return f"intent:{intent_name}"
    if skybridge:
        dom, act = str(skybridge[0] or ""), str(skybridge[1] or "")
        tiers = set(pol.get("step_up_skybridge") or ())
        if f"{dom}:{act}" in tiers or f"{dom}:*" in tiers:
            return f"skybridge:{dom}:{act}"
    t = _norm(text)
    if not t:
        return None
    classes = list(pol.get("step_up_phrase_classes") or ())
    for lang in _langs(t):
        groups = _section(lang).get("step_up") or {}
        for cls in classes:
            for frag in groups.get(cls) or []:
                try:
                    if re.search(rf"(?<!\w)(?:{frag})(?!\w)", t, re.IGNORECASE):
                        return f"phrase:{cls}"
                except re.error:
                    continue
    if pol.get("other_member", True) and other_member_names:
        for lang in _langs(t):
            for tpl in _section(lang).get("other_member") or []:
                for name in other_member_names:
                    try:
                        if re.search(rf"(?<!\w)(?:{tpl.replace('{name}', re.escape(name))})(?!\w)", t, re.IGNORECASE):
                            return "other_member"
                    except re.error:
                        continue
    return None


def private_read_kind(*, intent_name: Optional[str] = None, skybridge: Optional[tuple] = None) -> Optional[str]:
    """``calendar`` | ``lists`` | ``reminders`` when the turn READS a member's private domain (the card kinds)."""
    pol = policy()
    if intent_name:
        kind = (pol.get("private_read_intents") or {}).get(intent_name)
        if kind:
            return str(kind)
    if skybridge:
        dom, act = str(skybridge[0] or ""), str(skybridge[1] or "")
        if act in set((pol.get("private_read_skybridge") or {}).get(dom) or ()):
            return dom
    return None


_NAMES_CACHE: dict[str, Any] = {"at": -1e9, "rows": ()}


async def other_member_names(member_id: str) -> tuple:
    """Lower-cased display names (and first names) of every OTHER member in ``users``; cached 60 s. A failed read is
    ``()`` - the phrase and intent tiers still apply; this tier then cannot fire (documented limit)."""
    now = _clock()
    if now - float(_NAMES_CACHE["at"]) > 60.0:
        rows: list = []
        try:
            from db_pool import get_db_ctx

            async with get_db_ctx() as db:
                rows = [(str(r["id"]), str(r["name"] or "")) for r in await db.fetch("SELECT id, name FROM users")]
        except Exception as exc:
            logger.debug("voice_session member names unavailable: %s", exc)
            rows = []
        _NAMES_CACHE.update(at=now, rows=tuple(rows))
    out: set = set()
    for uid, name in _NAMES_CACHE["rows"]:
        if uid == member_id or uid in _SENTINELS:
            continue
        for cand in (name.strip().lower(), name.strip().lower().split(" ")[0] if name.strip() else ""):
            if len(cand) >= 2:
                out.add(cand)
    return tuple(sorted(out))


# -- the session state machine ----------------------------------------------------------------------------------------

@dataclass
class _Session:
    member_id: str
    panel_id: str
    opened_at: float
    last_verified_at: float
    generation: int


@dataclass
class TurnDecision:
    """What one spoken turn did to its panel's session, and the member scope the turn gets.

    ``scope_user``: the member whose private data this turn may use (None = guest unless the turn already named its
    speaker another way). ``by_session``: that scope came from the VOICE (the step-up tier applies). ``lock``: the turn
    WAS a lock phrase - the caller answers it and stops. ``events``: what to publish to the panel.
    """

    scope_user: Optional[str] = None
    by_session: bool = False
    lock: bool = False
    events: list = field(default_factory=list)


_SESSIONS: dict[str, _Session] = {}
_GEN = [0]


def reset() -> None:
    """Tests only: forget every session."""
    _SESSIONS.clear()
    _NAMES_CACHE.update(at=-1e9, rows=())


def _audit(event: str, member: str, panel: str, reason: str, **extra: Any) -> None:
    tail = "".join(f" {k}={v}" for k, v in extra.items())
    audit.info("VOICE_SESSION event=%s member=%s panel=%s reason=%s%s", event, member or "-", panel or "-", reason, tail)


def _close(panel_id: str, reason: str) -> Optional[dict]:
    s = _SESSIONS.pop(panel_id, None)
    if s is None:
        return None
    _audit("close", s.member_id, panel_id, reason)
    return {"panel_id": panel_id, "state": "closed", "member_id": s.member_id, "reason": reason}


def _expired(s: _Session, now: float) -> bool:
    return now - s.last_verified_at >= ttl_s()


def _expire(panel_id: str, now: float) -> Optional[dict]:
    s = _SESSIONS.get(panel_id)
    if s is not None and _expired(s, now):
        return _close(panel_id, "timeout")
    return None


def _open_or_renew(panel_id: str, member: str, now: float, model_reason: str) -> dict:
    s = _SESSIONS.get(panel_id)
    _GEN[0] += 1
    if s is not None and s.member_id == member:
        s.last_verified_at, s.generation = now, _GEN[0]
        _audit("renew", member, panel_id, "verified_voice", ttl_s=ttl_s())
        state = "renewed"
    else:
        s = _Session(member_id=member, panel_id=panel_id, opened_at=now, last_verified_at=now, generation=_GEN[0])
        _SESSIONS[panel_id] = s
        _audit("open", member, panel_id, "verified_voice", model=model_reason, ttl_s=ttl_s())
        state = "open"
    _arm_timer(panel_id, s.generation)
    return {"panel_id": panel_id, "state": state, "member_id": member, "ttl_s": ttl_s()}


def _arm_timer(panel_id: str, generation: int) -> None:
    """Close + tell the panel at TTL even when nobody speaks again (lazy expiry alone would leave the badge up)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.call_later(ttl_s() + 0.25, _on_timer, panel_id, generation)


def _on_timer(panel_id: str, generation: int) -> None:
    s = _SESSIONS.get(panel_id)
    if s is None or s.generation != generation:
        return
    ev = _expire(panel_id, _clock())
    if ev is not None:
        try:
            asyncio.ensure_future(publish([ev]))
        except Exception:  # pragma: no cover
            pass


def seconds_left(panel_id: str) -> Optional[float]:
    """Seconds until the open voice session on ``panel_id`` lapses (it renews on each verified turn), or None when none is open."""
    if not enabled():
        return None
    now = _clock()
    _expire(panel_id, now)
    s = _SESSIONS.get(panel_id)
    return max(0.0, ttl_s() - (now - s.last_verified_at)) if s else None


def active_member(panel_id: str) -> Optional[str]:
    """The member whose voice session is open on ``panel_id`` now (expiring it lazily), else None."""
    if not enabled():
        return None
    _expire(panel_id, _clock())
    s = _SESSIONS.get(panel_id)
    return s.member_id if s else None


def observe_turn(
    *,
    panel_id: str,
    caller_source: Optional[str],
    verdict: Optional[bool],
    claimed: Optional[str],
    pin_user: Optional[str],
    model_ok: bool,
    model_reason: str,
    text: str,
) -> Optional[TurnDecision]:
    """Apply one spoken turn to its panel's session and decide the member scope for the turn.

    Returns None (= the caller keeps today's identity logic) when the flag is off or the caller is not a panel device:
    only a panel's voice daemon carries a speaker verdict, and a turn that named its speaker itself
    (``identified_user_id`` from a trusted caller, a PIN-resumed replay) is outside this mechanism.
    ``claimed`` is the member of a claim the server ACCEPTED (threshold + consent); ``pin_user`` is the panel's FRESH
    PIN sign-in (the freshness-gated ``ui_panel_sessions`` owner), never a stale one.
    """
    if not enabled() or caller_source != "device":
        return None
    panel_id = str(panel_id or "")
    now = _clock()
    d = TurnDecision()
    ev = _expire(panel_id, now)
    if ev:
        d.events.append(ev)
    cur = _SESSIONS.get(panel_id)
    kind = phrase_kind(text)
    if kind and cur is not None:
        d.events.append(_close(panel_id, kind))
        cur = None
        if kind == "lock":
            # Answered by the caller ("Locked.") only when a voice session was really closed: with none open the words
            # take the ordinary path - a reply must never claim a lock that did not happen.
            d.lock = True
            d.scope_user = pin_user
            return d

    member = claimed if (verdict is True and claimed and claimed not in _SENTINELS) else None
    if member is None:
        # An unverified voice, or a device turn with no verdict at all: unknown is not the member.
        if cur is not None:
            d.events.append(_close(panel_id, "unverified_voice" if verdict is False else "no_verdict"))
        d.scope_user = pin_user
        return d

    if cur is not None and cur.member_id != member:
        d.events.append(_close(panel_id, "different_voice"))
        cur = None
    if pin_user and pin_user != member:
        # A fresh PIN sign-in of ANOTHER member: the voice neither overrides it nor reads it.
        _audit("refused", member, panel_id, "pin_session_other_member")
        d.scope_user = None
        return d
    if not model_ok:
        _audit("refused", member, panel_id, f"model_gate:{model_reason}")
        d.scope_user = member if pin_user == member else None
        return d
    if kind == "close":
        # "that's all": the session ends here; the turn itself is still the verified member's.
        d.scope_user, d.by_session = member, pin_user != member
        return d
    d.events.append(_open_or_renew(panel_id, member, now, model_reason))
    d.scope_user, d.by_session = member, pin_user != member
    return d


def close_member(member_id: str, reason: str) -> list:
    """Close every panel session of ``member_id`` (consent revoked, voice profile deleted). Returns the events."""
    out = []
    for pid in [p for p, s in list(_SESSIONS.items()) if s.member_id == member_id]:
        ev = _close(pid, reason)
        if ev:
            out.append(ev)
    return out


async def revoke_member(member_id: str, reason: str = "consent_revoked") -> None:
    """``close_member`` + tell the panels. Safe to call with the flag off (nothing is open then). Never raises.
    Also drops the member from the wake-warm speaker memo (consent revoked / voice profile deleted).

    ORDER MATTERS: (1) access is removed FIRST, in memory, synchronously - a slow disk or a stalled panel socket can
    never keep a revoked member's sessions (and private cards) open; (2) the memo cleanup is STARTED next, on a worker
    thread that runs to completion even if this coroutine is cancelled or a panel socket never answers; (3) only then
    are the panels told."""
    evs = []
    try:
        evs = close_member(str(member_id or ""), reason) or []
    except Exception as exc:  # pragma: no cover
        logger.warning("voice_session revoke failed for %s: %s", member_id, exc)
    forget = None
    try:
        import warm_speaker

        forget = asyncio.ensure_future(asyncio.to_thread(warm_speaker.forget_user, str(member_id or "")))
        forget.add_done_callback(lambda t: t.cancelled() or t.exception())   # never an unretrieved exception
    except Exception:  # noqa: BLE001
        forget = None
    try:
        if evs:
            await publish(evs)
    except Exception as exc:  # pragma: no cover
        logger.warning("voice_session revoke publish failed for %s: %s", member_id, exc)
    if forget is not None:
        try:
            await asyncio.shield(forget)
        except Exception:  # noqa: BLE001
            pass


# -- the panel side ---------------------------------------------------------------------------------------------------

_DISPLAY_CACHE: dict[str, str] = {}


async def display_name(member_id: str) -> str:
    """The member's name from ``users`` (identity, not a memory fact); the id itself when unreadable."""
    if member_id in _DISPLAY_CACHE:
        return _DISPLAY_CACHE[member_id]
    name = ""
    try:
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            row = await db.fetchrow("SELECT name FROM users WHERE id = $1", member_id)
        name = ((row["name"] if row else "") or "").strip()
    except Exception as exc:
        logger.debug("voice_session display name unavailable for %s: %s", member_id, exc)
    if name and name.islower():
        name = name.title()
    name = name or member_id
    _DISPLAY_CACHE[member_id] = name
    return name


async def send_to_panel(panel_id: str, event_type: str, data: dict) -> int:
    """Deliver to THAT panel's own push channel only - never the 'all' fallback ``broadcast_to_panel`` uses, which
    would put one member's private data on every screen. Returns the number of sockets reached (0 = not delivered)."""
    try:
        from push import broadcaster

        return int(await broadcaster.broadcast(f"panel_{panel_id}", event_type, data) or 0)
    except Exception as exc:
        logger.debug("voice_session push %s to panel=%s failed: %s", event_type, panel_id, exc)
        return 0


async def publish(events: list) -> None:
    """Tell each event's panel (``voice_session`` frames: open / renewed / closed). Never raises."""
    for ev in events or []:
        if not ev:
            continue
        try:
            data = dict(ev)
            if data.get("state") in {"open", "renewed"}:
                data["display_name"] = await display_name(str(data.get("member_id") or ""))
                data["badge"] = text_for("badge_suffix", default="voice")
            await send_to_panel(str(data.get("panel_id") or ""), "voice_session", data)
        except Exception as exc:  # pragma: no cover
            logger.debug("voice_session publish failed: %s", exc)
