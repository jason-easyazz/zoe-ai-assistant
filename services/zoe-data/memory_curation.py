"""memory_curation - caregiver curation (memories-space P7; owner decisions D7 / D9 / D10, 2026-10-19).

A household admin adds and edits memories ABOUT a child ("Mia's teacher is Ms Lee", "swimming is on Tuesdays") from the desktop
or phone Memories page, so the child can ask Zoe later. The rules, each enforced here and pinned by ``tests/test_memory_curation.py``:

* **Who curates (D7).** Every ADULT ADMIN is the implicit curator of every CHILD: no grant row. "Admin" is the session role AND the
  authoritative ``auth_users`` role; "adult" and "child" come from the account (``manner_block._account_is_adult``, ``member_modes``),
  never the data-plane ``users`` mirror. A guest, a child, a non-admin adult, a panel (device token or kiosk credential) and a
  request without a session never curate.
* **Adults (D9).** A member own word wins: an admin curates another ADULT only in a category that adult opted in THEMSELF
  (``memory_curation_optins``, default none). D8 (carers acting without consent) is a legal question and is not built.
* **Authority (D9).** A curated row is written by the ``curator`` writer: class ``curated`` (``memory_authority``), above the
  member own passing statements and review UI. A contradicting remark of the child is held as a ``disputed`` note against it
  (``contradicts_id``) for the curator to keep or accept here. When an adult opts a category out again, its curated rows are
  released (restamped ``user_stated``: the adult word wins again); opting back in protects them again.
* **Sensitive (D10).** Health, medication and money are refused unless a curator turned that category on for the child (or the
  adult opted it in). The category is checked against the TEXT too (``restraint`` + ``provenance_answers.sensitive_classes``):
  "allergic to peanuts" filed under Routines is refused, not hidden. Feelings, family conflict and grief are never curated.
* **Provenance.** Every row carries ``curated_by`` / ``curated_by_name`` / ``curated_at`` / ``curated_via`` / ``curated_category``
  / ``curated_sensitive`` (first-class, ``memory_service.CURATED_KEYS``) beside the writer stamps. Every action is audited in
  ``memory_curation_audit`` BEFORE it runs (fail-closed) and again when it finished: ids and a category, never text.
* **Never on the wall (D11).** A sensitive curated row is private by category (``provenance_answers.sensitive_classes``), so the
  panel counts it and never shows or speaks it. The curation routes refuse a panel outright.
* **Forget is audited.** A curator forget rejects the row through ``MemoryService.review`` (its audit row), then erases it for
  real when physical erase is on - the same steps as any forget. The child cannot forget or correct it (``memory_space``).

A curator sees ONLY the curated rows of the member and the notes held against them - never the other memories of the member.
Pure of HTTP: ``routers/memories_curation.py`` turns :class:`CurationRefused` into a status code. Flag ``ZOE_MEMORIES_CURATORS``
(default OFF): off = the routes answer 404 and nothing here runs.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from typed_env import env_bool

logger = logging.getLogger(__name__)

WRITER = "curator"
CATEGORIES = ("routines", "people", "school", "dates", "likes", "other")
SENSITIVE = ("health", "medication", "money")
ALL_CATEGORIES = CATEGORIES + SENSITIVE
LABELS = {"routines": "Routines", "people": "People", "school": "School", "dates": "Important dates", "likes": "Likes",
          "other": "Other", "health": "Health", "medication": "Medication", "money": "Money"}
#: classes a curator never files for someone else (feelings are the member own; conflict and grief are not notes)
NEVER = frozenset({"affect", "family_conflict", "grief", "mood"})
TEXT_MAX = 280
ADMIN_ROLES = frozenset({"admin", "family-admin"})


class CurationRefused(Exception):
    """``status`` is the HTTP code, ``detail`` a safe sentence (never row text of anyone)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def enabled() -> bool:
    """``ZOE_MEMORIES_CURATORS`` - default OFF, read per call."""
    return env_bool("ZOE_MEMORIES_CURATORS", default=False)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _rows(db, sql: str, params: tuple = ()) -> list:
    cur = await db.execute(sql, params)
    return list(await cur.fetchall())


# -- accounts ---------------------------------------------------------------------------------------

@dataclass
class Account:
    user_id: str
    name: str
    role: str
    kind: str            # "adult" | "child" | "" (unknown / guest / not a household member)

    @property
    def short(self) -> str:
        return (self.name or "").split(" ")[0] or self.user_id


async def account(db, user_id: str) -> Account:
    """The AUTHORITATIVE account: ``auth_users`` role + ``member_modes``. Unknown = kind "". Raises on a failed read."""
    import manner_block
    import persona_layer

    uid = (user_id or "").strip()
    got = await _rows(db, "SELECT role FROM auth_users WHERE user_id = ?", (uid,)) if uid else []
    if not got or manner_block.is_guest(uid):
        return Account(uid, "", "", "")
    role = str(got[0][0] or "").strip().lower()
    names = await _rows(db, "SELECT name FROM users WHERE id = ?", (uid,))
    name = str((names[0][0] if names else "") or "").strip()[:40] or uid
    if await manner_block._account_is_adult(uid, db=db):
        kind = "adult"
    else:
        member = await persona_layer.load_member_mode(uid, db=db)
        kind = "child" if (role == "child" or member.minor or member.mode in manner_block.MINOR_MODES) else ""
    return Account(uid, name, role, kind)


async def require_curator(db, user: dict) -> Account:
    """The curator behind an already-identified member session (``memory_space.require_member`` ran first): an adult admin."""
    me = await account(db, str(user.get("user_id") or ""))
    if str(user.get("role") or "") not in ADMIN_ROLES or me.role not in ADMIN_ROLES or me.kind != "adult":
        raise CurationRefused(403, "Only a grown-up household admin can add memories for someone.")
    return me


# -- opt-ins ----------------------------------------------------------------------------------------

async def live_optins(db, member_id: str) -> dict:
    """category -> who turned it on, for the live (not revoked) opt-ins of ``member_id``."""
    rows = await _rows(db, "SELECT category, opted_in_by FROM memory_curation_optins WHERE member_user_id = ? "
                           "AND revoked_at IS NULL", (member_id,))
    return {str(r[0]): str(r[1]) for r in rows}


@dataclass
class Policy:
    member: Account
    allowed: frozenset
    sensitive_on: frozenset = field(default_factory=frozenset)


async def policy(db, curator: Account, member_id: str) -> Policy:
    """What ``curator`` may curate for ``member_id``. A child: every ordinary category, plus the sensitive ones a curator turned
    on. An adult: only what that adult opted in THEMSELF. Refuses yourself, a non-member and an adult with no opt-in."""
    if member_id == curator.user_id:
        raise CurationRefused(400, "These are your own memories: add them on your own Memories page.")
    member = await account(db, member_id)
    if member.kind not in ("adult", "child"):
        raise CurationRefused(404, "That is not someone in the household.")
    opts = await live_optins(db, member.user_id)
    if member.kind == "child":
        on = frozenset(c for c in SENSITIVE if c in opts)
        return Policy(member, frozenset(CATEGORIES) | on, on)
    own = frozenset(c for c, by in opts.items() if by == member.user_id and c in ALL_CATEGORIES)
    if not own:
        raise CurationRefused(403, f"{member.short} has not asked for help with any memories.")
    return Policy(member, own, frozenset(c for c in own if c in SENSITIVE))


def _sensitive_of(text: str) -> set:
    import provenance_answers as pa
    import restraint

    return set(restraint.classify(text)).union(pa.sensitive_classes(text))


def check_text(text: str, category: str, pol: Policy) -> tuple:
    """``(clean text, category, sensitive)`` or :class:`CurationRefused`. The category must be allowed, and the words must not be
    a sensitive class the category does not name (health words filed under Routines are refused, not hidden)."""
    clean = " ".join(str(text or "").split())[:TEXT_MAX]
    cat = str(category or "").strip().lower()
    if len(clean) < 3:
        raise CurationRefused(422, "Write the memory in a short sentence.")
    if cat not in ALL_CATEGORIES:
        raise CurationRefused(422, "Pick a category for this memory.")
    found = _sensitive_of(clean)
    if found.intersection(NEVER):
        raise CurationRefused(422, "Feelings, family arguments and grief are not something I keep notes about for someone else.")
    needs = set(c for c in ("health", "money") if c in found)
    if cat in SENSITIVE:
        needs.add("health" if cat == "medication" else cat)
    for need in sorted(needs):
        fits = cat in (("health", "medication") if need == "health" else (need,))
        if not fits:
            raise CurationRefused(422, f"That sounds like {LABELS[need].lower()} information. File it under {LABELS[need]}.")
    if cat not in pol.allowed:
        who = pol.member.short
        if pol.member.kind == "child":
            raise CurationRefused(403, f"{LABELS[cat]} notes for {who} are off. Turn them on first.")
        raise CurationRefused(403, f"{who} has not asked for help with {LABELS[cat].lower()}.")
    return clean, cat, cat in SENSITIVE


def _store_text(clean: str, member: Account) -> str:
    """The memory store convention ("User ..."): a sentence that starts with the member name names them as the user."""
    first = (member.name or "").split(" ")[0]
    if first and len(first) >= 2:
        m = re.match(rf"^{re.escape(first)}(’s|\x27s|s\x27)?\b", clean, re.IGNORECASE)
        if m:
            return ("User" + ("\x27s" if m.group(1) else "")) + clean[m.end():]
    return clean


def show_text(stored: str, member: Account) -> str:
    """The stored row as the curator reads it: "User" back to the member name."""
    first = (member.name or "").split(" ")[0] or "They"
    return re.sub(r"^User\b", first, stored or "")


# -- audit (fail-closed) ----------------------------------------------------------------------------

def row_ref(row_id: str) -> str:
    return hashlib.sha256(str(row_id or "").encode()).hexdigest()[:16] if row_id else ""


async def audit(db, *, actor: str, member: str, action: str, phase: str, category: str = "", row_id: str = "",
                ok: Optional[bool] = None) -> None:
    """One audit row: ids, an action word, a category and a hash of the row id - never text. RAISES when it cannot be written."""
    await db.execute("INSERT INTO memory_curation_audit (id, at, actor_user_id, member_user_id, action, category, row_ref, phase, ok) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (uuid.uuid4().hex, _now(), actor, member, action, category or None, row_ref(row_id) or None, phase,
                      None if ok is None else (1 if ok else 0)))
    await db.commit()


async def _audited(db, actor: str, member: str, action: str, category: str = "", row_id: str = "") -> None:
    try:
        await audit(db, actor=actor, member=member, action=action, phase="requested", category=category, row_id=row_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("memory_curation: audit not writable (%s) - nothing changed", type(exc).__name__)
        raise CurationRefused(503, "The audit log is unavailable, so nothing was changed.")


async def _finished(db, actor: str, member: str, action: str, ok: bool, category: str = "", row_id: str = "") -> None:
    try:
        await audit(db, actor=actor, member=member, action=action, phase="finished", category=category, row_id=row_id, ok=ok)
    except Exception as exc:  # noqa: BLE001 - the change is made; the requested row already names it
        logger.warning("memory_curation: finished-audit not written (%s)", type(exc).__name__)


# -- rows -------------------------------------------------------------------------------------------

def _svc(svc: Any = None) -> Any:
    if svc is not None:
        return svc
    from memory_service import get_memory_service

    return get_memory_service()


def _is_curated(meta: dict, member_id: str) -> bool:
    return (str(meta.get("origin") or "") == WRITER
            and str(meta.get("user_id") or meta.get("wing") or "") == member_id)


def _view(ref: Any, member: Account) -> dict:
    md = ref.metadata or dict()
    return dict(id=ref.id, text=show_text(ref.text, member), category=str(md.get("curated_category") or "other"),
                sensitive=bool(md.get("curated_sensitive")), added_by=str(md.get("curated_by_name") or ""),
                added_at=str(md.get("curated_at") or ""))


async def curated_rows(pol: Policy, *, svc: Any = None) -> list:
    """The curated rows of the member the curator may see: a child all, an adult only the protected ones in opted-in categories."""
    import memory_authority as auth

    out = []
    for ref in await _svc(svc).list_by_status(user_id=pol.member.user_id, status="approved", limit=1000):
        md = ref.metadata or dict()
        if not _is_curated(md, pol.member.user_id):
            continue
        if pol.member.kind == "adult" and (md.get("authority_class") != auth.CURATED
                                           or str(md.get("curated_category") or "") not in pol.allowed):
            continue
        out.append(ref)
    return out


async def _one(pol: Policy, row_id: str, svc: Any) -> Any:
    rows = dict((r.id, r) for r in await curated_rows(pol, svc=svc))
    ref = rows.get(str(row_id or ""))
    if ref is None:
        raise CurationRefused(404, "That note is not there any more.")
    return ref


def _stamps(curator: Account, cat: str, sensitive: bool, via: str) -> dict:
    return dict(curated_by=curator.user_id, curated_by_name=curator.name, curated_at=_now(), curated_via=via,
                curated_category=cat, curated_sensitive=bool(sensitive))


async def add(db, curator: Account, member_id: str, text: str, category: str, *, via: str = "desktop", svc: Any = None) -> dict:
    pol = await policy(db, curator, member_id)
    clean, cat, sensitive = check_text(text, category, pol)
    await _audited(db, curator.user_id, pol.member.user_id, "add", cat)
    ref = await _svc(svc).ingest(
        _store_text(clean, pol.member), user_id=pol.member.user_id, source=WRITER, origin=WRITER, memory_type="fact",
        confidence=0.95, status="approved", scope="personal", tags=["curated", "curated:" + cat],
        metadata=_stamps(curator, cat, sensitive, via))
    await _finished(db, curator.user_id, pol.member.user_id, "add", ref is not None, cat, getattr(ref, "id", ""))
    if ref is None or str((ref.metadata or dict()).get("status") or "") != "approved":
        raise CurationRefused(409, "I could not save that one.")
    logger.info("MEMORY_CURATED action=add member_kind=%s category=%s", pol.member.kind, cat)
    return _view(ref, pol.member)


async def edit(db, curator: Account, member_id: str, row_id: str, text: str, *, via: str = "desktop", svc: Any = None) -> dict:
    pol = await policy(db, curator, member_id)
    old = await _one(pol, row_id, svc)
    clean, cat, sensitive = check_text(text, str((old.metadata or dict()).get("curated_category") or "other"), pol)
    await _audited(db, curator.user_id, pol.member.user_id, "edit", cat, old.id)
    ref = await _svc(svc).review(old.id, decision="edit", actor=curator.user_id, origin=WRITER,
                                 edits=_store_text(clean, pol.member), note="edited by a curator",
                                 metadata=_stamps(curator, cat, sensitive, via))
    await _finished(db, curator.user_id, pol.member.user_id, "edit", ref is not None, cat, old.id)
    if ref is None:
        raise CurationRefused(409, "I could not change that one.")
    return _view(ref, pol.member)


async def forget(db, curator: Account, member_id: str, row_id: str, *, svc: Any = None) -> None:
    """The audited forget: reject through ``MemoryService.review`` (its audit row), then erase for real when physical erase is on."""
    from memory_service import physical_erase_enabled

    svc = _svc(svc)
    pol = await policy(db, curator, member_id)
    old = await _one(pol, row_id, svc)
    cat = str((old.metadata or dict()).get("curated_category") or "")
    await _audited(db, curator.user_id, pol.member.user_id, "forget", cat, old.id)
    ok = await svc.review(old.id, decision="reject", actor=curator.user_id, origin=WRITER, note="removed by a curator") is not None
    if ok and hasattr(svc, "erase_rows") and physical_erase_enabled():
        await svc.erase_rows(pol.member.user_id, [old.id], actor=curator.user_id, reason="curated note removed by a curator")
    await _finished(db, curator.user_id, pol.member.user_id, "forget", ok, cat, old.id)
    if not ok:
        raise CurationRefused(409, "I could not remove that one.")


async def held(db, curator: Account, member_id: str, *, svc: Any = None) -> list:
    """Notes held against curated rows: what the member said that disagreed (``disputed``, ``contradicts_id`` a curated row)."""
    pol = await policy(db, curator, member_id)
    svc = _svc(svc)
    mine = dict((r.id, r) for r in await curated_rows(pol, svc=svc))
    out = []
    for ref in await svc.list_by_status(user_id=pol.member.user_id, status="disputed", limit=500):
        against = mine.get(str((ref.metadata or dict()).get("contradicts_id") or ""))
        if against is not None:
            out.append(dict(id=ref.id, text=show_text(ref.text, pol.member), against=_view(against, pol.member)))
    return out


async def decide_held(db, curator: Account, member_id: str, held_id: str, action: str, *, svc: Any = None) -> dict:
    """``keep``: the curated note stands, the held note is dismissed. ``accept``: the curated note becomes what the member said."""
    if action not in ("keep", "accept"):
        raise CurationRefused(422, "Choose keep or accept.")
    svc = _svc(svc)
    notes = dict((h["id"], h) for h in await held(db, curator, member_id, svc=svc))
    note = notes.get(str(held_id or ""))
    if note is None:
        raise CurationRefused(404, "That note is not there any more.")
    out = dict(kept=True)
    if action == "accept":
        out = await edit(db, curator, member_id, note["against"]["id"], note["text"], svc=svc)
    cat = note["against"]["category"]
    await _audited(db, curator.user_id, member_id, "held_" + action, cat, held_id)
    ok = await svc.review(held_id, decision="reject", actor=curator.user_id, origin=WRITER,
                          note="held note: the curator chose " + action) is not None
    await _finished(db, curator.user_id, member_id, "held_" + action, ok, cat, held_id)
    return out


# -- opt-in settings --------------------------------------------------------------------------------

async def _set_optin(db, member_id: str, category: str, on: bool, actor: str) -> bool:
    """Turn ``category`` on / off for ``member_id``. True when something changed."""
    live = await live_optins(db, member_id)
    if on == (category in live):
        return False
    now = _now()
    if on:
        await db.execute("INSERT INTO memory_curation_optins (member_user_id, category, opted_in_by, opted_in_at, revoked_at, revoked_by) "
                         "VALUES (?, ?, ?, ?, NULL, NULL) ON CONFLICT (member_user_id, category) DO UPDATE SET opted_in_by = ?, "
                         "opted_in_at = ?, revoked_at = NULL, revoked_by = NULL", (member_id, category, actor, now, actor, now))
    else:
        await db.execute("UPDATE memory_curation_optins SET revoked_at = ?, revoked_by = ? WHERE member_user_id = ? AND category = ?",
                         (now, actor, member_id, category))
    await db.commit()
    return True


def _category_list(keys) -> list:
    return [dict(key=c, label=LABELS[c], sensitive=c in SENSITIVE) for c in ALL_CATEGORIES if c in keys]


async def set_child_sensitive(db, curator: Account, child_id: str, category: str, on: bool) -> dict:
    """A curator turns a sensitive category on / off for a CHILD (D10: off by default)."""
    cat = str(category or "").lower()
    if cat not in SENSITIVE:
        raise CurationRefused(422, "Only health, medication and money are switched on separately.")
    if child_id == curator.user_id:
        raise CurationRefused(400, "These are your own memories.")
    member = await account(db, child_id)
    if member.kind != "child":
        raise CurationRefused(403, "Only the person themself can turn this on for an adult.")
    action = "sensitive_on" if on else "sensitive_off"
    await _audited(db, curator.user_id, child_id, action, cat)
    changed = await _set_optin(db, child_id, cat, on, curator.user_id)
    await _finished(db, curator.user_id, child_id, action, True, cat)
    return dict(category=cat, on=on, changed=changed)


async def my_settings(db, user_id: str) -> dict:
    """The member own view: which categories are opted in (an adult may change them; a child sees what a grown-up turned on)."""
    me = await account(db, user_id)
    if me.kind not in ("adult", "child"):
        raise CurationRefused(403, "Sign in to see this.")
    live = await live_optins(db, user_id)
    on = sorted(c for c, by in live.items() if me.kind == "child" or by == user_id)
    return dict(kind=me.kind, categories=_category_list(ALL_CATEGORIES), on=on, editable=me.kind == "adult")


async def set_my_optin(db, user_id: str, category: str, on: bool, *, svc: Any = None) -> dict:
    """An ADULT opts one of their own categories in to (or out of) curation. Out releases the curated rows of that category
    (restamped ``user_stated``: their own word wins again); back in protects them again."""
    cat = str(category or "").lower()
    if cat not in ALL_CATEGORIES:
        raise CurationRefused(422, "Pick a category.")
    me = await account(db, user_id)
    if me.kind != "adult":
        raise CurationRefused(403, "A grown-up in your household looks after this for you.")
    action = "optin" if on else "optout"
    await _audited(db, user_id, user_id, action, cat)
    changed = await _set_optin(db, user_id, cat, on, user_id)
    svc = _svc(svc)
    ids = [r.id for r in await svc.list_by_status(user_id=user_id, status="approved", limit=1000)
           if _is_curated(r.metadata or dict(), user_id) and str((r.metadata or dict()).get("curated_category") or "") == cat]
    restamped = await svc.set_curated_standing(user_id, ids, protect=on, actor=user_id) if ids else 0
    await _finished(db, user_id, user_id, action, True, cat)
    return dict(category=cat, on=on, changed=changed, rows=restamped)


async def members_for(db, curator: Account) -> list:
    """The people ``curator`` may curate: every child, and every adult with at least one opt-in of their own. No memory content."""
    out = []
    for row in await _rows(db, "SELECT user_id FROM auth_users ORDER BY user_id"):
        uid = str(row[0] or "")
        if not uid or uid == curator.user_id:
            continue
        try:
            pol = await policy(db, curator, uid)
        except CurationRefused:
            continue
        out.append(dict(user_id=pol.member.user_id, name=pol.member.name, kind=pol.member.kind,
                        categories=_category_list(pol.allowed), sensitive_on=sorted(pol.sensitive_on),
                        sensitive_switchable=list(SENSITIVE) if pol.member.kind == "child" else []))
    return out
