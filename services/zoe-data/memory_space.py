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
import logging
import re
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
    "day_review": "From your day review, approved by you",
    "confirmed": "You confirmed this",
    "told": "You told me",
    "derived": "From something you said",
    "inferred": "I worked this out",
}


def provenance_kind(it: Any) -> str:
    """``day_review`` | ``confirmed`` | ``told`` | ``derived`` | ``inferred`` for a gathered item. Pure."""
    import memory_authority as auth

    origin = str(getattr(it, "origin", "") or "")
    cls = str(getattr(it, "cls", "") or "")
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
    if not day:
        return base
    return f"{base} ({day})" if kind == "inferred" else f"{base} on {day}"


def _item(it: Any) -> dict:
    return {
        "id": it.id,
        "text": _clean(it.text),
        "when": _when(it.epoch),
        "epoch": it.epoch,
        # "told": the owner's own words (or a fact they stated); "inferred": Zoe picked it up from what they said
        "source": "inferred" if it.inferred else "told",
        "provenance": provenance_kind(it),
        "provenance_label": provenance_label(it),
    }


def group_key(it: Any) -> str:
    """The screen's group for an item: ``pa.group_of``'s, plus ``dates`` (a birthday is a date before it is a person's)."""
    if _DATES_RE.search(it.text or ""):
        return "dates"
    return it.group


def build_view(known: "pa.Known") -> dict:
    """The wire shape of what Zoe knows, from an already-gathered, OWN-rows-only ``Known``. Pure.

    ``groups`` are non-empty only, in display order; each is ``{key, label, kind, entries|items, more}``. People are merged
    entries (one per person: name, relationship, facts) with no row id - a person is forgotten by voice, which asks for the PIN.
    ``private`` maps a class to a COUNT. No private text ever appears.
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
        groups.append({"key": key, "label": label, "kind": "items", "items": [_item(i) for i in rows[:GROUP_MAX_ITEMS]],
                       "more": max(0, len(rows) - GROUP_MAX_ITEMS)})
    private = {PRIVATE_LABELS.get(c, c): len(v) for c, v in sorted((known.private or {}).items()) if v}
    return {
        "groups": groups,
        "private": private,
        "unsure": int(known.unconfirmed or 0),
        # what the screen lists (people count once each) - NOT known.total, which also counts the private rows held back
        "total": sum(len(g.get("entries") or g.get("items") or []) + g["more"] for g in groups),
    }


async def read_view(user_id: str, *, svc: Any = None) -> dict:
    """What Zoe knows about ``user_id`` (own rows only). Raises on a store failure - the caller says so, it never shows 'nothing'."""
    known = await pa.gather(user_id, svc=svc)
    return build_view(known)


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
    exp = mp.Explained(seq=0, row_id=rid, ts=0.0, text=ref.text or "")
    twins = await pa.twins_of(user_id, ref, svc=svc)
    if status == "approved":
        if await svc.review(ref.id, decision="reject", actor=user_id, note="forget_memory_space") is None:
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
            await svc.erase_rows(user_id, gone, actor=user_id, reason="forgotten by request (memories screen)")
            physical = True
    erased = await pa._erase_words(user_id, exp, ref.text or "")
    logger.info("MEMORY_SPACE_FORGET user=%s rows=%d", user_id, len(gone))
    if status == "rejected" and len(gone) == 1 and not erased and not physical:
        return "gone"          # already forgotten earlier and nothing was left to remove
    return "forgotten"


async def correct_row(user_id: str, row_id: str, new_text: str, *, utterance: str = "", session_id: Optional[str] = None,
                      svc: Any = None) -> str:
    """Correct ONE of the caller's rows to ``new_text`` through the EXISTING correction path
    (``correction_apply.apply_row_correction``: ``MemoryService.review(edit)`` supersedes - the old row is kept as history with
    ``superseded_by_id``, the new one carries the correcting words as its evidence under the user-class writer). The twin rows the
    same utterance left are retired too (``provenance_answers.fix``'s rule), so the old value cannot come back through a sibling.

    ``'corrected'`` | ``'gone'`` (absent / someone else's / not approved - one answer, no oracle) | ``'protected'`` (a curated
    row, :func:`curated_locked`) | ``'off'`` (``ZOE_CORRECTION_APPLY`` is off) | ``'refused'`` (no change, an opt-out, or the
    store refused the edit - e.g. it names a forgotten entity). Raises when the store cannot be read.
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
    if str((ref.metadata or {}).get("status") or "approved").lower() != "approved":
        return "gone"
    if await curated_locked(user_id, ref.metadata):
        return "protected"
    if not correction_apply.enabled():
        return "off"
    if not new:
        return "refused"
    twins = await pa.twins_of(user_id, ref, svc=svc)
    res = await correction_apply.apply_row_correction(user_id, rid, new, utterance=utterance or new, session_id=session_id,
                                                      svc=svc)
    if res is None:
        return "refused"
    for tw in twins:
        try:
            await svc.review(tw.id, decision="reject", actor=user_id, note="superseded by a correction")
        except Exception as exc:  # noqa: BLE001 - the corrected row already stands
            logger.debug("memory_space: twin retire skipped (%s)", type(exc).__name__)
    logger.info("MEMORY_SPACE_CORRECT user=%s twins=%d", user_id, len(twins))
    return "corrected"
