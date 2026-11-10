"""Named guests: a person who is not a member, whom Zoe knows by voice at the panel, greets by name and remembers.

Owner request 2026-11-08: a friend talked to Zoe on the panel and she did not know who it was. "She'll remember the conversations
she has had with that person", and the friend "doesn't need an account, just be identified ... if she talks she just gets treated
like a guest". Runbook: ``docs/knowledge/named-guests.md``.

WHERE THINGS LIVE
  the person   a row in the enrolling member's OWN people layer (``people``, visibility 'personal'); an existing person of that
               name is linked, never duplicated (the people graph, ADR-relationship-memory).
  the voice    ONE person-voice registry shared with Omi: ``learned_voiceprints`` with ``channel='panel'`` (0071 put the channel in
               the key; the pendant's rows are ``'pendant'`` and are never served to the panel - microphone drift).
  the memory   ``named_guest_turns``: what Zoe and that guest said, keyed by (member layer, person). Read ONLY on a turn the server
               matched to that guest; never served to any other speaker, the member who enrolled them included (default: never).

THE GUARANTEES (each pinned by ``tests/test_named_guests.py``, with a break-the-fix control)
  1. A guest match NEVER yields member scope: the turn's user stays the guest sentinel, the claim is a separate field
     (``speaker.guest`` / ``guest_score``) that the member gate never reads, and a guest id sent as a MEMBER claim finds no
     ``speaker_profiles`` row and is dropped. No PIN-free session, no member data, no member memory.
  2. When the guest scores above the member the panel also named, the MEMBER claim is dropped (``on`` only): a voice closer to a
     guest than to a member is not that member.
  3. A soft-deleted person, or a name the member FORGOT (the forget ledger, strict: an outage means no guest) is never matched or
     served to a panel. A child is NOT refused (owner 2026-11-08: voice works the same for adults and children): a person who is or
     may be a child (``person_age``) is MARKED ``Guest.child`` and the turn gets the child-safe block (``guest_line``).
  Enrolment (only a verified adult member, through a single-use ticket) is ``named_guest_enrol.py``; what is kept and erasing is
  ``named_guest_memory.py``.

FLAGS (registered in ``flag_registry``)
  ``ZOE_NAMED_GUESTS``        off (DEFAULT) | shadow | on. off: the sync serves no guest, claims are ignored, the tier is inert.
                              shadow: guests are served and scored, the server LOGS who it would greet, nothing changes.
  ``ZOE_NAMED_GUEST_MEMORY``  remember a guest's conversations (default off; only meaningful with ``ZOE_NAMED_GUESTS=on``).
  ``ZOE_NAMED_GUEST_RECALL_TURNS`` how many past exchanges ride into a guest turn (default 6).
"""
from __future__ import annotations

import base64
import contextvars
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import speaker_models
from typed_env import flag

logger = logging.getLogger(__name__)

CHANNEL = "panel"


def mode() -> str:
    return str(flag("ZOE_NAMED_GUESTS"))


@dataclass(frozen=True)
class Guest:
    person_id: str
    name: str
    owner_user_id: str
    score: float = 0.0
    child: bool = False      # a possible child (``person_age``): recognised exactly like anyone, handled child-safe (never refused)


# The guest this turn's voice matched (bound by voice_command, read by the brain blocks and the post-turn memory pass). A
# ContextVar: a task the turn spawns copies it, a concurrent turn of another panel never sees it.
_TURN: contextvars.ContextVar[Optional[Guest]] = contextvars.ContextVar("named_guest_turn", default=None)


def bind_turn(guest: Optional[Guest]) -> None:
    _TURN.set(guest)


def current() -> Optional[Guest]:
    return _TURN.get()


def _safe(text: Any, limit: int) -> str:
    """One line of stored / member-entered text for a prompt: no newlines, no block markers, bounded."""
    s = re.sub(r"[\r\n\t]+", " ", str(text or "")).replace("[", "(").replace("]", ")").strip()
    return s[:limit]


# ── the claim (pure) ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _finite(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def guest_claim(payload: Optional[dict]) -> Optional[tuple[str, float]]:
    """``(person_id, score)`` of the panel's GUEST candidate (``speaker.guest`` / ``speaker.guest_score``), or None."""
    block = (payload or {}).get("speaker")
    if not isinstance(block, dict):
        return None
    gid = str(block.get("guest") or "").strip()
    score = _finite(block.get("guest_score"))
    if not gid or score is None or len(gid) > 64:
        return None
    return gid, score


def member_claim_beaten(payload: Optional[dict]) -> bool:
    """True when the panel's guest candidate scored ABOVE its member candidate: the member claim must not stand (``on`` only)."""
    g = guest_claim(payload)
    if g is None or mode() != "on":
        return False
    p = payload or {}
    member_score = _finite(p.get("voice_score"))
    if member_score is None and isinstance(p.get("speaker"), dict):
        member_score = _finite(p["speaker"].get("score"))
    return member_score is not None and g[1] > member_score


def _claim_gate(payload: dict, caller: dict) -> Optional[tuple[str, float]]:
    """The panel's guest candidate if it clears the SAME server bar as a member claim (device on the LAN, the acting model, the
    model's threshold for this clip length and window), else None. Pure apart from the flag."""
    c = caller or {}
    if c.get("source") != "device" or c.get("via_relay") or c.get("via_tunnel"):
        return None
    g = guest_claim(payload)
    if g is None:
        return None
    block = (payload or {}).get("speaker") or {}
    model_id = str(block.get("model_id") or "")
    if model_id != speaker_models.acting_model():
        return None
    threshold = speaker_models.claim_threshold(model_id, block.get("speech_s"), str((payload or {}).get("window") or ""))
    if threshold is None or g[1] < threshold:
        return None
    member_score = _finite(block.get("score"))
    if member_score is not None and member_score >= g[1]:
        return None
    return g


async def accept(payload: dict, caller: dict, *, member_identified: bool) -> Optional[Guest]:
    """The named guest this turn's voice is, or None. Never raises; any doubt is None (= the plain guest turn of today)."""
    try:
        m = mode()
        if m == "off" or member_identified:
            return None
        g = _claim_gate(payload, caller)
        if g is None:
            cand = guest_claim(payload)
            if m == "shadow" and cand is not None and (caller or {}).get("source") == "device":
                await _log_margins(m, payload, cand, await _lookup(cand[0], speaker_models.acting_model(), cand[1]), matched=False)
            return None
        guest = await _lookup(g[0], speaker_models.acting_model(), g[1])
        if guest is None:
            return None
        await _log_margins(m, payload, g, guest, matched=True)
        if m == "shadow":
            logger.info("NAMED_GUEST mode=shadow would_greet=1 score=%.3f", g[1])
            return None
        logger.info("NAMED_GUEST mode=on matched=1 score=%.3f", g[1])
        return guest
    except Exception as exc:  # noqa: BLE001
        logger.warning("named_guests: accept failed (plain guest turn): %s", type(exc).__name__)
        return None


async def _log_margins(m: str, payload: dict, cand: tuple[str, float], guest: Optional["Guest"], *, matched: bool) -> None:
    """The match MARGINS (guest score minus the model's bar, and minus the member candidate's score), counts and scores only, tagged
    ``child=0|1``: ResNet34 is trained on adult voices, so whether a child-marked profile needs its own bar is read from these lines
    (shadow week: ``grep NAMED_GUEST_MARGIN``). Near-misses (under the bar) are logged too, in shadow only. Never raises."""
    if guest is None:
        return
    try:
        block = (payload or {}).get("speaker") or {}
        bar = speaker_models.claim_threshold(str(block.get("model_id") or ""), block.get("speech_s"), str((payload or {}).get("window") or ""))
        member = _finite(block.get("score"))
        logger.info("NAMED_GUEST_MARGIN mode=%s child=%d matched=%d score=%.3f margin_bar=%s margin_member=%s", m, int(guest.child),
                    int(matched), cand[1], "na" if bar is None else "%.3f" % (cand[1] - bar),
                    "na" if member is None else "%.3f" % (cand[1] - member))
    except Exception:  # noqa: BLE001 - a log line is never worth a turn
        pass


async def _lookup(person_id: str, model_id: str, score: float) -> Optional[Guest]:
    from db_compat import get_compat_db
    from person_age import person_may_be_child

    async with get_compat_db() as db:
        async with db.execute(
            "SELECT v.owner_user_id, p.name, p.relationship, p.birthday FROM learned_voiceprints v JOIN people p ON p.id = v.people_id "
            "WHERE v.people_id = ? AND v.channel = ? AND v.model_id = ? AND v.learning_enabled = 1 AND p.user_id = v.owner_user_id "
            "AND (p.deleted = 0 OR p.deleted IS NULL) LIMIT 1", (person_id, CHANNEL, model_id)) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    name = _safe(row[1], 40)
    if not name or await _forgotten(str(row[0]), name):
        return None
    return Guest(person_id=person_id, name=name, owner_user_id=str(row[0]), score=score,
                 child=person_may_be_child({"relationship": row[2], "birthday": row[3]}))


async def _forgotten(owner_user_id: str, name: str) -> bool:
    """The serve wall: a guest whose name the member forgot is never matched, fed to a panel or recalled, even if an erase did
    not land. STRICT: a ledger outage raises (every caller fails closed)."""
    import memory_forgotten as mf

    return await mf.matches(owner_user_id, name, strict=True)


# ── the panel feed ────────────────────────────────────────────────────────────────────────────────────────────────────────

async def sync_entries(served_models: Iterable[str]) -> list[dict]:
    """The guest profiles a panel scores against, beside the members' (``GET /api/voice/profiles/sync`` -> ``guests``).
    ``[]`` with the flag off. The caller has already checked the device token and the LAN."""
    if mode() == "off":
        return []
    from db_compat import get_compat_db

    served = set(served_models)
    async with get_compat_db() as db:
        async with db.execute(
            "SELECT v.people_id, p.name, v.embedding, v.model_id, p.relationship, p.birthday, v.owner_user_id FROM learned_voiceprints v "
            "JOIN people p ON p.id = v.people_id WHERE v.channel = ? AND v.learning_enabled = 1 AND p.user_id = v.owner_user_id "
            "AND (p.deleted = 0 OR p.deleted IS NULL) ORDER BY v.people_id", (CHANNEL,)) as cur:
            rows = await cur.fetchall()
    out = []
    for r in rows:
        if r[3] not in served:
            continue
        if await _forgotten(str(r[6]), _safe(r[1], 40)):        # raises on a ledger outage: the caller serves no guest at all
            continue
        out.append(dict(guest_id=r[0], display_name=_safe(r[1], 40), embedding_base64=base64.b64encode(bytes(r[2])).decode("ascii"),
                        model_id=r[3]))
    return out


# ── the brain ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def guest_line(guest: Guest) -> str:
    if guest.child:
        from member_register import KID_REGISTER

        return (f"You are talking with {guest.name}, a child who is a guest of this household: not a member and not the owner. "
                f"Address them as {guest.name}. {KID_REGISTER} Keep everything child-safe: nothing frightening, violent, sexual or "
                f"adult-only (no alcohol, drugs, gambling or adult jokes), no flirting, no secrets from their grown-ups. If they seem "
                f"upset, hurt or unsafe, be kind and suggest they tell a grown-up who is here. Do not share or change anything private "
                f"of any household member (their lists, calendar, reminders, messages or memories).")
    return (f"You are talking with {guest.name}, a guest of this household: not a member and not the owner. Address them as "
            f"{guest.name}. They have guest permissions only: do not share or change anything private of any household member "
            f"(their lists, calendar, reminders, messages or memories).")


async def brain_block(user_id: str) -> str:
    """The identity-slot block for a turn matched to a named guest (both brain lanes), or "". Never raises."""
    try:
        guest = current()
        from user_filters import is_unidentified

        if not is_unidentified(user_id):
            return ""
        from identity_facts import BLOCK_CLOSE, BLOCK_OPEN

        if guest is None:
            import named_guest_learning     # an unknown voice Zoe is now sure of: ask its name (ZOE_GUEST_VOICE_LEARN=on)

            return f"{BLOCK_OPEN}\n{named_guest_learning.ask_line()}\n{BLOCK_CLOSE}" if named_guest_learning.asking() else ""

        lines = [guest_line(guest)]
        import named_guest_memory

        past = await named_guest_memory.recall_lines(guest)
        if past:
            lines.append(f"Earlier conversations with {guest.name} (theirs only):")
            lines.extend(past)
        import conversation_notes     # notes about their earlier conversations (ZOE_CONVERSATION_NOTES): THEIR layer, theirs only

        notes = await conversation_notes.guest_lines(guest)
        if notes:
            lines.append(f"Notes from earlier conversations with {guest.name} (theirs only, not a member's):")
            lines.extend(notes)
        return f"{BLOCK_OPEN}\n" + "\n".join(lines) + f"\n{BLOCK_CLOSE}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("named_guests: brain block skipped: %s", type(exc).__name__)
        return ""
