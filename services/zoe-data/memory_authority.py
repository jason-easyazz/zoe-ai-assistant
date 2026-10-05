"""memory_authority — who SAID a fact decides who may change it.

Why this exists (live 2026-10-05, generalised from the identity incident): the nightly
digest read a day transcript holding a speech-to-text fragment that named a third person,
asserted a fact about the owner from it, and its contradiction pass called
``MemoryService.review(edit)`` — which SUPERSEDED a row the owner had stated himself and
carried that row's ``source``/``session_id`` forward, so the new row looked like a regex
write. Model inference outranked the user's own words, and the provenance lied about it.
PR #1866 walled off ONE attribute (the user's name); this module is the class
(docs/research/memory-fidelity-audit-2026-10-05.md §2, §6 P1.1; docs/knowledge/memory-authority.md).

The rule (enforced at ONE choke point, ``MemoryService``: ``ingest`` / ``review`` /
``supersede_by`` / ``archive_duplicate``; this module is the pure half — no I/O, no store):

* Every row carries a PROVENANCE CLASS (``authority_class``) from an ALLOW-LIST, ranked:

      operator 6 > user_confirmed 5 > user_stated 4 > user_stated_derived 3
              > user_unverified 2 > model_from_turn 1 > model_from_transcript 0

  plus ``authority`` (the owner's three-way view: user_stated | user_confirmed | inferred),
  ``authority_basis``, ``origin`` (the writer), ``turn_ref`` and ``model`` (LLM writers).
  An UNKNOWN writer is rank 0 (fail-closed). Rows written before this existed derive a class
  on read (``legacy_class``); ``scripts/maintenance/memory_authority_backfill.py`` reports
  and, on request, stamps the same answer.
* A write may supersede / archive / contradict an existing row only if its class is a DIRECT
  user class (rank >= 4: typed or spoken by the person, a later statement of theirs always wins;
  a model's paraphrase of them, ``user_stated_derived``, never overrides a direct statement) or
  its power is >= the row's rank. Anything else is DEMOTED to a ``disputed`` CANDIDATE row linked by
  ``contradicts_id`` (never recalled, askable later) and logs
  ``AUTHORITY_BLOCKED writer=<name> kind=<attr>`` (labels only — never text).
* A supersede records the NEW writer's provenance. It never inherits the old row's
  ``source`` / ``session_id`` / ``user_turn_id`` / (for a model writer) excerpt.
* The user approving a candidate IS the user stating it (``user_confirmed``), and retires the
  row it disputed. #1866's identity wall is the special case "the user's own name is never
  writable by an automatic writer at all".

How a model-assisted writer EARNS ``user_stated`` (``resolve_write``): it read the user's turn
and the user's OWN text supports the fact (``supports``) — subject, value and attribute in one
user sentence, and the user speaking in the first person for a fact about themself. Assistant
turns never count; neither does a third person's name that merely appears in a transcript.

``ZOE_MEMORY_AUTHORITY`` = ``enforce`` (default) | ``shadow`` (log
``AUTHORITY_WOULD_BLOCK``, change nothing) | ``off``. Provenance is stamped in every mode.
Default argued in docs/knowledge/memory-authority.md: the wall only ever parks a MODEL's
overwrite of something the user said as a candidate (lossless, reversible); a writer that has
the user's turn earns ``user_stated`` through ``supports``, so what the user said is never
refused.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_MEMORY_AUTHORITY"

# ── provenance classes (rank = power over existing rows) ─────────────────────
OPERATOR = "operator"
USER_CONFIRMED = "user_confirmed"
USER_STATED = "user_stated"            # the person's own words, or a deterministic extractor over them
USER_STATED_DERIVED = "user_stated_derived"  # a MODEL's paraphrase that the user's own turn supports
USER_UNVERIFIED = "user_unverified"   # voice turn attributed only by panel binding (P1.3, follow-up)
MODEL_FROM_TURN = "model_from_turn"
MODEL_FROM_TRANSCRIPT = "model_from_transcript"
RANK = {
    OPERATOR: 6, USER_CONFIRMED: 5, USER_STATED: 4, USER_STATED_DERIVED: 3, USER_UNVERIFIED: 2,
    MODEL_FROM_TURN: 1, MODEL_FROM_TRANSCRIPT: 0,
}
CLASSES = tuple(RANK)
#: a class at or above this is the PERSON speaking DIRECTLY: only these always override. A
#: model's paraphrase of the user (rank 3) beats model classes but never a direct statement -
#: both would otherwise be "user_stated" and the NEWER one would win, so a mis-paraphrase of an
#: older sentence in a day transcript could overwrite what the person said later.
USER_RANK = RANK[USER_STATED]
DERIVED_RANK = RANK[USER_STATED_DERIVED]

# The owner's three-way view, derived from the class.
INFERRED = "inferred"
AUTHORITIES = (USER_STATED, USER_CONFIRMED, INFERRED)
PROTECTED = frozenset({USER_STATED, USER_CONFIRMED})


def authority_of(cls: str) -> str:
    if cls in (OPERATOR, USER_CONFIRMED):
        return USER_CONFIRMED
    if cls in (USER_STATED, USER_STATED_DERIVED):
        return USER_STATED
    return INFERRED


# Provenance keys the edit path must NEVER carry forward from the row it supersedes.
PROVENANCE_KEYS = frozenset({
    "authority", "authority_class", "authority_basis", "origin", "turn_ref", "model",
    "contradicts_id", "authority_blocked", "duplicate_of",
})


def mode() -> str:
    """``enforce`` (default) | ``shadow`` | ``off``. Per-call env read."""
    raw = os.environ.get("ZOE_MEMORY_AUTHORITY")
    if raw is None:
        return "enforce"
    v = raw.strip().lower()
    if v in ("0", "false", "no", "off", ""):
        return "off"
    if v == "shadow":
        return "shadow"
    return "enforce"


def enabled() -> bool:
    """Is the wall ACTING (``enforce``)?"""
    return mode() == "enforce"


AFFECT_ENV = "ZOE_AFFECT_CONSENT_GATE"
AFFECT_KEYS = ("affect", "valence", "intensity")


def affect_gate_mode() -> str:
    """``members`` (default: guests and known minors never get an affective record) | ``optin``
    (also requires the member to have opted in to the persona layer - a stored mode row) |
    ``off``. Per-call env read. docs/governance/emotional-safety-note.md section 6."""
    raw = os.environ.get("ZOE_AFFECT_CONSENT_GATE")
    if raw is None:
        return "members"
    v = raw.strip().lower()
    if v in ("0", "false", "no", "off", ""):
        return "off"
    return "optin" if v == "optin" else "members"


def is_affective(memory_type: Optional[str], metadata: Optional[Mapping[str, Any]] = None) -> bool:
    """An ``emotional_moment`` row: a RECORD of how someone seems (governance note section 6)."""
    return (memory_type or "") == "emotional_moment"


def carries_affect(metadata: Optional[Mapping[str, Any]]) -> bool:
    """An ordinary fact carrying a feeling in its metadata (``affect`` / ``valence`` /
    ``intensity``, stored ``candidate_``-prefixed)."""
    md = metadata or {}
    return any(md.get(k) or md.get(f"candidate_{k}") for k in AFFECT_KEYS)


def active() -> bool:
    """Is the wall at least watching (``shadow`` or ``enforce``)?"""
    return mode() != "off"


# ── writer allow-list ─────────────────────────────────────────────────────────

def _automatic_sources() -> frozenset[str]:
    try:
        from identity_facts import AUTOMATIC_SOURCES

        return frozenset(AUTOMATIC_SOURCES)
    except Exception:  # noqa: BLE001 — the class must degrade, never break a write
        return frozenset()


OPERATOR_WRITERS = frozenset({
    "operator", "operator-cleanup", "identity_audit", "admin", "system",
    "samantha_live_cleanup",
})
#: The review UI IS the person: approving / editing a row there is "yes, that's right".
USER_CONFIRMED_WRITERS = frozenset({"review_ui", "user_confirmed"})
#: The person's own words, typed or dictated, or a deterministic extractor over their turn
#: (assistant text is never mined — tests/test_memory_extractor_purity.py).
USER_STATED_WRITERS = frozenset({
    "voice_fact", "proposal", "manual", "explicit_teach", "conversation_correction", "chat", "chat_regex",
    "chat_regex_fallback", "voice_regex", "conversation", "voice", "skybridge_action",
})
DETERMINISTIC_USER_WRITERS = frozenset({"chat_regex", "chat_regex_fallback", "voice_regex", "conversation", "voice"})
USER_STATED_PREFIXES = ("note_", "journal_")   # note_<action>, journal_<action> (the UI)
#: the people-UI mirror rows ("person_created" / "person_updated"): exact names, NOT a prefix -
#: "person_extractor_llm" is a model.
USER_STATED_WRITERS_EXTRA = frozenset({"person_created", "person_updated", "person_deleted"})
#: Writers fed by the panel / voice lane: their turns can be mis-attributed to the bound member.
VOICE_LANE_WRITERS = frozenset({"voice_fact", "voice_regex", "voice", "voice_turn_digest"})
#: Model writers that READ one user turn: user_stated only when that turn supports the fact.
MODEL_FROM_TURN_WRITERS = frozenset({
    "turn_digest", "voice_turn_digest", "person_extractor_llm", "brain_tool", "mcp",
    "zoe_agent", "decay_sweep",
})
#: Model writers that read a whole day's transcript: same, against the user turns of it.
TRANSCRIPT_WRITERS = frozenset({"digest", "idle_consolidation"})
#: Automatic writers that never read a user turn (beyond the two sets above).
INFERRED_ONLY_AUTOMATIC = frozenset({
    "consolidation", "synthesis", "music_digest", "ambient", "implicit_supersede",
    "profile-analysis", "hindsight_retain_candidate", "decay_sweep", "mcp",
})
#: Writers that never have user text behind them (and every UNKNOWN writer): rank 0.
LLM_WRITERS = frozenset({
    "turn_digest", "voice_turn_digest", "digest", "idle_consolidation", "consolidation",
    "synthesis", "music_digest", "profile-analysis", "person_extractor_llm",
})


def writer_class(writer: str, *, user_id: str = "") -> str:
    """The class a writer has BEFORE any anchor is considered (unanchored model writers sit
    at their floor; unknown writers at rank 0)."""
    w = (writer or "").strip()
    if w in OPERATOR_WRITERS:
        return OPERATOR
    if w in USER_CONFIRMED_WRITERS or (user_id and w == user_id):
        return USER_CONFIRMED
    if w in USER_STATED_WRITERS or w in USER_STATED_WRITERS_EXTRA or w.startswith(USER_STATED_PREFIXES):
        return USER_STATED
    if w in MODEL_FROM_TURN_WRITERS:
        return MODEL_FROM_TURN
    return MODEL_FROM_TRANSCRIPT


def writer_is_inferred(writer: str, *, user_id: str = "") -> bool:
    """True when this writer, unanchored, is below the user classes (the actors the wall can
    refuse). ``writer == user_id`` is the account acting on its own rows."""
    return RANK[writer_class(writer, user_id=user_id)] < USER_RANK


def is_known_writer(writer: str) -> bool:
    """On the allow-list (a lane / writer name, as opposed to an account id or a stray
    string): only these become a row's ``source``."""
    w = (writer or "").strip()
    return (w in OPERATOR_WRITERS or w in USER_CONFIRMED_WRITERS or w in USER_STATED_WRITERS
            or w in USER_STATED_WRITERS_EXTRA or w.startswith(USER_STATED_PREFIXES) or w in MODEL_FROM_TURN_WRITERS
            or w in TRANSCRIPT_WRITERS or w in LLM_WRITERS or w in _automatic_sources())


def model_for(writer: str) -> str:
    if (writer or "").strip() not in LLM_WRITERS:
        return ""
    return os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")


@dataclass(frozen=True)
class Resolved:
    cls: str
    basis: str

    @property
    def authority(self) -> str:
        return authority_of(self.cls)

    @property
    def rank(self) -> int:
        """STANDING: how well the row it becomes is protected from later writes."""
        return RANK[self.cls]

    @property
    def power(self) -> int:
        """POWER over existing rows (= ``rank``: a model's paraphrase of the user never
        overrides a direct statement, however fresh the turn it paraphrases)."""
        return RANK[self.cls]


def resolve_write(writer: str, text: str, *, anchor_text: Optional[str] = None,
                  claimed: Optional[str] = None, user_id: str = "",
                  prompt_text: Optional[str] = None,
                  speaker_verified: Optional[bool] = None) -> Resolved:
    """The class a NEW row gets, from who is writing it and what the user's own turn says.
    ``claimed`` can only DOWNGRADE (``inferred``), or confirm (``user_confirmed``) for a
    user-class writer — a model writer cannot claim authority; only the anchor gives it."""
    w = (writer or "").strip()
    base = writer_class(w, user_id=user_id)
    if claimed == INFERRED:
        return Resolved(MODEL_FROM_TRANSCRIPT, "claimed_inferred")
    if RANK[base] >= USER_RANK:
        # P1.3 hook: a VOICE-lane self-fact whose speaker the speaker-id did not confirm is
        # `user_unverified` (a guest, or another member, may be talking to a panel bound to
        # the owner). ``None`` = the lane does not report a verdict yet (today: the voice
        # daemon does not) -> unchanged. Only a self-fact; only the voice-lane writers.
        if (speaker_verified is False and w in VOICE_LANE_WRITERS
                and _USER_SUBJECT_RE.match(text or "")):
            return Resolved(USER_UNVERIFIED, "speaker_not_verified")
        if claimed == USER_CONFIRMED and base == USER_STATED:
            return Resolved(USER_CONFIRMED, "user_confirmed")
        return Resolved(base, "deterministic_user_turn" if w in DETERMINISTIC_USER_WRITERS
                        else "explicit_source")
    if w in MODEL_FROM_TURN_WRITERS or w in TRANSCRIPT_WRITERS:
        if (anchor_text or prompt_text) and supports(text, anchor_text or "", prompt_text):
            if speaker_verified is False and w in VOICE_LANE_WRITERS:
                return Resolved(USER_UNVERIFIED, "speaker_not_verified")
            return Resolved(USER_STATED_DERIVED, "anchored_user_turn")
        return Resolved(base, "unanchored" if anchor_text else "no_user_evidence")
    return Resolved(MODEL_FROM_TRANSCRIPT, "automatic_writer")


def row_power(meta: Mapping[str, Any], text: str = "") -> int:
    """Power over existing rows of the write that produced this row (= its rank)."""
    return RANK[row_class(meta, text)]


def may_override(power: int, target_cls: str) -> bool:
    """May a write of this POWER supersede / archive / contradict a row of ``target_cls``?
    A direct user class always may (a later statement of the person's wins); below that,
    power >= the row's rank."""
    if target_cls == OPERATOR:
        # an operator's deliberate cleanup is not undone by a spoken sentence a panel may have
        # mis-attributed (P1.3): it takes a confirming action (the review UI) or an operator
        return power >= RANK[USER_CONFIRMED]
    return power >= USER_RANK or power >= RANK.get(target_cls, 0)


def provenance(writer: str, res: Resolved, *, turn_ref: Optional[str] = None,
               model: Optional[str] = None) -> dict[str, Any]:
    """The flat metadata a row carries about WHO wrote it (chroma-safe scalars)."""
    out: dict[str, Any] = {
        "authority": res.authority,
        "authority_class": res.cls,
        "authority_basis": res.basis,
        "origin": (writer or "")[:64],
    }
    if turn_ref:
        out["turn_ref"] = str(turn_ref)[:128]
    m = model or model_for(writer)
    if m:
        out["model"] = str(m)[:96]
    return out


# ── reading a row's class (stored, else derived) ──────────────────────────────

def row_class(meta: Mapping[str, Any], text: str = "") -> str:
    """The class a stored row has: the stamped ``authority_class``, else a stamped
    ``authority`` mapped to its floor, else ``legacy_class``."""
    meta = meta or {}
    c = str(meta.get("authority_class") or "")
    if c in RANK:
        return c
    a = str(meta.get("authority") or "")
    if a in AUTHORITIES:
        return {USER_STATED: USER_STATED, USER_CONFIRMED: USER_CONFIRMED,
                INFERRED: MODEL_FROM_TRANSCRIPT}[a]
    return legacy_class_basis(meta, text)[0]


def row_authority(meta: Mapping[str, Any], text: str = "") -> str:
    return authority_of(row_class(meta, text))


def row_rank(meta: Mapping[str, Any], text: str = "") -> int:
    return RANK[row_class(meta, text)]


def legacy_class_basis(meta: Mapping[str, Any], text: str = "") -> tuple[str, str]:
    """``(class, basis)`` for a row written before provenance existed (the backfill rule).

    * reviewed by a MODEL (digest / consolidation / turn_digest / ...): inferred — the text
      is that writer's whatever ``source`` was carried forward (the 2026-10-05 incident row);
    * reviewed by a person: ``user_confirmed``;
    * source on the user allow-list (voice_fact, chat_regex, review_ui, notes ...): the
      class of that source;
    * source a model writer that READ the user's turn: ``user_stated`` only if the stored
      ``source_excerpt`` (the user's utterance) supports the text, else its floor;
    * any other source (unknown = rank 0): ``model_from_transcript``.
    """
    reviewed_by = str(meta.get("reviewed_by") or "").strip()
    source = str(meta.get("source") or meta.get("added_by") or "").strip()
    uid = str(meta.get("user_id") or meta.get("wing") or "")
    src_cls = writer_class(source, user_id=uid)
    if reviewed_by and _is_model_actor(reviewed_by) and reviewed_by != uid:
        return (MODEL_FROM_TURN if reviewed_by in MODEL_FROM_TURN_WRITERS
                else MODEL_FROM_TRANSCRIPT), "legacy_reviewed_by_model"
    if RANK[src_cls] >= USER_RANK:
        return src_cls, "legacy_user_path"
    if source in MODEL_FROM_TURN_WRITERS or source in TRANSCRIPT_WRITERS:
        excerpt = str(meta.get("source_excerpt") or meta.get("candidate_source_excerpt") or "")
        if excerpt and text and supports(text, excerpt):
            return USER_STATED_DERIVED, "legacy_anchored_excerpt"
        if reviewed_by:
            return USER_CONFIRMED, "legacy_reviewed_by_person"
        return src_cls, "legacy_unanchored"
    if reviewed_by:
        return USER_CONFIRMED, "legacy_reviewed_by_person"
    return MODEL_FROM_TRANSCRIPT, "legacy_automatic_source"


def _is_model_actor(name: str) -> bool:
    """A reviewer that is a MODEL / batch pass, as opposed to a person. Only for reading
    LEGACY ``reviewed_by`` values: an account id or any other label that names no known
    automatic writer is a person (a live WRITE from an unknown label is still rank 0)."""
    n = (name or "").strip()
    return (n in MODEL_FROM_TURN_WRITERS or n in TRANSCRIPT_WRITERS or n in LLM_WRITERS
            or n in INFERRED_ONLY_AUTOMATIC or n in _automatic_sources())


def legacy_class(meta: Mapping[str, Any], text: str = "") -> str:
    return legacy_class_basis(meta, text)[0]


def legacy_authority_basis(meta: Mapping[str, Any], text: str = "") -> tuple[str, str]:
    cls, basis = legacy_class_basis(meta, text)
    return authority_of(cls), basis


def is_protected(meta: Mapping[str, Any], text: str = "") -> bool:
    return row_authority(meta, text) in PROTECTED


# ── anchoring: does the USER'S OWN turn support this fact? ────────────────────

_FIRST_PERSON = frozenset({"i", "im", "i'm", "ive", "i've", "id", "i'd", "ill", "i'll", "my",
                           "me", "mine", "myself", "we", "were", "we're", "our", "ours", "us",
                           "weve", "we've"})
_USER_SUBJECT_RE = re.compile(r"^\s*(?:the\s+)?(?:user|speaker|i|my)\b", re.IGNORECASE)

# Words that carry no claim (articles, copulas, the frame of a negation / change cue).
_STOP = frozenset("""
the and but for with from into onto this that these those there here their them they his her its
user users speaker have has had was were been being are not never longer anymore any more
dropped stopped quit gave given used really very just also still currently now then than year years
who whom what when where while which about some one ones got get gets going doing does did
""".split())
_DIGIT_ORD = re.compile(r"^(\d+)(?:st|nd|rd|th)$")

# Attribute CUE classes: a fact that names an attribute ("name", where they live, work, age,
# birthday, a liking, an allergy) is supported only by a user sentence that names it too —
# this is what separates "my name is X" from a transcript that merely MENTIONS X.
_CUE_WORDS = {
    "name": "name names named call called calling",
    "home": "live lives lived living move moved moving reside resides resided based settle "
            "settled home house from stay stays relocate relocated",
    "work": "work works worked working job jobs employ employed employer career occupation "
            "profession company business hired start started join joined",
    "age": "old age aged turn turned",
    "birth": "birthday born bday birth dob",
    "like": "like likes liked love loves loved enjoy enjoys prefer prefers preferred favourite "
            "favorite fan adore adores into",
    "allergy": "allergic allergy allergies intolerant intolerance",
}


def _stem(tok: str) -> str:
    t = tok.lower().strip("'’")
    if t.endswith(("'s", "’s")):
        t = t[:-2]
    m = _DIGIT_ORD.match(t)
    if m:
        return m.group(1)
    if len(t) > 4 and t.endswith("ies"):
        t = t[:-3] + "y"
    elif len(t) > 5 and t.endswith("ing"):
        t = t[:-3]
    elif len(t) > 4 and t.endswith("ed"):
        t = t[:-2]
    elif len(t) > 4 and t.endswith("es"):
        t = t[:-2]
    elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    if len(t) > 3 and t.endswith("e"):
        t = t[:-1]
    return t


_CUE_OF: dict[str, str] = {_stem(w): cls for cls, words in _CUE_WORDS.items() for w in words.split()}


# First-person present / change-of-state shapes that name the attribute without its noun:
# "I'm 41 now" (age), "I'm in Perth now" (home), "I'm at Acme now" (work), "I'm Alex" (name).
_SHAPE_CUES = (
    ("age", re.compile(r"\bI(?:['’]m| am)\s+\d{1,3}\b|\bturn(?:ed|ing|s)?\s+\d{1,3}\b", re.IGNORECASE)),
    ("home", re.compile(r"\bI(?:['’]m| am)\s+(?:now\s+)?in\s+\w+(?:\s+\w+){0,3}?\s+now\b"
                        r"|\bI(?:['’]m| am)\s+now\s+in\s+\w+", re.IGNORECASE)),
    ("work", re.compile(r"\bI(?:['’]m| am)\s+(?:now\s+)?at\s+\w+(?:\s+\w+){0,3}?\s+now\b"
                        r"|\bI(?:['’]m| am)\s+now\s+at\s+\w+", re.IGNORECASE)),
    ("name", re.compile(r"\b(?:I(?:['’]m| am)|this is|it(?:['’]s| is))\s+[A-Z][a-z]+")),
)


def _window_cues(win: str, stems: set[str]) -> set[str]:
    out = {_CUE_OF[s] for s in stems if s in _CUE_OF}
    out |= {cls for cls, rx in _SHAPE_CUES if rx.search(win)}
    return out


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", _fold(text))


def _sentences(text: str) -> list[str]:
    parts = [p for p in re.split(r"(?<=[.!?])\s+|\n+", text or "") if p and p.strip()]
    return parts or ([text] if (text or "").strip() else [])


def _normalise_dates(text: str) -> str:
    try:
        from date_locale import normalize_numeric_dates

        return normalize_numeric_dates(text)
    except Exception:  # noqa: BLE001
        return text


def _fact_parts(fact: str) -> tuple[bool, set[str], set[str], set[str]]:
    """``(about_user, value_tokens, cue_classes, other_tokens)`` of a stored fact."""
    about_user = bool(_USER_SUBJECT_RE.match(fact or ""))
    value: set[str] = set()
    cues: set[str] = set()
    other: set[str] = set()
    for i, w in enumerate(_words(fact)):
        low = w.lower()
        base = re.sub(r"['’]s$", "", low)
        if low in _STOP or base in _STOP or low in _FIRST_PERSON:
            continue
        s = _stem(low)
        if not s:
            continue
        if s in _CUE_OF:
            cues.add(_CUE_OF[s])
            continue
        if any(ch.isdigit() for ch in w) or (w[:1].isupper() and i > 0 and low not in {"user", "users"}):
            value.add(s)
        elif len(s) > 2:
            other.add(s)
    if other:
        # "User's dog is named Teddy": the dog + the name are the claim, and "my dog Teddy"
        # states both. Only a fact whose attribute IS the name ("User's name is X") needs
        # the user to say "name" / "called".
        cues.discard("name")
    return about_user, value, cues, other


_RELATION = (r"(?:wife|husband|partner|girlfriend|boyfriend|fianc\w*|spouse|son|daughter|kids?|child|"
             r"children|brother|sister|mum|mom|mother|dad|father|grandma|grandmother|grandpa|"
             r"grandfather|aunt|uncle|cousin|niece|nephew|friend|mate|boss|colleague|coworker|"
             r"neighbou?r|parents?|sibling|in-laws?|family|baby|girls|boys|ex)")
#: "my sister", "my wife's", "our best friend": a possessive of ANOTHER PERSON
_MY_RELATION_RE = re.compile(rf"\b(?:my|our)\s+(?:[a-z]+\s+){{0,2}}?({_RELATION})s?(?:['’]s)?\b", re.IGNORECASE)
_NEG_RE = re.compile(r"\b(?:not|never|no|none|nobody|nothing|neither|nor)\b|n['’]t\b|\bany ?more\b",
                     re.IGNORECASE)
_USED_TO_RE = re.compile(r"\bused to\b|\bformerly\b|\bpreviously\b|\bwas living\b", re.IGNORECASE)
#: a stated END of a state ("no longer" / "any more" are also negations above)
_ENDED_RE = re.compile(r"\b(?:stopped|dropped|quit|gave up|given up|ended|left|no longer)\b|\bany ?more\b", re.IGNORECASE)
_HYPOTHETICAL_RE = re.compile(
    r"\b(?:wish|if|maybe|perhaps|might|hope|hoping|someday|supposedly|apparently|imagine|pretend|"
    r"would|could)\b", re.IGNORECASE)
_QUESTION_START_RE = re.compile(r"^\s*(?:do|does|did|am|are|is|can|could|will|would|should)\s+"
                                r"(?:i|we|you|my)\b", re.IGNORECASE)


_LEAD_INTERJECTION_RE = re.compile(r"^\s*(?:(?:no|nope|nah|yes|yeah|yep|actually|well|oh|sorry|um|uh|hi|hey)[,.!\s]+)+",
                                   re.IGNORECASE)


def _window_is_a_statement_about_the_user(win: str, fact: str) -> bool:
    """The user's OWN, affirmative, first-person statement - not a question, a wish, a
    negation the fact does not share, or a sentence about someone else's relative.
    (Review of #1868: "my sister lives in Perth" must not support "User lives in Perth".)"""
    win = _LEAD_INTERJECTION_RE.sub("", win)
    if "?" in win or _QUESTION_START_RE.match(win):
        return False
    if bool(_NEG_RE.search(win)) != bool(_NEG_RE.search(fact or "")):
        return False
    if _HYPOTHETICAL_RE.search(win):
        return False
    # POLARITY / TENSE / CHANGE-OF-STATE must AGREE between the user's words and the fact (no
    # entailment across them): "I live in X" does not support "User no longer lives in X", and
    # "I used to live in X" does not support "User lives in X" (nor the reverse). The cue words
    # stay in _STOP for token coverage (paraphrase tolerance); this is where they are enforced.
    for cue in (_USED_TO_RE, _ENDED_RE):
        if bool(cue.search(win)) != bool(cue.search(fact or "")):
            return False
    fact_l = (fact or "").lower()
    for m in _MY_RELATION_RE.finditer(win):
        rel = m.group(1).lower()
        try:
            from memory_quality import _role_variants

            names = {v.lower() for v in _role_variants(rel)} | {rel}
        except Exception:  # noqa: BLE001
            names = {rel}
        if not any(re.search(rf"\b{re.escape(n)}s?\b", fact_l) for n in names):
            return False  # the sentence is about the user's relative; the fact does not say so
    return True


def supports(fact: str, user_text: str, prompt_text: Optional[str] = None) -> bool:
    """Does the user's OWN turn text support ``fact``?

    ``user_text`` is user turns only (the caller must never pass assistant text). True when
    one sentence (or two adjacent ones of the same turn) holds ALL of the fact's value
    tokens (names, numbers, places), the attribute CUE the fact names (name / where they
    live / work / age / birthday / a liking / an allergy), most of its other words, and —
    for a fact about the user — the user speaking in the first person. A third person's
    name merely appearing in a transcript supports nothing: "uh Casey is coming over, I
    think" does not support "User's name is Casey" (no name cue).

    ``prompt_text`` is the ASSISTANT question the user is answering ("Where do you live
    now?"). It is never evidence by itself - it only lets a SHORT elliptical answer ("no,
    Perth now", "it's Alex") be read in context: the answer must carry every value token of
    the fact, and the question must name the same attribute the fact does.
    """
    if not fact or not user_text:
        return False
    about_user, value, cues, other = _fact_parts(fact)
    if not (value or cues or other):
        return False
    # One TURN per line (the digests join user turns with newlines). A window is a sentence,
    # or two adjacent sentences of the SAME turn - never across turns: "my dog is Teddy"
    # on one turn plus "Rex is coming over" on the next is not "my dog is Rex".
    windows: list[str] = []
    for line in _normalise_dates(user_text).split("\n"):
        sents = _sentences(line)
        windows += sents + [f"{sents[i]} {sents[i + 1]}" for i in range(len(sents) - 1)]
    for win in windows:
        ws = _words(win)
        stems = {_stem(w) for w in ws}
        if value and not value <= stems:
            continue
        if cues and not cues <= _window_cues(win, stems):
            continue
        if other and len(other & stems) / len(other) < 0.6:
            continue
        if about_user and not any(w.lower() in _FIRST_PERSON for w in ws):
            continue
        if not _window_is_a_statement_about_the_user(win, fact):
            continue
        return True
    return _answers_a_question(fact, value, cues, user_text, prompt_text)


def _answers_a_question(fact: str, value: set[str], cues: set[str], user_text: str,
                        prompt_text: Optional[str]) -> bool:
    """A short elliptical user answer to an assistant question about the fact's attribute."""
    if not prompt_text or "?" not in prompt_text or not value:
        return False
    p_stems = {_stem(w) for w in _words(prompt_text)}
    p_cues = {_CUE_OF[s] for s in p_stems if s in _CUE_OF}
    p_cues |= {"name"} if re.search(r"\bname\b", prompt_text, re.IGNORECASE) else set()
    if not cues or not cues <= p_cues:
        return False
    for line in _normalise_dates(user_text).split("\n"):
        ans = _LEAD_INTERJECTION_RE.sub("", line).strip()
        if not ans or "?" in ans or len(ans.split()) > 8 or _HYPOTHETICAL_RE.search(ans):
            continue
        if bool(_NEG_RE.search(ans)) != bool(_NEG_RE.search(fact or "")):
            continue
        if value <= {_stem(w) for w in _words(ans)}:
            return True
    return False


# ── same subject + same attribute, different value ───────────────────────────

_WORK_RE = re.compile(r"\bworks?\s+(?:at|for|as|in)\s+(?P<v>[\w'’\- ]{2,40}?)(?:[.,;!?]|$)", re.IGNORECASE)
_AGE_RE = re.compile(r"\b(?P<v>\d{1,3})\s+years?\s+old\b|\baged?\s+(?P<w>\d{1,3})\b", re.IGNORECASE)


def kind_of(text: str) -> str:
    """A CLOSED-vocabulary label for what a fact is about (safe to log: never text)."""
    t = (text or "").lower()
    try:
        from identity_facts import asserted_user_name

        if asserted_user_name(text):
            return "name"
    except Exception:  # noqa: BLE001
        pass
    for kind, rx in (
        ("pet", r"\b(?:dog|cat|puppy|kitten|pet|rabbit|bird)\b"),
        ("relationship", r"\b(?:wife|husband|partner|spouse|girlfriend|boyfriend|son|daughter|"
                         r"brother|sister|mum|mom|mother|dad|father|friend|boss)\b"),
        ("birthday", r"\bbirthday\b|\bborn\b|\bdob\b"),
        ("age", r"\byears? old\b|\baged?\s+\d"),
        ("home", r"\blives?\b|\bliving\b|\bmoved\b|\bbased\b|\bhome\b"),
        ("work", r"\bworks?\b|\bjob\b|\bemploy|\bcareer\b|\boccupation\b"),
        ("health", r"\ballerg|\bintoleran|\bdiagnos|\bmedicat"),
        ("preference", r"\blikes?\b|\bloves?\b|\bprefers?\b|\bfavou?rite\b|\benjoys?\b"),
        ("name", r"\bname\b|\bcalled\b|\bnamed\b"),
    ):
        if re.search(rx, t):
            return kind
    return "other"


# Distilled third-person shapes ("User prefers tea."): a verb that takes ONE object at a time.
# Likes / loves / enjoys / takes / owns are deliberately absent - a person has several.
_EXCL_VERB_RE = re.compile(
    r"^\s*(?:the\s+)?user\s+(?P<verb>prefers|drives|studies|attends|supports)\s+(?P<obj>.+?)\s*[.!?]*\s*$",
    re.IGNORECASE)
#: values that exclude each other ("vegetarian" / "vegan"; "single" / "married")
_EXCL_GROUPS = (
    frozenset({"vegetarian", "vegan", "pescatarian", "pescetarian", "omnivore", "carnivore"}),
    frozenset({"single", "married", "divorced", "widowed", "engaged", "separated"}),
)
_OBJ_STOP = frozenset({"a", "an", "the", "to", "of", "my", "their", "his", "her"})


def _obj_tokens(obj: str) -> frozenset[str]:
    return frozenset(t for t in re.findall(r"[a-z0-9]+", (obj or "").lower()) if t not in _OBJ_STOP)


def _verb_object(text: str) -> tuple[str, frozenset[str]]:
    m = _EXCL_VERB_RE.match(text or "")
    return (m.group("verb").lower(), _obj_tokens(m.group("obj"))) if m else ("", frozenset())


def _slot_value(rx: re.Pattern[str], text: str) -> str:
    m = rx.search(text or "")
    if not m:
        return ""
    v = next((g for g in m.groups() if g), "")
    return re.sub(r"\s+", " ", v).strip().lower()


def conflict_kind(new_text: str, old_text: str) -> Optional[str]:
    """If ``new_text`` contradicts ``old_text`` (same subject, same attribute, a DIFFERENT
    value — or a stated END of it), the closed-vocabulary kind of the attribute, else None.
    Built on the shared reconciler's attribute matcher (``memory_quality``) and the
    implicit-supersede subject / home / change-cue matchers (``memory_supersede``) so
    'same attribute' means one thing across the store."""
    try:
        from memory_quality import _attribute_key, _attrs_match, _same_value
        from memory_supersede import exclusive_conflict, is_tombstone, same_topic, subject_key
    except Exception:  # noqa: BLE001 — a matcher outage must not break a write
        return None
    if not new_text or not old_text or subject_key(new_text) != subject_key(old_text):
        return None
    if exclusive_conflict(new_text, old_text):
        return "home"
    ka, kb = _attribute_key(new_text), _attribute_key(old_text)
    if ka and kb and _attrs_match(ka, kb) and not _same_value(new_text, old_text, ka):
        return kind_of(old_text)
    for rx in (_WORK_RE, _AGE_RE):
        vn, vo = _slot_value(rx, new_text), _slot_value(rx, old_text)
        if vn and vo and vn != vo:
            return kind_of(old_text)
    if is_tombstone(new_text) and same_topic(new_text, old_text):
        return kind_of(old_text)
    # "User prefers tea." vs "User prefers coffee." - same verb, a different single object
    (vn, on), (vo, oo) = _verb_object(new_text), _verb_object(old_text)
    if vn and vn == vo and on and oo and not (on <= oo or oo <= on):
        return kind_of(old_text)
    # mutually exclusive states ("User is vegetarian." / "User is vegan.")
    wn, wo = set(re.findall(r"[a-z]+", new_text.lower())), set(re.findall(r"[a-z]+", old_text.lower()))
    for group in _EXCL_GROUPS:
        gn, go = wn & group, wo & group
        if gn and go and gn != go:
            return kind_of(old_text)
    return None


def find_conflict(new_text: str, rows: list[Any], writer_power: int, *,
                  exclude_id: str = "") -> Optional[tuple[Any, str]]:
    """The first APPROVED row in ``rows`` that ``new_text`` contradicts and that OUTRANKS a
    writer of ``writer_power`` (a direct user class never needs this: it may override), with
    the attribute kind, else None. ``rows`` are MemoryRef-likes (``id``, ``text``, ``metadata``)."""
    if writer_power >= RANK[OPERATOR]:
        return None
    for r in rows[:2000]:  # newest first (list_by_status): bounded, this runs on the event loop
        meta = getattr(r, "metadata", None) or {}
        if getattr(r, "id", "") == exclude_id or str(meta.get("status") or "") != "approved":
            continue
        text = getattr(r, "text", "") or ""
        if may_override(writer_power, row_class(meta, text)):
            continue
        kind = conflict_kind(new_text, text)
        if kind:
            return r, kind
    return None


def is_candidate(ref: Any) -> bool:
    """Is this MemoryRef a held-back authority CANDIDATE (a disputed write)?"""
    md = getattr(ref, "metadata", None) or {}
    return bool(md.get("authority_blocked")) and str(md.get("status") or "") in ("disputed", "pending")


def log_blocked(writer: str, kind: str, *, user_id: str = "", action: str = "") -> None:
    """The ONE log line for a refused write: labels only - never the fact text. In ``shadow``
    mode the line is ``AUTHORITY_WOULD_BLOCK`` and nothing else changes."""
    tag = "AUTHORITY_BLOCKED" if enabled() else "AUTHORITY_WOULD_BLOCK"
    logger.info("%s writer=%s kind=%s%s%s", tag, writer, kind or "other",
                f" action={action}" if action else "", f" user={user_id}" if user_id else "")
