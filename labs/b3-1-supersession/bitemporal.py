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
        """The persisted ``attribute_key`` when the row carries one (a legacy
        or unrecognised phrasing keyed at write time), else parsed from text.
        Reconciliation groups by THIS, never by re-parsing neighbour text."""
        return self.attribute_key or attribute_key(self.text)

    def subjects(self) -> frozenset[str]:
        """Subjects the row is about: text mentions plus the key's subject."""
        found = subjects(self.text)
        k = self.key()
        return found | {k.split("/", 1)[0]} if k else found


def intervals_overlap(a: Fact, b: Fact) -> bool:
    """Graphiti's contradiction precondition: validity intervals overlap.

    Half-open intervals ``[valid_from, valid_until)``; an open end is ±inf. A
    fact that ended at or before the other started cannot contradict it.
    """
    a_from = parse_ts(a.valid_from) or FAR_PAST
    a_until = parse_ts(a.valid_until) or FAR_FUTURE
    b_from = parse_ts(b.valid_from) or FAR_PAST
    b_until = parse_ts(b.valid_until) or FAR_FUTURE
    if a_from >= a_until or b_from >= b_until:
        return False   # an EMPTY window ([t, t), a fact retired at its own start) is true at no instant
    return a_from < b_until and b_from < a_until


def invalidate(old: Fact, new: Fact, now: Optional[datetime] = None) -> Fact:
    """Graphiti's write, never a delete: close the old validity window at the
    new fact's start, retire the row now, and link forward.

    The closing edge is ``new.valid_from`` (``now`` when the successor has no
    start) — an old window that ran PAST the successor's start is cut there,
    so the two never overlap in event time. Two clamps keep the window
    well-formed: a window that already ended earlier is never extended, and a
    backdated successor (starting before the old fact did) closes the old
    window at its own start — an empty ``[from, from)`` window, never an end
    before the start.
    """
    now = now or utcnow()
    until = new.valid_from or now.isoformat()
    end = parse_ts(until)
    old_until = parse_ts(old.valid_until)
    if old_until is not None and old_until < end:
        until, end = old.valid_until, old_until
    old_from = parse_ts(old.valid_from)
    if old_from is not None and end < old_from:
        until = old.valid_from
    return replace(old, valid_until=until, expired_at=now.isoformat(), superseded_by=new.id)


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


_SUBJECT_MENTION_RE = re.compile(
    r"(?<![\w-])(?:user|person [a-z]|i|my|me)(?![\w-])", re.IGNORECASE,
)


def subjects(text: str) -> frozenset[str]:
    """Every subject a text mentions, anywhere in it, canonicalised
    (``i``/``my``/``me`` → ``user``): "Neil, Person A's dad, says hi" →
    {"person a"}. Empty when the text names nobody."""
    return frozenset(_canon_subject(m.group(0)) for m in _SUBJECT_MENTION_RE.finditer(_normalize(text)))


def same_subject(a, b) -> bool:
    """Two texts (or :class:`Fact` rows) are about the same person when they
    share a subject, or when neither names anyone. A fact about Person B can
    never merge into, supersede, or be judged against a fact about Person A."""
    sa = a.subjects() if isinstance(a, Fact) else subjects(a)
    sb = b.subjects() if isinstance(b, Fact) else subjects(b)
    return bool(sa & sb) if (sa or sb) else True


def attribute_key(text: str) -> Optional[str]:
    """'user/employer', 'person a/dog name', … or None when unrecognised."""
    subject, attr, _ = parse_fact(text)
    if subject is None or attr is None:
        return None
    return f"{subject}/{attr}"


def value_seq(text: str) -> list[str]:
    """The value's salient tokens IN ORDER (framing + stopwords removed)."""
    _, _, rest = parse_fact(text)
    return [t for t in re.findall(r"[a-z0-9]+", rest) if t not in _VALUE_STOPWORDS]


def value_tokens(text: str) -> set[str]:
    return set(value_seq(text))


def _extends(short: list[str], long: list[str]) -> bool:
    """``long`` is ``short`` plus detail APPENDED after it (same head entity)."""
    return bool(short) and len(long) >= len(short) and long[:len(short)] == short


def same_value(a: str, b: str) -> bool:
    """Equal after normalisation, or one value is the other with detail
    APPENDED ("globex" ~ "globex as a senior engineer"; "neil" ~ "neil, spelled
    n-e-i-l"): the head entity is unchanged, the rest is enrichment. A token
    added BEFORE or inside the head is a potentially different entity —
    "york" vs "new york" — and is a correction, not a rephrasing (the old
    set-subset rule called those identical). A correction proper leaves a
    leftover token on EACH side."""
    va, vb = value_seq(a), value_seq(b)
    return _extends(va, vb) or _extends(vb, va)


# ── Richer-fact chooser (mem0: keep the fact with the most information) ───────

RICHNESS_MARGIN = 4  # extra salient chars needed to call a fact "richer"


def information(text: str) -> int:
    """Salient characters of the VALUE a fact asserts (framing words are not
    information: "is employed by Globex" and "works at Globex" both carry
    exactly "globex"). Unkeyed text is measured whole."""
    return sum(len(t) for t in value_tokens(text))


def richer(candidate: str, existing: str, *, candidate_key: Optional[str] = None,
           existing_key: Optional[str] = None) -> str:
    """Return whichever text carries more information; ties keep ``existing``.

    Two guards keep "richer" from meaning "longer":
      * structure beats chatter — a keyed fact ("person a's father's name is
        neil") is never replaced by an unkeyed paraphrase ("Neil, Person A's
        dad, says hi"), however many tokens the chatter has;
      * the candidate must EXTEND the existing value (its tokens, then more)
        — replacing "neil, spelled n-e-i-l" with "neil the fisherman" would
        lose the spelling, so it is not richer, it is different.
    """
    if (candidate_key or attribute_key(candidate)) is None and (existing_key or attribute_key(existing)) is not None:
        return existing
    vc, ve = value_seq(candidate), value_seq(existing)
    if not _extends(ve, vc):
        return existing
    if information(candidate) > information(existing) + RICHNESS_MARGIN:
        return candidate
    return existing


# ── Transitions (mem0: "switched from X to Y" records both) ───────────────────

_TRANSITION_RE = re.compile(
    r"^(?P<subj>(?:the\s+)?(?:user|person [a-z]|i)\b)\s+"
    r"(?P<verb>switched|moved|changed|went|relocated)\s+(?P<jobs>jobs?\s+)?from\s+(?P<old>.+?)\s+to\s+(?P<new>.+?)"
    r"(?:\s+(?:in|on|as of|since)\s+(?P<when>\d{4}(?:-\d{2}(?:-\d{2})?)?))?$",
    re.IGNORECASE,
)
_PLACE_WORDS_RE = re.compile(r"\b(?:city|town|suburb|village|street|avenue|road)\b")
_ORG_WORDS_RE = re.compile(r"\b(?:corp|corporation|inc|ltd|llc|pty|co|company|industries|logistics|group|labs)\b")

_TRANSITION_FRAMING = {
    "employer": "works at",
    "residence": "lives in",
}


def _transition_attribute(m: "re.Match", attr_hint: Optional[str]) -> str:
    """Which attribute a "<verb> from X to Y" changes. Explicit signals first
    (``attr_hint``, a ``jobs`` qualifier, an org suffix, a place word); then
    the verb: a bare "moved/relocated from X to Y" is a change of ADDRESS,
    while "switched/changed/went from X to Y" is a change of employer."""
    if attr_hint:
        return attr_hint
    values = m.group("old") + " " + m.group("new")
    if m.group("jobs") or _ORG_WORDS_RE.search(values):
        return "employer"
    if _PLACE_WORDS_RE.search(values) or m.group("verb") in ("moved", "relocated"):
        return "residence"
    return "employer"


def split_transition(text: str, attr_hint: Optional[str] = None,
                     when: Optional[str] = None) -> Optional[tuple[str, str, Optional[str]]]:
    """"Person A switched from Acme to Globex" → ("person a works at acme",
    "person a works at globex", when). None when not a transition.

    The attribute comes from :func:`_transition_attribute`: "User moved from
    London to Paris" is a residence change; "User moved jobs from Initech to
    Globex" (or any org-suffixed pair) is an employer change."""
    m = _TRANSITION_RE.match(_normalize(text))
    if not m:
        return None
    subj = _canon_subject(m.group("subj").replace("the ", ""))
    attr = _transition_attribute(m, attr_hint)
    framing = _TRANSITION_FRAMING.get(attr, "works at")
    old = f"{subj} {framing} {m.group('old').strip()}"
    new = f"{subj} {framing} {m.group('new').strip()}"
    return old, new, (m.group("when") or when)


# ── Interval arithmetic (half-open [start, end) over aware datetimes) ─────────


def _union(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for start, end in sorted(i for i in intervals if i[0] < i[1]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _subtract(intervals: list[tuple[datetime, datetime]],
              covers: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """``intervals`` minus ``covers`` — every slice left uncovered, in order."""
    out = list(_union(intervals))
    for c_start, c_end in _union(covers):
        nxt: list[tuple[datetime, datetime]] = []
        for start, end in out:
            if c_end <= start or end <= c_start:
                nxt.append((start, end))
                continue
            if start < c_start:
                nxt.append((start, c_start))
            if c_end < end:
                nxt.append((c_end, end))
        out = nxt
    return out


# ── Controller ────────────────────────────────────────────────────────────────

ADD, UPDATE, SUPERSEDE, NONE = "ADD", "UPDATE", "SUPERSEDE", "NONE"
_DECISIONS = {ADD, UPDATE, SUPERSEDE, NONE}

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
    # OTHER live rows of the same attribute that assert a different value over
    # an overlapping window: every one is retired by ``apply``, not just
    # ``target_id`` (several contradicting rows — e.g. a duplicate left by an
    # interrupted new-row-first write — must all close, or stale facts keep
    # competing in recall).
    also_close: list[int] = field(default_factory=list)
    # For UPDATE: the validity window the surviving row should carry — the
    # UNION of the incoming interval and every overlapping same-value row
    # (stored Acme [2020, 2025) + new Acme [2024, ∞) → [2020, ∞)), clamped so
    # it never overlaps a conflicting row closed by ``also_close``.
    interval: Optional[tuple[Optional[str], Optional[str]]] = None
    # Rows whose validity window is REWRITTEN but which stay live (event-time
    # history, not a retirement): the split case — a same-value survivor whose
    # earlier period is kept, closed at the start of the conflict that
    # interrupted it, while the value continues in a NEW row from the boundary.
    retime: dict[int, tuple[Optional[str], Optional[str]]] = field(default_factory=dict)
    # Further NEW rows to write (after ``write_as``): the extra slices a value
    # keeps around richer rows or a conflict — plain Globex [2021, ∞) around
    # rich Globex [2023, 2025) keeps [2021, 2023) on its own row and gets
    # [2025, ∞) as a new row here.
    extra_rows: list[Fact] = field(default_factory=list)
    # The boundary every ``also_close`` row (and a SUPERSEDE target) is closed
    # at: the incoming fact's start as reconciled. Carried explicitly because
    # ``write_as`` may be a LATER slice of the value (plain Globex resuming in
    # 2025 after a richer 2024–2025 row) and must not move the close edge.
    close_at: Optional[str] = None


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
      3. There is NO text-similarity shortcut. Two facts merge only through
         a shared REAL attribute key (which carries the subject: "person b
         works at acme" never merges into "person a works at acme"; a
         same-name mother and father are two facts). Two UNKEYED texts never
         merge on wording alone ("likes hiking" vs "likes biking" are 0.95
         similar and distinct); they go to the judge.
      4. Same attribute key: same value → NONE / UPDATE by richness
         (``richer_rule=False`` is the negative control: newest wins); the
         survivor's window becomes the UNION of the overlapping same-value
         windows — SPLIT around a conflict that ran before the incoming start
         (earlier period kept as live history, value continued in a new row
         from the boundary; ``Decision.retime``) — and every other same-value
         duplicate is retired via ``also_close``; but a
         same value over a DISJOINT window is a repeated occurrence and ADDs a
         separate interval (worked at Acme 2018–2021 and again from 2024);
         different value → SUPERSEDE if overlapping (EVERY overlapping
         contradicting row closes, via ``also_close``), ADD (history) if not.
      5. Key unknown on either side → ``judge`` (LLM) with integer ids, shown
         ONLY the neighbours about the same subject whose key is unknown or
         equal to the new fact's (a known, different attribute is never shown,
         so the model cannot override deterministic attribute separation); an
         id outside the shown set, or no judge, degrades to ADD.
    Keys come from :meth:`Fact.key` (persisted ``attribute_key`` first).
    """
    now = now or utcnow()
    extra_closed: list[Fact] = []
    write_as: Optional[Fact] = None
    split = split_transition(new.text)
    if split:
        old_text, to_text, when = split
        new = replace(new, text=to_text, valid_from=new.valid_from or when,
                      attribute_key=attribute_key(to_text))
        write_as = new
        old_key = attribute_key(old_text)
        closed_from = new.valid_from or now.isoformat()
        edge = parse_ts(closed_from)
        live_same_key = [n for n in neighbours if n.is_live and n.key() == old_key]
        # The closed "from" side is written ONLY when the store holds no live
        # row for that attribute at all. A same-value row that covers the
        # boundary is the thing being superseded; a same-value row over an
        # earlier window already records the occurrence; and ANY other-value
        # history (Globex 2012–2024, "switched from Acme" in 2025) means the
        # statement only establishes precedence — which the "to" side plus
        # its closure already capture — not when Acme began, so no dated
        # Acme row is invented. With nothing recorded, an open-start closed
        # row is the honest minimum ("was at Acme until the switch").
        if not live_same_key:
            extra_closed.append(Fact(
                id=-1, text=old_text, valid_from=None, valid_until=closed_from,
                created_at=now.isoformat(), attribute_key=old_key,
            ))

    live = [n for n in neighbours if n.is_live]

    def _decision(event: str, target: Optional[int], why: str, *, text: Optional[str] = None,
                  also_close: Optional[list[int]] = None,
                  interval: Optional[tuple[Optional[str], Optional[str]]] = None,
                  retime: Optional[dict] = None,
                  write: Optional[Fact] = None, extra_rows: Optional[list[Fact]] = None) -> Decision:
        return Decision(event, target, why, text=text, extra_closed=extra_closed,
                        write_as=write if write is not None else write_as,
                        also_close=list(also_close or []), interval=interval, retime=dict(retime or {}),
                        extra_rows=list(extra_rows or []), close_at=new.valid_from)

    if not live:
        return _decision(ADD, None, "no live neighbours")

    def _overlaps(n: Fact) -> bool:
        return (not overlap_check) or intervals_overlap(new, n)

    new_key = new.key()
    same_attr = [n for n in live if n.key() == new_key] if new_key else []
    # every live same-attribute row asserting a DIFFERENT value over an
    # overlapping window — all of them must close, whatever the decision
    stale = [n for n in same_attr if not same_value(new.text, n.text) and _overlaps(n)]
    # every live same-attribute row asserting the SAME value over an
    # overlapping window — one survives, the rest are duplicates to retire
    same_val_overlapping = [n for n in same_attr if same_value(new.text, n.text) and _overlaps(n)]
    close_edge = parse_ts(new.valid_from) or now   # where every also_close row is cut

    def _merge(existing: Fact, why: str) -> Decision:
        """Fold ``new`` into the overlapping same-value rows.

        Rule (README, "interval rule"): a row's DETAILS are asserted only from
        that row's own start. Rows are ranked by richness; rows of equal
        richness are absorbed into one (their windows unioned — nothing is
        backdated, the value is identical); a strictly plainer row keeps only
        the slice BEFORE the richer rows begin, as live history (plain Globex
        2021–, rich Globex 2023– → plain [2021, 2023), rich [2023, …)); a
        plainer row keeps EVERY slice richer rows do not cover — before,
        between and after them (plain [2021, ∞) around rich [2023, 2025) →
        plain [2021, 2023), rich [2023, 2025), plain [2025, ∞)); the richer
        row owns only its own window. A stored row keeps the slice at its own
        start in place (``retime``); every further slice is a new row
        (``extra_rows``); a row whose own start is covered is closed to an
        empty window and retired. Every conflicting row closes at the incoming
        start (the boundary), and the conflict's span [its start, boundary) is
        removed from every slice (the split).
        """
        if not _overlaps(existing):
            return _decision(ADD, None, why + " but intervals disjoint (repeated occurrence, new interval)")
        stored = [existing] + [n for n in same_val_overlapping if n.id != existing.id]
        also = [n.id for n in stale]
        retime: dict[int, tuple[Optional[str], Optional[str]]] = {}
        boundary_s = new.valid_from or now.isoformat()

        # ranking → levels (richest first); the incoming joins the existing
        # row's level unless the richer rule says it genuinely carries more
        if not richer_rule:
            levels = [[new] + stored]                       # newest wins: one level, incoming text
            top_text = new.text
        else:
            keep = richer(new.text, existing.text, candidate_key=new_key, existing_key=existing.key())
            new_info = information(new.text) if keep != existing.text else min(information(new.text), information(existing.text))
            buckets: dict[int, list[Fact]] = {}
            for r in stored:
                buckets.setdefault(information(r.text), []).append(r)
            buckets.setdefault(new_info, []).append(new)
            levels = [buckets[k] for k in sorted(buckets, reverse=True)]
            top_text = new.text if keep != existing.text else next(r for r in levels[0] if r is not new).text \
                if any(r is not new for r in levels[0]) else new.text

        earlier = [n for n in stale if (parse_ts(n.valid_from) or FAR_PAST) < close_edge]
        cut_row = min(earlier, key=lambda n: parse_ts(n.valid_from) or FAR_PAST) if earlier else None
        # the conflict span [cut, boundary) is removed from every slice of the value
        conflict = [((parse_ts(cut_row.valid_from) or FAR_PAST), close_edge)] if cut_row else []

        strs: dict[datetime, Optional[str]] = {FAR_PAST: None, FAR_FUTURE: None}   # dt → original string

        def _dt(value: Optional[str], default: datetime) -> datetime:
            d = parse_ts(value) or default
            strs.setdefault(d, value)
            return d
        _dt(cut_row.valid_from, FAR_PAST) if cut_row else None
        _dt(new.valid_from, FAR_PAST) if new.valid_from else None
        strs.setdefault(close_edge, boundary_s)

        def _window(rows: list[Fact]) -> list[tuple[datetime, datetime]]:
            """Union of the rows' windows. A STORED row with no start was
            recorded as history of unknown depth and keeps it (FAR_PAST); an
            undated INCOMING observation makes no start claim and adopts the
            level's earliest known start (never pushing a dated row's start
            to unknown, never inventing depth). A missing end is OPEN."""
            starts = [_dt(r.valid_from, FAR_PAST) for r in rows if r.valid_from]
            known = min(starts) if starts else FAR_PAST
            return _union([(known if (r is new and not r.valid_from) else _dt(r.valid_from, FAR_PAST),
                            _dt(r.valid_until, FAR_FUTURE)) for r in rows])

        write: Optional[Fact] = None
        extra_rows: list[Fact] = []
        event, target, text_out = NONE, existing.id, existing.text
        owned: list[tuple[datetime, datetime]] = []        # what richer levels own
        for i, rows in enumerate(levels):
            slices = _subtract(_window(rows), owned + conflict)
            owned = _union(owned + _window(rows))
            head = next((r for r in rows if r is not new), None)   # a stored row keeps its id
            level_text = top_text if i == 0 else (head.text if head is not None else new.text)
            for r in rows:
                if r is new or r is head:
                    continue
                also.append(r.id)                                  # absorbed duplicate → retired
            if head is not None:
                own_start = _dt(head.valid_from, FAR_PAST)
                if slices and slices[0][0] <= own_start:
                    # the row keeps the slice at its own start (an equal peer may extend it backwards)
                    f, u = strs[slices[0][0]], strs[slices[0][1]]
                    if (f, u) != (head.valid_from, head.valid_until):
                        retime[head.id] = (f, u)
                    rest = slices[1:]
                else:
                    # its own start is covered (a conflict or richer row predates it):
                    # closed to an empty window at its own start, then retired
                    also.append(head.id)
                    if head.valid_from is not None:
                        retime[head.id] = (head.valid_from, head.valid_from)
                    rest = slices
                if head is existing and (head.id in retime or (i == 0 and level_text != existing.text)):
                    event, text_out = UPDATE, level_text
            else:
                rest = slices
            for f_dt, u_dt in rest:
                # every further slice is a NEW row carrying the level's text
                extra_rows.append(replace(write_as or new, text=level_text, valid_from=strs[f_dt],
                                          valid_until=strs[u_dt], attribute_key=new_key))
        if extra_rows:
            if (existing.id in also and len(levels) > 1 and levels[0] == [new]
                    and len(extra_rows) == 1 and not stale):
                # the richer incoming covers the existing row's whole window → UPDATE in place
                also.remove(existing.id)
                retime.pop(existing.id, None)
                retime[existing.id] = (extra_rows[0].valid_from, extra_rows[0].valid_until)
                event, text_out, extra_rows = UPDATE, top_text, []
            else:
                write, extra_rows = extra_rows[0], extra_rows[1:]
                event, target = ADD, None
        interval = retime.pop(existing.id, None) if event == UPDATE else None
        reason = why + (" (newest wins)" if not richer_rule else
                        " (split around a conflict)" if cut_row is not None and event == ADD else
                        " (candidate richer)" if event == UPDATE and text_out == new.text and new.text != existing.text else
                        " (existing at least as rich)")
        return _decision(event, target if event != ADD else None, reason, text=text_out,
                         also_close=also, interval=interval, retime=retime, write=write, extra_rows=extra_rows)

    # 3) (removed) a near-exact-text shortcut used to live here; once it
    #    required a real matching key it was a strict subset of 4) that picked
    #    its target by wording instead of richness, so 4) is the only path.

    # 4) same attribute — a different value with the same key ("…is Jo" vs
    #    "…is Joe") is a correction and takes the contradiction branch
    if new_key:
        # same value: only rows whose window OVERLAPS are candidates (a rich
        # disjoint historical row must not outrank a terse overlapping current
        # one); among those, the richest survives and the rest are retired
        if same_val_overlapping:
            target = max(same_val_overlapping, key=lambda n: information(n.text))
            return _merge(target, "same attribute, same value")
        # a same value only over disjoint windows is a repeated occurrence → a separate interval
        if stale:
            # supersede the most recent live assertion; close every other contradicting row too
            target = max(stale, key=lambda n: parse_ts(n.valid_from) or FAR_PAST)
            return _decision(SUPERSEDE, target.id, "same attribute, different value, intervals overlap",
                             also_close=[n.id for n in stale if n.id != target.id])
        if same_attr:
            return _decision(ADD, None, "same attribute, intervals disjoint (history / repeated occurrence)")
        # known attribute, no neighbour shares it → distinct fact
        if all(n.key() for n in live):
            return _decision(ADD, None, "different attribute")

    # 5) judge — only for the ambiguous remainder, only over shown ids, and
    #    only shown the neighbours about the SAME subject whose key is unknown
    #    or equal to the new fact's: an id the judge was not shown is rejected,
    #    so it can never retire or overwrite another person's fact, nor a
    #    known DIFFERENT attribute of the same person (a new employer fact
    #    never sees the residence row).
    if judge is None:
        return _decision(ADD, None, "attribute unknown, no judge → add")
    shown_facts = [n for n in live if same_subject(new, n)
                   and (n.key() is None or new_key is None or n.key() == new_key)]
    if not shown_facts:
        return _decision(ADD, None, "attribute unknown, no eligible neighbour (same subject, unknown or same attribute) → add")
    shown = [(n.id, n.text) for n in shown_facts]
    try:
        verdict = judge(new.text, shown)
    except Exception as exc:  # the LLM step must never lose a fact
        return _decision(ADD, None, f"judge failed ({type(exc).__name__}) → add")
    validated = _validate_judge(verdict, shown_facts)
    if validated is None:
        return _decision(ADD, None, "judge named an id it was not shown / bad shape → add")
    event, target_id = validated
    if event == ADD:
        return _decision(ADD, None, "judge: unrelated")
    target = next(n for n in shown_facts if n.id == target_id)
    if event == SUPERSEDE:
        if not _overlaps(target):
            return _decision(ADD, None, "judge said contradiction but intervals disjoint (history)")
        return _decision(SUPERSEDE, target.id, "judge: contradiction, intervals overlap")
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
    close_at = decision.close_at if decision.close_at is not None else new.valid_from
    if decision.write_as is not None:
        new = decision.write_as
    next_id = (max(store) + 1) if store else 1
    for closed in decision.extra_closed:
        store[next_id] = replace(closed, id=next_id, created_at=now.isoformat())
        next_id += 1
    successor: Optional[Fact] = None
    if decision.event == NONE:
        successor = store.get(decision.target_id) if decision.target_id is not None else None
    elif decision.event == UPDATE and decision.target_id is not None:
        old = store[decision.target_id]
        vf, vu = decision.interval if decision.interval is not None else (old.valid_from, old.valid_until)
        store[decision.target_id] = replace(old, text=decision.text or new.text, valid_from=vf, valid_until=vu)
        successor = store[decision.target_id]
    else:
        new_row = replace(new, id=next_id, created_at=now.isoformat(),
                          attribute_key=attribute_key(new.text) if new.attribute_key is None else new.attribute_key)
        store[next_id] = new_row
        successor = new_row
        if decision.event == SUPERSEDE and decision.target_id is not None:
            store[decision.target_id] = invalidate(store[decision.target_id], replace(new_row, valid_from=close_at), now)
    for extra in decision.extra_rows:
        next_id = (max(store) + 1) if store else 1
        store[next_id] = replace(extra, id=next_id, created_at=now.isoformat(),
                                 attribute_key=attribute_key(extra.text) if extra.attribute_key is None else extra.attribute_key)
    # a row's window rewritten in place (its slice at its own start), staying live
    for rid, (vf, vu) in decision.retime.items():
        if rid in store:
            store[rid] = replace(store[rid], valid_from=vf, valid_until=vu)
    # every OTHER contradicting live row closes too, linked to the surviving
    # row but closed at the INCOMING fact's start (the window the contradiction
    # was measured against) — never at the surviving row's own, possibly much
    # earlier, valid_from
    for cid in decision.also_close:
        if successor is not None and cid in store and store[cid].is_live and cid != successor.id:
            edge = replace(successor, valid_from=close_at)
            store[cid] = invalidate(store[cid], edge, now)
    return store


def live_texts(store: dict[int, Fact]) -> list[str]:
    return [f.text for f in store.values() if f.is_live and f.valid_until is None]


__all__ = [
    "ADD", "UPDATE", "SUPERSEDE", "NONE", "Decision", "Fact", "Judge",
    "apply", "attribute_key", "enabled", "information", "intervals_overlap",
    "invalidate", "live_texts", "parse_fact", "reconcile", "richer",
    "same_subject", "same_value", "split_transition", "subjects", "value_seq", "value_tokens",
]
