"""Write-quality gate for Zoe's conversational memory (mem0-style).

The store was polluted because the conversational write paths accepted
anything: interrogatives stored as facts ("Do you remember what my mum's name
is?"), LLM meta-rambling ("The provided statements illustrate a pattern…"),
and near-duplicate / contradictory rows ("My dad's name is Neil" written 5×).

This module is the single source of truth for *whether a conversational
candidate is shaped like a storable personal fact* and for the *near-dedup /
supersession* decision. It is:

  * Pure + offline — `is_storable_fact` is a deterministic string check (regex
    only, no model call, no I/O), so it is safe to unit-test exhaustively and
    cheap to run on every write.
  * CONSERVATIVE — a false-reject (losing a real fact) is worse than a stored
    fact, so anything that isn't a clear non-fact is ACCEPTED. The reject rules
    only fire on shapes we have actually observed as junk.

It is wired into the CONVERSATIONAL writers only (expert_dispatch.store_fact,
memory_extractor.extract_and_ingest, zoe_agent._mempalace_add). The structured
writers (notes / journal / people / user_profile routers) are already
structured and are NOT gated here.

The gate runs on WRITE, which is always a background path — it never blocks the
spoken voice reply.
"""

from __future__ import annotations

import logging
import re
import time
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from memory_metrics import record_reconcile_failopen
    _METRICS_OK = True
except Exception:  # pragma: no cover - optional instrumentation module
    _METRICS_OK = False

# Patient search budget for the WRITE path (not the latency-gated turn path).
# The 2 s default failed open to [] under load and silently degraded every
# correction to ADD (live Telegram repro 2026-07-13). MemoryService.search
# swallows its own TimeoutError and returns [], so a fail-open that burned
# ~this whole budget was a TIMEOUT; a fast empty is a cold / genuinely-empty
# store — the elapsed time is what tells the two fail-open causes apart.
_RECONCILE_SEARCH_TIMEOUT_S = 15.0


def _record_failopen(cause: str) -> bool:
    """Count a reconcile fail-open-to-ADD event; return True if the rate is now
    sustained (so the caller escalates WARNING → ERROR).

    Pure observability: never raises, never changes the fail-open ADD decision
    (Jason's rule: duplicates over lost facts). Degrades to a no-op when the
    metrics module is unavailable."""
    if not _METRICS_OK:
        return False
    try:
        status = record_reconcile_failopen(cause)
        return bool(status.get("sustained"))
    except Exception:  # pragma: no cover - metrics must never break the write
        return False


# ---------------------------------------------------------------------------
# is_storable_fact — fact-shape check
# ---------------------------------------------------------------------------

# Minimum useful length. Below this it's almost never a real fact ("ok", "yes").
_MIN_LEN = 6

# Interrogative openers — "do/did/can you remember…", "what/when/where/who is…".
# These are RECALL questions, not facts. expert_dispatch.store_fact already has
# a richer store-vs-recall split; this is the defensive backstop for the other
# writers (and a second layer for store_fact).
_QUESTION_OPENER_RE = re.compile(
    r"^\s*(?:hey\s+|ok\s+|so\s+|um\s+|uh\s+)*"
    r"(?:"
    r"do|did|does|don'?t|can|could|would|will|should|are|is|was|were|have|has|had|am|"
    r"what'?s?|what|when'?s?|when|where'?s?|where|who'?s?|who|whose|which|why|how|"
    r"remind\s+me\s+(?:what|who|when|where|how|why|which)"
    r")\b",
    re.IGNORECASE,
)

# Low-information meta / LLM-rambling. Observed: rows starting with "The
# provided…", "The context…", "The concept…", "The following…", "Based on…",
# "It seems…", "These statements…". These are model commentary, never a fact.
_META_OPENER_RE = re.compile(
    r"^\s*(?:"
    r"the\s+(?:provided|context|concept|following|statement|statements|above|user'?s|"
    r"information|text|passage|conversation|response|key|main|overall)|"
    r"these\s+(?:statements?|facts?|points?|examples?)|"
    r"this\s+(?:statement|passage|text|response|conversation)|"
    r"based\s+on\b|"
    r"in\s+summary\b|"
    r"to\s+summari[sz]e\b|"
    r"it\s+(?:seems|appears|looks\s+like)\b|"
    r"here\s+(?:is|are)\s+(?:a\s+)?(?:summary|some|the)\b|"
    r"as\s+an\s+ai\b|"
    r"i\s+(?:cannot|can'?t|don'?t\s+have)\b"
    r")",
    re.IGNORECASE,
)

# Dialogue-transcript echoes stored as "facts": "Zoe: is being addressed in a
# conversation", "Jason: Has nothing scheduled for this week". A leading
# "<Speaker>:" turn attribution — a generic role label, or any capitalised name
# followed by a third-person predicate — is transcript noise, never a
# first-person fact. (These slip past the personal-subject check below because
# the capitalised speaker name reads as a "concrete token" and is accepted.)
_SPEAKER_ECHO_RE = re.compile(
    r"^\s*(?:zoe|assistant|the\s+assistant|user|human|ai|bot|system|speaker\s*\d*)\s*:",
    re.IGNORECASE,
)
_TRANSCRIPT_ECHO_RE = re.compile(
    r"^\s*[A-Z][A-Za-z'-]+\s*:\s+(?i:is|are|was|were|has|have|had|will|would|"
    r"does|do|did|likes?|wants?|needs?|said|says|asked|mentioned|added)\b",
)
# A weather report ("It's 17 degrees and mainly clear ... feels like 10 degrees")
# is ephemeral, never a durable personal fact — a background extractor turning an
# assistant weather reply into a "memory" pollutes recall. Require BOTH a
# temperature signal AND a weather-condition marker so a real preference like
# "I like it at 20 degrees" is NOT caught.
_WEATHER_REPORT_RE = re.compile(
    r"\bfeels?\s+like\b[^.]*\bdegrees?\b"                       # "feels like 10 degrees"
    r"|\bdegrees?\b[^.]*\bfeels?\s+like\b"                      # "10 degrees ... feels like"
    r"|\bdegrees?\b[^.]*\b(?:mainly\s+)?(?:clear|cloudy|sunny|overcast|rain(?:ing|y)?|showers?|drizzle|storm|snow|sleet|humid|windy|fog(?:gy)?|haz[ey]|frost(?:y)?|freezing|breezy|hail|mist(?:y)?)\b"
    r"|\b(?:mainly\s+clear|clear\s+sky|partly\s+cloudy|mostly\s+(?:sunny|cloudy))\b[^.]*\bdegrees?\b",
    re.IGNORECASE,
)

# A first/second-person subject — "I …", "my …", "you …", "your …", "we / our".
# A genuine personal fact almost always has one. Memory-command forms
# ("remember that …", "note that …") are personal facts too.
_PERSONAL_SUBJECT_RE = re.compile(
    r"\b(?:i|i'?m|i'?ve|i'?ll|i'?d|my|mine|me|myself|"
    r"you|you'?re|you'?ve|your|yours|"
    r"we|we'?re|our|ours)\b",
    re.IGNORECASE,
)
# A "do/can/will YOU remember…?" RECALL question contains 'remember' but is NOT
# a teach command — exclude it so the question check still fires.
_RECALL_QUESTION_RE = re.compile(
    r"\b(?:do|did|does|can|could|would|will)\s+(?:you|ya)\s+"
    r"(?:remember|recall|recollect|know|have)\b",
    re.IGNORECASE,
)
# An imperative memory command ("remember that …", "note that …"). The
# lookbehinds stop "you/ya/i remember…" (a recall) from reading as a command.
_MEMORY_COMMAND_RE = re.compile(
    r"\b(?:don'?t\s+forget|note\s+that|make\s+a\s+note|keep\s+in\s+mind)\b"
    r"|(?<!you )(?<!ya )(?<!i )\bremember\b",
    re.IGNORECASE,
)
# A concrete noun-ish token: a capitalised word (a name/place) or a digit
# (date, number, age). Used only as a fallback when there is NO personal
# subject — to avoid rejecting an odd-but-real fragment.
_CONCRETE_TOKEN_RE = re.compile(r"\b[A-Z][a-z]+\b|\d")

# Some extraction templates emit a third-person summary ("User's name is …",
# "Preference: user likes …", "Person the user met: …"). Those are legitimate
# stored shapes even though they lack a first/second-person pronoun.
_EXTRACTED_PREFIX_RE = re.compile(
    r"^\s*(?:user(?:'?s)?\b|preference\b|favou?rite\b|person\s+the\s+user\b|"
    r"important\s+note\b)",
    re.IGNORECASE,
)


def is_storable_fact(text: str) -> tuple[bool, str]:
    """Return ``(storable, reason)`` for a conversational memory candidate.

    REJECT (storable=False) clear non-facts:
      * empty / too short
      * interrogatives / questions ("do you remember…?", trailing "?")
      * low-information meta / LLM-rambling ("The provided…", "Based on…")
      * no personal subject AND no concrete noun (generic filler)

    ACCEPT (storable=True) declarative personal facts/preferences
    ("My dad's name is Neil", "I prefer morning coffee", "remember that …").

    Lean toward ACCEPT when unsure — a false-reject loses a real fact, which is
    worse than letting one junk row through. ``reason`` is "" when accepted, or a
    short stable label (for metrics/logging) when rejected.
    """
    raw = (text or "").strip()
    if not raw:
        return False, "empty"
    if len(raw) < _MIN_LEN:
        return False, "too_short"

    # A memory command ("remember that my mum likes NCIS") is always a fact,
    # even though it can superficially look like other shapes — accept early.
    # But "do you remember…?" is a recall question, not a command.
    has_memory_command = bool(_MEMORY_COMMAND_RE.search(raw)) and not _RECALL_QUESTION_RE.search(raw)

    # Trailing "?" or an interrogative opener → a question, not a fact. But a
    # memory command that merely *contains* a question word ("remember that I
    # asked who's coming") is still a teach, so the command guard wins.
    if not has_memory_command:
        if raw.rstrip().endswith("?"):
            return False, "question_mark"
        if _QUESTION_OPENER_RE.match(raw):
            return False, "interrogative"

    # LLM meta-rambling — never a personal fact.
    if _META_OPENER_RE.match(raw):
        return False, "meta_rambling"

    # Dialogue-transcript echoes ("Zoe: is being addressed…", "Jason: Has
    # nothing scheduled…") — a speaker-attributed turn, never a stored fact.
    if _SPEAKER_ECHO_RE.match(raw) or _TRANSCRIPT_ECHO_RE.match(raw):
        return False, "transcript_echo"

    if has_memory_command:
        return True, ""

    # Ephemeral weather report captured as a "fact" (e.g. an extractor scraping an
    # assistant weather reply). Never durable — reject before the concrete-token
    # fallback would otherwise accept it on its numbers. Only a SUBJECTLESS report
    # ("It's 17 degrees and clear…") is dropped: a first/second-person sentence
    # ("my baby feels like she has 38 degrees fever", "my room feels 15 degrees
    # colder") is a real owned fact and must fall through to the personal-subject
    # accept below — false-rejecting a real fact is worse than storing one.
    if _WEATHER_REPORT_RE.search(raw) and not _PERSONAL_SUBJECT_RE.search(raw):
        return False, "weather_report"

    # Accept the structured-extraction summary shapes ("User's name is …").
    if _EXTRACTED_PREFIX_RE.match(raw):
        return True, ""

    # A genuine personal fact has a first/second-person subject.
    if _PERSONAL_SUBJECT_RE.search(raw):
        return True, ""

    # No personal subject. Only keep it if it carries a concrete noun/number
    # (could be a real but oddly-phrased fact); otherwise it's generic filler.
    if _CONCRETE_TOKEN_RE.search(raw):
        return True, ""
    return False, "no_subject_no_concrete"


# ---------------------------------------------------------------------------
# looks_like_correction — correction/negation turn-shape detector (QA F8)
# ---------------------------------------------------------------------------
#
# "No Caitlin is allergic to shellfish, I don't believe Jessica is allergic to
# anything" was stored VERBATIM as a taught "fact" by expert_dispatch.store_fact
# ("Got it — I'll remember No Caitlin is allergic to shellfish, you don't
# believe Jessica…", live prod 2026-07). Turns shaped like a correction or a
# negation of something previously said belong to the correction path
# (memory_extractor, which runs first per #1242's ordering) — when that path
# produces nothing, the safe behavior is to store NOTHING, never the raw text.
#
# NOTE the deliberate ambiguity of "No <Name> is allergic to Y": it reads both
# as "No, <Name> IS allergic" (a correction) and "No <Name> is allergic" (a
# negation). Both readings mean the raw string is junk as a stored fact —
# detection is what matters, not disambiguation.

_CORRECTIONISH_RES = (
    # "no, …" / "no that's wrong…" — a leading negation of the prior exchange.
    re.compile(r"^\s*no\s*[,!.:;-]", re.IGNORECASE),
    # "no <Name> is/does/has/isn't…" — the ambiguous negation-correction shape.
    # Case-insensitive on the subject too: voice/lazy typing produces
    # "no caitlin is allergic…" (Greptile P1). Idiom subjects that make "no X"
    # ordinary English ("no one is…", "no way…") are excluded below.
    re.compile(
        r"^\s*no\s+(?!(?:one|body|way|thanks|worries|problem|matter|doubt|"
        r"idea|need|more|longer|kidding|wonder|rush|offense|offence)\b)"
        r"(?:my\s+)?[A-Za-z][\w'-]*\s+"
        r"(?:is|are|was|were|does|do|did|has|have|had|"
        r"isn'?t|aren'?t|wasn'?t|doesn'?t|don'?t|hasn'?t|can'?t|won'?t)\b",
        re.IGNORECASE,
    ),
    # "that's wrong / not right / incorrect / not true"
    re.compile(
        r"^\s*that'?s\s+(?:wrong|incorrect|not\s+(?:right|true|correct))\b",
        re.IGNORECASE,
    ),
    # "actually …" / "wait no …" / "i meant …" — correction openers.
    re.compile(r"^\s*actually\b", re.IGNORECASE),
    re.compile(r"^\s*wait\s*[,!]?\s*no\b", re.IGNORECASE),
    re.compile(r"^\s*(?:sorry\s*[,!]?\s*)?i\s+meant\b", re.IGNORECASE),
)

# The specifically AMBIGUOUS "No <Name> is …" negation shape — safest to store
# nothing and ask, since both readings contradict a verbatim store.
_AMBIGUOUS_NEGATION_RE = re.compile(
    r"^\s*no\s+(?!(?:one|body|way|thanks|worries|problem|matter|doubt|"
    r"idea|need|more|longer|kidding|wonder|rush|offense|offence|my)\b)"
    r"(?P<name>[A-Za-z][\w'-]*)\s+"
    r"(?:is|are|was|were|does|do|did|has|have|had)\b",
    re.IGNORECASE,
)


def looks_like_correction(text: str) -> bool:
    """True when the turn is SHAPED like a correction/negation of prior context
    ("no, …", "no <Name> is …", "that's wrong…", "actually …", "i meant …").

    Such a turn must never be stored verbatim as a taught fact: the correction
    path (memory_extractor) owns it, and when that path yields nothing the
    caller should drop it rather than store junk (QA review F8)."""
    raw = (text or "").strip()
    if not raw:
        return False
    return any(rx.match(raw) for rx in _CORRECTIONISH_RES)


def ambiguous_negation_subject(text: str) -> Optional[str]:
    """The <Name> of an ambiguous "No <Name> is/does/has …" negation, else None.

    "No Caitlin is allergic to shellfish" reads BOTH as "No, Caitlin IS
    allergic" and "No Caitlin is allergic" — no deterministic writer should
    guess. Callers use the name to ask for clarification instead of storing."""
    m = _AMBIGUOUS_NEGATION_RE.match((text or "").strip())
    if not m:
        return None
    name = m.group("name")
    # Voice/lazy typing yields "no caitlin is…" — present the name properly,
    # but never mangle an already-cased one (McKenna).
    return name if name[:1].isupper() else name.capitalize()


# ---------------------------------------------------------------------------
# Near-dedup / supersession (mem0 ADD vs UPDATE idea)
# ---------------------------------------------------------------------------

# Pull the attribute being asserted from "my <attr> is/are/was <value>",
# "user's <attr> is <value>", or "<subject>'s <attr> is <value>" so we can tell
# "my dad's name is Neil" from "my dad's name is spelt N-E-I-L" (same attribute).
# Match a PERSONAL attribute assertion only — not any "X is Y" clause. Two
# shapes qualify so the first-person original and its distilled third-person
# restatement key the same way:
#   (a) a first/second-person possessive subject — "my/your/our <attr> is …"
#   (b) a possessive phrase — "<owner>'s <attr> is …" ("dad's name is …",
#       "user's father's name is …", "Neil's job is …")
# Requiring either a personal possessive pronoun or an explicit possessive
# marker keeps generic statements ("The weather is nice") from being read as
# attribute assertions. The subject is stripped from the key by _attribute_key.
_ATTR_RE = re.compile(
    r"(?:"
    r"\b(?:my|your|our)\s+([a-z][a-z '`-]*?)"          # (a) personal-pronoun subject
    r"|\b[a-z][a-z]*'?s\s+([a-z][a-z '`-]*?)"           # (b) possessive owner ('s)
    r")\s+(?:is|are|was|were|=|:)\b",
    re.IGNORECASE,
)

# Relation/attribute synonyms — distillation rephrases ("dad"→"father",
# "mum"→"mother"), which must NOT defeat same-attribute detection. Map each
# variant onto a single canonical token.
_ATTR_SYNONYMS = {
    "dad": "father", "daddy": "father", "father": "father", "papa": "father",
    "mum": "mother", "mom": "mother", "mommy": "mother", "mummy": "mother",
    "mother": "mother", "mama": "mother",
    "wife": "spouse", "husband": "spouse", "spouse": "spouse", "partner": "spouse",
    "kid": "child", "kids": "child", "children": "child", "child": "child",
    "son": "child", "daughter": "child",
}
# Subject/filler tokens that carry no attribute meaning — dropped from the key so
# "user's father's name" and "my dad's name" reduce to the same key ("father name").
_ATTR_STOPWORDS = {
    "s", "the", "a", "an", "user", "users", "my", "your", "our", "his", "her",
    "their", "of",
}
# Additional filler dropped when comparing the VALUE of two facts (copulas,
# articles) so "name is Neil" vs "name is Tom" share no value token and read as
# a correction, not the same value.
_VALUE_STOPWORDS = _ATTR_STOPWORDS | {
    "is", "are", "was", "were", "be", "been", "am",
    "and", "or", "to", "in", "on", "at", "it", "that", "this",
}

# How similar two candidate texts must be to count as "the same fact" for the
# skip-near-exact-duplicate path. High, to stay conservative.
_NEAR_DUP_RATIO = 0.92
# Lower bar for "same attribute, different value" supersession — we additionally
# require the extracted attribute keys to match, so this can be looser.
_SUPERSEDE_RATIO = 0.45
# When two same-attribute facts assert the SAME core value (e.g. both say the
# name is "Neil"), the only difference is detail/phrasing. We then keep whichever
# carries more information and drop the other — never accumulate both.
_RICHNESS_MARGIN = 4  # min extra meaningful chars to call one fact "richer"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower()).strip(" .!?,")


def _attribute_key(text: str) -> Optional[str]:
    """Best-effort: the attribute a 'X is Y' statement is about ('father name').

    Handles first- and third-person framings ("my dad's name is …", "user's
    father's name is …", "her mum's name is …") and canonicalises relation
    synonyms (dad/father, mum/mother) so distilled rephrasings still key to the
    same attribute. Returns None when the text isn't an attribute assertion
    (then we fall back to pure similarity for the dedup decision)."""
    m = _ATTR_RE.search(text or "")
    if not m:
        return None
    # _ATTR_RE has two alternative capture groups (pronoun-subject / possessive);
    # exactly one matches.
    raw = m.group(1) or m.group(2) or ""
    attr = re.sub(r"[^a-z ]+", " ", raw.lower())
    # collapse possessive/subject noise ("dad 's name" → "dad name", drop "user")
    tokens = [t for t in attr.split() if t not in _ATTR_STOPWORDS]
    tokens = [_ATTR_SYNONYMS.get(t, t) for t in tokens]
    return " ".join(tokens).strip() or None


def _value_tokens(text: str) -> set[str]:
    """The salient (non-stopword) value tokens of a fact — used to tell whether
    two same-attribute facts assert the same underlying value ("Neil") or a
    genuinely different/updated one.

    Tokens are canonicalised through ``_ATTR_SYNONYMS`` so a relation word that
    is ALSO part of the attribute ("dad" in "my dad's name is …") maps to the
    same token the attribute key uses ("father") and is therefore stripped by
    ``_value_only`` — otherwise it would leak in as a fake shared value and mask
    a real correction (Neil → Tom)."""
    norm = _normalize(text)
    return {_ATTR_SYNONYMS.get(t, t)
            for t in re.findall(r"[a-z0-9]+", norm) if t not in _VALUE_STOPWORDS}


def _value_only(text: str, attr_key: Optional[str]) -> set[str]:
    """Value tokens with the attribute-key tokens removed, so what's left is the
    asserted VALUE ("neil" / "tom") rather than the attribute ("father name")."""
    return _value_tokens(text) - set((attr_key or "").split())


def _same_value(a: str, b: str, attr_key: Optional[str]) -> bool:
    """True when two same-attribute facts assert the same underlying value.

    One fact's value tokens must be a SUBSET of the other's: a pure rephrasing
    has equal token sets ("red Toyota" / "Toyota red") and a richer restatement
    is a superset ("Neil" / "Neil, spelled N-E-I-L"), so both still merge. A
    correction that swaps the distinctive value keeps a leftover token on EACH
    side ("red Toyota" vs "red Honda"), so a merely-shared incidental modifier
    ("red", "iced", "small brown") can no longer mask it — the difference is
    non-empty in both directions → False → supersede, not skip."""
    va, vb = _value_only(a, attr_key), _value_only(b, attr_key)
    return bool(va and vb) and (va <= vb or vb <= va)


def _information(text: str) -> int:
    """A cheap proxy for how much information a fact carries: the count of
    salient (non-stopword) characters. The richer fact (e.g. the one that also
    spells the name) wins so we never replace detail with a sparser restatement."""
    return sum(len(t) for t in _value_tokens(text))


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _attrs_match(a: str, b: str) -> bool:
    """Same attribute despite phrasing: exact key match OR one key's tokens are a
    subset of the other's ("birthday" ⊆ "friend jessica birthday"). QA review F2:
    the correction "Jessica's birthday is March 25" keyed 'birthday' while the
    stale "My friend Jessica's birthday is March 15" keyed 'friend jessica
    birthday' — exact-key equality never fired, so the correction ADDed instead
    of superseding. The similarity gate (_SUPERSEDE_RATIO) still guards against
    unrelated facts that share a generic token.
    """
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


def classify_against_existing(
    text: str,
    existing: list[tuple[str, str]],
) -> tuple[str, Optional[str]]:
    """Decide ADD / UPDATE / SKIP for a candidate against existing memories.

    ``existing`` is a list of ``(mem_id, mem_text)`` from a semantic search
    (e.g. ``MemoryService.search(text, user_id, limit≈3)``). Returns:

      * ("add",    None)    — no close match; store as new.
      * ("update", mem_id)  — the candidate is the SAME fact as ``mem_id`` but
                              genuinely richer / more current → supersede it.
      * ("skip",   mem_id)  — the candidate is a near-duplicate of an existing
                              fact that is at least as informative (e.g. the
                              existing one already spells the name) → keep the
                              existing row, store nothing.

    Conservative on both merge directions:
      * Only returns "update"/"skip" when we're confident it's the SAME fact —
        a near-exact text duplicate, or a same-attribute assertion with the
        same core value. A same-attribute fact with a DIFFERENT value is an
        update; matching attribute + matching value collapses to skip/update by
        richness.
      * Genuinely distinct facts (different attribute, or a different value that
        doesn't read as a correction) → "add"; when unsure, keep both.
    """
    if not existing:
        return "add", None

    cand_attr = _attribute_key(text)
    best_dup: tuple[float, Optional[str], str] = (0.0, None, "")
    best_attr: tuple[float, Optional[str], str] = (0.0, None, "")

    for mem_id, mem_text in existing:
        if not mem_text:
            continue
        sim = _similarity(text, mem_text)
        if sim > best_dup[0]:
            best_dup = (sim, mem_id, mem_text)
        mem_attr = _attribute_key(mem_text)
        if cand_attr and mem_attr and sim > best_attr[0] and _attrs_match(cand_attr, mem_attr):
            best_attr = (sim, mem_id, mem_text)

    # 1) Near-exact text duplicate. Two near-identical strings can still differ
    #    only in the VALUE ("…name is Jo" vs "…name is Joe") — that's a
    #    correction, so supersede. Only when the value matches is it pure
    #    phrasing/detail, and we keep the richer copy.
    if best_dup[0] >= _NEAR_DUP_RATIO and best_dup[1]:
        if cand_attr and not _same_value(text, best_dup[2], cand_attr):
            return "update", best_dup[1]
        return _merge_decision(text, best_dup[2], best_dup[1])

    # 2) Same attribute → it's the same fact. If they assert the same core value
    #    ("name is Neil" both ways) keep the richer one; if the value genuinely
    #    differs, supersede the stale row rather than accumulate contradictions.
    if best_attr[1] and best_attr[0] >= _SUPERSEDE_RATIO:
        if _same_value(text, best_attr[2], cand_attr):
            return _merge_decision(text, best_attr[2], best_attr[1])
        # Different value for the same attribute → treat as a correction/update.
        return "update", best_attr[1]

    return "add", None


def _merge_decision(
    candidate: str, existing_text: str, existing_id: str,
) -> tuple[str, Optional[str]]:
    """Same fact, two phrasings: keep whichever carries more information.

    SKIP (store nothing) when the existing row is at least as rich — this is the
    common consolidation case where a distilled restatement ("User's father's
    name is Neil") is a sparser echo of a richer stored fact ("My dad's name is
    Neil, spelled N-E-I-L"). UPDATE only when the candidate is meaningfully
    richer, so corrections that also add detail still land.
    """
    if _information(candidate) > _information(existing_text) + _RICHNESS_MARGIN:
        return "update", existing_id
    return "skip", existing_id


# ── User-anchored relationship validation ────────────────────────────────────
# A 4B extractor, asked to say WHOSE relative a person is, defaults to "the
# user" when the text doesn't say ("Emily is the wife" — describing a FRIEND's
# family — became "Emily is the user's wife", live 2026-07-12). Rule: a fact may
# anchor a relationship role to the user ONLY if the source turn literally says
# "my <role>" (adjectives allowed: "my male friend"). Producers pass the source
# turn text; unsupported claims are dropped, never stored.

_USER_ANCHORED_ROLE_RE = re.compile(
    r"(?:\buser'?s?\b|\bspeaker'?s?\b|\bmy\b|\bof\s+(?:the\s+)?(?:user|speaker|mine|me|myself)\b)",
    re.IGNORECASE,
)
_ROLE_WORD_RE = re.compile(
    r"\b(wife|husband|partner|girlfriend|boyfriend|fianc[eé]e?|spouse"
    r"|son|daughter|kid|children|child|girl|boy|baby"
    r"|friend|mate|buddy|bestie"
    r"|brother|sister|mum|mom|mother|dad|father|grandma|grandmother|grandpa"
    r"|grandfather|aunt|uncle|niece|nephew|cousin|parent|sibling|grandparent"
    r"|colleague|coworker|boss|neighbour|neighbor)s?\b",
    re.IGNORECASE,
)


_ROLE_SYNONYMS: tuple[frozenset[str], ...] = (
    frozenset({"mum", "mom", "mother"}),
    frozenset({"dad", "father"}),
    frozenset({"grandma", "grandmother"}),
    frozenset({"grandpa", "grandfather"}),
    frozenset({"kid", "child", "children"}),
    frozenset({"neighbour", "neighbor"}),
    frozenset({"colleague", "coworker"}),
    frozenset({"girl", "daughter"}),
    frozenset({"boy", "son"}),
    frozenset({"wife", "spouse"}),
    frozenset({"husband", "spouse"}),
    frozenset({"partner", "spouse"}),
    frozenset({"friend", "mate", "buddy", "bestie"}),
)


# DIRECTIONAL hyponyms: a GENERIC role in a fact is supported by any of its
# specific forms in the source ("my mum" supports "user's parent"), but never
# the reverse ("my dad" must NOT support "user's mother" — that stays confined
# to the symmetric synonym groups above).
_ROLE_HYPONYMS: dict[str, frozenset[str]] = {
    "parent": frozenset({"mum", "mom", "mother", "dad", "father"}),
    "sibling": frozenset({"brother", "sister"}),
    "grandparent": frozenset({"grandma", "grandmother", "grandpa", "grandfather"}),
    "kid": frozenset({"son", "daughter", "girl", "boy"}),
    "child": frozenset({"son", "daughter", "girl", "boy"}),
    "children": frozenset({"son", "daughter", "girl", "boy"}),
}


def _role_variants(role: str) -> frozenset[str]:
    """The role plus its everyday synonyms (mum/mother, kid/child, ...) and —
    for generic roles — its specific hyponyms (parent ← mum/dad)."""
    out = {role}
    for group in _ROLE_SYNONYMS:
        if role in group:
            out |= group
    out |= _ROLE_HYPONYMS.get(role, frozenset())
    return frozenset(out)


def user_relationship_claim_unsupported(fact_text: str, source_text: str) -> bool:
    """True when ``fact_text`` anchors a relationship role to the USER but the
    source turn never says "my <role>" — i.e. the extractor guessed the anchor.

    Non-relationship facts, third-party-anchored facts ("wife of Lindsay"), and
    user anchors the source supports ("my male friend" → "user's friend") pass.
    """
    fact = (fact_text or "").strip()
    if not fact or not _USER_ANCHORED_ROLE_RE.search(fact):
        return False
    # group(1) is already the canonical singular — the plural `s?` sits OUTSIDE
    # the capture group ("kids" → "kid"; "children" is an explicit alternative).
    # Never rstrip("s"): it strips ALL trailing s's ("boss" → "bo") and would
    # break the "my boss" support check.
    roles = {m.group(1).lower() for m in _ROLE_WORD_RE.finditer(fact)}
    if not roles:
        return False  # user-anchored but no relationship role → not our concern
    src = (source_text or "").lower()
    # EVERY detected role must be supported — a compound fact ("user's wife and
    # daughter") must not ride one supported role past an unsupported one.
    for role in roles:
        supported = False
        # "my <role>" with up to two adjectives between ("my male friend",
        # "my best mate"); plural tolerated ("my girls"). Synonyms count: the
        # source saying "my mum" supports a fact phrased "user's mother", and
        # "a friend of mine" phrasing supports a user's-friend fact.
        for variant in _role_variants(role):
            if re.search(rf"\bmy\s+(?:\w+\s+){{0,2}}{re.escape(variant)}s?\b", src) or re.search(
                rf"\b{re.escape(variant)}s?\s+of\s+mine\b", src
            ):
                supported = True
                break
        if not supported:
            return True  # at least one user-anchored role the source never stated
    return False


# ---------------------------------------------------------------------------
# Shared cross-writer reconciliation (QA review F9)
# ---------------------------------------------------------------------------

# Tokens that are NOT person-name signals: calendar words, generic anchors, and
# user-anchored kinship/role words. Kinship possessives ("my dad's…" /
# "User's father's…") anchor to the USER, not a named third person — treating
# them as names blocked legitimate same-person reconciliation ("User's father's
# name is Neil" vs "My dad's name is Neil"), which expert_dispatch relied on.
_GUARD_CAL_WORDS = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday", "my", "the", "user",
    "dad", "father", "mum", "mom", "mother", "brother", "sister", "wife",
    "husband", "partner", "son", "daughter", "grandma", "grandmother",
    "grandpa", "grandfather", "aunt", "uncle", "cousin", "friend",
}


# Object of a name-attribute phrase ("…name is Neil", "named Neil", "is called
# Neil"): that token is the attribute VALUE, not a person the row is ABOUT.
# Without this exclusion, a user-anchored name fact ("my dad's name is Neil")
# could never be superseded by a titleless correction ("my dad's name is
# Kevin") — the value token blocked the guard (residual QA F2 for kinship
# name facts). Subjects stay protected: in "Jessica's name is …" the
# possessive "Jessica" is still a guard token.
# The relation words come from _GUARD_CAL_WORDS' kinship tail (defined above);
# "my wife is Sarah" / "User's dad is Neil" are user-anchored relation facts,
# so the trailing name is the VALUE of that relation, not a person the guard
# should protect from same-attribute supersedes.
_KINSHIP_RELATION_WORDS = (
    "dad|father|mum|mom|mother|brother|sister|wife|husband|partner|son|"
    "daughter|grandma|grandmother|grandpa|grandfather|aunt|uncle|cousin"
)
_NAME_VALUE_RE = re.compile(
    r"(?:\bname\s+is|\bnamed|\bis\s+called|\bname['’]s"
    rf"|\b(?:{_KINSHIP_RELATION_WORDS})\s+is)\s+(?:spelt\s+|spelled\s+)?"
    r"((?:[A-Z][A-Za-z'’-]*)(?:\s+[A-Z][A-Za-z'’-]*)*)"
)


def _guard_name_tokens(t: str) -> set[str]:
    """Name signals in ``t``: capitalized tokens AND lowercase possessives
    ("karen's birthday…" — users type lowercase), minus calendar/stop words
    and minus tokens that appear ONLY as a name-attribute value (see
    ``_NAME_VALUE_RE``)."""
    caps = {w.lower() for w in re.findall(r"\b[A-Z][a-z]+\b", t)}
    poss = {w.lower() for w in re.findall(r"\b([a-z]+)['’]s\b", t)}
    # A value may be multi-word ("Van Morrison") — every word of it is a
    # value token, or the trailing words would keep the row guarded.
    # Values may be multi-word or punctuated ("Van Morrison", "Mary-Jane",
    # "D'Arcy Smith") — every letter-run of the value is a value token, or a
    # leftover token would keep the row guarded.
    values = {
        w.lower()
        for m in _NAME_VALUE_RE.findall(t)
        for w in re.findall(r"[A-Za-z]+", m)
    }
    # A value token is excluded only when it has no OTHER appearance in the
    # text (a possessive or second mention still marks the row as about them).
    only_values = {
        v for v in values
        if len(re.findall(rf"\b{re.escape(v)}\b", t, flags=re.IGNORECASE)) == 1
        and v not in poss
    }
    return {w for w in (caps | poss) if w not in _GUARD_CAL_WORDS} - only_values


def guard_existing_by_entity(
    text: str,
    existing: list[tuple[str, str]],
    title: Optional[str] = None,
) -> list[tuple[str, str]]:
    """Entity guard (Greptile P1/security): filter ``existing`` rows that could
    belong to a DIFFERENT person than the candidate is about.

    Semantic search can return another person's same-attribute fact ("Karen's
    birthday is…" for a Jessica correction) — superseding it would overwrite the
    wrong person's memory. ``title`` is the candidate's person anchor when the
    writer knows one (e.g. person_extractor's person name); when the candidate
    has no anchor, any name token in an existing row must also appear in the
    candidate (namesake / third-person-name protection)."""
    title = (title or "").strip()
    if title:
        toks = title.split()
        first = toks[0].lower()
        if len(toks) > 1:
            # Full-name candidate → the row must mention the FULL name
            # (a bare "Jessica" row also passes; same person by intro).
            needle = title.lower()
            return [
                (i, t) for i, t in existing
                if needle in t.lower()
                or (first in t.lower()
                    and not re.search(rf"\b(?i:{re.escape(first)})\s+(?:[A-Z][a-z]|[a-z]+['’]s\b)", t))
            ]
        # Bare-name candidate ("Jessica") → refuse rows where that name is part
        # of a LONGER full name ("Jessica Smith") — two people can share a
        # first name (Greptile P1); ambiguity → ADD.
        return [
            (i, t) for i, t in existing
            if first in t.lower()
            and not re.search(rf"\b(?i:{re.escape(first)})\s+(?:[A-Z][a-z]|[a-z]+['’]s\b)", t)
        ]
    # Titleless candidates (templates/corrections/digest facts without a person
    # anchor) must not supersede a row about a named third person the candidate
    # never mentions. Conservative: any name token in the existing row must
    # appear in the candidate.
    cand_names = _guard_name_tokens(text)
    return [
        (i, t) for i, t in existing
        if not (_guard_name_tokens(t) - cand_names)
    ]


async def reconcile_for_ingest(
    svc,
    text: str,
    user_id: str,
    *,
    title: Optional[str] = None,
    limit: int = 3,
) -> tuple[str, Optional[str]]:
    """Shared ADD/UPDATE/SKIP decision for ALL conversational memory writers.

    QA review F9: each writer (memory_extractor, person_extractor,
    memory_digest, expert_dispatch) used to blind-ADD near-duplicate or
    contradicting rows. This helper is the single reconciliation seam: it
    searches ``svc`` for near-duplicate / same-attribute rows, applies the
    entity guards (namesake protection, third-person-name guards), then asks
    :func:`classify_against_existing` what to do.

    Returns ``("add"|"update"|"skip", mem_id_or_None)``. NEVER raises — any
    failure degrades to ``("add", None)`` so a real fact is never lost because
    reconciliation errored. Every such fail-open ADD is COUNTED (by cause) via
    :func:`memory_metrics.record_reconcile_failopen` so the duplicate-factory
    risk is watchable; the decision itself is unchanged."""
    t0 = time.monotonic()
    try:
        # Patient search: this is the background WRITE path, not the
        # latency-gated turn path (see _RECONCILE_SEARCH_TIMEOUT_S).
        try:
            hits = await svc.search(
                text, user_id=user_id, limit=limit,
                timeout_s=_RECONCILE_SEARCH_TIMEOUT_S,
            )
        except TypeError:
            # Fakes/older services without the timeout_s kwarg.
            hits = await svc.search(text, user_id=user_id, limit=limit)
        if not hits:
            # Empty here means the fact is stored as ADD without a supersession
            # check (fail-open — duplicates over lost facts). Distinguish the
            # cause: a search that burned ~the whole budget TIMED OUT (embedder
            # busy under load); a fast empty is a cold / genuinely-empty store.
            # Count it so a sustained burst is alertable, and escalate the log
            # from WARNING → ERROR once the rate trips. No raw memory text in
            # logs (personal data) — length only.
            elapsed = time.monotonic() - t0
            cause = (
                "search_timeout"
                if elapsed >= _RECONCILE_SEARCH_TIMEOUT_S * 0.9
                else "empty_results"
            )
            sustained = _record_failopen(cause)
            (logger.error if sustained else logger.warning)(
                "reconcile_for_ingest: no search hits (user=%s, text_len=%d, "
                "cause=%s, elapsed=%.1fs) — storing as ADD without supersession "
                "check%s",
                user_id, len(text), cause, elapsed,
                " [SUSTAINED fail-open rate — reconciliation is duplicating "
                "writes; check search/embedder health]" if sustained else "",
            )
        existing = [
            (getattr(h, "id", ""), getattr(h, "text", "") or "")
            for h in (hits or [])
            if getattr(h, "text", None)
        ]
        existing = guard_existing_by_entity(text, existing, title)
        return classify_against_existing(text, existing)
    except Exception as exc:
        # Exception type only — backend errors can embed the query (which is
        # the raw candidate memory text) in the exception message.
        sustained = _record_failopen("search_error")
        (logger.error if sustained else logger.warning)(
            "reconcile_for_ingest unavailable (%s) — plain add%s",
            type(exc).__name__,
            " [SUSTAINED fail-open rate]" if sustained else "",
        )
        return "add", None


__all__ = [
    "is_storable_fact",
    "looks_like_correction",
    "ambiguous_negation_subject",
    "classify_against_existing",
    "guard_existing_by_entity",
    "reconcile_for_ingest",
    "user_relationship_claim_unsupported",
]
