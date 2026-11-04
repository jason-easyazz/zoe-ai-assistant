"""person_profile - the per-person profile read-model and its deterministic answer (person-centred memory, phase 1 slice 1).

Why. "What do you know about <contact>?" fell through to the brain, which paraphrased whatever the recall packet held: no order,
no sources, and with a thin graph a denial (5 of 13 recent turns). Design: ``docs/architecture/person-centred-memory.md`` sections
3.2 and 3.4; owner decisions D1, D6 and D7 (accepted 2026-11-03).

What. ``compose`` builds ONE person's profile from what already exists - never a copy: the ``people`` row, ``person_important_dates``
(plus the legacy ``people.birthday``, read-through per D9), ``person_gift_ideas``, the current ``person_relationships`` edges and
the memory rows linked to the person (``entity_id`` = ``people.id``, or the ``slug:<name>`` of a name-only stub). Every fact line
carries its authority (``user_stated`` | ``user_confirmed`` | ``inferred``), its writer and its day. ``render`` turns it into a
spoken answer (who they are to you -> one life-now item -> the next date -> "want more?") or a grouped chat card with authority
chips. Zero LLM, bounded reads.

Rules (each pinned by ``tests/test_person_profile.py`` with a negative control):
- D1: only ``approved`` rows are served; a pending (approve-before-write) row never is; an inferred row the owner's words do not
  support is counted as "to check", never said; an approved inferred row is labelled "I think".
- D6: the caller (``provenance_answers``) has already refused a guest and an unverified speaker; reads are scoped to the asker's
  own people and rows; a private-class row (health, money, feelings, private) is counted, never read.
- D7: a child's profile holds only what the owner said or confirmed: no inferred line, no health or mood row (not even counted),
  no gift idea a model proposed.
- Forgotten means forever: a forgotten name answers nothing; a row naming something forgotten is dropped (fails closed).

Flag ``ZOE_PERSON_PROFILE_ANSWERS`` (bool, default OFF): off = ``provenance_answers`` sends the question to the brain as before.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from person_age import person_may_be_child

logger = logging.getLogger(__name__)

ENV = "ZOE_PERSON_PROFILE_ANSWERS"
READ_BUDGET_S = 3.0
LINES_MAX = 12          # fact lines kept per profile (newest first)
SPOKEN_MAX_CHARS = 120  # one spoken fact

USER_STATED, USER_CONFIRMED, INFERRED = "user_stated", "user_confirmed", "inferred"
CHIPS = {USER_STATED: "you said", USER_CONFIRMED: "you confirmed", INFERRED: "I think"}


def enabled() -> bool:
    import typed_env

    return bool(typed_env.flag("ZOE_PERSON_PROFILE_ANSWERS"))


# ── facets (pure) ─────────────────────────────────────────────────────────────

_LIKES_RE = re.compile(
    r"\b(?:likes?|loves?|adores?|prefers?|favou?rite|enjoys?|fan\s+of|into|hates?|dislikes?|can['’]?t\s+stand|"
    r"vegetarian|vegan|doesn['’]?t\s+(?:like|eat|drink))\b", re.IGNORECASE)
_LIFE_NOW_RE = re.compile(
    r"\b(?:works?\s+(?:at|for|as|in)|working|job|new\s+job|started|starting|studying|university|school|moving|moved|"
    r"retir\w+|travel\w*|trip|flying|holiday|renovat\w+|building|project|training\s+for|interview|engaged|wedding|expecting)\b",
    re.IGNORECASE)


def facet_of(text: str) -> str:
    """``likes`` | ``life_now`` | ``""`` (unfaceted stays unfaceted; never guessed). Likes win: "loves her new job" is a like. Pure."""
    t = text or ""
    if _LIKES_RE.search(t):
        return "likes"
    if _LIFE_NOW_RE.search(t):
        return "life_now"
    return ""


# ── dates (pure) ──────────────────────────────────────────────────────────────

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_DAY_MONTH_RE = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]{3,9})\.?(?:,?\s+(\d{4}))?$")
_MONTH_DAY_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?$")


def parse_birthday(value: Any) -> Optional[tuple]:
    """``(month, day)`` from a legacy ``people.birthday`` value (ISO / vCard ``--MM-DD`` / "15 March" / "March 15" / a
    numeric day-first date), else None. Pure."""
    v = re.sub(r"\s+", " ", str(value or "")).strip()
    if not v:
        return None
    try:
        import contacts_vcard

        got = contacts_vcard.parse_date(v)
        if got:
            return got[1], got[2]
    except Exception:  # noqa: BLE001
        pass
    for rx, d_i, m_i in ((_DAY_MONTH_RE, 1, 2), (_MONTH_DAY_RE, 2, 1)):
        m = rx.match(v)
        if m and m.group(m_i).lower() in _MONTHS:
            mo, d = _MONTHS[m.group(m_i).lower()], int(m.group(d_i))
            return (mo, d) if 1 <= d <= calendar.monthrange(2000, mo)[1] else None
    try:
        import date_locale

        nd = date_locale.parse_numeric_date(v, purpose="birthday")
        if nd is not None and not getattr(nd, "ambiguous", False):
            return nd.month, nd.day
    except Exception:  # noqa: BLE001
        pass
    return None


def next_occurrence(month: int, day: int, ref: date) -> date:
    """The next ``month``/``day`` on or after ``ref`` (29 Feb -> 28 Feb in a common year). Pure."""
    def on(year: int) -> date:
        return date(year, month, min(day, calendar.monthrange(year, month)[1]))

    nxt = on(ref.year)
    return nxt if nxt >= ref else on(ref.year + 1)


def spoken_date(month: int, day: int) -> str:
    return f"{day} {calendar.month_name[month]}"


# ── the model ─────────────────────────────────────────────────────────────────

@dataclass
class Line:
    text: str
    authority: str
    source: str = ""
    epoch: float = 0.0
    facet: str = ""


@dataclass
class Profile:
    person_id: str
    name: str
    relationship: str = ""
    how_we_met: str = ""
    child: bool = False
    dates: list = field(default_factory=list)       # [(kind, month, day, days_until)] soonest first
    gifts: list = field(default_factory=list)       # [description]
    relations: list = field(default_factory=list)   # [(other name, their role)]
    lines: list = field(default_factory=list)       # [Line] newest first
    private: int = 0                                # private-class rows: counted, never read
    to_check: int = 0                               # inferred rows nothing the owner said supports

    @property
    def first(self) -> str:
        return (self.name.split() or ["They"])[0]


async def _rows(db, sql: str, params: tuple) -> list:
    try:
        cur = await db.execute(sql, params)
        return [tuple(r) for r in await cur.fetchall()]
    except Exception as exc:  # noqa: BLE001 - a missing satellite table is an empty facet, not a failed answer
        logger.debug("person_profile: read skipped (%s)", type(exc).__name__)
        return []


async def _forgotten(user_id: str, text: str) -> bool:
    """Does ``text`` name something this user forgot? Fails CLOSED (an error hides the row)."""
    try:
        import memory_forgotten
        from memory_tombstones import matching_tombstone

        return bool(matching_tombstone(user_id, text) or await memory_forgotten.matches(user_id, text, strict=True))
    except Exception:  # noqa: BLE001
        return True


def _line(ref: Any, user_id: str, child: bool) -> tuple:
    """``(kind, Line | None)``: kind is ``line`` | ``private`` | ``check`` | ``skip``."""
    import memory_authority as auth
    import own_words
    import provenance_answers as pa

    meta = getattr(ref, "metadata", None) or {}
    text = re.sub(r"\s+", " ", str(getattr(ref, "text", "") or "")).strip()
    if not text or not pa._owns(meta, user_id) or re.match(r"^person in contacts\b", text, re.IGNORECASE):
        return "skip", None
    if str(meta.get("memory_type") or "") == "state_change" or str(meta.get("status") or "") != "approved":
        return "skip", None
    if own_words.is_pasted_row(meta) or own_words.instruction_shaped(text) or auth.is_unverified(meta):
        return "skip", None
    try:
        cls = auth.row_class(meta, text)
        authority = auth.authority_of(cls)
        unanchored = (str(meta.get("authority_basis") or "") in auth.NO_STANDING_BASES
                      and auth.row_rank(meta, text) < auth.DERIVED_RANK)
    except Exception:  # noqa: BLE001
        authority, unanchored = INFERRED, False
    classes = pa.sensitive_classes(text, meta)
    if child and (classes & {"health", "mood"}):
        return "skip", None                         # D7: no health or mood facet on a child, not even a count
    if unanchored:
        return "check", None
    if child and authority == INFERRED:
        return "skip", None                         # D7: guardian-stated only
    if classes:
        return "private", None
    return "line", Line(text, authority, str(meta.get("origin") or meta.get("source") or ""),
                        pa._row_epoch(meta) or 0.0, facet_of(text))


async def compose(db, user_id: str, person_id: str, *, svc: Any = None, today: Optional[date] = None) -> Optional[Profile]:
    """The profile of one of ``user_id``'s own live people, or None (not theirs, deleted, or forgotten). Raises on a memory-store
    failure (the caller says so); a missing satellite table is an empty facet."""
    got = await _rows(db, "SELECT id, name, relationship, birthday, how_we_met FROM people "
                          "WHERE id = ? AND user_id = ? AND COALESCE(deleted, 0) = 0", (person_id, user_id))
    if not got:
        got = [r + ("",) for r in await _rows(db, "SELECT id, name, relationship, birthday FROM people "
                                                  "WHERE id = ? AND user_id = ? AND COALESCE(deleted, 0) = 0", (person_id, user_id))]
    if not got:
        return None
    pid, name, rel, legacy_bday, met = (str(x or "").strip() for x in got[0])
    if not name or await _forgotten(user_id, name):
        return None
    prof = Profile(pid, name, rel, met, child=person_may_be_child({"relationship": rel, "birthday": legacy_bday}))
    today = today or date.today()

    # dates: person_important_dates first, the legacy column only when no birthday row exists
    seen_kinds = set()
    for kind, mo, d in await _rows(db, "SELECT date_type, month, day FROM person_important_dates "
                                       "WHERE person_id = ? AND user_id = ?", (pid, user_id)):
        try:
            mo, d = int(mo), int(d)
            prof.dates.append((str(kind or "date"), mo, d, (next_occurrence(mo, d, today) - today).days))
            seen_kinds.add(str(kind or ""))
        except (TypeError, ValueError):
            continue
    if "birthday" not in seen_kinds:
        md = parse_birthday(legacy_bday)
        if md:
            prof.dates.append(("birthday", md[0], md[1], (next_occurrence(md[0], md[1], today) - today).days))
    prof.dates.sort(key=lambda x: x[3])

    for desc, source in await _rows(db, "SELECT description, source FROM person_gift_ideas WHERE person_id = ? AND user_id = ? "
                                         "AND COALESCE(status, 'idea') = 'idea' ORDER BY created_at DESC", (pid, user_id)):
        if desc and not (prof.child and str(source or "") != "manual") and not await _forgotten(user_id, str(desc)):
            prof.gifts.append(str(desc).strip())

    for a, b, a_to_b, b_to_a, other_a, other_b in await _rows(
            db, "SELECT r.person_a_id, r.person_b_id, r.rel_a_to_b, r.rel_b_to_a, pa.name, pb.name FROM person_relationships r "
                "JOIN people pa ON pa.id = r.person_a_id JOIN people pb ON pb.id = r.person_b_id "
                "WHERE r.user_id = ? AND (r.person_a_id = ? OR r.person_b_id = ?) AND r.valid_to IS NULL "
                "AND COALESCE(pa.deleted, 0) = 0 AND COALESCE(pb.deleted, 0) = 0", (user_id, pid, pid)):
        # rel_a_to_b is what b is to a (role_triples, contacts_store): the OTHER person's role to this one
        other, role = (other_b, a_to_b) if str(a) == pid else (other_a, b_to_a)
        if other and not await _forgotten(user_id, str(other)):
            prof.relations.append((str(other), str(role or "")))

    from memory_extractor import _slug_body

    if svc is None:
        from memory_service import get_memory_service

        svc = get_memory_service()
    refs = await asyncio.wait_for(svc.list_by_entity(user_id, [pid, "slug:" + _slug_body(name)], status="approved"),
                                  READ_BUDGET_S)
    seen_text = set()
    for ref in refs:
        kind, line = _line(ref, user_id, prof.child)
        if kind == "private":
            prof.private += 1
        elif kind == "check":
            prof.to_check += 1
        elif line is not None:
            key = re.sub(r"\W+", " ", line.text).strip().lower()
            if key in seen_text or await _forgotten(user_id, line.text):
                continue
            seen_text.add(key)
            prof.lines.append(line)
    prof.lines.sort(key=lambda ln: ln.epoch, reverse=True)
    del prof.lines[LINES_MAX:]
    return prof


# ── rendering (pure) ──────────────────────────────────────────────────────────

def _fact(line: Line, name: str) -> str:
    import contacts_conversation as cc
    import provenance_answers as pa

    t = pa.second_person(pa.trim(cc.fact_clause(line.text, name), SPOKEN_MAX_CHARS))
    return f"I think {t}" if line.authority == INFERRED else t


def _date_phrase(prof: Profile) -> str:
    if not prof.dates:
        return ""
    kind, mo, d, days = prof.dates[0]
    what = f"{prof.first}'s {kind.replace('_', ' ')}"
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
    return f"{what} is on {spoken_date(mo, d)}, {when}."


def render(prof: Profile, *, voice: bool) -> str:
    """The answer, built only from stored rows. Voice: about three sentences in a fixed order. Chat: the grouped card."""
    who = f"{prof.name} is your {prof.relationship}." if prof.relationship else f"{prof.name} is in your contacts."
    if prof.how_we_met:
        who += f" You met {prof.how_we_met.rstrip('.')}."
    if voice:
        parts = [who]
        pick = next((ln for ln in prof.lines if ln.facet == "life_now"), None) or (prof.lines[0] if prof.lines else None)
        if pick is not None:
            parts.append(_fact(pick, prof.name).rstrip(".") + ".")
        if prof.dates:
            parts.append(_date_phrase(prof))
        more = len(prof.lines) - (1 if pick else 0) + len(prof.gifts) + len(prof.relations)
        if not prof.lines and not prof.dates:
            parts.append(f"I don't know much about {prof.first} yet. Do you know when their birthday is?")
        elif more > 0:
            parts.append("Want more?")
        if prof.private:
            parts.append("Some things are private, so I only share those if you ask.")
        return " ".join(parts)
    out = [who]
    if prof.dates:
        out.append("Coming up: " + "; ".join(
            f"{k.replace('_', ' ')} {spoken_date(mo, d)} ({'today' if n == 0 else f'in {n} days'})" for k, mo, d, n in prof.dates))
    for facet, title in (("life_now", "Life now"), ("likes", "Likes"), ("", "Other")):
        rows = [ln for ln in prof.lines if ln.facet == facet]
        if rows:
            out.append(f"{title}:")
            out.extend(f"- {_fact(ln, prof.name).removeprefix('I think ')} ({CHIPS.get(ln.authority, 'I think')})" for ln in rows)
    if prof.gifts:
        out.append("Gift ideas: " + "; ".join(prof.gifts[:5]))
    if prof.relations:
        out.append("People: " + "; ".join(f"{n} ({r})" if r else n for n, r in prof.relations[:6]))
    if not prof.lines and not prof.dates:
        out.append(f"I don't know much about {prof.first} yet. Do you know when their birthday is?")
    if prof.private:
        out.append(f"{prof.private} private note{'s' if prof.private != 1 else ''} I only share if you ask.")
    if prof.to_check:
        out.append(f"{prof.to_check} thing{'s' if prof.to_check != 1 else ''} I want to check with you first.")
    return "\n".join(out)


def ambiguous_reply(names: list) -> str:
    shown = [n for n in names if n][:3]
    if len(shown) < 2:
        return "I know more than one person by that name. Which one do you mean?"
    return f"I know more than one person by that name: {', '.join(shown[:-1])} and {shown[-1]}. Which one do you mean?"
