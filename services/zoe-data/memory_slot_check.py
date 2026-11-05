"""memory_slot_check - person-keyed single-value contradictions and ageing, as a VIEW over the rows (nothing copied, nothing stored).

Why (Samantha initiative item 15, lane C E7; ``docs/architecture/person-centred-memory.md`` section 3.3 "Contradictions" and "Age",
accepted 2026-11-03): a person has ONE employer, ONE home city, ONE partner, ONE diet at a time. Two standing rows that disagree
about one of those are served side by side today, and nothing ever asks whether a life-now fact is still true. The owner's rule
("memory must be flawless") decides which one stands: user-stated / confirmed outrank inferred, and an inference NEVER supersedes
what the person said.

What (pure; the caller passes the rows it already read):

* ``slot_of(text)`` -> ``Slot(name, value)`` for the four single-value slots (English patterns; a negated, past or hoped-for
  statement - "no longer works at", "used to", "wants to move to" - is not a value). ``key_of(meta, text)`` -> the PERSON the row
  is about: the row's ``entity_id`` (a ``people`` row, resolved at the ingest choke point), else the owner when the text is about
  them ("User ...", "I ..."); anything else is unkeyed and skipped (no fuzzy subject guess).
* ``conflicts(rows)`` pairs standing rows (approved, not superseded / invalidated) of one person and one slot whose values differ,
  older -> newer, and decides each with ``memory_authority``'s own rule:
    - ``hold_newer``: the newer row may not override the older (``may_override`` is False - an inference against something the
      person said): the newer row is HELD (not served) and one question asks the person;
    - ``hide_older``: the newer row is the person's own word and the older one only an inference: the inference is not served;
    - ``ask``: both are the person's word (or both inferences): nothing is hidden (the dates are in the packet) and
      one question asks whether the older one is still true - it is retired only on their answer.
* ``age(meta, text, now)`` -> ``fresh`` | ``due`` | ``ageless``. Life-now facts (employer, city, diet, ``facet=life_now``) are due a
  "still true?" check 90 days after they were last stated or confirmed, mood rows after 14; an explicit ``review_after`` wins;
  everything else (a birthday, an allergy, a relationship, a partner) never ages. ``due`` is never a silent drop: it is a question.
* ``apply_to_packet`` - the ONE serving hook (``routers/memories.memory_for_prompt``): ``ZOE_MEMORY_SLOT_CHECK`` = off (default) |
  shadow (log what would be held, change nothing) | on (drop held rows from the packet). It looks only at the rows the packet was
  about to serve, so a contradiction is caught when both sides would reach the brain; ``report`` covers the whole store.

Forget-complete by construction: every output is row ids plus slot names. A forgotten row is gone from the store, so the next read
has nothing to pair it with; there is no stored verdict to erase. The values ``slot_of`` extracts exist only inside one call.
"""
from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

ENV = "ZOE_MEMORY_SLOT_CHECK"
DAY_S = 86400.0
LIFE_NOW_DAYS = 90
MOOD_DAYS = 14
#: at most this many questions per member per report (the homework cap: lane C E7, "<= 1 question per member per week")
QUESTION_CAP = 1

SLOTS = ("employer", "home_city", "partner", "diet")
#: slots whose value changes with a person's life: they age into a "still true?" question. A partner is a relationship, and
#: relationships never age (person-centred-memory 3.3): a change there is a contradiction, not an expiry.
LIFE_NOW_SLOTS = frozenset({"employer", "home_city", "diet"})

HOLD_NEWER = "hold_newer"
HIDE_OLDER = "hide_older"
ASK = "ask"

_STANDING_BLOCKED = frozenset({"superseded", "archived", "rejected", "pending", "disputed", "deleted"})

_OFF = frozenset({"", "0", "off", "false", "no"})
_ON = frozenset({"1", "on", "true", "yes", "enforce"})


def mode() -> str:
    """``off`` (default; unset or unrecognised) | ``shadow`` | ``on``. Per-call env read."""
    from typed_env import env_str

    raw = env_str(ENV, "off").strip().lower()
    if raw == "shadow":
        return "shadow"
    if raw in _ON:
        return "on"
    return "off"


# ── slots (pure) ──────────────────────────────────────────────────────────────

_VALUE = r"(?P<v>[a-z0-9][a-z0-9&'.\- ]{0,48}?)"
_END = (r"(?=\s*(?:[,.;:!?()]|$|\s(?:and|but|since|now|these days|for now|currently|again|as|who|which|where|because|so|in|near|with|"
        r"from|on|at)\b))")
_PARTNER_NOUN = r"(?:partner|wife|husband|girlfriend|boyfriend|fianc[eé]e?|spouse)"
_DIETS = r"(?P<v>vegan|vegetarian|pescatarian|pescetarian|flexitarian|carnivore|keto|paleo)"

#: (form, pattern). A ``verb`` pattern's subject is the words before it ("User works at ..."); a ``noun`` pattern's subject is
#: the possessor before it ("User's wife is ...").
_SLOT_RES: "dict[str, tuple[tuple[str, re.Pattern[str]], ...]]" = {
    "employer": (
        ("verb", re.compile(r"\b(?:works|work|is working|am working|are working|now works|still works|has started working|started working|has a job|got a job|"
                            r"has a new job|started a new job|started a job)\s+(?:at|for|with)\s+" + _VALUE + _END)),
        ("noun", re.compile(r"\b(?:job|work|workplace|employer) is (?:at |with )?" + _VALUE + _END)),
        ("verb", re.compile(r"\bis employed (?:by|at)\s+" + _VALUE + _END)),
    ),
    "home_city": (
        ("verb", re.compile(r"\b(?:lives|live|is living|am living|are living|now lives|still lives|has moved|moved|has relocated|relocated)\s+(?:in|to)\s+"
                            + _VALUE + _END)),
        ("noun", re.compile(r"\b(?:home|hometown|home town) is (?:in )?" + _VALUE + _END)),
    ),
    "partner": (
        ("noun", re.compile(r"\b" + _PARTNER_NOUN + r"(?:'s name)? is (?:called |named )?" + _VALUE + _END)),
        ("verb", re.compile(r"\bis (?:married to|engaged to|dating|going out with|in a relationship with)\s+" + _VALUE + _END)),
    ),
    "diet": (
        ("verb", re.compile(r"\b(?:is|has gone|went|has become|became) (?:a |now |now a |fully |strictly )?" + _DIETS + r"\b")),
        ("verb", re.compile(r"\b(?:eats|follows|is on|keeps) (?:a |an )?" + _DIETS + r" diet\b")),
    ),
}

#: a word before the match (same clause) that makes the statement NOT a current value: negated, past, hoped-for, hypothetical,
#: about somebody's former partner, or reported doubt
_NOT_CURRENT_RE = re.compile(
    r"\b(?:not|no longer|n't|never|used to|formerly|previously|once|stopped|quit|until|wants? to|wanted to|would like|hopes? to|"
    r"might|may|planning|plans? to|thinking (?:about|of)|considering|if|ex|former|late|maybe|perhaps|probably|apparently)\b")
#: where a clause starts: the subject of the statement is the words between this and the match
_CLAUSE_START_RE = re.compile(r"[.!?;,]\s|\s(?:and|but)\s")
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+")
_TRAILING_RE = re.compile(r"[\s'.\-]+$")
_ADVERBS = frozenset({"now", "also", "still", "currently", "recently", "just", "actually", "finally"})
#: who the OWNER is in a stored row: the third-person note ("User ...") or the first / second person
_SELF_SUBJECTS = frozenset({"user", "the user", "i", "i'm", "you", "you're"})
_SELF_POSSESSORS = frozenset({"user's", "the user's", "my", "your"})
_POSSESSIVE_RE = re.compile(r"(?:'s|s')$")
_PRONOUN_POSSESSORS = frozenset({"his", "her", "their", "my", "your"})


@dataclass(frozen=True)
class Slot:
    name: str
    value: str        # normalised, lower case; lives only inside a call
    subject: str = "" # the words the statement is about ("user", "ana's", ...), normalised
    form: str = "verb"


def _norm_value(v: str) -> str:
    v = re.sub(r"\s+", " ", (v or "").strip().lower())
    v = _LEADING_ARTICLE_RE.sub("", v)
    return _TRAILING_RE.sub("", v)


def _clause_start(low: str, start: int) -> int:
    return max((m.end() for m in _CLAUSE_START_RE.finditer(low, 0, start)), default=0)


def _subject(low: str, start: int, form: str) -> Optional[str]:
    """The subject words of the clause, or None when they do not name ONE holder of the slot ("User's sister works at ..." is the
    sister's job, not the user's; "Ana's sister's husband" is nobody this row is keyed to)."""
    words = [w for w in low[_clause_start(low, start):start].split() if w not in _ADVERBS]
    if not words and form == "verb":
        # a coordinated clause shares the sentence's subject ("User left Acme and now works at Globex"): only the owner's
        # subject is carried over - a named subject two clauses back is too far to trust
        head = low[max((m.end() for m in re.finditer(r"[.!?;]\s", low[:start])), default=0):start].split()
        for n in (2, 1):
            if " ".join(head[:n]) in _SELF_SUBJECTS:
                return " ".join(head[:n])
        return None
    if not words or len(words) > 4:
        return None
    if form == "verb":
        return None if any(_POSSESSIVE_RE.search(w) for w in words) else " ".join(words)
    owners = [w for w in words if _POSSESSIVE_RE.search(w) or w in _PRONOUN_POSSESSORS]
    if len(owners) != 1 or words[-1] != owners[0]:
        return None
    return " ".join(words)


def slot_of(text: str) -> Optional[Slot]:
    """The single-value slot this text states, or None. One slot per row: a row that states two (or none) is not paired. Pure."""
    low = " ".join((text or "").lower().replace("\u2019", "'").split())
    found: "list[Slot]" = []
    for name in SLOTS:
        for form, rx in _SLOT_RES[name]:
            m = rx.search(low)
            if not m:
                continue
            before = low[_clause_start(low, m.start()):m.start()]
            if _NOT_CURRENT_RE.search(before) or re.search(r"\bto\s*$", before):
                break       # negated / past / hoped-for, or an infinitive ("would love to work at"): not a current value
            subject = _subject(low, m.start(), form)
            v = _norm_value(m.group("v"))
            if v and subject is not None:
                found.append(Slot(name, v, subject, form))
            break
    return found[0] if len(found) == 1 else None


def same_value(a: str, b: str) -> bool:
    """"acme" and "acme corp" are one employer: the shorter's words all in the longer's. Pure."""
    ta, tb = set(a.split()), set(b.split())
    return bool(ta and tb) and (ta <= tb or tb <= ta)


def _named_subject(s: Slot) -> str:
    """The NAME a non-owner statement is about ("ana" for "Ana works at" / "Ana's husband is"), "" for the owner or a pronoun."""
    if s.subject in _SELF_SUBJECTS or s.subject in _SELF_POSSESSORS or s.subject in _PRONOUN_POSSESSORS:
        return ""
    if s.subject in ("he", "she", "they", "him", "them"):
        return ""
    return _POSSESSIVE_RE.sub("", s.subject)


def key_of(meta: Optional[Mapping[str, Any]], text: str, slot: Optional[Slot] = None) -> str:
    """Whom the row's slot is about: ``self`` (the owner: "User works at ...", "My wife is ..."), ``person:<entity_id>`` (the
    ``people`` row the ingest choke point resolved), or "" (unkeyed: skipped - no fuzzy subject guess). Pure."""
    s = slot if slot is not None else slot_of(text)
    if s is None:
        return ""
    if s.subject in (_SELF_SUBJECTS if s.form == "verb" else _SELF_POSSESSORS):
        return "self"
    ent = str((meta or {}).get("entity_id") or "").strip()
    return f"person:{ent}" if ent else ""


# ── standing + time (pure) ────────────────────────────────────────────────────

def _iso_epoch(value: Any) -> Optional[float]:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.timestamp()


def standing(meta: Optional[Mapping[str, Any]]) -> bool:
    """Is the row a current, served fact? (approved / active, not superseded, not invalidated or expired)."""
    meta = meta or {}
    if str(meta.get("status") or "approved").lower() in _STANDING_BLOCKED:
        return False
    return not (meta.get("invalid_at") or meta.get("expired_at") or meta.get("superseded_by"))


def captured(meta: Optional[Mapping[str, Any]]) -> float:
    import recall_evidence

    return recall_evidence.row_epoch(dict(meta or {})) or 0.0


def last_confirmed(meta: Optional[Mapping[str, Any]], text: str = "") -> float:
    """When the person last stated or confirmed this: the capture time, a ``confirmed_at`` stamp, or - for a row the person
    approved - its ``reviewed_at``. An automatic writer's edit time is not a confirmation."""
    import memory_authority as ma

    meta = meta or {}
    best = captured(meta)
    stamps = [meta.get("confirmed_at")]
    if ma.row_class(meta, text) == ma.USER_CONFIRMED:
        stamps.append(meta.get("reviewed_at"))
    for s in stamps:
        e = _iso_epoch(s)
        if e is not None and e > best:
            best = e
    return best


@dataclass(frozen=True)
class Age:
    state: str                # "fresh" | "due" | "ageless"
    due_at: Optional[float] = None
    days_since_confirmed: Optional[int] = None


def _is_mood(meta: Mapping[str, Any]) -> bool:
    import memory_authority as ma

    return ma.is_affective(meta.get("memory_type"), meta) or str(meta.get("facet") or "") == "mood"


def age(meta: Optional[Mapping[str, Any]], text: str, now: float) -> Age:
    """How old the fact is since the person last said or confirmed it, and whether a "still true?" check is due. Pure."""
    meta = meta or {}
    last = last_confirmed(meta, text)
    since = int(max(0.0, now - last) // DAY_S) if last else None
    explicit = _iso_epoch(meta.get("review_after"))
    if explicit is not None:
        return Age("due" if now >= explicit else "fresh", explicit, since)
    if _is_mood(meta):
        window = MOOD_DAYS
    else:
        s = slot_of(text)
        if (s and s.name in LIFE_NOW_SLOTS) or str(meta.get("facet") or "") == "life_now":
            window = LIFE_NOW_DAYS
        else:
            return Age("ageless", None, since)
    if not last:
        return Age("ageless", None, None)      # no time to measure from: never guess an age
    due = last + window * DAY_S
    return Age("due" if now >= due else "fresh", due, since)


# ── conflicts (pure) ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Conflict:
    person: str        # "self" | "person:<entity_id>"
    slot: str
    older_id: str
    newer_id: str
    action: str        # HOLD_NEWER | HIDE_OLDER | ASK

    @property
    def hidden_id(self) -> str:
        return {HOLD_NEWER: self.newer_id, HIDE_OLDER: self.older_id}.get(self.action, "")


def _rid(ref: Any) -> str:
    return str(getattr(ref, "id", "") or (ref.get("id") if isinstance(ref, Mapping) else "") or "")


def _meta(ref: Any) -> Mapping[str, Any]:
    return (getattr(ref, "metadata", None) if not isinstance(ref, Mapping) else ref.get("metadata")) or {}


def _text(ref: Any) -> str:
    return str((getattr(ref, "text", None) if not isinstance(ref, Mapping) else ref.get("text")) or "")


def decide(older: Any, newer: Any) -> str:
    """The authority rule for one contradicting pair (older -> newer). Pure."""
    import memory_authority as ma

    om, nm = _meta(older), _meta(newer)
    o_cls = ma.row_class(om, _text(older))
    n_power = ma.row_power(nm, _text(newer))
    if not ma.may_override(n_power, o_cls):
        return HOLD_NEWER
    if n_power >= ma.USER_RANK and ma.RANK.get(o_cls, 0) < ma.USER_RANK:
        return HIDE_OLDER
    return ASK


def conflicts(rows: Iterable[Any]) -> "list[Conflict]":
    """Every contradicting pair among the STANDING rows, per person and slot, older -> newer (capture time, then id). A row a
    supersede already links to its successor is not a contradiction. Pure."""
    groups: "dict[tuple[str, str], list[tuple[float, str, Any, str]]]" = {}
    seen: "set[str]" = set()
    for ref in rows:
        rid, meta, text = _rid(ref), _meta(ref), _text(ref)
        if not rid or rid in seen or not standing(meta):
            continue
        seen.add(rid)
        s = slot_of(text)
        person = key_of(meta, text, s)
        if s is None or not person:
            continue
        groups.setdefault((person, s.name), []).append((captured(meta), rid, ref, s.value, _named_subject(s)))
    out: "list[Conflict]" = []
    for (person, slot), items in sorted(groups.items(), key=lambda kv: kv[0]):
        if len({x[4] for x in items if x[4]}) > 1:
            continue        # one person's rows name two different holders ("Ana's husband", "Ben's wife"): the link is not trustworthy
        items.sort(key=lambda x: (x[0], x[1]))
        for i, (_to, oid, oref, oval, _os) in enumerate(items):
            for _tn, nid, nref, nval, _ns in items[i + 1:]:
                if same_value(oval, nval):
                    continue
                if str(_meta(nref).get("supersedes_id") or "") == oid:
                    continue
                out.append(Conflict(person, slot, oid, nid, decide(oref, nref)))
    return out


def hidden_ids(found: Iterable[Conflict]) -> "frozenset[str]":
    return frozenset(c.hidden_id for c in found if c.hidden_id)


# ── the questions (pure) ──────────────────────────────────────────────────────

def question(c: Conflict, by_id: Mapping[str, Any], *, now: Optional[float] = None) -> str:
    """ONE question about one conflict, in the rows' own stored words behind their provenance: "On 3 October you told me that you
    work at Acme, and on Tuesday that you work at Globex - which is right?". Falls back to ``memory_disputes.question_text`` when a
    side has no speakable provenance (never an invented "you told me")."""
    import provenance_speech as ps

    older, newer = by_id.get(c.older_id), by_id.get(c.newer_id)
    if older is None or newer is None:
        return ""
    lead = ps.that_clause(_meta(older), _text(older), now=now, lead=True)
    nprov = ps.of(_meta(newer), _text(newer), now=now)
    if lead and nprov is not None:
        import provenance_answers

        newer_words = provenance_answers.second_person(_text(newer))
        if nprov.inferred:
            return f"{lead}, but {nprov.clause()} that {newer_words} - is that right now?"
        return f"{lead}, and {nprov.clause()} that {newer_words} - which is right?"
    import memory_disputes

    return memory_disputes.question_text(_text(older), _text(newer))


# ── the serving hook + the whole-store report ─────────────────────────────────

def apply_to_packet(user_id: str, facts: Sequence[Any], hits: Sequence[Any], recent: Optional[Sequence[Any]] = None,
                    *, force_mode: Optional[str] = None) -> tuple:
    """``(facts, hits, recent)`` with held rows removed (``on``), or unchanged (``off`` / ``shadow``; shadow logs one
    ``SLOT_CHECK`` line of counts and short ids). Only the rows the packet was about to serve are paired. Never raises."""
    m = force_mode or mode()
    if m == "off":
        return facts, hits, recent
    try:
        pool = list(recent or ()) + list(hits) + list(facts)
        found = conflicts(pool)
        hide = hidden_ids(found)
        if found:
            logger.info("SLOT_CHECK user=%s mode=%s conflicts=%d held=%d hidden=%s", user_id, m, len(found),
                        sum(1 for c in found if c.action == HOLD_NEWER), ",".join(sorted(h[:8] for h in hide)))
        if m != "on" or not hide:
            return facts, hits, recent

        def keep(rows: Sequence[Any]) -> list:
            return [r for r in rows if _rid(r) not in hide]

        return keep(facts), keep(hits), (keep(recent) if recent is not None else None)
    except Exception as exc:  # noqa: BLE001 - the packet is complete without this check
        logger.warning("SLOT_CHECK user=%s failed kind=%s (packet unchanged)", user_id, type(exc).__name__)
        return facts, hits, recent


def report(rows: Iterable[Any], now: float) -> "dict[str, Any]":
    """The whole-store view for ONE member's rows: counts, the conflicts (ids + slot + action), the held / hidden ids, the rows a
    "still true?" check is due on, and at most ``QUESTION_CAP`` question ids (newest conflict first). Ids only, no text. Pure."""
    rows = [r for r in rows if _rid(r)]
    found = conflicts(rows)
    keyed = sum(1 for r in rows if standing(_meta(r)) and key_of(_meta(r), _text(r)))
    due = [_rid(r) for r in rows if standing(_meta(r)) and age(_meta(r), _text(r), now).state == "due"]
    ranked = sorted(found, key=lambda c: next((captured(_meta(r)) for r in rows if _rid(r) == c.newer_id), 0.0), reverse=True)
    return {
        "rows": len(rows),
        "keyed": keyed,
        "conflicts": [{"person": c.person, "slot": c.slot, "older": c.older_id, "newer": c.newer_id, "action": c.action} for c in found],
        "held": sorted(c.newer_id for c in found if c.action == HOLD_NEWER),
        "hidden_older": sorted(c.older_id for c in found if c.action == HIDE_OLDER),
        "still_true_due": sorted(due),
        "questions": [{"older": c.older_id, "newer": c.newer_id} for c in ranked[:QUESTION_CAP]],
    }
