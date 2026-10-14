"""session_status - "who is signed in?" is answered from the resolved identity of the turn, never routed to the brain.

Live 2026-10-14 (voice regression probe): a spoken "who is logged in" went to the semantic router as a ``lists`` question and the
brain said it had no access to a list of who is logged in. The panel KNOWS who is signed in: the voice lane resolves one scope
identity per turn (``_scope_identity_user`` in routers/voice_tts.py: a fresh PIN session, a verified voice session or a named
speaker, never a stale binding, #2026), and ``None`` when nobody is.

This is a narrow, whole-utterance-anchored, read-only tier on the VOICE lane (it is a statement about THIS panel):

    member: "You're signed in as <their name>."  (plus the time left on their voice session, when one is open)
    nobody: "Nobody's signed in on this panel right now. Enter your PIN on the panel to sign in."

It names only the speaker's OWN account. A guest turn never hears a member's name, and neither answer lists anyone else.
Everything else (a different question, another channel, a service login such as "log in to Spotify") returns None and falls
through exactly as before. NEVER raises. Pure matcher plus one async answer; no flag (a correctness fix, like ``identity_facts``).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

TIER = "session_status"

_LEAD = r"^\W*(?:(?:so|and|hey|ok|okay|um|uh|zoe|please|quickly|just)[\s,]+)*"
_TAIL = r"(?:\s+(?:again|please|then|now|zoe|mate|thanks))*\W*$"

_SIGNED = r"(?:logged|signed)(?:\s+|-)?(?:in|on)"
# where: only THIS panel (never a service: "logged in to spotify" must not match)
_HERE = (r"(?:\s+(?:on|at|into|to|in|with)\s+(?:this|the|my)\s+(?:panel|screen|device|tablet|display|zoe|thing)"
         r"|\s+here|\s+(?:on\s+)?zoe)?")
_WHEN = r"(?:\s+(?:right\s+now|currently|at\s+the\s+moment|at\s+the\s+minute|today))?"
_ADV = r"(?:(?:currently|actually|even|still|right\s+now)\s+)?"


def _rx(body: str) -> re.Pattern:
    return re.compile(_LEAD + body + _TAIL, re.IGNORECASE)


_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    # who is signed in (on this panel)
    ("who", _rx(r"who(?:['’]s|\s+is|\s+are|s)\s+" + _ADV + _SIGNED + _HERE + _WHEN)),
    ("who", _rx(r"who(?:['’]s|\s+is|s)\s+(?:using|on)\s+(?:this|the)\s+(?:panel|screen|device|tablet|display)" + _WHEN)),
    ("who", _rx(r"who\s+do\s+you\s+(?:think|have|see)\s+(?:is\s+)?" + _SIGNED + _HERE + _WHEN)),
    ("who", _rx(r"(?:can\s+you\s+)?tell\s+me\s+who(?:['’]s|\s+is)\s+" + _SIGNED + _HERE + _WHEN)),
    # who am I signed in as / which account
    # NOT bare "who am I" / "who are you talking to" / "who do you think I am": the Skybridge identity card owns those
    # (skybridge_service._IDENTITY_QUERY_RE, tests/test_skybridge_router_veto.py), answered with a UI card.
    ("self", _rx(r"who\s+am\s+i\s+" + _SIGNED + r"\s+as" + _WHEN)),
    ("self", _rx(r"(?:which|what)\s+(?:account|profile|user)\s+(?:am\s+i\s+(?:using|on)|is\s+this)" + _WHEN)),
    ("self", _rx(r"(?:which|what)\s+(?:account|profile|user)\s+(?:am\s+i|is)\s+" + _SIGNED + r"(?:\s+(?:as|with))?" + _HERE + _WHEN)),
    ("self", _rx(r"whose\s+(?:account|profile)\s+is\s+this" + _WHEN)),
    # am I signed in / is anyone signed in
    ("am_i", _rx(r"am\s+i\s+" + _ADV + _SIGNED + _HERE + _WHEN)),
    ("anyone", _rx(r"(?:is|are)\s+(?:there\s+)?(?:anyone|anybody|someone|somebody)\s+" + _SIGNED + _HERE + _WHEN)),
    ("anyone", _rx(r"is\s+(?:anyone|anybody|someone|somebody)\s+using\s+(?:this|the)\s+(?:panel|screen|device)" + _WHEN)),
)


def question_kind(text: str) -> str:
    """"who" / "self" / "am_i" / "anyone" for a question about who is signed in on THIS panel, else "". Whole-utterance anchored
    (a service login, a history question or any other "who" never matches). Pure."""
    t = (text or "").strip()
    if not t or len(t) > 120:
        return ""
    for kind, rx in _PATTERNS:
        if rx.match(t):
            return kind
    return ""


GUEST_REPLY = "Nobody's signed in on this panel right now. Enter your PIN on the panel to sign in."


def _minutes(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return "less than a minute"
    m = (s + 30) // 60
    return f"about {m} minute{'s' if m != 1 else ''}"


async def _name_for(member: str) -> str:
    try:
        import identity_facts as _idf

        ident = await _idf.resolve_identity(member)
        if ident is not None and ident.name:
            return ident.name
    except Exception as exc:  # noqa: BLE001 - a name lookup must never break the answer
        logger.debug("session_status: identity lookup failed: %s", type(exc).__name__)
    try:
        import voice_session as _vs

        name = await _vs.display_name(member)
        if name and name != member:
            return name
    except Exception as exc:  # noqa: BLE001
        logger.debug("session_status: display name lookup failed: %s", type(exc).__name__)
    return ""


def reply_for(kind: str, member: Optional[str], name: str, left_s: Optional[float]) -> str:
    """The one-or-two sentence reply. Pure. ``member`` None means nobody is signed in."""
    if not member:
        return GUEST_REPLY
    who = f"You're signed in as {name}." if name else "You're signed in."
    if kind in ("am_i", "anyone"):
        who = f"Yes, you're signed in as {name}." if name else "Yes, you're signed in."
    if left_s is not None and left_s > 0:
        who += f" Your voice session has {_minutes(left_s)} left."
    return who


async def answer(text: str, user_id: str, *, channel: Optional[str], extra_ctx: Optional[dict[str, Any]]) -> Optional[str]:
    """The reply for a recognised who-is-signed-in question on the voice lane, else None. NEVER raises.

    The identity is the one of the TURN: ``extra_ctx["scope_user"]`` when the voice lane supplies it (None there means nobody is
    signed in, whatever ``user_id`` says), else ``user_id`` when it is a real account (the replay harness)."""
    try:
        if (channel or "").strip().lower() != "voice":
            return None
        kind = question_kind(text)
        if not kind:
            return None
        ctx = extra_ctx or {}
        if "scope_user" in ctx:
            member = str(ctx.get("scope_user") or "").strip() or None
        else:
            member = (user_id or "").strip() or None
        if member:
            import identity_facts as _idf

            if not _idf._is_account_id(member):
                member = None
        if not member:
            return reply_for(kind, None, "", None)
        left_s: Optional[float] = None
        try:
            import voice_session as _vs

            panel_id = str(ctx.get("panel_id") or "")
            if panel_id and _vs.active_member(panel_id) == member:
                left_s = _vs.seconds_left(panel_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("session_status: session time unavailable: %s", type(exc).__name__)
        return reply_for(kind, member, await _name_for(member), left_s)
    except Exception as exc:  # noqa: BLE001
        logger.warning("session_status: answer failed (non-fatal): %s", type(exc).__name__)
        return None


async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None,
               extra_ctx: Optional[dict[str, Any]] = None):
    """The fast-tier hook: a ``DispatchResult`` or None. NEVER raises."""
    try:
        reply = await answer(text, user_id, channel=channel, extra_ctx=extra_ctx)
        if not reply:
            return None
        import expert_dispatch as _xd

        logger.info("SESSION_STATUS answered=1 member=%s", "no" if reply == GUEST_REPLY else "yes")
        return _xd.DispatchResult(domain="identity", reply=reply, intent="session_status", tier=TIER)
    except Exception as exc:  # noqa: BLE001
        logger.warning("session_status: tier failed (non-fatal): %s", type(exc).__name__)
        return None
