"""memory_space - the touch panel's "Memories" space: what Zoe knows about the signed-in member, and per-item forget.

Why. The estate (``dist/touch/home.html``) gets a Memories stage where the identified member sees what Zoe holds about them.
The voice tier already answers "what do you know about me?" from the owner's own rows (``provenance_answers``); this module is
the same read as DATA for a screen, with the same walls, so the screen can never show more than Zoe would say.

Walls (each pinned by ``tests/test_memory_space.py``, each with a break-the-fix control):

1. **Identity comes from the server, never from the page.** ``require_member`` takes the user the session resolved to and
   refuses: no ``X-Session-ID`` at all (a device token or nothing - a panel's standing binding is not a person), a degraded auth
   check, a guest role, or a guest-shaped id. A guest never gets rows, not even an empty list that names a member.
2. **Own rows only.** There is no ``user_id`` parameter anywhere. ``provenance_answers.gather`` reads the caller's rows and
   drops anything another owner wrote.
3. **Private classes are counted, never read** (health, money, private, mood) - a wall display in a family room shows
   "3 private notes are not shown here", never their words; they are pulled by name by voice, as ever.
4. **Forget is the owner's alone, and gives no oracle.** A row that does not exist, belongs to someone else, or is already
   gone answers the same ``gone``; a row of the caller's is rejected with its twins, erased for real (when physical erase is on)
   and its verbatim words removed - the steps of the voice "forget it" (``provenance_answers.forget_it``).

Pure of HTTP: the router (``routers/memories.py``) turns :class:`SpaceRefused` into a status code.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import re
import time
from typing import Any, Optional

import memory_provenance as mp
import provenance_answers as pa

logger = logging.getLogger(__name__)

#: group key -> heading, in display order. ``dates`` is this screen's own group (birthdays, anniversaries, deadlines).
GROUP_LABELS = (
    ("people", "People"),
    ("preferences", "Preferences"),
    ("routines", "Routines"),
    ("dates", "Important dates"),
    ("places", "Places"),
    ("other", "Other"),
)
PRIVATE_LABELS = {"health": "health", "money": "money", "private": "personal", "mood": "feelings"}
#: rows shown per group (the rest are counted in ``more``): the payload stays small enough for a wall panel
GROUP_MAX_ITEMS = 40
PEOPLE_MAX = 30
TEXT_MAX_CHARS = 280

_DATES_RE = re.compile(r"\b(?:birthdays?|anniversar(?:y|ies)|born\s+on|due\s+date|deadline)\b", re.IGNORECASE)

#: a request must carry a session id; ``get_current_user`` also resolves a device token to the panel's bound user, and that
#: standing binding is not a person at the screen
SESSION_HEADER = "x-session-id"

SIGN_IN_DETAIL = "Sign in to see what Zoe knows about you."


class SpaceRefused(Exception):
    """The request may not see or change memories. ``status`` is the HTTP code, ``detail`` a safe sentence (never a row)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def require_member(user: Optional[dict], *, has_session_header: bool) -> str:
    """The member id behind a request, or :class:`SpaceRefused`. Fails closed on every doubt. Pure."""
    if not has_session_header:
        raise SpaceRefused(401, SIGN_IN_DETAIL)
    if not isinstance(user, dict) or user.get("auth_degraded"):
        raise SpaceRefused(401, SIGN_IN_DETAIL)
    uid = str(user.get("user_id") or "").strip()
    role = str(user.get("role") or "").strip().lower()
    if not uid or role in ("", "guest") or pa._is_guest(uid):
        raise SpaceRefused(403, SIGN_IN_DETAIL)
    try:
        from memory_service import is_guest_memory_user

        if is_guest_memory_user(uid):
            raise SpaceRefused(403, SIGN_IN_DETAIL)
    except SpaceRefused:
        raise
    except Exception:  # noqa: BLE001 - the shared predicate is a second wall; the local one above already held
        pass
    return uid


def _clean(text: str) -> str:
    return pa.trim(pa.second_person(text), TEXT_MAX_CHARS)


def _when(epoch: Optional[float]) -> str:
    if not epoch:
        return ""
    try:
        return pa.short_day(epoch)
    except Exception:  # noqa: BLE001
        return ""


#: how a row came to be held, in the member's words (``provenance``): the writer and the authority class decide, never a model
PROVENANCE_LABELS = {
    "curated": "Added for you",
    "day_review": "From your day review, approved by you",
    "confirmed": "You confirmed this",
    "told": "You told me",
    "derived": "From something you said",
    "inferred": "I worked this out",
}


def provenance_kind(it: Any) -> str:
    """``curated`` | ``day_review`` | ``confirmed`` | ``told`` | ``derived`` | ``inferred`` for a gathered item. Pure."""
    import memory_authority as auth

    origin = str(getattr(it, "origin", "") or "")
    cls = str(getattr(it, "cls", "") or "")
    if origin == CURATED_ORIGIN:
        return "curated"
    if origin == "omi_review":
        return "day_review"
    if cls == auth.USER_CONFIRMED:
        return "confirmed"
    if not getattr(it, "inferred", True):
        return "told"
    if cls == auth.USER_STATED_DERIVED:
        return "derived"
    return "inferred"


def provenance_label(it: Any) -> str:
    """"You told me on 3 Oct" / "From your day review, approved by you on 3 Oct" / "I worked this out (3 Oct)". Pure."""
    kind = provenance_kind(it)
    day = _when(getattr(it, "epoch", None))
    base = PROVENANCE_LABELS[kind]
    if kind == "curated" and getattr(it, "curated_by", ""):
        base = f"Added for you by {it.curated_by}"
    if not day:
        return base
    return f"{base} ({day})" if kind == "inferred" else f"{base} on {day}"


#: the STAGE of a row on the owner's own page, in four plain words, derived ONLY from the stored authority class / writer
#: (``memory_authority.RANK``), never from a model's judgement:
#:   added     - curated (6) or operator (6): someone else (a grown-up, an admin) put it there for the member
#:   confirmed - user_confirmed (5): the member approved it (the review page, a day review)
#:   told      - user_stated (4): the member's own words, or a deterministic extractor over them
#:   noticed   - user_stated_derived (3) and every lower class: Zoe's reading of what they said, a guess until confirmed
STAGE_LABELS = {"added": "Added for you", "confirmed": "Confirmed", "told": "You told me", "noticed": "I noticed"}

#: the channel a writer stands for, in the member's words (``came_from.channel``); an unknown writer has no channel, never a guess
CHANNEL_LABELS = {"voice": "By voice", "chat": "In chat", "pendant": "From your pendant"}
_CHAT_WRITERS = frozenset({"chat", "chat_regex", "chat_regex_fallback", "conversation", "conversation_correction", "turn_digest"})


def stage_of(it: Any) -> str:
    """``added`` | ``confirmed`` | ``told`` | ``noticed`` for a gathered item (see :data:`STAGE_LABELS`). Pure."""
    import memory_authority as auth

    origin = str(getattr(it, "origin", "") or "")
    cls = str(getattr(it, "cls", "") or "")
    if origin == CURATED_ORIGIN or cls in (auth.CURATED, auth.OPERATOR):
        return "added"
    if cls == auth.USER_CONFIRMED or origin == "omi_review":
        return "confirmed"
    if cls and auth.RANK.get(cls, -1) >= auth.USER_RANK:
        return "told"
    return "noticed"   # an unknown or unread class is the weakest stage, never promoted


def channel_of(meta: Optional[dict]) -> str:
    """``voice`` | ``chat`` | ``pendant`` | "" from the row's stored writer (``source`` / ``origin``). Pure."""
    import memory_authority as auth

    meta = meta or {}
    writers = {str(meta.get("source") or "").strip(), str(meta.get("origin") or "").strip()} - {""}
    if any(w.startswith("omi") for w in writers):
        return "pendant"
    if writers & auth.VOICE_LANE_WRITERS or any(w.startswith("voice") for w in writers):
        return "voice"
    if writers & _CHAT_WRITERS:
        return "chat"
    return ""


def came_from(it: Any) -> dict:
    """Where a row came from, for its OWNER's page: ``{quote, channel, channel_label, day, kept}``. Pure.

    ``quote`` is the owner's OWN words only (``provenance_answers.owner_quote``: the excerpt of a per-turn writer after the
    own-words wall - never a pasted text, an unverified voice, a model's paraphrase or another speaker's words). A row that keeps
    a source the wall will not quote (a pendant recording, a curator's note, a transcript pass) gives the day and channel only
    (``kept`` True, ``quote`` ""); a row with no source record at all is ``kept`` False ("source not kept").
    """
    meta = getattr(it, "meta", None) or {}
    try:
        quote = pa.owner_quote(meta, getattr(it, "text", "") or "") if meta else ""
    except Exception:  # noqa: BLE001 - a quote that cannot be checked is not shown
        quote = ""
    channel = channel_of(meta)
    kept = bool(quote or str(meta.get("source_excerpt") or "").strip() or str(meta.get("user_turn_id") or "").strip())
    return {"quote": quote, "channel": channel, "channel_label": CHANNEL_LABELS.get(channel, ""),
            "day": _when(getattr(it, "epoch", None)), "kept": kept}


def _item(it: Any, mutes: Optional[list] = None) -> dict:
    stage = stage_of(it)
    out = {
        "id": it.id,
        "text": _clean(it.text),
        "when": _when(it.epoch),
        "epoch": it.epoch,
        # "told": the owner's own words (or a fact they stated); "inferred": Zoe picked it up from what they said
        "source": "inferred" if it.inferred else "told",
        "provenance": provenance_kind(it),
        "provenance_label": provenance_label(it),
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "came_from": came_from(it),
    }
    if mutes is not None:   # None = the mute list is unknown (restraint not enforcing, or the read failed): say nothing
        import restraint

        out["muted"] = bool(restraint.muted(it.text, str(it.id), mutes))
    return out


def group_key(it: Any) -> str:
    """The screen's group for an item: ``pa.group_of``'s, plus ``dates`` (a birthday is a date before it is a person's)."""
    if _DATES_RE.search(it.text or ""):
        return "dates"
    return it.group


def build_view(known: "pa.Known", *, mutes: Optional[list] = None, let_go: Optional[list] = None) -> dict:
    """The wire shape of what Zoe knows, from an already-gathered, OWN-rows-only ``Known``. Pure.

    ``groups`` are non-empty only, in display order; each is ``{key, label, kind, entries|items, more}``. People are merged
    entries (one per person: name, relationship, facts) with no row id - a person is forgotten by voice, which asks for the PIN.
    ``private`` maps a class to a COUNT. No private text ever appears. ``mutes`` (the owner's ACTIVE, ENFORCED mutes) marks each item
    ``muted``; ``let_go`` (already-walled rows, :func:`read_let_go`) is passed through; either None leaves its key out.
    """
    buckets: dict = {key: [] for key, _ in GROUP_LABELS}
    for it in known.items:
        buckets.setdefault(group_key(it), buckets["other"]).append(it)
    groups = []
    for key, label in GROUP_LABELS:
        rows = buckets.get(key) or []
        if key == "people":
            entries = pa.people_entries(rows, known.people)
            if not entries:
                continue
            shown = [
                {
                    "name": e.name,
                    "relationship": (e.rel if e.rel and e.rel.lower() not in ("contact", "other", "unknown") else ""),
                    # an entry the parser could not name ("someone") is just its facts - no heading
                    "label": e.label() if (e.name or e.rel) else "",
                    "facts": [pa.trim(f, TEXT_MAX_CHARS) for f, _ in e.facts][:6],
                    "when": _when(e.epoch),
                }
                for e in entries[:PEOPLE_MAX]
            ]
            groups.append({"key": key, "label": label, "kind": "people", "entries": shown,
                           "more": max(0, len(entries) - len(shown))})
            continue
        if not rows:
            continue
        groups.append({"key": key, "label": label, "kind": "items", "items": [_item(i, mutes) for i in rows[:GROUP_MAX_ITEMS]],
                       "more": max(0, len(rows) - GROUP_MAX_ITEMS)})
    private = {PRIVATE_LABELS.get(c, c): len(v) for c, v in sorted((known.private or {}).items()) if v}
    view = {
        "groups": groups,
        "private": private,
        "unsure": int(known.unconfirmed or 0),
        # what the screen lists (people count once each) - NOT known.total, which also counts the private rows held back
        "total": sum(len(g.get("entries") or g.get("items") or []) + g["more"] for g in groups),
    }
    if let_go is not None:
        view["let_go"] = let_go
    return view


#: the "Let go" list: rows the store retired (``superseded`` / ``archived``), newest first, bounded. A forgotten (``rejected``)
#: row is NEVER listed: forgotten means forever.
LET_GO_STATUSES = ("superseded", "archived")
LET_GO_MAX = 20
#: why a row was let go, from a CLOSED list, only when the stored row says so (no reason = no reason shown)
LET_GO_REASONS = {"replaced": "Replaced", "duplicate": "Duplicate", "expired": "Expired", "by_you": "Archived by you"}


def _epoch_of(raw: Any) -> Optional[float]:
    """An epoch from a stored number or ISO-8601 string, else None. Pure."""
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def let_go_reason(meta: dict, user_id: str, *, now: Optional[float] = None) -> str:
    """The closed-list reason key for a retired row, or "" when the stored row does not say. Pure."""
    meta = meta or {}
    status = str(meta.get("status") or "").lower()
    if status == "superseded":
        return "replaced"
    if status != "archived":
        return ""
    if str(meta.get("duplicate_of") or "").strip():
        return "duplicate"
    exp = _epoch_of(meta.get("expires_at"))
    if exp is not None and exp <= (now if now is not None else time.time()):
        return "expired"
    if str(meta.get("reviewed_by") or "") == user_id:
        return "by_you"
    return ""


async def read_let_go(user_id: str, *, svc: Any = None) -> list:
    """The owner's retired rows for the "Let go" list, through the SAME walls as :func:`provenance_answers.gather`: own rows only,
    forgotten entities dropped, no pasted / instruction-shaped / unverified / recorded-change rows, and a private class is left
    out entirely (it is never text on this page). Read-only. Raises on a store failure (the caller leaves the list out)."""
    import memory_authority as auth
    import own_words

    svc = svc or pa._svc_default()
    rows: list = []
    for status in LET_GO_STATUSES:
        rows.extend(await asyncio.wait_for(svc.list_by_status(user_id=user_id, status=status, limit=pa.SCAN_LIMIT, strict=True),
                                           pa.READ_BUDGET_S))
    rows = await pa._drop_forgotten(user_id, rows, pa._row_words)
    out = []
    for ref in rows:
        meta = ref.metadata or {}
        text = (ref.text or "").strip()
        if not text or not pa._owns(meta, user_id) or str(meta.get("status") or "").lower() not in LET_GO_STATUSES:
            continue
        if str(meta.get("memory_type") or "") == "state_change":
            continue
        if own_words.is_pasted_row(meta) or own_words.instruction_shaped(text) or auth.is_unverified(meta):
            continue
        if pa.sensitive_classes(text, meta):
            continue
        try:   # a guess no word of the owner's supports is never said back (gather's "unconfirmed" rule); unreadable = left out
            if (str(meta.get("authority_basis") or "") in auth.NO_STANDING_BASES
                    and auth.row_rank(meta, text) < auth.DERIVED_RANK):
                continue
        except Exception:  # noqa: BLE001
            continue
        reason = let_go_reason(meta, user_id)
        epoch = pa._row_epoch(meta)
        out.append({"id": ref.id, "text": _clean(text), "when": _when(epoch), "epoch": epoch,
                    "reason": reason, "reason_label": LET_GO_REASONS.get(reason, "")})
    out.sort(key=lambda r: r["epoch"] or 0.0, reverse=True)
    return out[:LET_GO_MAX]


async def _enforced_mutes(user_id: str) -> Optional[list]:
    """The owner's ACTIVE mutes when ``ZOE_RESTRAINT`` enforces them, else None. In ``shadow`` a mute is recorded but does not
    bite, so the page must not say "muted"; a failed read is None too (unknown is never "not muted")."""
    try:
        import restraint

        if restraint.mode() != "enforce":
            return None
        return list(await restraint.list_mutes_strict(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.info("memory_space: mute read skipped (%s)", type(exc).__name__)
        return None


async def read_view(user_id: str, *, svc: Any = None) -> dict:
    """What Zoe knows about ``user_id`` (own rows only). Raises on a store failure - the caller says so, it never shows 'nothing'.
    The "Let go" list and the mute marks are extras: their failure leaves their key out, never the view."""
    known = await pa.gather(user_id, svc=svc)
    mutes = await _enforced_mutes(user_id)
    try:
        let_go = await read_let_go(user_id, svc=svc)
    except Exception as exc:  # noqa: BLE001
        logger.info("memory_space: let-go read skipped (%s)", type(exc).__name__)
        let_go = None
    return build_view(known, mutes=mutes, let_go=let_go)


#: a curated row (``origin="curator"``, memories-space 2.3 / owner D7 + D9): only an ADULT member's own word changes it
CURATED_ORIGIN = "curator"


async def curated_locked(user_id: str, meta: Optional[dict]) -> bool:
    """True when ``meta`` is a curated row and ``user_id`` is not known to be an adult member (owner D9, 2026-10-19: a child's
    passing remark never overwrites what an admin curated for them). Fail-closed: an unreadable role locks a curated row. Rows
    that are not curated are never locked (no read at all).

    Adulthood comes from the AUTHORITATIVE account (``manner_block._account_is_adult``: no minor / kid ``member_modes`` row AND an
    adult ``auth_users`` role) - never the data-plane ``users`` mirror, which ``_ensure_user_and_chat_session`` fills with
    ``member`` for every chatting account, children included."""
    if str((meta or {}).get("origin") or "") != CURATED_ORIGIN:
        return False
    import memory_authority as auth

    if str((meta or {}).get("authority_class") or "") == auth.CURATED:
        return True            # protected (a child, or an adult who opted this category in): a curator changes it (P7)
    try:
        import manner_block
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            adult = await manner_block._account_is_adult(user_id, db=db)
    except Exception:  # noqa: BLE001
        return True
    return not adult


async def forget_row(user_id: str, row_id: str, *, svc: Any = None) -> str:
    """Forget ONE of the caller's rows: ``'forgotten'`` | ``'gone'`` (absent, someone else's, or already forgotten - one answer)
    | ``'protected'`` (a curated row a child may not remove, :func:`curated_locked`).

    The steps of ``provenance_answers.forget_it``: reject the row and its twins (one utterance leaves several), erase them for
    real when physical erase is on, remove the owner's verbatim words. RAISES when a step could not finish (the row may already
    be hidden): calling again redoes EVERY remaining step - a row that is already rejected is finished the same way, twins and
    erase included - so the caller must not report a forget that did not hold.
    """
    svc = svc or pa._svc_default()
    rid = str(row_id or "").strip()
    if not rid:
        return "gone"
    ref = await asyncio.wait_for(svc.get(rid), pa.READ_BUDGET_S)
    if ref is None or not pa._owns(ref.metadata or {}, user_id):
        return "gone"
    status = str((ref.metadata or {}).get("status") or "approved").lower()
    if status not in ("approved", "rejected"):
        return "gone"          # pending / disputed / archived rows are not on the screen: nothing here to forget
    if await curated_locked(user_id, ref.metadata):
        return "protected"
    import os_shadow
    await os_shadow.forget_for_erase(user_id)
    exp = _explained_for(rid, ref)
    twins = await pa.twins_of(user_id, ref, svc=svc, strict=True)    # a failed lookup stops us BEFORE the forget: retryable
    plan = await pa._plan_forget(user_id, [ref.id, *(tw.id for tw in twins)], exp, ref.text or "")   # PARK FIRST (os_shadow.plan_erase)
    if status == "approved":
        if await svc.review(ref.id, decision="reject", actor=user_id, note="forget_memory_space") is None:
            await plan.drop()
            raise RuntimeError("memory store refused the forget")
    gone = [ref.id]
    for tw in twins:
        if await svc.review(tw.id, decision="reject", actor=user_id, note="forget_memory_space") is None:
            raise RuntimeError("memory store refused a twin's forget")
        gone.append(tw.id)
    physical = False
    if hasattr(svc, "erase_rows"):
        from memory_service import physical_erase_enabled

        if physical_erase_enabled():
            # a failed physical erase is NOT "forgotten": the text is still on disk. The rows stay rejected; a retry finishes it.
            erased = await svc.erase_rows(user_id, gone, actor=user_id, reason="forgotten by request (memories screen)")
            plan.note_erase(result=erased)
            physical = True
    erased = await pa._erase_words(user_id, exp, ref.text or "")
    await plan.ok()
    logger.info("MEMORY_SPACE_FORGET user=%s rows=%d", user_id, len(gone))
    if status == "rejected" and len(gone) == 1 and not erased and not physical:
        return "gone"          # already forgotten earlier and nothing was left to remove
    return "forgotten"


def _explained_for(rid: str, ref: Any) -> "mp.Explained":
    """The row as a forget target WITH its provenance: an extracted memory's text ("User was scared of the thunder") is not the child's
    verbatim utterance, so the erase must also use the row's stored turn id and quote, or the words stay searchable."""
    meta = ref.metadata or {}
    return mp.Explained(seq=0, row_id=rid, ts=0.0, text=ref.text or "", quote=str(meta.get("source_excerpt") or ""),
                        turn_id=str(meta.get("user_turn_id") or ""))


def guardian_forget_open(meta: dict) -> bool:
    """True when a row is one a guardian's forget may act on: still approved, OR rejected BY an earlier guardian forget that did not
    finish (its twins / physical erase / verbatim words may remain), so the retry resumes every remaining step instead of the review
    route treating the already-rejected row as an ordinary reject and reporting success."""
    status = str((meta or {}).get("status") or "approved").lower()
    return status == "approved" or (status == "rejected" and (meta or {}).get("review_note") == "guardian_forget")


async def guardian_forget(owner: str, row_id: str, *, actor: str, svc: Any = None) -> str:
    """An ADMIN forgets ONE approved row of ANOTHER member on their behalf - the grown-up's one step for a child, whose own
    screen and voice forget are refused ("ask a grown-up"). The same steps as :func:`forget_row` (the row AND the twins one
    utterance left, physical erase when on, the member's verbatim words), but every reject is the admin's
    (``origin="admin"``, operator class), never the member's own. ``'forgotten'`` | ``'gone'`` (absent, not ``owner``'s, or
    not approved). RAISES when a step could not finish; calling again finishes it. Minor-affect safety cell (c),
    docs/governance/emotional-safety-note.md section 4.1: a feeling must not survive the guardian's forget in a twin."""
    svc = svc or pa._svc_default()
    rid = str(row_id or "").strip()
    ref = await asyncio.wait_for(svc.get(rid), pa.READ_BUDGET_S) if rid else None
    if ref is None or not pa._owns(ref.metadata or {}, owner):
        return "gone"
    if not guardian_forget_open(ref.metadata or {}):
        return "gone"
    import os_shadow
    await os_shadow.forget_for_erase(owner)
    twins = await pa.twins_of(owner, ref, svc=svc, strict=True)     # a failed lookup stops us BEFORE the forget
    plan = await pa._plan_forget(owner, [ref.id, *(tw.id for tw in twins)], _explained_for(rid, ref), ref.text or "")   # PARK FIRST
    gone = []
    for row in (ref, *twins):
        if str((row.metadata or {}).get("status") or "").lower() == "rejected":
            gone.append(row.id)                  # this row's reject already landed (an earlier, unfinished attempt): go on to the rest
            continue
        if await svc.review(row.id, decision="reject", actor=actor, origin="admin", note="guardian_forget") is None:
            raise RuntimeError("memory store refused the guardian's forget")
        gone.append(row.id)
    if hasattr(svc, "erase_rows"):
        from memory_service import physical_erase_enabled

        if physical_erase_enabled():
            erased = await svc.erase_rows(owner, gone, actor=actor, reason="forgotten by a guardian")
            plan.note_erase(result=erased)
    await pa._erase_words(owner, _explained_for(rid, ref), ref.text or "")
    await plan.ok()
    logger.info("MEMORY_SPACE_GUARDIAN_FORGET rows=%d", len(gone))
    return "forgotten"


async def correct_row(user_id: str, row_id: str, new_text: str, *, utterance: str = "", session_id: Optional[str] = None,
                      svc: Any = None) -> str:
    """Correct ONE of the caller's rows to ``new_text`` through the EXISTING correction path
    (``correction_apply.apply_row_correction``: ``MemoryService.review(edit)`` supersedes - the old row is kept as history with
    ``superseded_by_id``, the new one carries the correcting words as its evidence under the user-class writer). The twin rows the
    same utterance left are retired too (``provenance_answers.fix``'s rule), so the old value cannot come back through a sibling.

    ``'corrected'`` | ``'gone'`` (absent / someone else's / not approved - one answer, no oracle) | ``'protected'`` (a curated
    row, :func:`curated_locked`) | ``'off'`` (``ZOE_CORRECTION_APPLY`` is off) | ``'refused'`` (no change, an opt-out, or the
    store refused the edit - e.g. it names a forgotten entity). Raises when the store cannot be read, or when a twin could not be
    retired (the same call again finishes it: a superseded row whose successor already holds ``new_text`` retires the rest).
    """
    import correction_apply

    svc = svc or pa._svc_default()
    rid = str(row_id or "").strip()
    new = " ".join(str(new_text or "").split())[:TEXT_MAX_CHARS]
    if not rid:
        return "gone"
    ref = await asyncio.wait_for(svc.get(rid), pa.READ_BUDGET_S)
    if ref is None or not pa._owns(ref.metadata or {}, user_id):
        return "gone"
    status = str((ref.metadata or {}).get("status") or "approved").lower()
    if status == "superseded" and new:
        # a RETRY of this very correction whose twin retire did not finish: the successor already holds ``new`` - finish the twins
        succ_id = str((ref.metadata or {}).get("superseded_by_id") or "")
        succ = await asyncio.wait_for(svc.get(succ_id), pa.READ_BUDGET_S) if succ_id else None
        if (succ is not None and pa._owns(succ.metadata or {}, user_id)
                and " ".join((succ.text or "").split()) == new and correction_apply.enabled()):
            twins = [tw for tw in await pa.twins_of(user_id, ref, svc=svc, strict=True) if tw.id != succ_id]
            await retire_twins(user_id, twins, svc)
            logger.info("MEMORY_SPACE_CORRECT user=%s twins=%d (retry finished)", user_id, len(twins))
            return "corrected"
        return "gone"
    if status != "approved":
        return "gone"
    if await curated_locked(user_id, ref.metadata):
        return "protected"
    if not correction_apply.enabled():
        return "off"
    if not new:
        return "refused"
    twins = await pa.twins_of(user_id, ref, svc=svc, strict=True)     # a failed lookup stops us BEFORE the edit: retryable, no blind write
    res = await correction_apply.apply_row_correction(user_id, rid, new, utterance=utterance or new, session_id=session_id,
                                                      svc=svc)
    if res is None:
        return "refused"
    await retire_twins(user_id, twins, svc)
    logger.info("MEMORY_SPACE_CORRECT user=%s twins=%d", user_id, len(twins))
    return "corrected"


async def retire_twins(user_id: str, twins: list, svc: Any) -> None:
    """Reject every twin that still holds the old value. RAISES when one could not be retired (the store refused or failed): a
    correction is not acknowledged while a sibling still recalls the old value; calling :func:`correct_row` again with the same
    words finishes the remaining twins (its superseded-retry path)."""
    failed = 0
    for tw in twins:
        try:
            if await svc.review(tw.id, decision="reject", actor=user_id, note="superseded by a correction") is None:
                failed += 1
        except Exception as exc:  # noqa: BLE001 - counted; the caller hears "not finished"
            logger.warning("memory_space: twin retire failed (%s)", type(exc).__name__)
            failed += 1
    if failed:
        raise RuntimeError(f"memory store did not retire {failed} twin row(s) of a correction")
