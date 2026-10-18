"""intercom - the native LAN PA and intercom, Zoe-only (no Matrix): "tell the kitchen dinner's ready", "announce: ...", "call the kitchen".

Design: ``docs/knowledge/lan-intercom.md`` (the deep dive's section 8.3 "LAN PA and intercom, native (P0)", owner decisions 2026-10-18).

What it does today (the smallest working version):

* **Announce** - the member's words, spoken by Zoe's TTS on a NAMED panel ("tell the kitchen ...", "let the kitchen know ...") or on
  every other panel ("announce: ...", "tell everyone ..."). Delivery is the existing daemon lane (``voice_announce``): one row per
  target panel, trigger ``intercom_announce``, which is PANEL-SCOPED, so only the daemon whose device token IS that panel may claim it
  (a second panel can never play another panel's row) and the daemon speaks it on its next poll (5 s), with its own echo cooldowns.
* **Call** - "call / ring the kitchen" RINGS the target panel (a spoken "<name> would like to talk to you"). The live two-way audio
  leg (P2P WebRTC over the panel WebSocket) is NOT built yet; the reply says so. "Drop in on the kitchen" is decided by the policy
  (adults only) and, with no audio leg, rings instead and says why.

The call-permission rules (owner, 2026-10-18), enforced HERE, server side, before anything is queued:

* guests can't use it at all (no announcement, no ring, no drop-in) - the turn's identity is ``extra_ctx["scope_user"]`` (the voice
  lane's resolved identity: a fresh PIN, a verified voice session or a named speaker; ``None`` = nobody), never the payload;
* everyone rings first; only an ADULT may drop in (``manner_block.is_adult``: an allowlisted adult role and no minor / kid mode,
  fails closed);
* quiet hours hold: nothing is announced or rung inside the household quiet window (``comm_prefs`` / ``ZOE_QUIET_START_HOUR`` /
  ``_END_HOUR``);
* never to the panel the member is standing at, and only panels that hold a live device token (a panel with no speaker daemon has no
  voice to speak with).

An announcement or call a PERSON starts is that person speaking through Zoe, not Zoe speaking unprompted: it is OUTSIDE the
no-unprompted-speech rule (``ZOE_PROACTIVE_SPOKEN=0``, owner decision 2026-10-18, recorded in ``docs/VISION.md``) and does not read
that flag. It still obeys every rule above.

``ZOE_INTERCOM`` = off (DEFAULT; a typo is off) | shadow (decide + log ``INTERCOM``, queue nothing, answer nothing: the turn falls
through exactly as before) | on. Read per call. Voice lane only. Off returns before any parsing or DB read. NEVER raises.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

TIER = "intercom"
FLAG = "ZOE_INTERCOM"
MODES = ("off", "shadow", "on")

ANNOUNCE_TRIGGER = "intercom_announce"
RING_TRIGGER = "intercom_ring"
TRIGGERS = (ANNOUNCE_TRIGGER, RING_TRIGGER)
#: a spoken PA line older than this is worse than silence ("dinner's ready" a minute late is still useful, five minutes is not)
ANNOUNCE_TTL_S = 90
RING_TTL_S = 45
MAX_MESSAGE_CHARS = 200
#: the live audio leg for drop-in / an answered call (P2P WebRTC over the panel WebSocket) is not built yet
DROP_IN_TRANSPORT = False
#: identity-presence engine ``site_security.guests.intercom`` (design only, no store yet): the owner's 2026-10-18 rule is "guests
#: can't call", so this is a constant until that setting exists - and then it may only ever be loosened by the admin.
GUESTS_MAY_USE_INTERCOM = False

KINDS = ("announce", "ring", "drop_in")


def mode() -> str:
    from typed_env import env_str

    raw = env_str("ZOE_INTERCOM", "off").strip().lower()  # literal on purpose: tools/audit/flag_inventory.py greps it
    return raw if raw in MODES else "off"


# ── parsing (pure) ──────────────────────────────────────────────────────────────────────────────────────────────────────

_LEAD = r"^\W*(?:(?:hey|ok|okay|um|uh|zoe|please|so|and|can\s+you|could\s+you|would\s+you)[\s,]+)*"
_ALL_WORDS = (
    "everyone", "everybody", "the house", "the whole house", "the household", "all the panels", "all panels", "every panel",
    "every room", "all the rooms", "all rooms", "the family",
)
_PANEL_SUFFIX = r"(?:\s+(?:panel|screen|speaker))?"

# announce [to <target>][:,] [that] <message>   |   make an announcement[: ...]
_ANNOUNCE = re.compile(_LEAD + r"(?:make\s+an\s+announcement|announce)\b(?:\s+(?:to|in|on)\s+(?P<rest>.+)|\s*[:,\-]?\s*(?P<msg>.*))$",
                       re.IGNORECASE)
# tell <target> [that] <message>   |   let <target> know [that] <message>
_TELL = re.compile(_LEAD + r"tell\s+(?P<rest>.+)$", re.IGNORECASE)
_LET = re.compile(_LEAD + r"let\s+(?P<rest>.+)$", re.IGNORECASE)
# call / ring / buzz <target>   |   drop in on <target>
_RING = re.compile(_LEAD + r"(?:call|ring|buzz|intercom)\s+(?P<rest>.+?)\W*$", re.IGNORECASE)
_DROP = re.compile(_LEAD + r"drop\s+in\s+(?:on|to)\s+(?P<rest>.+?)\W*$", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    kind: str                 # announce | ring | drop_in
    target: str               # a normalised room / panel label, or "" = every other panel
    message: str = ""         # announce only


def norm_label(label: str) -> str:
    """'The Living Room panel' -> 'living room'. Pure."""
    t = re.sub(r"[^\w\s']", " ", (label or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^the\s+", "", t)
    t = re.sub(r"\s+(?:panel|screen|speaker)$", "", t)
    return t.strip()


def _clean_message(msg: str) -> str:
    m = re.sub(r"^\s*(?:that|to\s+say|saying|to)\s+", "", msg or "", flags=re.IGNORECASE).strip(" \t,:;-")
    m = re.sub(r"\s+", " ", m)
    return m[:MAX_MESSAGE_CHARS].strip()


def _split_target(rest: str, labels: Iterable[str]) -> Optional[tuple[str, str]]:
    """``rest`` starts with a target ("the kitchen that dinner's ready") -> (target, remainder), or None when it names no known
    panel. The LONGEST match wins ("living room" before "living"); "everyone" and friends mean every other panel (""). Pure."""
    src = (rest or "").strip()
    cands = [(w, "") for w in _ALL_WORDS] + [(lb, lb) for lb in labels if lb]
    best: Optional[tuple[int, str, str]] = None   # (matched length, target, remainder)
    for word, target in cands:
        m = re.match(r"(?:the\s+)?" + re.escape(word) + _PANEL_SUFFIX + r"(?=$|[\s,:;.!?])", src, re.IGNORECASE)
        if m and (best is None or m.end() > best[0]):
            best = (m.end(), target, src[m.end():])
    return None if best is None else (best[1], best[2])


_POLITE_TAIL = re.compile(r"^(?:\s*(?:please|now|for\s+me|zoe|thanks|thank\s+you)\b)*\W*$", re.IGNORECASE)


# A device word right after the room ("tell the kitchen light to stay on") makes it a smart-home turn, never an announcement.
_DEVICE_AFTER_TARGET = re.compile(
    r"^\s*(?:lights?|lamps?|fans?|tv|telly|television|heater|heating|aircon|air\s*con|blinds?|curtains?|switch(?:es)?|"
    r"plugs?|doors?|thermostat)\b", re.IGNORECASE)


def parse(text: str, labels: Iterable[str]) -> Optional[Request]:
    """The intercom request in ``text``, or None. ``labels`` are the normalised room / panel labels of the house's speaker panels
    (``norm_label``); a "tell" / "call" naming anything else ("tell me a joke", "call mum", "tell the kids ...") is NOT an intercom
    request and returns None, so the turn falls through. Whole-utterance anchored. Pure."""
    t = (text or "").strip()
    if not t or len(t) > 300:
        return None
    labels = [lb for lb in labels if lb]
    m = _ANNOUNCE.match(t)
    if m:
        if m.group("rest") is not None:
            split = _split_target(m.group("rest"), labels)
            if split is None:
                return None
            target, remainder = split
            return Request("announce", target, _clean_message(remainder))
        return Request("announce", "", _clean_message(m.group("msg") or ""))
    m = _DROP.match(t)
    if m:
        split = _split_target(m.group("rest"), labels)
        if split is None or not split[0] or not _POLITE_TAIL.match(split[1]):
            return None  # "drop in on everyone" is not a drop-in
        return Request("drop_in", split[0])
    m = _RING.match(t)
    if m:
        split = _split_target(m.group("rest"), labels)
        if split is None or not split[0] or not _POLITE_TAIL.match(split[1]):
            return None  # "call everyone" is not a call; "call the kitchen about the oven" is not either
        return Request("ring", split[0])
    for rx, let in ((_TELL, False), (_LET, True)):
        m = rx.match(t)
        if not m:
            continue
        split = _split_target(m.group("rest"), labels)
        if split is None:
            return None
        target, remainder = split
        if let:
            k = re.match(r"^\s*know\b", remainder, re.IGNORECASE)
            if not k:
                return None  # "let the kitchen light stay on" is not a message
            remainder = remainder[k.end():]
        elif _DEVICE_AFTER_TARGET.match(remainder):
            return None  # "tell the kitchen light to stay on" is a smart-home turn that names a room, not a message to it
        msg = _clean_message(remainder)
        return Request("announce", target, msg) if msg else None
    return None


# ── policy (pure) ───────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Decision:
    allowed: bool
    action: str               # announce | ring | drop_in | none
    reason: str               # guest | quiet_hours | no_target | only_here | empty | ok | drop_in_adults_only
    targets: tuple[str, ...] = field(default_factory=tuple)


def decide(req: Request, *, caller: Optional[str], caller_is_adult: bool, origin_panel: str,
           panels_by_label: dict[str, list[str]], all_panels: list[str], quiet: bool) -> Decision:
    """The owner's call-permission rules, in order. Pure.

    guests never; quiet hours hold; never the panel you are at; everyone rings first and only an adult may drop in (a non-adult's
    drop-in becomes a ring - the rule is "ring first", not "no")."""
    if not caller:  # GUESTS_MAY_USE_INTERCOM is False: an unidentified turn never reaches a target
        return Decision(False, "none", "guest")
    if quiet:
        return Decision(False, "none", "quiet_hours")
    if req.target:
        named = [p for p in panels_by_label.get(req.target, []) if p]
        if not named:
            return Decision(False, "none", "no_target")
        targets = [p for p in named if p != origin_panel]
        if not targets:
            return Decision(False, "none", "only_here")
    else:
        targets = [p for p in all_panels if p and p != origin_panel]
        if not targets:
            return Decision(False, "none", "no_target")
    targets = list(dict.fromkeys(targets))
    if req.kind == "announce":
        if not req.message:
            return Decision(False, "none", "empty")
        return Decision(True, "announce", "ok", tuple(targets))
    if req.kind == "drop_in":
        if caller_is_adult:
            return Decision(True, "drop_in", "ok", tuple(targets))
        return Decision(True, "ring", "drop_in_adults_only", tuple(targets))
    return Decision(True, "ring", "ok", tuple(targets))


# ── replies and spoken lines (pure) ─────────────────────────────────────────────────────────────────────────────────────

def _place(target: str) -> str:
    return f"the {target}" if target else "every other panel"


def spoken_line(action: str, sender_name: str, message: str, origin_label: str) -> str:
    """What the TARGET panel says. Pure."""
    who = sender_name or "Someone"
    if action == "announce":
        return f"Announcement from {sender_name}: {message}" if sender_name else f"Announcement: {message}"
    if origin_label:
        return f"{who} would like to talk to you, from the {origin_label} panel."
    return f"{who} would like to talk to you."


def reply_for(req: Request, d: Decision) -> str:
    """What the CALLER hears. Pure."""
    place = _place(req.target)
    if not d.allowed:
        return {
            "guest": "Only signed-in members of the household can use the intercom. Enter your PIN on the panel to sign in.",
            "quiet_hours": f"It's quiet hours, so I won't {'announce' if req.kind == 'announce' else 'ring'} anything right now.",
            "only_here": f"You're at the {req.target} panel already.",
            "no_target": ("There's no other panel in the house to send that to yet." if not req.target
                          else f"I can't reach a panel in the {req.target}."),
            "empty": "What would you like me to announce?",
        }.get(d.reason, "I can't do that right now.")
    if d.action == "announce":
        if req.target:
            return f"Okay, I've told {place}."
        n = len(d.targets)
        return "Okay, I've announced it." if n == 1 else f"Okay, I've announced it on {n} panels."
    note = " Two-way calls between panels aren't set up yet, so they'll need to come to you."
    if d.reason == "drop_in_adults_only":
        return f"Only adults can drop in, so I've rung {place} instead." + note
    if d.action == "drop_in":
        return f"Dropping in needs live audio between panels, which isn't set up yet, so I've rung {place} instead." + note
    return f"I've rung {place} and told them you'd like to talk." + note


# ── runtime ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

async def _speaker_panels(db) -> list[dict]:
    """Active panels that hold a live device token (the only ones with a voice daemon), with their room / name / location."""
    sql = ("SELECT p.panel_id, p.name, p.location, r.name AS room FROM panels p LEFT JOIN rooms r ON r.id = p.room_id "
           "WHERE p.is_active = 1 AND p.panel_id IN (SELECT panel_id FROM device_tokens WHERE revoked = 0)")
    async with db.execute(sql) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        out.append({"panel_id": str(r[0]), "name": r[1] or "", "location": r[2] or "", "room": r[3] or ""})
    return out


def label_index(panels: list[dict]) -> dict[str, list[str]]:
    """normalised label -> panel ids. A panel answers to its room, its location and its name. Pure."""
    idx: dict[str, list[str]] = {}
    for p in panels:
        for raw in (p.get("room"), p.get("location"), p.get("name")):
            lb = norm_label(str(raw or ""))
            if lb and p["panel_id"] not in idx.setdefault(lb, []):
                idx[lb].append(p["panel_id"])
    return idx


def origin_label(panels: list[dict], origin: str) -> str:
    for p in panels:
        if p["panel_id"] == origin:
            return norm_label(p.get("room") or p.get("location") or "")
    return ""


def _quiet_now(now: Optional[datetime] = None) -> bool:
    import comm_prefs
    from time_utils import zoe_timezone

    now = now or datetime.now(zoe_timezone())
    return comm_prefs.in_quiet_hours({}, now.hour * 60 + now.minute)


async def _sender_name(member: str) -> str:
    try:
        import identity_facts as _idf

        ident = await _idf.resolve_identity(member)
        if ident is not None and ident.name:
            return ident.name
    except Exception as exc:  # noqa: BLE001 - a name is optional
        logger.debug("intercom: name lookup failed: %s", type(exc).__name__)
    return ""


async def _is_adult(member: str) -> bool:
    try:
        import manner_block

        return bool(await manner_block.is_adult(member))
    except Exception:  # noqa: BLE001 - not knowing is not adult
        return False


def _turn_member(user_id: str, extra_ctx: Optional[dict[str, Any]]) -> Optional[str]:
    """The identity of the TURN (``session_status`` rule): ``scope_user`` when the voice lane supplies it, else a real account id."""
    import identity_facts as _idf
    from user_filters import is_unidentified

    ctx = extra_ctx or {}
    member = (str(ctx.get("scope_user") or "").strip() or None) if "scope_user" in ctx else ((user_id or "").strip() or None)
    if not member or is_unidentified(member) or not _idf._is_account_id(member):
        return None
    return member


async def handle(text: str, user_id: str, *, extra_ctx: Optional[dict[str, Any]], allow_writes: bool = True,
                 now: Optional[datetime] = None) -> Optional[tuple[Request, Decision, str]]:
    """Parse, decide and (mode ``on`` and writes allowed) queue. Returns (request, decision, reply) or None when ``text`` is not an
    intercom request. Raises only on a DB failure (``tier`` owns swallowing)."""
    from db_compat import get_compat_db

    import voice_announce

    ctx = extra_ctx or {}
    origin = str(ctx.get("panel_id") or "").strip()
    async with get_compat_db() as db:
        panels = await _speaker_panels(db)
        idx = label_index(panels)
        req = parse(text, idx.keys())
        if req is None:
            return None
        member = _turn_member(user_id, extra_ctx)
        adult = await _is_adult(member) if (member and req.kind == "drop_in") else False
        d = decide(req, caller=member, caller_is_adult=adult, origin_panel=origin, panels_by_label=idx,
                   all_panels=[p["panel_id"] for p in panels], quiet=_quiet_now(now))
        reply = reply_for(req, d)
        logger.info("INTERCOM mode=%s kind=%s target=%s allowed=%s action=%s reason=%s targets=%d member=%s",
                    mode(), req.kind, req.target or "*", int(d.allowed), d.action, d.reason, len(d.targets),
                    "yes" if member else "no")
        if d.allowed and allow_writes and mode() == "on":
            name = await _sender_name(member or "")
            where = origin_label(panels, origin)
            line = spoken_line("announce" if d.action == "announce" else "ring", name, req.message, where)
            trigger = ANNOUNCE_TRIGGER if d.action == "announce" else RING_TRIGGER
            ttl = ANNOUNCE_TTL_S if d.action == "announce" else RING_TTL_S
            for panel in d.targets:
                await voice_announce.enqueue_announcement(
                    db, user_id=member or "", message=line, panel_id=panel, trigger_type=trigger, ttl_s=ttl, commit=False)
            await db.commit()
    return req, d, reply


async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None,
               extra_ctx: Optional[dict[str, Any]] = None, allow_writes: bool = True):
    """The fast-tier hook: a ``DispatchResult`` (mode ``on``) or None. Off = None before any work; shadow decides, logs and answers
    nothing. NEVER raises."""
    try:
        m = mode()
        if m == "off" or (channel or "").strip().lower() != "voice":
            return None
        out = await handle(text, user_id, extra_ctx=extra_ctx, allow_writes=allow_writes)
        if out is None or m != "on":
            return None
        _req, d, reply = out
        import expert_dispatch as _xd

        return _xd.DispatchResult(domain="intercom", reply=reply, intent=f"intercom_{d.action if d.allowed else 'refused'}",
                                  tier=TIER)
    except Exception as exc:  # noqa: BLE001 - the tier never breaks a turn
        logger.warning("intercom: tier failed (non-fatal): %s", type(exc).__name__)
        return None
