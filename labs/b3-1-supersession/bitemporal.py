"""B3.1 lab — bi-temporal supersession + keep-the-richer-fact reconciliation.

Pure Python, no model, no I/O. This is the DETERMINISTIC core of the
reconciliation controller the design doc
(``docs/architecture/b3-1-bitemporal-supersession.md``) specifies:

* :func:`intervals_overlap` — Graphiti's rule (``edge_operations.py``): two facts
  can only contradict when their validity intervals overlap; a fact whose
  ``valid_until`` is at or before the new fact's ``valid_from`` is history, not a
  contradiction.
* :func:`invalidate` — Graphiti's write: never delete; set
  ``valid_until = new.valid_from`` and ``expired_at = now`` and link
  ``superseded_by``.
* :func:`attribute_key` — the normalisation M7 lacked: "works at X", "employed
  by X", "lives in X", "dog named X", "father is called X" all key to the SAME
  ``subject/attribute`` as their possessive cousins.
* :func:`richer` — mem0's "keep the fact which has the most information".
* :func:`split_transition` — mem0's transition rule: "switched from X to Y"
  records BOTH the closed old value and the open new one.
* :func:`reconcile` — the controller. Input: a new fact + top-k neighbours with
  small INTEGER ids (mem0: the model can only name an id it was shown; an id it
  was not shown is rejected and the decision degrades to ADD). Output: one of
  ``ADD / UPDATE / SUPERSEDE / NONE`` plus the target id. The LLM is an
  injectable ``judge`` callable used ONLY when the deterministic key extractor
  cannot decide (attribute unknown on one side); tests inject a fake.

Not wired to anything. ``ZOE_BITEMPORAL_SUPERSEDE`` (default off) is the flag
the prod wiring plan names; :func:`enabled` reads it so the inventory lists it
as a LAB flag now.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from typing import Callable, Optional

# ── Flag (lab-only reader; prod wiring reads the same name, default off) ─────


def enabled() -> bool:
    return (os.environ.get("ZOE_BITEMPORAL_SUPERSEDE") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


# ── Time helpers ──────────────────────────────────────────────────────────────

FAR_PAST = datetime(1, 1, 1, tzinfo=timezone.utc)
FAR_FUTURE = datetime(9999, 12, 31, tzinfo=timezone.utc)


def parse_ts(value) -> Optional[datetime]:
    """ISO date/datetime string (or date/datetime) → aware UTC datetime; None stays None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    s = str(value).strip().replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}", s):
        s += "-01-01"
    elif re.fullmatch(r"\d{4}-\d{2}", s):
        s += "-01"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Fact row ──────────────────────────────────────────────────────────────────


@dataclass
class Fact:
    """One stored fact with the bi-temporal columns of the schema plan.

    ``valid_from``/``valid_until`` — when the fact is TRUE in the world
    (event time; ``valid_until`` None = still true).
    ``created_at``/``expired_at`` — when the ROW was written / retired
    (transaction time; ``expired_at`` None = row is live).
    """
    id: int
    text: str
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    created_at: Optional[str] = None
    expired_at: Optional[str] = None
    superseded_by: Optional[int] = None
    attribute_key: Optional[str] = None
    meta: dict = field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        return self.expired_at is None

    def key(self) -> Optional[str]:
        return self.attribute_key or attribute_key(self.text)


def intervals_overlap(a: Fact, b: Fact) -> bool:
    """Graphiti's contradiction precondition: validity intervals overlap.

    Half-open intervals ``[valid_from, valid_until)``; an open end is ±inf. A
    fact that ended at or before the other started cannot contradict it.
    """
    a_from = parse_ts(a.valid_from) or FAR_PAST
    a_until = parse_ts(a.valid_until) or FAR_FUTURE
    b_from = parse_ts(b.valid_from) or FAR_PAST
    b_until = parse_ts(b.valid_until) or FAR_FUTURE
    return a_from < b_until and b_from < a_until


def invalidate(old: Fact, new: Fact, now: Optional[datetime] = None) -> Fact:
    """Graphiti's write, never a delete: close the old validity window at the
    new fact's start, retire the row now, and link forward."""
    now = now or utcnow()
    return replace(
        old,
        valid_until=old.valid_until or new.valid_from or now.isoformat(),
        expired_at=now.isoformat(),
        superseded_by=new.id,
    )


# ── Attribute-key normalisation ───────────────────────────────────────────────
#
# The M7 gap: memory_quality._attribute_key only keys possessive/copula
# assertions ("my dad's name is …"). Distilled facts arrive as "user works at
# acme", "person a is employed by globex", "person a has a dog named rex" —
# key None → the classifier falls back to text similarity → ADD → both
# employers persist. Each framing below maps to ONE canonical attribute.

_SUBJECT_RE = re.compile(
    r"^(?:the\s+)?(?P<subj>user|person [a-z]|i|my|me)\b(?:'s)?\s*", re.IGNORECASE,
)

# (regex over the lower-cased, subject-stripped text, canonical attribute).
# The value is everything after the match, with filler stripped.
_FRAMINGS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:currently\s+|now\s+)?(?:works?|working|is working|am working)\s+(?:at|for)\s+(?:the\s+)?"), "employer"),
    (re.compile(r"^(?:is|am|was)?\s*employed\s+(?:by|at)\s+(?:the\s+)?"), "employer"),
    (re.compile(r"^(?:job|employer|work|workplace)\s+is\s+(?:at\s+|with\s+)?"), "employer"),
    (re.compile(r"^(?:has|have)\s+(?:a\s+)?(?:new\s+)?job\s+at\s+"), "employer"),
    (re.compile(r"^(?:joined|started\s+(?:at|with)|moved\s+to\s+a\s+job\s+at)\s+"), "employer"),
    (re.compile(r"^(?:lives?|living|is living|am living|resides?|based)\s+(?:in|at)\s+"), "residence"),
    (re.compile(r"^(?:home|address|residence)\s+is\s+(?:in\s+)?"), "residence"),
    (re.compile(r"^(?:moved|relocated)\s+to\s+"), "residence"),
    (re.compile(r"^(?:father|dad|daddy|papa)(?:'s)?\s+(?:name\s+is|is\s+called|is\s+named)\s+"), "father name"),
    (re.compile(r"^(?:has|have)\s+a\s+(?:father|dad)\s+(?:named|called)\s+"), "father name"),
    (re.compile(r"^(?:mother|mum|mom|mama)(?:'s)?\s+(?:name\s+is|is\s+called|is\s+named)\s+"), "mother name"),
    (re.compile(r"^(?:has|have)\s+a\s+(?:mother|mum|mom)\s+(?:named|called)\s+"), "mother name"),
    (re.compile(r"^dog(?:'s)?\s+(?:name\s+is|is\s+called|is\s+named)\s+"), "dog name"),
    (re.compile(r"^(?:has|have)\s+a\s+dog\s+(?:named|called)\s+"), "dog name"),
    (re.compile(r"^cat(?:'s)?\s+(?:name\s+is|is\s+called|is\s+named)\s+"), "cat name"),
    (re.compile(r"^(?:has|have)\s+a\s+cat\s+(?:named|called)\s+"), "cat name"),
    (re.compile(r"^(?:drives?|driving|is driving|am driving)\s+(?:a|an)\s+"), "car"),
    (re.compile(r"^car\s+is\s+(?:a|an)\s+"), "car"),
    (re.compile(r"^(?:favou?rite\s+colou?r)\s+is\s+"), "favourite colour"),
    (re.compile(r"^(?:favou?rite\s+food)\s+is\s+"), "favourite food"),
    (re.compile(r"^birthday\s+is\s+(?:on\s+)?"), "birthday"),
    (re.compile(r"^(?:was\s+born|born)\s+on\s+"), "birthday"),
    (re.compile(r"^(?:phone|phone\s+number|mobile|mobile\s+number)\s+is\s+"), "phone"),
    (re.compile(r"^(?:is|am)\s+allergic\s+to\s+"), "allergy"),
    (re.compile(r"^(?:has|have)\s+an?\s+allergy\s+to\s+"), "allergy"),
    (re.compile(r"^(?:studies|studying|is studying|am studying)\s+(?:at\s+)?"), "school"),
    (re.compile(r"^(?:school|university|uni)\s+is\s+"), "school"),
]

_VALUE_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "am", "and",
    "or", "to", "in", "on", "at", "it", "that", "this", "of", "now",
    "currently", "still", "these", "days", "as", "for", "with", "since",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower()).strip(" .!?,")


def _canon_subject(raw: str) -> str:
    raw = raw.lower()
    return "user" if raw in ("i", "my", "me", "user") else raw


def parse_fact(text: str) -> tuple[Optional[str], Optional[str], str]:
    """→ (subject, attribute, value-remainder). subject/attribute None when the
    text is not a recognised attribute assertion (then the judge decides)."""
    norm = _normalize(text)
    m = _SUBJECT_RE.match(norm)
    if not m:
        return None, None, norm
    subject = _canon_subject(m.group("subj"))
    rest = norm[m.end():]
    for pat, attr in _FRAMINGS:
        fm = pat.match(rest)
        if fm:
            return subject, attr, rest[fm.end():]
    return subject, None, rest


def attribute_key(text: str) -> Optional[str]:
    """'user/employer', 'person a/dog name', … or None when unrecognised."""
    subject, attr, _ = parse_fact(text)
    if subject is None or attr is None:
        return None
    return f"{subject}/{attr}"


def value_tokens(text: str) -> set[str]:
    _, _, rest = parse_fact(text)
    return {t for t in re.findall(r"[a-z0-9]+", rest) if t not in _VALUE_STOPWORDS}


def same_value(a: str, b: str) -> bool:
    """memory_quality._same_value's subset rule: a rephrasing has equal value
    tokens, a richer restatement is a superset; a correction leaves a leftover
    token on EACH side."""
    va, vb = value_tokens(a), value_tokens(b)
    return bool(va and vb) and (va <= vb or vb <= va)


# ── Richer-fact chooser (mem0: keep the fact with the most information) ───────

RICHNESS_MARGIN = 4  # extra salient chars needed to call a fact "richer"


def information(text: str) -> int:
    """Salient characters of the VALUE a fact asserts (framing words are not
    information: "is employed by Globex" and "works at Globex" both carry
    exactly "globex"). Unkeyed text is measured whole."""
    return sum(len(t) for t in value_tokens(text))


def richer(candidate: str, existing: str) -> str:
    """Return whichever text carries more information; ties keep ``existing``.

    Two guards keep "richer" from meaning "longer":
      * structure beats chatter — a keyed fact ("person a's father's name is
        neil") is never replaced by an unkeyed paraphrase ("Neil, Person A's
        dad, says hi"), however many tokens the chatter has;
      * the candidate must CONTAIN the existing value (superset) — replacing
        "neil, spelled n-e-i-l" with "neil the fisherman" would lose the
        spelling, so it is not richer, it is different.
    """
    if attribute_key(candidate) is None and attribute_key(existing) is not None:
        return existing
    vc, ve = value_tokens(candidate), value_tokens(existing)
    if not ve <= vc:
        return existing
    if information(candidate) > information(existing) + RICHNESS_MARGIN:
        return candidate
    return existing


# ── Transitions (mem0: "switched from X to Y" records both) ───────────────────

_TRANSITION_RE = re.compile(
    r"^(?P<subj>(?:the\s+)?(?:user|person [a-z]|i)\b)\s+"
    r"(?:switched|moved|changed|went)\s+(?:jobs?\s+)?from\s+(?P<old>.+?)\s+to\s+(?P<new>.+?)"
    r"(?:\s+(?:in|on|as of|since)\s+(?P<when>\d{4}(?:-\d{2}(?:-\d{2})?)?))?$",
    re.IGNORECASE,
)

_TRANSITION_FRAMING = {
    "employer": "works at",
    "residence": "lives in",
}


def split_transition(text: str, attr_hint: Optional[str] = None,
                     when: Optional[str] = None) -> Optional[tuple[str, str, Optional[str]]]:
    """"Person A switched from Acme to Globex" → ("person a works at acme",
    "person a works at globex", when). None when not a transition.

    The attribute is guessed from the values' framing hint (employer unless the
    values look like places / ``attr_hint`` says residence)."""
    m = _TRANSITION_RE.match(_normalize(text))
    if not m:
        return None
    subj = _canon_subject(m.group("subj").replace("the ", ""))
    attr = attr_hint or ("residence" if re.search(r"\b(?:city|town|suburb|street|avenue|road)\b",
                                                   m.group("old") + " " + m.group("new")) else "employer")
    framing = _TRANSITION_FRAMING.get(attr, "works at")
    old = f"{subj} {framing} {m.group('old').strip()}"
    new = f"{subj} {framing} {m.group('new').strip()}"
    return old, new, (m.group("when") or when)


# ── Controller ────────────────────────────────────────────────────────────────

ADD, UPDATE, SUPERSEDE, NONE = "ADD", "UPDATE", "SUPERSEDE", "NONE"
_DECISIONS = {ADD, UPDATE, SUPERSEDE, NONE}

NEAR_DUP_RATIO = 0.92

# judge(new_text, [(id, text), …]) → {"event": ADD|UPDATE|SUPERSEDE|NONE, "id": int|None}
Judge = Callable[[str, list[tuple[int, str]]], dict]


@dataclass
class Decision:
    event: str
    target_id: Optional[int] = None
    reason: str = ""
    # For UPDATE: the text the surviving row should carry (richer of the two).
    text: Optional[str] = None
    # For transitions: extra facts to write as already-CLOSED history.
    extra_closed: list[Fact] = field(default_factory=list)
    # The fact as it should be WRITTEN (a transition rewrites "switched from X
    # to Y" into the open "…works at Y" row; None = write ``new`` unchanged).
    write_as: Optional[Fact] = None


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _validate_judge(verdict: dict, candidates: list[Fact]) -> Optional[tuple[str, Optional[int]]]:
    """mem0's id rule: the judge may only name an id it was shown."""
    if not isinstance(verdict, dict):
        return None
    event = str(verdict.get("event", "")).upper()
    if event not in _DECISIONS:
        return None
    target = verdict.get("id")
    if event == ADD:
        return ADD, None
    ids = {c.id for c in candidates}
    if not isinstance(target, int) or isinstance(target, bool) or target not in ids:
        return None
    return event, target


def reconcile(
    new: Fact,
    neighbours: list[Fact],
    *,
    judge: Optional[Judge] = None,
    overlap_check: bool = True,
    richer_rule: bool = True,
    now: Optional[datetime] = None,
) -> Decision:
    """Decide what to do with ``new`` against its top-k ``neighbours``.

    Deterministic order:
      1. Transition text → split; the "to" side is reconciled below and the
         "from" side is emitted as already-closed history if absent.
      2. Contradiction candidates = live neighbours whose validity interval
         OVERLAPS the new fact's (``overlap_check=False`` is the negative
         control that reproduces Graphiti-less "newest wins").
      3. Near-exact text duplicate → NONE / UPDATE by richness.
      4. Same attribute key: same value → NONE / UPDATE by richness
         (``richer_rule=False`` is the negative control: newest wins);
         different value → SUPERSEDE if overlapping, ADD (history) if not.
      5. Key unknown on either side → ``judge`` (LLM) with integer ids; an id
         outside the shown set, or no judge, degrades to ADD.
    """
    now = now or utcnow()
    extra_closed: list[Fact] = []
    write_as: Optional[Fact] = None
    split = split_transition(new.text)
    if split:
        old_text, to_text, when = split
        new = replace(new, text=to_text, valid_from=new.valid_from or when)
        write_as = new
        old_key = attribute_key(old_text)
        if not any(attribute_key(n.text) == old_key and same_value(n.text, old_text)
                   for n in neighbours if n.is_live):
            closed_from = new.valid_from or now.isoformat()
            extra_closed.append(Fact(
                id=-1, text=old_text, valid_from=None, valid_until=closed_from,
                created_at=now.isoformat(), attribute_key=old_key,
            ))

    live = [n for n in neighbours if n.is_live]
    if not live:
        return Decision(ADD, None, "no live neighbours", extra_closed=extra_closed, write_as=write_as)

    def _merge(existing: Fact, why: str) -> Decision:
        if not richer_rule:
            # negative control: newest phrasing always wins
            return Decision(UPDATE, existing.id, why + " (newest wins)", text=new.text,
                            extra_closed=extra_closed, write_as=write_as)
        keep = richer(new.text, existing.text)
        if keep == existing.text:
            return Decision(NONE, existing.id, why + " (existing at least as rich)",
                            text=existing.text, extra_closed=extra_closed, write_as=write_as)
        return Decision(UPDATE, existing.id, why + " (candidate richer)", text=new.text,
                        extra_closed=extra_closed, write_as=write_as)

    new_key = attribute_key(new.text)

    # 3) near-exact duplicate (value-checked: "…is Jo" vs "…is Joe" is a correction)
    best = max(live, key=lambda n: _similarity(new.text, n.text))
    if _similarity(new.text, best.text) >= NEAR_DUP_RATIO:
        if new_key and attribute_key(best.text) == new_key and not same_value(new.text, best.text):
            if not overlap_check or intervals_overlap(new, best):
                return Decision(SUPERSEDE, best.id, "near-dup text, different value", extra_closed=extra_closed, write_as=write_as)
        else:
            return _merge(best, "near-exact duplicate")

    # 4) same attribute
    if new_key:
        same_attr = [n for n in live if attribute_key(n.text) == new_key]
        same_attr_same_val = [n for n in same_attr if same_value(new.text, n.text)]
        if same_attr_same_val:
            target = max(same_attr_same_val, key=lambda n: information(n.text))
            return _merge(target, "same attribute, same value")
        if same_attr:
            contradicting = [n for n in same_attr if (not overlap_check) or intervals_overlap(new, n)]
            if contradicting:
                # supersede the most recent live assertion of that attribute
                target = max(contradicting, key=lambda n: parse_ts(n.valid_from) or FAR_PAST)
                return Decision(SUPERSEDE, target.id, "same attribute, different value, intervals overlap",
                                extra_closed=extra_closed, write_as=write_as)
            return Decision(ADD, None, "same attribute, different value, intervals disjoint (history)",
                            extra_closed=extra_closed, write_as=write_as)
        # known attribute, no neighbour shares it → distinct fact
        if all(attribute_key(n.text) for n in live):
            return Decision(ADD, None, "different attribute", extra_closed=extra_closed, write_as=write_as)

    # 5) judge — only for the ambiguous remainder, only over shown ids
    if judge is None:
        return Decision(ADD, None, "attribute unknown, no judge → add", extra_closed=extra_closed, write_as=write_as)
    shown = [(n.id, n.text) for n in live]
    try:
        verdict = judge(new.text, shown)
    except Exception as exc:  # the LLM step must never lose a fact
        return Decision(ADD, None, f"judge failed ({type(exc).__name__}) → add", extra_closed=extra_closed, write_as=write_as)
    validated = _validate_judge(verdict, live)
    if validated is None:
        return Decision(ADD, None, "judge named an id it was not shown / bad shape → add",
                        extra_closed=extra_closed, write_as=write_as)
    event, target_id = validated
    if event == ADD:
        return Decision(ADD, None, "judge: unrelated", extra_closed=extra_closed, write_as=write_as)
    target = next(n for n in live if n.id == target_id)
    if event == SUPERSEDE:
        if overlap_check and not intervals_overlap(new, target):
            return Decision(ADD, None, "judge said contradiction but intervals disjoint (history)",
                            extra_closed=extra_closed, write_as=write_as)
        return Decision(SUPERSEDE, target.id, "judge: contradiction, intervals overlap",
                        extra_closed=extra_closed, write_as=write_as)
    # judge says same fact (UPDATE/NONE): the richness rule decides, not the judge
    return _merge(target, "judge: same fact")


# ── Apply (in-memory store, mirrors the prod write order) ─────────────────────


def apply(decision: Decision, new: Fact, store: dict[int, Fact],
          now: Optional[datetime] = None) -> dict[int, Fact]:
    """Write ``decision`` into ``store`` (id → Fact). Never deletes a row.

    Write order mirrors ``memory_service.review(edit)``: the NEW row lands
    first, then the old one is retired — a crash between the two leaves a
    duplicate, never a hole."""
    now = now or utcnow()
    if decision.write_as is not None:
        new = decision.write_as
    next_id = (max(store) + 1) if store else 1
    for closed in decision.extra_closed:
        store[next_id] = replace(closed, id=next_id, created_at=now.isoformat())
        next_id += 1
    if decision.event == NONE:
        return store
    if decision.event == UPDATE and decision.target_id is not None:
        old = store[decision.target_id]
        store[decision.target_id] = replace(old, text=decision.text or new.text)
        return store
    new_row = replace(new, id=next_id, created_at=now.isoformat(),
                      attribute_key=attribute_key(new.text) if new.attribute_key is None else new.attribute_key)
    store[next_id] = new_row
    if decision.event == SUPERSEDE and decision.target_id is not None:
        store[decision.target_id] = invalidate(store[decision.target_id], new_row, now)
    return store


def live_texts(store: dict[int, Fact]) -> list[str]:
    return [f.text for f in store.values() if f.is_live and f.valid_until is None]


__all__ = [
    "ADD", "UPDATE", "SUPERSEDE", "NONE", "Decision", "Fact", "Judge",
    "apply", "attribute_key", "enabled", "information", "intervals_overlap",
    "invalidate", "live_texts", "parse_fact", "reconcile", "richer",
    "same_value", "split_transition", "value_tokens",
]
