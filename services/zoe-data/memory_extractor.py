"""Unified conversational memory extraction for Zoe.

This module turns user utterances into structured memory candidates and can
persist them through MemoryService. It is intentionally lightweight:
- regex/template extraction (no model call)
- conservative skip rules to avoid noisy writes
- idempotent ingest via MemoryService
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


def _record_quality_reject(source: str, reason: str, text: str) -> None:
    """Log + count a write-quality reject so dropped candidates are auditable."""
    logger.info("MEMORY_QUALITY_REJECT source=%s reason=%s text=%r",
                source, reason, (text or "")[:120])
    try:
        from memory_metrics import memory_quality_reject_count
        memory_quality_reject_count.labels(source=source, reason=reason).inc()
    except Exception:
        pass


@dataclass(frozen=True)
class MemoryCandidate:
    text: str
    memory_type: str = "fact"
    title: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    confidence: float = 0.72
    source_excerpt: str = ""


_SKIP_PREFIXES = (
    "what is",
    "what are",
    "how do",
    "how does",
    "explain",
    "tell me about",
    "what time",
    "what day",
    "what date",
    "hello",
    "hi",
    "hey",
    "thanks",
    "thank you",
)


_TEMPLATE_PATTERNS: list[tuple[str, str, float]] = [
    # Explicit memory commands
    (r"(?:please\s+)?remember\s+(?:that\s+|this\s+)?(.{5,280})", "User asked me to remember: {0}", 0.95),
    (r"don'?t\s+forget\s+(?:that\s+)?(.{5,220})", "Important note: {0}", 0.9),

    # Preferences
    (r"i\s+(?:prefer|like|love|enjoy)\s+(.{3,120})", "Preference: user likes {0}", 0.78),
    (r"i\s+(?:don'?t\s+like|dislike|hate)\s+(.{3,120})", "Preference: user dislikes {0}", 0.78),
    (r"my\s+favou?rite\s+(?:\w+\s+)?is\s+(.{2,90})", "Favourite: {0}", 0.8),

    # Location / employer / origin
    (r"i\s+live\s+in\s+(.{2,80})", "User lives in {0}", 0.8),
    (r"i\s+work\s+(?:at|for)\s+(.{2,80})", "User works at/for {0}", 0.8),
    (r"i(?:'m|\s+am)\s+from\s+(.{2,80})", "User is from {0}", 0.78),
    (r"my\s+(?:lucky\s+)?number\s+is\s+(\d+)", "User's lucky number is {0}", 0.85),

    # User's own name and age
    (r"my\s+full\s+name\s+is\s+([A-Za-z][A-Za-z' -]{1,80})", "User's full name is {0}", 0.93),
    (r"my\s+name\s+is\s+([A-Za-z][A-Za-z' -]{1,60})", "User's name is {0}", 0.92),
    (r"(?:people\s+)?call\s+me\s+([A-Za-z][A-Za-z' -]{1,40})", "User goes by {0}", 0.88),
    (r"i(?:'m|\s+am)\s+(\d{1,3})\s+years?\s+old", "User is {0} years old", 0.88),

    # Profession / identity
    (r"i(?:'m|\s+am)\s+a(?:n)?\s+([\w][\w\s]{3,60}?)(?:\s*[.,;!?]|$)", "User's job/role: {0}", 0.75),

    # Pets — "my dog's name is X" / "my dog is named X" / "my dog is called X"
    (
        r"my\s+(dog|cat|pet|bird|fish|rabbit|hamster|horse|pup(?:py)?|kitten|parrot|turtle|snake|lizard|ferret|guinea\s+pig)"
        r"(?:'s\s+name\s+is\s+|\s+is\s+named\s+|\s+is\s+called\s+|\s+is\s+)([A-Za-z][A-Za-z' -]{1,50})",
        "User's {0} is named {1}", 0.88,
    ),
    # "I have a dog named Teddy" / "I have a dog called Teddy"
    (
        r"i\s+have\s+a(?:n)?\s+(dog|cat|pet|bird|fish|rabbit|hamster|horse|pup(?:py)?|kitten|parrot|turtle|snake|lizard|ferret|guinea\s+pig)"
        r"\s+(?:named|called)\s+([A-Za-z][A-Za-z' -]{1,50})",
        "User has a {0} named {1}", 0.88,
    ),

    # Family relationships — "my wife is Sarah" / "my wife's name is Sarah"
    (
        r"my\s+(wife|husband|partner|girlfriend|boyfriend|son|daughter|kid|child|brother|sister"
        r"|mom|dad|father|mother|grandma|grandpa|grandfather|grandmother|aunt|uncle|niece|nephew|friend)"
        r"(?:'s\s+name\s+is\s+|\s+is\s+named\s+|\s+is\s+called\s+)\s*([A-Za-z][A-Za-z' -]{1,60})",
        "User's {0} is named {1}", 0.87,
    ),
    # "my wife is [name]" (without "named/called" — lower confidence, name heuristic)
    (
        r"my\s+(wife|husband|partner|girlfriend|boyfriend|son|daughter|brother|sister|dad|mom|father|mother)\s+is\s+"
        r"([A-Z][A-Za-z]{1,40})\b(?!\s+(?:a|an|the|very|really|so|going|trying))",
        "User's {0} is {1}", 0.78,
    ),

    # General "my X is Y" for named things the user cares about
    (
        r"my\s+([\w]{3,30})\s+is\s+named\s+([A-Za-z][A-Za-z' -]{1,60})",
        "User's {0} is named {1}", 0.84,
    ),

    # Birthday / anniversary
    (r"my\s+birthday\s+is\s+(.{3,60})", "User's birthday is {0}", 0.85),
    (r"i\s+was\s+born\s+(?:on\s+)?(.{3,60})", "User was born on {0}", 0.82),
]


_PERSON_PATTERN = re.compile(
    r"\bi\s+met\s+([A-Za-z][A-Za-z' -]{1,48}?)(?=\s+(?:who|that|and|he|she|they|is|was)\b|[.,;!?]|$)"
    r"(?:[,\s]+(?:who|that|and)?\s*(?:is|was)\s+(?:an|a)?\s*([A-Za-z][A-Za-z' -]{1,64}))?",
    re.IGNORECASE,
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip(" \t\n\r.,;:!?"))


def _person_name_from_fragment(fragment: str) -> str:
    stop = {"who", "that", "and", "is", "was", "she", "he", "they", "today", "tomorrow", "yesterday"}
    out: list[str] = []
    for raw in re.split(r"\s+", _clean(fragment)):
        tok = raw.strip(".,;:!?")
        if not tok:
            continue
        low = tok.lower()
        if low in stop:
            break
        if not re.fullmatch(r"[A-Za-z][A-Za-z'_-]*", tok):
            break
        # A possessive ends the name: "my friend Jessica's birthday is…" must
        # anchor "Jessica", never mint a person called "Jessica's birthday" (QA F2).
        if low.endswith("'s") or low.endswith("\u2019s"):
            out.append(tok[:-2])
            break
        out.append(tok)
        if len(out) >= 2:
            break
    return " ".join(out)


def _should_skip(user_message: str) -> bool:
    msg = _clean(user_message).lower()
    if not msg:
        return True
    if msg.startswith("remember "):
        return False
    return any(msg.startswith(prefix) for prefix in _SKIP_PREFIXES)


# ── Anaphora context: the PRIOR user turn ────────────────────────────────────
#
# Entity-less corrections ("wait no sorry I meant saturday not friday") and
# pronoun-subject facts ("she's a doctor" right after "my wife's name is Emma")
# are unanchorable from the current turn alone — live hard-gate 2026-07-07
# proved both classes stored NOTHING. The prior USER message provides the
# anchor.
#
# The cheapest prior-turn access is an in-process per-(user, session)
# last-user-message LRU maintained by ``extract_and_ingest`` itself: zero
# call-site plumbing, no DB read on the turn path. Trade-offs (accepted,
# documented in the PR): process-local (zoe-data is a single process), cleared
# on restart (corrections span adjacent turns seconds apart, so a lost anchor
# across a restart is acceptable — the fact is simply not stored, never
# mis-anchored), bounded at ``_PREV_TURN_MAX`` sessions.
#
# PURITY: only USER-authored text may enter this cache — never the assistant
# reply (poisoned-store bug 2026-07-07, tests/test_memory_extractor_purity.py).
_PREV_TURN_MAX = 512
_prev_user_turns: "OrderedDict[tuple[str, str], str]" = OrderedDict()


def _prev_turn_key(user_id: Optional[str], session_id: Optional[str]) -> tuple[str, str]:
    return ((user_id or "").strip(), (session_id or "").strip())


def note_user_turn(user_id: Optional[str], session_id: Optional[str], user_message: str) -> None:
    """Record the current USER message as the prior turn for the next capture."""
    msg = (user_message or "").strip()
    if not msg:
        return
    key = _prev_turn_key(user_id, session_id)
    _prev_user_turns[key] = msg
    _prev_user_turns.move_to_end(key)
    while len(_prev_user_turns) > _PREV_TURN_MAX:
        _prev_user_turns.popitem(last=False)


def recall_prev_user_turn(user_id: Optional[str], session_id: Optional[str]) -> str:
    """The prior USER message for this (user, session), or '' when unknown."""
    return _prev_user_turns.get(_prev_turn_key(user_id, session_id), "")


# Correction shapes with no entity of their own — the entity lives in the
# PRIOR user message ("my dentist appointment got moved to friday" →
# "wait no sorry I meant saturday not friday"). Conservative: both the new and
# the old value must be present, "not <old>" required, and <old> must actually
# occur in the prior message or nothing is stored (no hallucinated anchor).
_CORRECTION_RES = (
    re.compile(
        r"^(?:oh[,!\s]+)?(?:wait[,!\s]+)?(?:no[,!\s]+)?(?:sorry[,!\s]+)?"
        r"i\s+meant\s+(?:to\s+say\s+)?(?P<new>.{1,60}?)\s*,?\s+not\s+(?P<old>.{1,60}?)[.!?]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:oh[,!\s]+)?(?:no[,!\s]+)?(?:wait[,!\s]+)?(?:sorry[,!\s]+)?"
        r"actually[,!\s]*(?:it'?s\s+|it\s+is\s+|i\s+meant\s+)?"
        r"(?P<new>.{1,60}?)\s*,?\s+not\s+(?P<old>.{1,60}?)[.!?]*$",
        re.IGNORECASE,
    ),
)

# Possessive-pronoun fact after a person introduction: "her birthday is
# actually March 25" / "her daughter is named Poppy" following "my friend
# Jessica…". The QA review (F2/F3) showed these were stored RAW (minting a
# person literally named "her") or dropped entirely while the reply claimed
# "I'm keeping track of that". Anchored the same way as _PRONOUN_FACT_RE.
_POSSESSIVE_FACT_RE = re.compile(
    r"^(?:and\s+|oh[,\s]+|btw[,\s]+|by\s+the\s+way[,\s]+)?(?P<pron>her|his)\s+"
    r"(?P<attr>[a-z][\w' -]{1,30}?)\s+is\s+"
    r"(?:actually\s+|named\s+|called\s+)?(?P<val>[\w' /:.-]{1,60}?)[.!?]*$",
    re.IGNORECASE,
)

# Pronoun-subject fact right after a person introduction: "she's a doctor"
# following "my wife's name is Emma". Anchored ONLY when the prior message
# introduced a person; gender-incompatible relations refuse the anchor.
_PRONOUN_FACT_RE = re.compile(
    r"^(?:and\s+|oh[,\s]+|btw[,\s]+|by\s+the\s+way[,\s]+)?(?P<pron>she|he)\s*(?:'s|\s+is)\s+"
    # Any predicate, not just "a/an <noun>": "allergic to nuts", "a doctor",
    # "from Boston", "tall". The `also` filler ("she's also allergic…") is stripped
    # in _pronoun_fact_candidates, and ephemeral "-ing" states are skipped there.
    r"(?P<pred>[A-Za-z][\w\s'-]{1,60}?)[.!?]*$",
    re.IGNORECASE,
)

_RELATION_WORDS = (
    "wife|husband|partner|girlfriend|boyfriend|son|daughter|kid|child|brother|sister"
    "|mom|dad|father|mother|grandma|grandpa|grandfather|grandmother|aunt|uncle|niece|nephew|friend"
)
_PERSON_INTRO_RES = (
    # "my wife's name is Emma" / "my wife is named Emma" / "my wife is called Emma"
    re.compile(
        rf"my\s+(?P<rel>{_RELATION_WORDS})"
        r"(?:'s\s+name\s+is\s+|\s+is\s+named\s+|\s+is\s+called\s+)\s*"
        r"(?P<name>[A-Za-z][A-Za-z' -]{1,60})",
        re.IGNORECASE,
    ),
    # "my wife is Emma" (bare form, name heuristic — mirrors _TEMPLATE_PATTERNS,
    # which is searched with re.IGNORECASE at call time, so this carries the
    # flag too: "My wife is Emma" must still anchor).
    re.compile(
        rf"my\s+(?P<rel>{_RELATION_WORDS})\s+is\s+"
        r"(?P<name>[A-Z][A-Za-z]{1,40})\b(?!\s+(?:a|an|the|very|really|so|going|trying))",
        re.IGNORECASE,
    ),
    # "I have a friend Caitlin Farrell" / "my friend Caitlin" / "my coworker named
    # Sam" — the bare "my {rel} {Name}" / "I have a {rel} {Name}" introductions (no
    # copula) that the patterns above miss, so a follow-up "she is allergic to nuts"
    # can anchor to the friend. Only the PREFIX is case-insensitive; the NAME is
    # case-sensitive (must start uppercase) so "my son loves soccer" doesn't anchor
    # a person named "loves". _person_name_from_fragment trims trailing non-name
    # tokens ("my friend Caitlin is a teacher" → "Caitlin").
    re.compile(
        rf"(?i:(?:i\s+have\s+(?:a|an)\s+|my\s+)(?P<rel>{_RELATION_WORDS})\s+(?:named\s+|called\s+)?)"
        r"(?P<name>[A-Z][A-Za-z' -]{1,60})",
    ),
)

_FEMALE_RELATIONS = frozenset(
    {"wife", "girlfriend", "daughter", "sister", "mom", "mother", "grandma", "grandmother", "aunt", "niece"}
)
_MALE_RELATIONS = frozenset(
    {"husband", "boyfriend", "son", "brother", "dad", "father", "grandpa", "grandfather", "uncle", "nephew"}
)


def _person_intro_from(prev_user_message: str) -> tuple[str, Optional[str]]:
    """(name, relation) introduced in the prior USER message, or ('', None).

    Person introductions only — pet/thing patterns deliberately excluded so a
    pronoun fact is never anchored to a non-person.
    """
    for rx in _PERSON_INTRO_RES:
        m = rx.search(prev_user_message)
        if m:
            name = _person_name_from_fragment(m.group("name"))
            if name:
                return name, m.group("rel").lower()
    pm = _PERSON_PATTERN.search(prev_user_message)
    if pm:
        name = _person_name_from_fragment(pm.group(1))
        # Anchor only to the leading Capitalized tokens — "Steve at the market"
        # must anchor as "Steve", and an all-lowercase fragment is no anchor.
        cap_tokens: list[str] = []
        for tok in name.split():
            if not tok[:1].isupper():
                break
            cap_tokens.append(tok)
        if cap_tokens:
            return " ".join(cap_tokens), None
    return "", None


def _correction_candidates(
    user_message: str, prev_user_message: str, seen: set[str]
) -> list[MemoryCandidate]:
    """Resolve an entity-less correction against the prior USER message.

    Returns [] unless a correction shape matches AND the corrected-away value
    actually occurs in the prior message — a correction with no anchor must
    store nothing rather than guess.
    """
    msg = _clean(user_message)
    match = None
    for rx in _CORRECTION_RES:
        match = rx.match(msg)
        if match:
            break
    if not match:
        return []
    new_val = _clean(match.group("new"))
    old_val = _clean(match.group("old"))
    if not new_val or not old_val or new_val.lower() == old_val.lower():
        return []
    prev = _clean(prev_user_message)
    if not prev:
        return []
    old_rx = re.compile(rf"\b{re.escape(old_val)}\b", re.IGNORECASE)
    if not old_rx.search(prev):
        return []
    # Lambda replacement: re.sub would otherwise interpret backslash escapes
    # (\1, \g<...>) inside user-supplied new_val as template references.
    corrected = _clean(old_rx.sub(lambda _m: new_val, prev, count=1))
    source_excerpt = _clean(user_message)[:220]
    # Prefer re-mining the corrected sentence through the normal templates so a
    # correctable templated fact ("my wife's name is Emma" → "…Anna") lands in
    # its canonical shape; otherwise store the corrected sentence itself.
    mined = _mine_templates(corrected, source_excerpt, seen)
    if mined:
        return mined
    text = f"Correction: {corrected}"
    key = text.lower()
    if key in seen:
        return []
    seen.add(key)
    return [
        MemoryCandidate(
            text=text,
            memory_type="fact",
            confidence=0.8,
            source_excerpt=source_excerpt,
        )
    ]


def _pronoun_fact_candidates(
    user_message: str, prev_user_message: str, seen: set[str]
) -> list[MemoryCandidate]:
    """Anchor a pronoun-subject fact to the person the prior message introduced."""
    m = _PRONOUN_FACT_RE.match(_clean(user_message))
    if not m:
        return []
    name, relation = _person_intro_from(_clean(prev_user_message))
    if not name:
        return []
    pron = m.group("pron").lower()
    if relation in _FEMALE_RELATIONS and pron == "he":
        return []
    if relation in _MALE_RELATIONS and pron == "she":
        return []
    pred = _clean(m.group("pred"))
    # "she's ALSO allergic to shellfish" → drop the filler so the fact reads clean.
    pred = re.sub(r"^also\s+", "", pred, flags=re.IGNORECASE).strip()
    if not pred:
        return []
    # Skip ephemeral present-continuous states ("she is going to the store"): a
    # durable person-fact never leads with a gerund, but allergies / roles /
    # traits / origins ("allergic to nuts", "a doctor", "tall", "from Boston") don't.
    if pred.split(maxsplit=1)[0].lower().endswith("ing"):
        return []
    anchor = f"{name} (user's {relation})" if relation else name
    text = f"{anchor} is {pred}"
    key = text.lower()
    if key in seen:
        return []
    seen.add(key)
    return [
        MemoryCandidate(
            text=text,
            memory_type="person",
            title=name,
            entity_type="person",
            entity_id=name.lower().replace(" ", "_"),
            confidence=0.75,
            source_excerpt=_clean(user_message)[:220],
        )
    ]


def _possessive_fact_candidates(
    user_message: str, prev_user_message: str, seen: set[str]
) -> list[MemoryCandidate]:
    """Anchor a possessive-pronoun fact ("her birthday is actually March 25",
    "her daughter is named Poppy") to the person the prior message introduced.

    QA review F2/F3: these were previously stored raw (minting a person named
    "her") or silently dropped while the reply claimed the fact was kept.
    """
    m = _POSSESSIVE_FACT_RE.match(_clean(user_message))
    if not m:
        return []
    name, relation = _person_intro_from(_clean(prev_user_message))
    if not name:
        return []
    pron = m.group("pron").lower()
    if relation in _FEMALE_RELATIONS and pron == "his":
        return []
    if relation in _MALE_RELATIONS and pron == "her":
        return []
    attr = _clean(m.group("attr"))
    val = _clean(m.group("val"))
    if not attr or not val:
        return []
    # Ephemeral states ("her flight is boarding now") are not durable person-facts.
    if attr.split(maxsplit=1)[0].lower().endswith("ing"):
        return []
    if val.split(maxsplit=1)[0].lower().endswith("ing"):
        return []
    text = f"{name}'s {attr} is {val}"
    key = text.lower()
    if key in seen:
        return []
    seen.add(key)
    return [
        MemoryCandidate(
            text=text,
            memory_type="person",
            title=name,
            entity_type="person",
            entity_id=name.lower().replace(" ", "_"),
            confidence=0.75,
            source_excerpt=_clean(user_message)[:220],
        )
    ]


def _slug_body(name: str) -> str:
    """Name → the slug body person_extractor uses (``lower``, spaces → ``_``)."""
    return (name or "").strip().lower().replace(" ", "_")


async def _resolve_unique_person_uuid(name: str, user_id: str, db) -> Optional[str]:
    """``people.id`` for ``name`` ONLY when the match is unambiguous, else None.

    ``person_extractor._resolve_person_uuid`` does a substring ``LIKE`` and returns
    the FIRST row, so a short extracted name ("Sam") can silently hard-link to a
    longer contact ("Samantha"). For a HARD ``person`` link we require certainty:

    * a unique exact (case-insensitive) name match → link it;
    * a single non-exact substring hit → link ONLY when the extracted name is a
      whole NAME TOKEN of that contact (first-name "Katie" → "Katie Brown"),
      never a mere sub-token ("Al" ⊂ "Alice"), which would attach the fact to
      the wrong person;
    * anything else (zero matches, a genuinely ambiguous set, or a sub-token
      match) → None, so the caller keeps the fact ``person_pending`` rather than
      guess.

    Uses the same dual placeholder idiom as ``_resolve_person_uuid``.
    """
    name = (name or "").strip()
    if not name or not user_id or db is None:
        return None
    try:
        try:
            cur = await db.execute(
                "SELECT id, name FROM people WHERE user_id=$1 AND deleted=0 "
                "AND lower(name) LIKE lower($2)",
                user_id, f"%{name}%",
            )
        except Exception:
            cur = await db.execute(
                "SELECT id, name FROM people WHERE user_id=? AND deleted=0 "
                "AND lower(name) LIKE lower(?)",
                (user_id, f"%{name}%"),
            )
        rows = await cur.fetchall()
    except Exception as exc:
        logger.debug("memory_extractor: unique person resolve failed for %r: %s", name, exc)
        return None
    if not rows:
        return None
    target = name.lower()
    exact = [r for r in rows if str(r[1] or "").strip().lower() == target]
    if len(exact) == 1:
        return str(exact[0][0])
    if len(exact) > 1:
        return None  # multiple contacts with the same exact name — don't guess
    if len(rows) == 1:
        # Single substring hit, no exact match. Accept it ONLY when the extracted
        # name is a whole token of the contact's name ("Katie" → "Katie Brown"),
        # never a sub-token ("Al" ⊂ "Alice") — a sub-token would hard-link the
        # fact to the wrong person.
        if target in str(rows[0][1] or "").lower().split():
            return str(rows[0][0])
        return None
    return None  # ambiguous substring set (e.g. "Sam" ⊂ {"Sam","Samantha"})


async def _resolve_person_link(name: str, user_id: str, db) -> tuple[str, str]:
    """Map a person name to ``(entity_type, entity_id)`` — person_extractor's rule.

    Resolved to a real ``people`` row → ``("person", <people.id>)``; otherwise the
    honest pending marker ``("person_pending", "slug:<name>")``. This exists so a
    person-fact is NEVER stored as ``entity_type="person"`` with a bare name slug,
    which mislabels an unlinked fact as if it were keyed to the people table (the
    graph-linkage bug this producer had). Mirrors
    ``person_extractor._ingest_to_mempalace``'s convention exactly.
    """
    person_uuid = await _resolve_unique_person_uuid(name, user_id, db)
    if person_uuid:
        return "person", str(person_uuid)
    return "person_pending", f"slug:{_slug_body(name)}"


def _mine_templates(text: str, source_excerpt: str, seen: set[str]) -> list[MemoryCandidate]:
    """Run the template patterns over ``text`` (behavior-identical extraction loop)."""
    out: list[MemoryCandidate] = []
    for pattern, template, confidence in _TEMPLATE_PATTERNS:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        groups = tuple(_clean(g) for g in m.groups())
        if any(not g for g in groups):
            continue
        cand_text = _clean(template.format(*groups))
        key = cand_text.lower()
        if len(cand_text) < 8 or key in seen:
            continue
        seen.add(key)
        memory_type = "preference" if cand_text.startswith(("Preference:", "Favourite:")) else "fact"
        out.append(
            MemoryCandidate(
                text=cand_text,
                memory_type=memory_type,
                confidence=confidence,
                source_excerpt=source_excerpt,
            )
        )
    return out


def extract_candidates(
    user_message: str,
    assistant_response: str = "",
    prev_user_message: Optional[str] = None,
) -> list[MemoryCandidate]:
    """Extract memory candidates from a user turn.

    CONTRACT: user-fact candidates come from ``user_message`` ONLY.
    ``assistant_response`` is deliberately never mined — Zoe's own sentences
    ("... but I don't have any notes about your coffee preferences.") must
    never become stored user facts, or the recall packet reinforces her past
    denials forever (poisoned-store bug, 2026-07-07). The parameter is kept
    for call-site compatibility. Pinned by tests/test_memory_extractor_purity.py.

    ``prev_user_message`` is the PRIOR user turn (user-authored text only, same
    purity contract). When present it anchors two otherwise-unstorable shapes:
    entity-less corrections ("wait no sorry I meant saturday not friday") and
    pronoun-subject facts ("she's a doctor" after a person introduction).
    Without it those shapes store nothing — never a guessed anchor.
    """
    if _should_skip(user_message):
        return []

    source_excerpt = _clean(user_message)[:220]
    seen: set[str] = set()
    out: list[MemoryCandidate] = _mine_templates(user_message, source_excerpt, seen)

    pm = _PERSON_PATTERN.search(user_message)
    if pm:
        person = _person_name_from_fragment(pm.group(1))
        detail = _clean(pm.group(2) or "")
        if person:
            text = f"Person the user met: {person}"
            if detail:
                text += f" ({detail})"
            key = text.lower()
            if key not in seen:
                seen.add(key)
                out.append(
                    MemoryCandidate(
                        text=text,
                        memory_type="person",
                        title=person,
                        entity_type="person",
                        entity_id=person.lower().replace(" ", "_"),
                        confidence=0.86,
                        source_excerpt=source_excerpt,
                    )
                )

    prev = (prev_user_message or "").strip()
    if prev:
        out.extend(_correction_candidates(user_message, prev, seen))
        out.extend(_pronoun_fact_candidates(user_message, prev, seen))
        out.extend(_possessive_fact_candidates(user_message, prev, seen))

    return out


async def _load_recent_user_messages(
    user_id: str, session_id: str, current_message: str, limit: int = 6
) -> list[str]:
    """Recent USER messages in this session (newest first), excluding the current.

    Durable fallback for the in-process prev-turn LRU: prior turns handled by paths
    that never called ``note_user_turn`` would otherwise lose the pronoun anchor.
    Best-effort — returns [] on any error/miss.
    """
    from db_pool import get_db_ctx  # type: ignore[import]
    # Owner-scope the lookback: a shared panel session carries messages from
    # MULTIPLE users (per-message ownership in cm.metadata, else the session
    # owner). Without this, user B's pronoun could anchor to user A's intro and
    # the fact would be written in the wrong user's store. Reuses the digest's
    # canonical owner expression (no literal '?' — the compat layer counts them).
    from memory_digest import _message_owner_expr  # type: ignore[import]

    owner_expr = _message_owner_expr()
    cur = (current_message or "").strip()
    async with get_db_ctx() as db:
        rows = await (
            await db.execute(
                "SELECT cm.content FROM chat_messages cm "
                "JOIN chat_sessions cs ON cm.session_id = cs.id "
                "WHERE cm.session_id = ? AND cm.role = 'user' "
                f"AND {owner_expr} = ? "
                "ORDER BY cm.created_at::timestamptz DESC LIMIT ?",
                (session_id, user_id, limit),
            )
        ).fetchall()
    out: list[str] = []
    for r in rows:
        content = ((r[0] if not isinstance(r, dict) else r.get("content")) or "").strip()
        if content and content != cur:
            out.append(content)
    return out


async def _load_prev_user_message(user_id: str, session_id: str, current_message: str) -> str:
    """Most recent USER message in this session other than the current one."""
    recent = await _load_recent_user_messages(user_id, session_id, current_message, limit=5)
    return recent[0] if recent else ""


async def extract_and_ingest(
    user_message: str,
    assistant_response: str = "",
    *,
    user_id: str,
    session_id: Optional[str] = None,
    source: str = "chat_regex",
    auto_approve: bool = True,
    prev_user_message: Optional[str] = None,
) -> int:
    """Extract candidates and ingest them via MemoryService.

    Returns the number of successfully written/accepted candidates.

    ``prev_user_message`` overrides the anaphora anchor; when ``None`` (the
    per-turn call sites) the prior USER message is resolved from the in-process
    per-(user, session) LRU, and the current message is recorded for the next
    turn. Pass ``""`` to disable anchoring explicitly.
    """
    from memory_service import get_memory_service

    if prev_user_message is None:
        cur = (user_message or "").strip()
        prev_user_message = recall_prev_user_turn(user_id, session_id)
        # Fall back to the durable session history (chat_messages) when the
        # in-process LRU is EITHER empty (the prior turn was handled by a
        # contact/notes/expert path that never called note_user_turn) OR poisoned
        # with the CURRENT message (a second extract_and_ingest for this same turn
        # noted it first, so recall_prev returns the current text). Both cases lost
        # the pronoun anchor — the reason "she is allergic to nuts" didn't link.
        if (not prev_user_message.strip() or prev_user_message.strip() == cur) and session_id:
            try:
                prev_user_message = await _load_prev_user_message(
                    user_id, session_id, user_message
                )
            except Exception as exc:  # never let history lookup break extraction
                logger.debug("prev-user-message DB fallback failed: %s", exc)
                prev_user_message = ""
        # A retried/duplicated turn must not anchor to itself.
        if prev_user_message.strip() == cur:
            prev_user_message = ""
        # Pronoun CHAIN anchoring: "I have a friend Caitlin" / "she is allergic to
        # nuts" / "she's ALSO allergic to shellfish" — the third turn's literal
        # prev is the second (no person intro), so the anchor would break after one
        # hop. When the message is pronoun-shaped and the literal prev introduces
        # no person, walk recent session history (newest first, bounded) for the
        # most recent turn that DOES introduce one, and anchor there. Corrections
        # are untouched — they only fire on their own "no/actually" shapes and this
        # swap happens only for a pronoun-fact-shaped message with an intro-less prev.
        if (
            session_id
            and _PRONOUN_FACT_RE.match(_clean(user_message))
            and not _person_intro_from(_clean(prev_user_message))[0]
        ):
            try:
                for older in await _load_recent_user_messages(
                    user_id, session_id, user_message
                ):
                    if _person_intro_from(_clean(older))[0]:
                        prev_user_message = older
                        break
            except Exception as exc:
                logger.debug("pronoun chain-anchor lookback failed: %s", exc)
    try:
        candidates = extract_candidates(
            user_message, assistant_response, prev_user_message=prev_user_message
        )
    finally:
        # Record the current USER turn even when nothing extracts — the next
        # turn's correction/pronoun may anchor to it.
        note_user_turn(user_id, session_id, user_message)
    if not candidates:
        return 0

    svc = get_memory_service()
    saved = 0
    base_turn_id = hashlib.sha1(user_message.encode("utf-8", "ignore")).hexdigest()[:16]
    status = "approved" if auto_approve else "pending"

    # ── fact→person linkage hygiene ─────────────────────────────────────────
    # The person-fact candidates carry a bare name slug as ``entity_id`` with
    # ``entity_type="person"`` — which mislabels an unresolved fact as if it were
    # keyed to the people table. Resolve the name to a real ``people.id`` first
    # (person_extractor's convention): resolved → ``person`` + UUID; unresolved →
    # ``person_pending`` + ``slug:``. Only opens a DB when a person-fact is
    # present, so ordinary turns keep their existing (no-DB) fast path.
    person_idx = [i for i, c in enumerate(candidates) if (c.entity_type or "") == "person"]
    resolved_links: dict[int, tuple[str, str]] = {}
    if person_idx:
        from person_extractor import _ensure_db
        _db, _opened = await _ensure_db(None)
        try:
            for i in person_idx:
                c = candidates[i]
                name = (c.title or "").strip() or (c.entity_id or "").replace("_", " ")
                resolved_links[i] = await _resolve_person_link(name, user_id, _db)
        finally:
            if _opened and _db is not None:
                try:
                    await _db.close()
                except Exception:
                    pass

    try:
        from memory_quality import is_storable_fact
    except Exception:
        is_storable_fact = lambda _t: (True, "")  # gate unavailable → degrade to plain store

    for idx, c in enumerate(candidates):
        # Write-quality gate (mem0-style): drop candidates that aren't shaped
        # like a storable personal fact before they reach the store. Conservative
        # — the regex templates already produce structured text, so this only
        # catches genuine non-facts (interrogatives / meta) that slip through.
        storable, reason = is_storable_fact(c.text)
        if not storable:
            _record_quality_reject(source, reason, c.text)
            continue
        user_turn_id = f"{base_turn_id}-{idx}"
        entity_type, entity_id = resolved_links.get(idx, (c.entity_type, c.entity_id))
        # Reconciliation (mem0 ADD/UPDATE/SKIP) — QA review F2: value corrections
        # ("her birthday is actually March 25") were stored as NEW rows and the
        # stale value kept outranking the fix. classify_against_existing existed
        # but was never invoked on this write path. UPDATE → supersede the stale
        # row via review(edit) (links old→new); SKIP → drop the sparser echo;
        # ADD → plain ingest below. Best-effort: any error falls through to ADD.
        try:
            from memory_quality import classify_against_existing
            hits = await svc.search(c.text, user_id=user_id, limit=3)
            op, target_id = classify_against_existing(
                c.text, [(h.id, h.text or "") for h in hits if h.text]
            )
        except Exception as exc:
            logger.debug("reconciliation unavailable (%s) — plain ingest", exc)
            op, target_id = "add", None
        if op == "skip":
            continue
        if op == "update" and target_id:
            try:
                new_ref = await svc.review(
                    target_id,
                    decision="edit",
                    edits=c.text,
                    actor=source,
                    note="conversational correction supersede (QA F2)",
                )
                if new_ref is not None:
                    saved += 1
                    logger.info(
                        "memory_extractor: superseded %s with correction %r",
                        target_id, c.text[:60],
                    )
                    continue
            except Exception as exc:
                logger.warning("correction supersede failed (%s) — plain ingest", exc)
        ref = await svc.ingest(
            c.text,
            user_id=user_id,
            source=source,
            session_id=session_id,
            user_turn_id=user_turn_id,
            memory_type=c.memory_type,
            confidence=c.confidence,
            status=status,
            tags=["conversation", "auto_extract"],
            entity_type=entity_type,
            entity_id=entity_id,
        )
        if ref is not None:
            saved += 1
    if saved > 0:
        try:
            from zoe_agent import _invalidate_user_facts_cache
            _invalidate_user_facts_cache(user_id)
        except Exception:
            pass
    return saved


__all__ = [
    "MemoryCandidate",
    "extract_candidates",
    "extract_and_ingest",
    "note_user_turn",
    "recall_prev_user_turn",
]

